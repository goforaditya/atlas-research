"""Pipeline wiring: stage metadata, the StageEvent emitter and the LangGraph StateGraph for all 12 stages.

Topology (Section 4): S1 -> S2 -> {S3 -> S4 -> S5, S7}; S5 -> {S6, S9}; {S5, S7} -> S8; {S6, S8, S9} -> S10 -> S11 -> S12.
Every node emits a running event and one terminal event; event_seq comes from one lock-protected counter.
"""
from __future__ import annotations

import asyncio
import json
import operator
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Optional, TypedDict

from pydantic import BaseModel

from backend.schemas.common import ActorBadge, StageId, StageStatus
from backend.schemas.events import CodeVsLlmAction, ExplainStagePanelData, StageEvent, ToolCallTrace
from backend.schemas.state import ResearchRunState


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


STATE_FIELDS = set(ResearchRunState.model_fields) - {"run_id", "mode", "company_query", "status", "started_at_ms"}


def as_run_state(state: dict[str, Any]) -> ResearchRunState:
    """A ResearchRunState view of the graph state (stage outputs are already model instances)."""
    return ResearchRunState(run_id=state.get("run_id", "run"), mode="live", company_query=state.get("company_query", ""),
                            status="running", started_at_ms=state.get("started_at_ms", 0),
                            **{k: v for k, v in state.items() if k in STATE_FIELDS and v is not None})


# ---------------------------------------------------------------- the StateGraph


def merge_registry(a: Optional[dict[str, Any]], b: Optional[dict[str, Any]]) -> dict[str, Any]:
    return {**(a or {}), **(b or {})}


class GraphState(TypedDict, total=False):
    run_id: str
    company_query: str
    started_at_ms: int
    today: date
    status: str
    entity: Any
    sub_questions: list
    fiscal_years: list
    line_items: list
    quarterly_items: list
    quarterly_metrics: list
    management_roster: list
    reconciliation_checks: list
    metrics_ledger: list
    red_flags: list
    news_events: list
    sentiment: Any
    peer_comps: list
    market_advantages: list
    market_disadvantages: list
    market_positioning_prose: Optional[str]
    insights: list
    provenance_nodes: list
    provenance_edges: list
    contradictions: list
    summary: Any
    verification: Any
    # Keys several stages contribute to (written concurrently by S3/S7 and S6/S8/S9).
    stated_gaps: Annotated[list, operator.add]
    evidence_registry: Annotated[dict, merge_registry]
    sources_read_count: Annotated[int, operator.add]


REDUCED_KEYS = ("stated_gaps", "evidence_registry", "sources_read_count")
CRITICAL_STAGES = {StageId.S01_ENTITY, StageId.S03_RETRIEVE, StageId.S04_EXTRACT}
RUNNING_LOG: dict[StageId, str] = {
    StageId.S01_ENTITY: "Searching BSE and SEC registries",
    StageId.S02_PLAN: "Generating 12 sub-questions across 4 buckets",
    StageId.S03_RETRIEVE: "Fetching annual reports and statements",
    StageId.S04_EXTRACT: "Locating statement pages and extracting tables",
    StageId.S05_METRICS: "compute_all_metrics(line_items)",
    StageId.S06_RED_FLAGS: "Evaluating the red-flag rules",
    StageId.S07_NEWS: "Fetching press and exchange releases",
    StageId.S08_SENTIMENT: "Fetching public posts and filtering bots",
    StageId.S09_MARKET: "Selecting listed sector peers",
    StageId.S10_CONTRADICTION: "Comparing T1 figures with T2/T3/T4 claims",
    StageId.S11_SYNTHESIS: "Synthesising insights with [[token]] citations",
    StageId.S12_VERIFY: "Scanning prose for numeric tokens",
}


class PipelineHalted(Exception):
    def __init__(self, stage: StageId, message: str):
        super().__init__(f"{stage.value}: {message}")
        self.stage = stage


@dataclass
class Reducer:
    """Running totals of the reducer keys, so each event carries the already-reduced value (Section 4)."""

    values: dict[str, Any] = field(default_factory=lambda: {"stated_gaps": [], "evidence_registry": {}, "sources_read_count": 0})

    def add(self, patch: dict[str, Any]) -> dict[str, Any]:
        if "stated_gaps" in patch:
            self.values["stated_gaps"] = self.values["stated_gaps"] + list(patch["stated_gaps"])
        if "evidence_registry" in patch:
            self.values["evidence_registry"] = {**self.values["evidence_registry"], **patch["evidence_registry"]}
        if "sources_read_count" in patch:
            self.values["sources_read_count"] += int(patch["sources_read_count"])
        return {**patch, **{k: self.values[k] for k in REDUCED_KEYS if k in patch}}


