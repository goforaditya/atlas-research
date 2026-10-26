from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .common import ActorBadge, StageId, StageStatus


class ToolCallTrace(BaseModel):
    call_id: str
    tool_name: str = Field(..., description="e.g., 'search_exchange', 'extract_pdf_tables', 'compute_metrics'")
    actor: ActorBadge
    arguments: dict[str, Any]
    duration_ms: int
    status: Literal["ok", "error", "fallback"]
    raw_output_sample: str = Field(..., max_length=1200, description="Truncated JSON or text sample for Explain panel")


class CodeVsLlmAction(BaseModel):
    actor: ActorBadge
    action_summary: str = Field(..., description="e.g., 'Computed 38 ratios using Python Decimal module'")


class ExplainStagePanelData(BaseModel):
    stage_id: StageId
    stage_title: str
    purpose_one_sentence: str
    inputs_summary: list[str]
    tool_calls: list[ToolCallTrace]
    actions: list[CodeVsLlmAction]
    decision_made: str
    decision_rationale: str
    failure_modes_and_guards: list[str]


class StageEvent(BaseModel):
    event_seq: int = Field(..., description="Monotonically increasing integer starting at 1")
    run_id: str
    timestamp_ms: int = Field(..., description="Epoch milliseconds")
    elapsed_ms: int = Field(..., description="Milliseconds since run start")
    stage_id: StageId
    stage_status: StageStatus
    progress_pct: int = Field(..., ge=0, le=100)
    log_line: str = Field(..., description="Short terminal-style log line for Agent Run card")
    explain_panel: ExplainStagePanelData | None = Field(
        None, description="Required on completed/warning/failed events; optional on running events"
    )
    state_patch: dict[str, Any] = Field(
        default_factory=dict,
        description="Partial update to ResearchRunState unlocked at this step for timeline scrubbing",
    )
