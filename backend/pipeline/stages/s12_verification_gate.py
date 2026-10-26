"""Stage 12: the verification gate. Every prose number must match a cited ledger value. No model call."""
from __future__ import annotations

from typing import Any

from backend.engine.verification_gate import run_verification_gate
from backend.pipeline.graph import RunContext, StageResult, Timer, as_run_state, panel, trace
from backend.schemas.common import ActorBadge, StageId, StageStatus

S = StageId.S12_VERIFY
C = ActorBadge.CODE
GUARDS = ["A number without a resolving citation, or one that differs from the cited formatted value, removes its sentence",
          "Fact or inference cards without metric, item, red-flag or T1/T2 news support become hypotheses",
          "Blocked tokens do not stop the run; the stage reports a warning and the count"]


async def run(ctx: RunContext, state: dict[str, Any]) -> StageResult:
    t = Timer()
    report, gated = run_verification_gate(as_run_state(state))
    calls = [trace(ctx, "run_verification_gate", C, {"insights": len(gated.insights)}, t.ms, report)]
    gaps = []
    if report.unverified_tokens_blocked:
        gaps.append(f"Verification gate removed {report.unverified_tokens_blocked} sentence(s) with unverified numbers")
    actions = [(C, f"Scanned {report.total_numeric_tokens_scanned} numeric token(s); "
                   f"{report.verified_against_ledger} matched the ledger"),
               (C, f"Blocked {report.unverified_tokens_blocked}; downgraded {len(report.claims_downgraded_to_hypothesis)} "
                   "claim(s) to hypothesis")]
    p = panel(S, ["Executive summary, insights, positioning, news summaries"], calls, actions,
              "Passed" if report.passed else f"{report.unverified_tokens_blocked} unverified token(s) removed",
              "Every surviving number traces to a ledger row, line item, peer cell or verbatim snippet.", GUARDS)
    patch = {"verification": report, "insights": gated.insights, "summary": gated.summary, "news_events": gated.news_events,
             "market_advantages": gated.market_advantages, "market_disadvantages": gated.market_disadvantages,
             "market_positioning_prose": gated.market_positioning_prose, "status": "completed"}
    if gaps:
        patch["stated_gaps"] = gaps
    status = StageStatus.COMPLETED if report.passed else StageStatus.WARNING
    return StageResult(status, f"{report.verified_against_ledger}/{report.total_numeric_tokens_scanned} tokens verified; "
                       f"{report.unverified_tokens_blocked} blocked", patch, p)
