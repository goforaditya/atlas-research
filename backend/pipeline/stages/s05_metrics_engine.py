"""Stage 5: the calculation ledger, in Decimal code. No model call."""
from __future__ import annotations

from typing import Any

from backend.engine.metrics_calculator import compute_all_metrics, compute_quarterly_metrics
from backend.pipeline.graph import RunContext, StageResult, Timer, panel, trace
from backend.schemas.common import ActorBadge, StageId, StageStatus

S = StageId.S05_METRICS
C = ActorBadge.CODE
GUARDS = ["Division by zero: status nm_zero_div with value None",
          "Negative base for growth or CAGR: status nm_negative_base with value None",
          "Missing input: status missing_input; the metric is shown as unavailable, never estimated"]


async def run(ctx: RunContext, state: dict[str, Any]) -> StageResult:
    items = state.get("line_items", [])
    t = Timer()
    ledger = compute_all_metrics(items)
    quarterly = compute_quarterly_metrics(state.get("quarterly_items", []))
    computed = sum(r.status == "computed" for r in ledger)
    sample = [{"metric_id": r.metric_id, "formula": r.formula_substituted, "value": r.formatted_value,
               "source": r.primary_source_ref} for r in ledger if r.status == "computed"][:3]
    calls = [trace(ctx, "compute_all_metrics", C, {"line_items": len(items)}, t.ms, sample)]
    if quarterly:
        calls.append(trace(ctx, "compute_quarterly_metrics", C, {"quarterly_items": len(state.get("quarterly_items", []))},
                           0, [r.metric_id for r in quarterly]))
    actions = [(C, f"Computed {len(ledger)} ledger rows with Decimal arithmetic ({computed} with values)"),
               (C, "Recorded each formula template, substituted inputs and source page")]
    p = panel(S, [f"{len(items)} line items"], calls, actions, f"{computed} of {len(ledger)} metrics computed",
              "Every metric is a fixed formula over cited line items; the model never computes a figure.", GUARDS)
    status = StageStatus.COMPLETED if ledger else StageStatus.WARNING
    return StageResult(status, f"Computed {computed} metrics; {len(ledger) - computed} not meaningful or missing inputs",
                       {"metrics_ledger": ledger, "quarterly_metrics": quarterly}, p)
