"""Pre-build check: every fixture in the manifest is schema-valid and its numbers re-derive exactly.

    python -m scripts.verify_golden_fixtures

Exits non-zero (failing the Docker build) on any problem.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from backend.config import get_settings
from backend.engine.metrics_calculator import compute_all_metrics
from backend.engine.red_flag_evaluator import evaluate_red_flag_rules
from backend.replay.player import FixtureError, load_fixture, project_final_state
from backend.schemas import LineItem, ResearchRunState, StageId, StageStatus

TERMINAL = {StageStatus.COMPLETED, StageStatus.WARNING, StageStatus.FAILED}


def verify_fixture(path: Path) -> list[str]:
    problems: list[str] = []
    try:
        fx = load_fixture(path)
    except (FixtureError, ValueError) as exc:
        return [str(exc)]
    for e in fx.events:
        if e.stage_status in TERMINAL and e.explain_panel is None:
            problems.append(f"event {e.event_seq}: terminal event without explain_panel")
    finished = {e.stage_id for e in fx.events if e.stage_status in (StageStatus.COMPLETED, StageStatus.WARNING)}
    if finished != set(StageId):
        problems.append(f"stages not finished: {sorted(s.value for s in set(StageId) - finished)}")
    raw = project_final_state(fx)
    try:
        state = ResearchRunState.model_validate({
            "run_id": "run_verify", "mode": "replay", "company_query": fx.header["company_query"],
            "status": "running", "started_at_ms": 0, **raw,
        })
    except ValueError as exc:
        return problems + [f"final state invalid: {exc}"]
    if state.status != "completed":
        problems.append("final state is not completed")
    for c in state.reconciliation_checks:
        if c.status == "failed":
            problems.append(f"reconciliation failed: {c.check_id} ({c.failure_reason})")
    # Numbers must re-derive exactly from the fixture's own line items
    annual: list[LineItem] = [li for li in state.line_items if li.field_key != "segment_revenue"]
    recomputed = {r.metric_id: (r.value, r.formatted_value) for r in compute_all_metrics(annual)}
    for row in state.metrics_ledger:
        if recomputed.get(row.metric_id) != (row.value, row.formatted_value):
            problems.append(f"metric {row.metric_id} does not re-derive: {row.formatted_value} vs {recomputed.get(row.metric_id)}")
    flags = {r.rule_id: r.status for r in evaluate_red_flag_rules(state.metrics_ledger, state.line_items, get_settings().atlas_rules_path)}
    for r in state.red_flags:
        if flags.get(r.rule_id) != r.status:
            problems.append(f"red flag {r.rule_id} status {r.status} does not re-evaluate ({flags.get(r.rule_id)})")
    if state.verification is None:
        problems.append("no verification report")
    elif not fx.header.get("synthetic") and not state.verification.passed:
        problems.append(f"verification gate blocked {state.verification.unverified_tokens_blocked} token(s)")
    return problems


def main() -> int:
    golden_dir = get_settings().atlas_golden_dir
    manifest_path = golden_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"FAIL: {manifest_path} missing", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failed = False
    for entry in manifest["replays"]:
        path = golden_dir / entry["file"]
        problems = verify_fixture(path) if path.exists() else [f"{path} missing"]
        if problems:
            failed = True
            print(f"FAIL {entry['replay_id']}:", *problems, sep="\n  - ", file=sys.stderr)
        else:
            print(f"ok   {entry['replay_id']} ({entry['total_events']} events)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
