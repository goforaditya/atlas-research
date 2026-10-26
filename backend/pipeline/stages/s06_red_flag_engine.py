"""Stage 6: declarative red-flag rules over the ledger. No model call."""
from __future__ import annotations

from typing import Any

from backend.engine.red_flag_evaluator import evaluate_red_flag_rules
from backend.pipeline.graph import RunContext, StageResult, Timer, panel, trace
from backend.schemas.common import ActorBadge, StageId, StageStatus

S = StageId.S06_RED_FLAGS
C = ActorBadge.CODE
GUARDS = ["Rules are parsed with a whitelisted AST at load time; eval is never called",
          "Any missing input marks the rule insufficient_data instead of guessing",
          "Explanations are filled from formatted ledger values, never raw numbers"]


async def run(ctx: RunContext, state: dict[str, Any]) -> StageResult:
    rules_path = ctx.settings.atlas_rules_path
    t = Timer()
    flags = evaluate_red_flag_rules(state.get("metrics_ledger", []), state.get("line_items", []), rules_path)
    fired = [f for f in flags if f.fired]
    insufficient = [f for f in flags if f.status == "insufficient_data"]
    calls = [trace(ctx, "evaluate_red_flag_rules", C, {"rules": str(rules_path.name)}, t.ms,
                   [{"rule": f.rule_id, "status": f.status, "observed": f.observed_value_formatted} for f in flags])]
    actions = [(C, f"Evaluated {len(flags)} rules against the ledger"),
               (C, f"{len(fired)} fired, {len(flags) - len(fired) - len(insufficient)} clear, "
                   f"{len(insufficient)} not evaluated for missing inputs")]
    p = panel(S, [f"{len(state.get('metrics_ledger', []))} ledger rows", rules_path.name], calls, actions,
              f"{len(fired)} red flag(s) fired" + (f": {', '.join(f.rule_name for f in fired)}" if fired else ""),
              "Thresholds are declared in YAML and applied identically to every company.", GUARDS)
    return StageResult(StageStatus.COMPLETED, f"{len(fired)} of {len(flags)} rules fired", {"red_flags": flags}, p)
