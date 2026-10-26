"""Pipeline wiring: stage metadata and the StageEvent emitter shared by live runs and fixture building.

The LangGraph StateGraph for all 12 stages is added in Task 14; the emitter below is what it uses to
assign event_seq and progress_pct (Section 4, event rules).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from backend.schemas.common import StageId, StageStatus
from backend.schemas.events import ExplainStagePanelData, StageEvent


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
