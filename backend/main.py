"""FastAPI entry point: REST + SSE under /api/v1, static SPA mount, and lifespan hooks (Section 8)."""
from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.exceptions import HTTPException as StarletteHTTPException
from ulid import ULID

from backend.config import Settings, get_settings
from backend.db import Database
from backend.engine.exporter import build_markdown, export_filename, render_pdf
from backend.replay.player import Fixture, load_fixture, parse_speed, play, project_final_state, rebind
from backend.schemas import (
    CreateRunRequest,
    CreateRunResponse,
    HealthResponse,
    ReplayInfo,
    ReplayListResponse,
    ResearchRunState,
    RunMode,
    StageEvent,
)

API = "/api/v1"
PAGE_NAME = re.compile(r"^[a-z0-9_]+\.(png|pdf)$")
HEARTBEAT_SEC = 15


class ApiError(Exception):
    def __init__(self, status: int, error_code: str, message: str, details: Optional[dict] = None):
        self.status, self.error_code, self.message, self.details = status, error_code, message, details


def _error(status: int, code: str, message: str, details: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error_code": code, "message": message, "details": details})


# ---------------------------------------------------------------- run registry

@dataclass
class RunRecord:
    """A run known to this instance. Replay runs replay their fixture per connection; live runs buffer events."""

    run_id: str
    mode: RunMode
    company_query: str
    started_at_ms: int
    replay_id: Optional[str] = None
    replay_speed: float = 1.0
    events: list[StageEvent] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    done: bool = False
    error: Optional[dict] = None
    new_event: asyncio.Event = field(default_factory=asyncio.Event)
    persisted: bool = False

    def add_event(self, event: StageEvent) -> None:
        self.events.append(event)
        self.state.update(event.state_patch)
        self.new_event.set()


class LiveRateLimiter:
    """Sliding one-hour window of live-run starts per client IP (in memory, single instance)."""

    def __init__(self, per_hour: int):
        self.per_hour = per_hour
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, ip: str) -> bool:
        now = time.monotonic()
        q = self.hits[ip]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= self.per_hour:
            return False
        q.append(now)
        return True


def new_run_id() -> str:
    return f"run_{ULID()}"


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------- fixtures

def load_replays(settings: Settings) -> tuple[dict[str, Fixture], dict[str, dict]]:
    golden = settings.atlas_golden_dir
    manifest_path = golden / "manifest.json"
    fixtures: dict[str, Fixture] = {}
    entries: dict[str, dict] = {}
    if not manifest_path.exists():
        return fixtures, entries
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest.get("replays", []):
        path = golden / entry["file"]
        if path.exists():
            fixtures[entry["replay_id"]] = load_fixture(path)
            entries[entry["replay_id"]] = entry
    return fixtures, entries


def replay_state(app: FastAPI, record: RunRecord) -> dict[str, Any]:
    fx: Fixture = app.state.fixtures[record.replay_id]
    events = [rebind(e, record.run_id, record.started_at_ms) for e in fx.events]
    final = project_final_state(fx)
    return {
        "run_id": record.run_id, "mode": record.mode.value, "company_query": record.company_query,
        "started_at_ms": record.started_at_ms, "sources_read_count": 0, **final,
        "status": final.get("status", "completed"),
        "completed_at_ms": record.started_at_ms + (fx.events[-1].elapsed_ms if fx.events else 0),
        "events": [e.model_dump(mode="json") for e in events],
    }


def run_state(app: FastAPI, record: RunRecord) -> dict[str, Any]:
    if record.mode is RunMode.REPLAY:
        return replay_state(app, record)
    return {
        "run_id": record.run_id, "mode": record.mode.value, "company_query": record.company_query,
        "started_at_ms": record.started_at_ms, "sources_read_count": 0,
        "status": "completed" if record.done and not record.error else ("failed" if record.error else "running"),
        **record.state,
        "events": [e.model_dump(mode="json") for e in record.events],
    }


