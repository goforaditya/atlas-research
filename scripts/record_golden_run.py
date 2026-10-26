"""Record a live pipeline run as a replay fixture (Phase 2, Section 10.2).

    python -m scripts.record_golden_run --query "Block, Inc." --output data/golden_runs/block_nyse.jsonl.gz \\
        --compare data/golden_inputs/block_nyse.yaml

Runs the full live pipeline, validates every StageEvent and, with --compare, refuses to write the fixture if any
line item differs from the human-verified YAML. The YAML must be complete (no TODO values) for --compare.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import yaml

from backend.engine.unit_normaliser import normalise_units, parse_cell_decimal
from backend.schemas.common import CurrencyUnit
from backend.schemas.events import StageEvent
from backend.schemas.state import ResearchRunState
from scripts.build_golden_fixture import SCHEMA_VERSION, update_manifest, validate_inputs, write_fixture

TOLERANCE = Decimal("0.01")  # in the reporting unit (₹ crore or US$ million): rounding of the printed figure


def fold(run_id: str, query: str, events: list[StageEvent]) -> ResearchRunState:
    """Shallow-merge every state_patch (the client's rule) and validate the result."""
    state: dict[str, Any] = {"run_id": run_id, "mode": "live", "company_query": query, "status": "running",
                             "started_at_ms": events[0].timestamp_ms - events[0].elapsed_ms if events else 0}
    for ev in events:
        state.update(ev.state_patch)
    return ResearchRunState.model_validate(state)


def compare_line_items(state: ResearchRunState, data: dict) -> list[str]:
    """Every verified YAML line item must be present in the live run with the same normalised value."""
    currency = CurrencyUnit(data["entity"]["currency"])
    live = {(li.fiscal_period, li.field_key, li.segment_name): li for li in state.line_items + state.quarterly_items}
    problems = []
    for section in ("line_items", "quarterly_items"):
        for row in data.get(section) or []:
            key = (str(row["period"]), row["field_key"], row.get("segment"))
            expected = normalise_units(parse_cell_decimal(str(row["value"])), row["unit"], currency)
            got = live.get(key)
            label = f"{key[0]} {key[1]}" + (f" ({key[2]})" if key[2] else "")
            if got is None:
                problems.append(f"{label}: missing from the live run (expected {expected})")
            elif (got.value is None) != (expected is None) or (
                    got.value is not None and expected is not None and abs(got.value - expected) > TOLERANCE):
                problems.append(f"{label}: live {got.value} != verified {expected} ({row['doc']} p.{row['page']})")
    return problems


def display_for(state: ResearchRunState) -> dict[str, Any]:
    e = state.entity
    return {"company_name": e.legal_name, "ticker": f"{e.exchange}: {e.ticker}" if e.exchange and e.ticker else (e.ticker or ""),
            "sector": e.sector, "jurisdiction": e.jurisdiction,
            "cached_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}


async def record(query: str, run_id: str) -> list[StageEvent]:
    from backend.config import get_settings
    from backend.pipeline import run_live_pipeline

    events: list[StageEvent] = []

    async def on_event(ev: StageEvent) -> None:
        StageEvent.model_validate_json(ev.model_dump_json())
        events.append(ev)

    await run_live_pipeline(run_id, query, on_event, get_settings(), None)
    return events


async def amain(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--query", required=True)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--compare", type=Path, help="human-verified golden input YAML")
    ap.add_argument("--replay-id", help="defaults to the output file stem")
    args = ap.parse_args(argv)
    replay_id = args.replay_id or args.output.name.split(".")[0]

    data = None
    if args.compare:
        data = yaml.safe_load(args.compare.read_text(encoding="utf-8"))
        errors = validate_inputs(data)
        if errors:
            print(f"{args.compare} is not fully verified yet ({len(errors)} problems):", file=sys.stderr)
            print("\n".join(f"  - {e}" for e in errors[:30]), file=sys.stderr)
            return 1

    run_id = f"run_record_{replay_id}"
    try:
        events = await record(args.query, run_id)
    except Exception as exc:  # noqa: BLE001 - report and exit non-zero
        print(f"Live run failed: {exc}", file=sys.stderr)
        return 1
    state = fold(run_id, args.query, events)
    if state.status != "completed":
        print("The run did not reach the verification gate; fixture not written.", file=sys.stderr)
        return 1
    if data is not None:
        problems = compare_line_items(state, data)
        if problems:
            print(f"{len(problems)} line item(s) differ from {args.compare}; fixture not written:", file=sys.stderr)
            print("\n".join(f"  - {p}" for p in problems), file=sys.stderr)
            return 1

    header = {"_meta": True, "replay_id": replay_id, "company_query": args.query,
              "recorded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "built_by": "record_golden_run",
              "schema_version": SCHEMA_VERSION, "total_events": len(events), "synthetic": False}
    write_fixture(args.output, header, [json.loads(e.model_dump_json()) for e in events])
    manifest_data = {"replay_id": replay_id, "company_query": args.query,
                     "display": (data or {}).get("display") or display_for(state)}
    update_manifest(args.output.parent, manifest_data, header, args.output.name)
    print(f"Wrote {args.output} ({len(events)} events; gate {'passed' if state.verification.passed else 'warning'})")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    return asyncio.run(amain(argv))


if __name__ == "__main__":
    raise SystemExit(main())
