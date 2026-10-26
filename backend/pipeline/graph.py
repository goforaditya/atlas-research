"""Pipeline wiring: stage metadata and the StageEvent emitter shared by live runs and fixture building.

The LangGraph StateGraph for all 12 stages is added in Task 14; the emitter below is what it uses to
assign event_seq and progress_pct (Section 4, event rules).
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

from backend.schemas.common import ActorBadge, StageId, StageStatus
from backend.schemas.events import CodeVsLlmAction, ExplainStagePanelData, StageEvent, ToolCallTrace


@dataclass(frozen=True)
class StageMeta:
    number: int
    short_label: str
    title: str
    purpose: str


STAGE_META: dict[StageId, StageMeta] = {
    StageId.S01_ENTITY: StageMeta(1, "Entity", "Entity Resolution",
        "Disambiguate the search string into one canonical legal entity, listing, currency and jurisdiction."),
    StageId.S02_PLAN: StageMeta(2, "Plan", "Research Planning",
        "Generate research sub-questions and route numerical questions to T1/T2 filings only."),
    StageId.S03_RETRIEVE: StageMeta(3, "Filings", "Filing Retrieval",
        "Retrieve official multi-year filings and reject unverified aggregator pages."),
    StageId.S04_EXTRACT: StageMeta(4, "Extract", "Statement Extraction & Reconciliation",
        "Extract consolidated statement line items and verify accounting identities in Python."),
    StageId.S05_METRICS: StageMeta(5, "Metrics", "Deterministic Metrics Engine",
        "Compute every financial metric in pure Python Decimal code."),
    StageId.S06_RED_FLAGS: StageMeta(6, "Red Flags", "Red-Flag Rules Engine",
        "Evaluate declarative YAML threshold rules against the calculation ledger."),
    StageId.S07_NEWS: StageMeta(7, "News", "News Intelligence",
        "Deduplicate press and regulatory disclosures, cluster events and check filing corroboration."),
    StageId.S08_SENTIMENT: StageMeta(8, "Sentiment", "Sentiment with Signal/Noise Filter",
        "Strip bots and duplicates in code, then separate corroborated signal from noise."),
    StageId.S09_MARKET: StageMeta(9, "Peers", "Market and Peers",
        "Benchmark against listed peers using the same engines and code-computed medians."),
    StageId.S10_CONTRADICTION: StageMeta(10, "Conflicts", "Contradiction Resolver",
        "Detect cross-source discrepancies and resolve them by source tier weight."),
    StageId.S11_SYNTHESIS: StageMeta(11, "Synthesise", "Cross-Source Synthesis",
        "Write insights that cite pre-computed ledger tokens and build the provenance graph."),
    StageId.S12_VERIFY: StageMeta(12, "Verify", "Verification Gate",
        "Audit every prose number against the ledger and downgrade unsupported claims."),
}

TERMINAL = {StageStatus.COMPLETED, StageStatus.WARNING, StageStatus.FAILED}


@dataclass
class EventEmitter:
    """Assigns a strictly increasing event_seq and progress_pct = round(100 x finished stages / 12)."""

    run_id: str
    started_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    seq: int = 0
    finished: set[StageId] = field(default_factory=set)
    events: list[StageEvent] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def emit(
        self,
        stage_id: StageId,
        status: StageStatus,
        log_line: str,
        *,
        elapsed_ms: Optional[int] = None,
        explain_panel: Optional[ExplainStagePanelData] = None,
        state_patch: Optional[dict[str, Any]] = None,
    ) -> StageEvent:
        if status in TERMINAL and explain_panel is None:
            raise ValueError(f"{stage_id.value}: explain_panel is required on {status.value} events")
        if status in (StageStatus.COMPLETED, StageStatus.WARNING):
            self.finished.add(stage_id)
        self.seq += 1
        now = int(time.time() * 1000)
        elapsed = elapsed_ms if elapsed_ms is not None else now - self.started_at_ms
        event = StageEvent(
            event_seq=self.seq,
            run_id=self.run_id,
            timestamp_ms=self.started_at_ms + elapsed,
            elapsed_ms=elapsed,
            stage_id=stage_id,
            stage_status=status,
            progress_pct=round(100 * len(self.finished) / 12),
            log_line=log_line,
            explain_panel=explain_panel,
            state_patch=state_patch or {},
        )
        self.events.append(event)
        return event

    async def emit_async(self, *args: Any, **kwargs: Any) -> StageEvent:
        async with self._lock:
            return self.emit(*args, **kwargs)


# ---------------------------------------------------------------- stage plumbing shared by all 12 stages

@dataclass
class StageResult:
    status: StageStatus  # completed | warning | failed
    log_line: str
    patch: dict[str, Any]
    panel: ExplainStagePanelData


@dataclass
class RunContext:
    """Everything a stage needs besides the run state. Source clients are None when unavailable."""

    settings: Any
    llm: Any  # LLMClient
    emitter: EventEmitter
    sec: Any = None  # SecEdgarClient
    india: Any = None  # IndiaFilingsClient
    news: Any = None  # NewsFetcher
    sentiment: Any = None  # SentimentFetcher
    download: Optional[Callable[[str], Awaitable[bytes]]] = None
    close_price: Optional[Callable[..., Awaitable[Any]]] = None
    work_dir: Path = field(default_factory=lambda: Path("."))
    scratch: dict[str, Any] = field(default_factory=dict)  # cross-stage private data (documents, snippets, claims)
    call_seq: int = 0

    def next_call_id(self) -> str:
        self.call_seq += 1
        return f"tc_{self.call_seq:03d}"


def to_json(value: Any) -> Any:
    """Pydantic models and Decimals -> JSON-safe values for state_patch."""
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [to_json(v) for v in value]
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    return value


def trace(ctx: RunContext, tool: str, actor: ActorBadge, args: dict[str, Any], duration_ms: int,
          sample: Any, status: str = "ok") -> ToolCallTrace:
    text = sample if isinstance(sample, str) else json.dumps(to_json(sample), ensure_ascii=False, default=str)
    return ToolCallTrace(call_id=ctx.next_call_id(), tool_name=tool, actor=actor, arguments=to_json(args),
                         duration_ms=max(0, int(duration_ms)), status=status, raw_output_sample=text[:1200])


def from_fetch(ctx: RunContext, t: Any) -> ToolCallTrace:
    """A sources.FetchTrace -> ToolCallTrace (always CODE)."""
    return trace(ctx, t.tool_name, ActorBadge.CODE, t.arguments, t.duration_ms, t.raw_output_sample, t.status)


def panel(stage: StageId, inputs: list[str], calls: list[ToolCallTrace], actions: list[tuple[ActorBadge, str]],
          decision: str, rationale: str, guards: list[str]) -> ExplainStagePanelData:
    meta = STAGE_META[stage]
    return ExplainStagePanelData(
        stage_id=stage, stage_title=meta.title, purpose_one_sentence=meta.purpose, inputs_summary=inputs,
        tool_calls=calls, actions=[CodeVsLlmAction(actor=a, action_summary=s) for a, s in actions],
        decision_made=decision, decision_rationale=rationale, failure_modes_and_guards=guards,
    )


class Timer:
    def __init__(self) -> None:
        self.t0 = time.perf_counter()

    @property
    def ms(self) -> int:
        return int((time.perf_counter() - self.t0) * 1000)


def emit_result(ctx: RunContext, stage: StageId, result: StageResult, elapsed_ms: Optional[int] = None) -> StageEvent:
    """The terminal StageEvent for one stage run, with a JSON-safe state_patch."""
    return ctx.emitter.emit(stage, result.status, result.log_line, elapsed_ms=elapsed_ms, explain_panel=result.panel,
                            state_patch=to_json(result.patch))
