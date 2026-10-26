from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .common import CurrencyUnit, EvidenceCitation, RunMode
from .events import StageEvent
from .financials import LineItem, MetricLedgerRow, ReconciliationCheck, RedFlagResult
from .intelligence import (
    ContradictionRecord,
    InsightCard,
    ManagementPerson,
    NewsEvent,
    PeerCompRow,
    ProvenanceEdge,
    ProvenanceNode,
    SentimentAggregate,
)


class ResolvedEntity(BaseModel):
    query: str
    legal_name: str
    ticker: str | None = None
    exchange: str | None = None
    jurisdiction: Literal["IN", "US", "GLOBAL_PRIVATE"]
    is_listed: bool
    cik: str | None = Field(None, description="SEC CIK, zero-padded to 10 digits, US only")
    bse_scrip_code: str | None = None
    listing_date: str | None = None
    sector: str
    headquarters: str
    fy_end_month: str
    currency: CurrencyUnit
    rejected_candidates: list[str] = Field(default_factory=list)


class ExecutiveSummary(BaseModel):
    verdict_headline: str
    verdict_prose: str
    fact_count: int = Field(..., description="Counted by code from insights after Stage 12, never by the LLM")
    inference_count: int
    hypothesis_count: int
    business_overview: str
    revenue_mix_summary: str
    management_summary: str
    governance_watch_note: str | None = None
    opportunities: list[str]
    risks: list[str]
    quarterly_direction_note: str
    analyst_follow_up_notes: list[str]


class VerificationGateReport(BaseModel):
    total_numeric_tokens_scanned: int
    verified_against_ledger: int
    unverified_tokens_blocked: int
    claims_downgraded_to_hypothesis: list[str]
    passed: bool


class ResearchRunState(BaseModel):
    run_id: str
    mode: RunMode
    company_query: str
    status: Literal["running", "completed", "failed"]
    started_at_ms: int
    completed_at_ms: int | None = None
    sources_read_count: int = 0
    entity: ResolvedEntity | None = None
    sub_questions: list[str] = Field(default_factory=list)
    fiscal_years: list[str] = Field(default_factory=list)
    line_items: list[LineItem] = Field(default_factory=list)
    quarterly_items: list[LineItem] = Field(
        default_factory=list, description="Latest 5 quarters, unaudited; never mixed into line_items"
    )
    quarterly_metrics: list[MetricLedgerRow] = Field(
        default_factory=list, description="QoQ / YoY revenue and EBITDA margin only"
    )
    management_roster: list[ManagementPerson] = Field(default_factory=list)
    reconciliation_checks: list[ReconciliationCheck] = Field(default_factory=list)
    metrics_ledger: list[MetricLedgerRow] = Field(default_factory=list)
    red_flags: list[RedFlagResult] = Field(default_factory=list)
    news_events: list[NewsEvent] = Field(default_factory=list)
    sentiment: SentimentAggregate | None = None
    peer_comps: list[PeerCompRow] = Field(default_factory=list)
    market_advantages: list[str] = Field(default_factory=list)
    market_disadvantages: list[str] = Field(default_factory=list)
    market_positioning_prose: str | None = None
    insights: list[InsightCard] = Field(default_factory=list)
    provenance_nodes: list[ProvenanceNode] = Field(default_factory=list)
    provenance_edges: list[ProvenanceEdge] = Field(default_factory=list)
    contradictions: list[ContradictionRecord] = Field(default_factory=list)
    summary: ExecutiveSummary | None = None
    verification: VerificationGateReport | None = None
    evidence_registry: dict[str, EvidenceCitation] = Field(default_factory=dict)
    stated_gaps: list[str] = Field(default_factory=list)
    events: list[StageEvent] = Field(default_factory=list)


# --- API request/response envelopes (Section 8) ---

class CreateRunRequest(BaseModel):
    company_query: str = Field(..., min_length=1, max_length=200)
    mode: RunMode
    replay_id: str | None = None
    replay_speed: float = 1.0


class CreateRunResponse(BaseModel):
    run_id: str
    mode: RunMode
    company_query: str
    stream_url: str
    state_url: str


class ReplayInfo(BaseModel):
    replay_id: str
    company_name: str
    ticker: str
    sector: str
    jurisdiction: Literal["IN", "US", "GLOBAL_PRIVATE"]
    cached_at: str
    total_events: int
    headline_verdict: str


class ReplayListResponse(BaseModel):
    replays: list[ReplayInfo]


class HealthResponse(BaseModel):
    status: str
    sqlite_wal: bool
    golden_runs_loaded: list[str]
    llm_configured: bool
    version: str


class ErrorResponse(BaseModel):
    error_code: str
    message: str
    details: dict | None = None