# ---------------------------------------------------------------- app

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    db = Database(settings.atlas_db_path)
    await db.connect()
    app.state.db = db
    app.state.fixtures, app.state.replay_entries = load_replays(settings)
    app.state.runs = {}
    app.state.rate_limiter = LiveRateLimiter(settings.max_live_runs_per_hour_per_ip)
    try:
        yield
    finally:
        await db.close()


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Atlas Research", version=settings.version, lifespan=lifespan)
    app.state.settings = settings

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _error(exc.status, exc.error_code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(422, "VALIDATION_ERROR", "Request validation failed",
                      {"errors": json.loads(json.dumps(exc.errors(), default=str))})

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(exc.status_code, "HTTP_ERROR")
        return _error(exc.status_code, code, str(exc.detail))

    def get_record(run_id: str) -> RunRecord:
        record = app.state.runs.get(run_id)
        if record is None:
            raise ApiError(404, "RUN_NOT_FOUND", f"Unknown run {run_id}")
        return record

    # 1. health
    @app.get(f"{API}/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok", sqlite_wal=await app.state.db.wal_enabled(),
            golden_runs_loaded=sorted(app.state.fixtures), llm_configured=settings.llm_configured,
            version=settings.version,
        )

    # 2. replays
    @app.get(f"{API}/replays", response_model=ReplayListResponse)
    async def replays() -> ReplayListResponse:
        out = []
        for rid, fx in app.state.fixtures.items():
            entry = app.state.replay_entries[rid]
            summary = project_final_state(fx).get("summary") or {}
            out.append(ReplayInfo(
                replay_id=rid, company_name=entry["company_name"], ticker=entry["ticker"], sector=entry["sector"],
                jurisdiction=entry["jurisdiction"], cached_at=entry.get("cached_at", ""),
                total_events=fx.header["total_events"], headline_verdict=summary.get("verdict_headline", ""),
                synthetic=bool(fx.header.get("synthetic", False)), company_query=fx.header["company_query"],
            ))
        out.sort(key=lambda r: (r.synthetic, r.company_name))
        return ReplayListResponse(replays=out)

    # 3. create run
    @app.post(f"{API}/runs", status_code=201, response_model=CreateRunResponse)
    async def create_run(body: CreateRunRequest, request: Request) -> CreateRunResponse:
        ip = client_ip(request)
        run_id = new_run_id()
        now = int(time.time() * 1000)
        if body.mode is RunMode.REPLAY:
            if not body.replay_id or body.replay_id not in app.state.fixtures:
                raise ApiError(404, "REPLAY_NOT_FOUND", f"Unknown replay {body.replay_id!r}",
                               {"available": sorted(app.state.fixtures)})
            record = RunRecord(run_id, RunMode.REPLAY, body.company_query, now, replay_id=body.replay_id,
                               replay_speed=body.replay_speed)
            speed = body.replay_speed
            stream_url = f"{API}/runs/{run_id}/stream?speed={speed}&replay_id={body.replay_id}"
        else:
            suggest = {"suggest_replay_ids": sorted(app.state.fixtures)}
            if not settings.llm_configured:
                raise ApiError(503, "LLM_NOT_CONFIGURED", "Live mode needs GEMINI_API_KEY; use a replay instead.", suggest)
            if await app.state.db.llm_spend_today() >= settings.daily_llm_budget_usd:
                raise ApiError(503, "LLM_BUDGET_EXHAUSTED", "Today's live LLM budget is spent; use a replay.", suggest)
            if not app.state.rate_limiter.allow(ip):
                raise ApiError(429, "LIVE_QUOTA_EXCEEDED", "Live run limit reached for this hour.", suggest)
            record = RunRecord(run_id, RunMode.LIVE, body.company_query, now)
            start_live_run(app, record)
            stream_url = f"{API}/runs/{run_id}/stream"
        app.state.runs[run_id] = record
        await app.state.db.insert_run(run_id, record.mode.value, body.company_query, record.replay_id, ip)
        return CreateRunResponse(run_id=run_id, mode=record.mode, company_query=body.company_query,
                                 stream_url=stream_url, state_url=f"{API}/runs/{run_id}")

    # 4. stream
    @app.get(f"{API}/runs/{{run_id}}/stream")
    async def stream(run_id: str, request: Request, speed: Optional[str] = Query(None),
                     replay_id: Optional[str] = Query(None)) -> EventSourceResponse:
        record = app.state.runs.get(run_id)
        if record is None:
            # Replay runs are stateless: restart the named replay afresh under the same run id.
            if replay_id and replay_id in app.state.fixtures:
                fx = app.state.fixtures[replay_id]
                record = RunRecord(run_id, RunMode.REPLAY, fx.header["company_query"], int(time.time() * 1000),
                                   replay_id=replay_id)
                app.state.runs[run_id] = record
            else:
                raise ApiError(404, "RUN_NOT_FOUND", f"Unknown run {run_id}")
        try:
            speed_value = parse_speed(speed if speed is not None else record.replay_speed)
        except ValueError as exc:
            raise ApiError(422, "VALIDATION_ERROR", f"Invalid speed {speed!r}") from exc
        last_id = request.headers.get("last-event-id")
        after_seq = int(last_id) if last_id and last_id.isdigit() else 0
        gen = (replay_stream(app, record, speed_value, after_seq) if record.mode is RunMode.REPLAY
               else live_stream(app, record, after_seq))
        return EventSourceResponse(
            gen, ping=HEARTBEAT_SEC,
            ping_message_factory=lambda: ServerSentEvent(event="heartbeat", data=json.dumps({"ts": int(time.time() * 1000)})),
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # 5. state snapshot
    @app.get(f"{API}/runs/{{run_id}}")
    async def get_run(run_id: str) -> JSONResponse:
        return JSONResponse(run_state(app, get_record(run_id)))

    # 6. evidence
    @app.get(f"{API}/runs/{{run_id}}/evidence/{{evidence_id}}")
    async def evidence(run_id: str, evidence_id: str) -> JSONResponse:
        state = run_state(app, get_record(run_id))
        ev = find_evidence(state, evidence_id)
        if ev is None:
            raise ApiError(404, "EVIDENCE_NOT_FOUND", f"Unknown evidence {evidence_id}")
        image = ev.get("excerpt_image_path")
        pdf_name = image[:-4] + ".pdf" if image else None
        pdf_exists = bool(pdf_name) and (settings.atlas_golden_dir / "fixtures_pdfs" / pdf_name).exists()
        items = [li for li in state.get("line_items", []) if li["evidence"]["evidence_id"] == evidence_id]
        periods = {li["fiscal_period"] for li in items}
        checks = [c for c in state.get("reconciliation_checks", []) if c["fiscal_period"] in periods]
        return JSONResponse({
            "evidence": ev,
            "excerpt_image_url": f"{API}/fixtures/pages/{image}" if image else None,
            "source_pdf_url": f"{API}/fixtures/pages/{pdf_name}" if pdf_exists else None,
            "linked_line_items": items,
            "linked_reconciliation_checks": checks,
        })

    # 6b. page images
    @app.get(f"{API}/fixtures/pages/{{filename}}")
    async def fixture_page(filename: str) -> FileResponse:
        if not PAGE_NAME.fullmatch(filename):
            raise ApiError(404, "EVIDENCE_NOT_FOUND", "Invalid page filename")
        path = settings.atlas_golden_dir / "fixtures_pdfs" / filename
        if not path.is_file():
            raise ApiError(404, "EVIDENCE_NOT_FOUND", f"No page {filename}")
        return FileResponse(path)

    # 7. export the clean report
    @app.get(f"{API}/runs/{{run_id}}/export")
    async def export(run_id: str, format: str = Query("md")) -> Response:
        if format not in ("md", "pdf"):
            raise ApiError(422, "VALIDATION_ERROR", "format must be md or pdf")
        record = get_record(run_id)
        if record.mode is RunMode.LIVE and not record.done:
            raise ApiError(409, "RUN_NOT_COMPLETE", "The run is still in progress; export it once it completes.")
        state = ResearchRunState.model_validate(run_state(app, record))
        synthetic = bool(record.replay_id and app.state.fixtures[record.replay_id].header.get("synthetic"))
        filename = export_filename(state, record.replay_id, format)
        disposition = {"Content-Disposition": f'attachment; filename="{filename}"'}
        if format == "md":
            body = build_markdown(state, synthetic=synthetic)
            return Response(body, media_type="text/markdown; charset=utf-8", headers=disposition)
        pdf = await asyncio.to_thread(render_pdf, state, synthetic=synthetic)
        return Response(pdf, media_type="application/pdf", headers=disposition)

    mount_spa(app, settings.atlas_frontend_dist)
    return app


def find_evidence(state: dict[str, Any], evidence_id: str) -> Optional[dict]:
    reg = state.get("evidence_registry") or {}
    if evidence_id in reg:
        return reg[evidence_id]
    pools = [
        *(li["evidence"] for li in state.get("line_items", [])),
        *(li["evidence"] for li in state.get("quarterly_items", [])),
        *(n["evidence"] for n in state.get("news_events", [])),
        *(p["evidence"] for p in ((state.get("sentiment") or {}).get("items") or [])),
        *(c[k] for c in state.get("contradictions", []) for k in ("source_a_evidence", "source_b_evidence")),
    ]
    return next((e for e in pools if e["evidence_id"] == evidence_id), None)


async def replay_stream(app: FastAPI, record: RunRecord, speed: Optional[float], after_seq: int) -> AsyncIterator[ServerSentEvent]:
    fx: Fixture = app.state.fixtures[record.replay_id]
    if after_seq == 0:
        yield ServerSentEvent(event="run_started", retry=3000, data=json.dumps({
            "run_id": record.run_id, "mode": record.mode.value, "company_query": record.company_query,
            "started_at_ms": record.started_at_ms}))
    else:
        yield ServerSentEvent(comment="resumed", retry=3000)
    async for event in play(fx, record.run_id, speed, after_seq=after_seq, started_at_ms=record.started_at_ms):
        yield ServerSentEvent(event="stage_event", id=str(event.event_seq), data=event.model_dump_json())
    final = project_final_state(fx)
    yield ServerSentEvent(event="run_completed", data=json.dumps({
        "run_id": record.run_id, "elapsed_ms": fx.events[-1].elapsed_ms if fx.events else 0,
        "sources_read_count": final.get("sources_read_count", 0),
        "metrics_count": len(final.get("metrics_ledger", [])),
    }))
    if not record.persisted:
        record.persisted = True
        await app.state.db.finish_run(record.run_id, "completed", {"replay_id": record.replay_id})


async def live_stream(app: FastAPI, record: RunRecord, after_seq: int) -> AsyncIterator[ServerSentEvent]:
    if after_seq == 0:
        yield ServerSentEvent(event="run_started", retry=3000, data=json.dumps({
            "run_id": record.run_id, "mode": record.mode.value, "company_query": record.company_query,
            "started_at_ms": record.started_at_ms}))
    else:
        yield ServerSentEvent(comment="resumed", retry=3000)
    sent = after_seq
    while True:
        pending = [e for e in record.events if e.event_seq > sent]
        for event in pending:
            sent = event.event_seq
            yield ServerSentEvent(event="stage_event", id=str(event.event_seq), data=event.model_dump_json())
        if record.done:
            break
        record.new_event.clear()
        try:
            await asyncio.wait_for(record.new_event.wait(), timeout=HEARTBEAT_SEC)
        except TimeoutError:
            yield ServerSentEvent(event="heartbeat", data=json.dumps({"ts": int(time.time() * 1000)}))
    if record.error:
        yield ServerSentEvent(event="run_error", data=json.dumps({"run_id": record.run_id, **record.error}))
    else:
        yield ServerSentEvent(event="run_completed", data=json.dumps({
            "run_id": record.run_id, "elapsed_ms": record.events[-1].elapsed_ms if record.events else 0,
            "sources_read_count": record.state.get("sources_read_count", 0),
            "metrics_count": len(record.state.get("metrics_ledger", [])),
        }))


def start_live_run(app: FastAPI, record: RunRecord) -> None:
    """Start the live pipeline as a background task; events are buffered on the record and persisted."""
    from backend.pipeline import LIVE_PIPELINE_READY, run_live_pipeline

    if not LIVE_PIPELINE_READY:
        raise ApiError(503, "LLM_NOT_CONFIGURED", "The live pipeline is not available in this build; use a replay.",
                       {"suggest_replay_ids": sorted(app.state.fixtures)})

    async def on_event(event: StageEvent) -> None:
        record.add_event(event)
        await app.state.db.append_event(record.run_id, event.event_seq, event.model_dump_json())

    async def runner() -> None:
        try:
            await run_live_pipeline(record.run_id, record.company_query, on_event, app.state.settings, app.state.db)
        except Exception as exc:  # surfaced to the client as run_error
            stage = record.events[-1].stage_id.value if record.events else "s01_entity_resolution"
            record.error = {"stage_id": stage, "error_code": "PIPELINE_ERROR", "message": str(exc)[:500]}
        finally:
            record.done = True
            record.new_event.set()
            await app.state.db.finish_run(record.run_id, "failed" if record.error else "completed", record.state)

    app.state.__dict__.setdefault("tasks", set())
    task = asyncio.create_task(runner())
    app.state.tasks.add(task)
    task.add_done_callback(app.state.tasks.discard)


def mount_spa(app: FastAPI, dist: Path) -> None:
    """Serve the built SPA (frontend/dist) with an index.html fallback for client-side routes."""

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> Response:
        if full_path.startswith("api/"):
            raise ApiError(404, "NOT_FOUND", f"No route /{full_path}")
        index = dist / "index.html"
        if not index.exists():
            return JSONResponse({"message": "Atlas Research API. Build the frontend (cd frontend && npm run build) to serve the UI."})
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)


app = create_app()