def stage_runners() -> dict[StageId, Callable[[RunContext, dict[str, Any]], Awaitable[StageResult]]]:
    from backend.pipeline.stages import (
        s01_entity_resolution,
        s02_research_planning,
        s03_filing_retrieval,
        s04_statement_extraction,
        s05_metrics_engine,
        s06_red_flag_engine,
        s07_news_intelligence,
        s08_sentiment_filter,
        s09_market_peers,
        s10_contradiction_resolver,
        s11_synthesis,
        s12_verification_gate,
    )

    mods = [s01_entity_resolution, s02_research_planning, s03_filing_retrieval, s04_statement_extraction,
            s05_metrics_engine, s06_red_flag_engine, s07_news_intelligence, s08_sentiment_filter, s09_market_peers,
            s10_contradiction_resolver, s11_synthesis, s12_verification_gate]
    return {sid: m.run for sid, m in zip(StageId, mods, strict=True)}


def build_graph(ctx: RunContext, on_event: Callable[[StageEvent], Awaitable[None]],
                runners: Optional[dict[StageId, Callable[..., Awaitable[StageResult]]]] = None) -> Any:
    from langgraph.graph import END, START, StateGraph

    runners = runners or stage_runners()
    reducer = Reducer()

    async def send(stage: StageId, status: StageStatus, log: str, panel_data: Optional[ExplainStagePanelData],
                   patch: dict[str, Any]) -> None:
        async with ctx.emitter._lock:
            full = reducer.add(patch) if patch else {}
            ev = ctx.emitter.emit(stage, status, log, explain_panel=panel_data, state_patch=to_json(full))
            await on_event(ev)

    def node(stage: StageId) -> Callable[[GraphState], Awaitable[dict[str, Any]]]:
        async def run_stage(state: GraphState) -> dict[str, Any]:
            await send(stage, StageStatus.RUNNING, RUNNING_LOG[stage], None, {})
            try:
                result = await runners[stage](ctx, dict(state))
            except PipelineHalted:
                raise
            except Exception as exc:  # noqa: BLE001 - a stage bug becomes a failed event, not a silent hang
                result = StageResult(StageStatus.FAILED, f"{STAGE_META[stage].title} failed: {type(exc).__name__}", {},
                                     panel(stage, [], [], [(ActorBadge.CODE, "The stage raised an error")], "Stage failed",
                                           f"{type(exc).__name__}: {exc}"[:400], []))
            await send(stage, result.status, result.log_line, result.panel, result.patch)
            if result.status == StageStatus.FAILED and stage in CRITICAL_STAGES:
                raise PipelineHalted(stage, result.log_line)
            return {k: v for k, v in result.patch.items() if k in GraphState.__annotations__}
        return run_stage

    g = StateGraph(GraphState)
    for sid in StageId:
        g.add_node(sid.value, node(sid))
    s = {sid: sid.value for sid in StageId}
    g.add_edge(START, s[StageId.S01_ENTITY])
    g.add_edge(s[StageId.S01_ENTITY], s[StageId.S02_PLAN])
    g.add_edge(s[StageId.S02_PLAN], s[StageId.S03_RETRIEVE])
    g.add_edge(s[StageId.S02_PLAN], s[StageId.S07_NEWS])
    g.add_edge(s[StageId.S03_RETRIEVE], s[StageId.S04_EXTRACT])
    g.add_edge(s[StageId.S04_EXTRACT], s[StageId.S05_METRICS])
    g.add_edge(s[StageId.S05_METRICS], s[StageId.S06_RED_FLAGS])
    g.add_edge(s[StageId.S05_METRICS], s[StageId.S09_MARKET])
    g.add_edge([s[StageId.S05_METRICS], s[StageId.S07_NEWS]], s[StageId.S08_SENTIMENT])
    g.add_edge([s[StageId.S06_RED_FLAGS], s[StageId.S08_SENTIMENT], s[StageId.S09_MARKET]], s[StageId.S10_CONTRADICTION])
    g.add_edge(s[StageId.S10_CONTRADICTION], s[StageId.S11_SYNTHESIS])
    g.add_edge(s[StageId.S11_SYNTHESIS], s[StageId.S12_VERIFY])
    g.add_edge(s[StageId.S12_VERIFY], END)
    return g.compile()


async def execute(ctx: RunContext, company_query: str, on_event: Callable[[StageEvent], Awaitable[None]], *,
                  today: Optional[date] = None, runners: Optional[dict[StageId, Any]] = None) -> dict[str, Any]:
    """Run the compiled graph; returns the final graph state. Raises PipelineHalted on a critical stage failure."""
    graph = build_graph(ctx, on_event, runners)
    initial: GraphState = {"run_id": ctx.emitter.run_id, "company_query": company_query, "started_at_ms": ctx.emitter.started_at_ms,
                           "today": today or datetime.now(UTC).date(), "stated_gaps": [], "evidence_registry": {},
                           "sources_read_count": 0}
    return await graph.ainvoke(initial)
