"""Golden-set checks (Section 11.2) plus the fixture builder's refusal rule and replay timing.

Real-company assertions read the expected raw values from the human-verified YAML. While a YAML still
contains TODO figures, its checks are skipped with that reason (the builder refuses to build it).
"""
from __future__ import annotations

import copy
from decimal import Decimal as D
from pathlib import Path

import pytest
import yaml

from backend.config import REPO_ROOT, get_settings
from backend.engine.unit_normaliser import normalise_units, parse_cell_decimal
from backend.replay.player import load_fixture, project_final_state, schedule_delays_ms
from backend.schemas import ResearchRunState
from scripts.build_golden_fixture import GoldenInputError, build_fixture, validate_inputs

INPUTS = REPO_ROOT / "data" / "golden_inputs"
GOLDEN = get_settings().atlas_golden_dir
REAL = ["paytm_nse", "pine_labs_nse", "block_nyse"]


def _yaml(replay_id: str) -> dict:
    return yaml.safe_load((INPUTS / f"{replay_id}.yaml").read_text(encoding="utf-8"))


def _state(replay_id: str) -> ResearchRunState:
    fx = load_fixture(GOLDEN / f"{replay_id}.jsonl.gz")
    raw = project_final_state(fx)
    return ResearchRunState.model_validate({"run_id": "r", "mode": "replay", "company_query": fx.header["company_query"],
                                            "status": "running", "started_at_ms": 0, **raw})


def _require_verified(replay_id: str) -> ResearchRunState:
    problems = validate_inputs(_yaml(replay_id))
    if problems:
        pytest.skip(f"{replay_id}: awaiting human-verified figures ({len(problems)} TODO field(s))")
    if not (GOLDEN / f"{replay_id}.jsonl.gz").exists():
        pytest.fail(f"{replay_id}: YAML is complete but the fixture has not been built")
    return _state(replay_id)


def _yaml_value(data: dict, key: str, period: str) -> D:
    row = next(r for r in data["line_items"] if r["field_key"] == key and str(r["period"]) == period)
    return normalise_units(parse_cell_decimal(str(row["value"])), row["unit"], data["entity"]["currency"])


def _metric(state: ResearchRunState, metric_id: str):
    return next(r for r in state.metrics_ledger if r.metric_id == metric_id)


# ---------------------------------------------------------------- refusal rule

@pytest.mark.parametrize("replay_id", REAL)
def test_builder_refuses_yaml_with_todo_figures(replay_id, tmp_path):
    data = _yaml(replay_id)
    if not validate_inputs(data):
        pytest.skip("YAML is complete")
    with pytest.raises(GoldenInputError, match="Refusing to build"):
        build_fixture(data, tmp_path)


@pytest.mark.parametrize("field", ["page", "verified_by", "value"])
def test_builder_refuses_numeric_field_without_provenance(field, tmp_path):
    data = copy.deepcopy(_yaml("sample_synthetic"))
    data["line_items"][0][field] = "TODO"
    with pytest.raises(GoldenInputError, match=field):
        build_fixture(data, tmp_path)


def test_builder_refuses_document_without_source_url(tmp_path):
    data = copy.deepcopy(_yaml("sample_synthetic"))
    data["documents"]["nexora_ar_fy26"]["source_url"] = ""
    with pytest.raises(GoldenInputError, match="source_url"):
        build_fixture(data, tmp_path)


# ---------------------------------------------------------------- synthetic sample (worked examples, Section 5)

def test_synthetic_sample_reproduces_worked_examples():
    s = _state("sample_synthetic")
    expected = {
        "met_rev_yoy_fy26": "17.6%", "met_rev_cagr_fy26": "28.0%", "met_ebitda_margin_fy26": "15.0%",
        "met_ebitda_margin_chg_bps_fy26": "-100 bps", "met_pat_margin_fy26": "7.8%", "met_roe_fy26": "19.2%",
        "met_dso_fy26": "84.3 d", "met_dso_fy24": "58.7 d", "met_receivables_yoy_fy26": "44.3%",
        "met_cfo_to_pat_fy26": "0.67x", "met_cfo_yoy_fy26": "-24.4%", "met_fcf_fy26": "-₹12 cr", "met_fcf_fy25": "₹122 cr",
        "met_fcf_margin_fy26": "-0.3%", "met_net_debt_to_ebitda_fy26": "0.16x", "met_net_debt_to_ebitda_fy25": "-0.41x",
        "met_gross_debt_yoy_fy26": "67.7%", "met_interest_cover_fy26": "9.4x", "met_interest_cover_fy25": "12.0x",
        "met_current_ratio_fy26": "1.32x", "met_current_ratio_fy24": "1.44x",
    }
    assert {k: _metric(s, k).formatted_value for k in expected} == expected
    assert _metric(s, "met_rev_cagr_fy26").metric_label == "Revenue CAGR (FY22–FY26)"
    assert all(c.status != "failed" for c in s.reconciliation_checks)
    assert s.verification.passed and s.verification.unverified_tokens_blocked == 0
    fired = {r.rule_id for r in s.red_flags if r.fired}
    assert {"RF_01_RECEIVABLES_STRETCH", "RF_02_LOW_CASH_CONVERSION", "RF_03_NEGATIVE_FCF"} <= fired
    assert s.sentiment.positive_pct + s.sentiment.neutral_pct + s.sentiment.negative_pct == 100


# ---------------------------------------------------------------- real companies

def test_paytm():
    s = _require_verified("paytm_nse")
    y = _yaml("paytm_nse")
    r24, r25 = _yaml_value(y, "revenue", "FY24"), _yaml_value(y, "revenue", "FY25")
    assert _metric(s, "met_rev_yoy_fy25").value == (r25 - r24) / r24
    if _yaml_value(y, "pat", "FY25") < 0:
        assert _metric(s, "met_cfo_to_pat_fy25").status == "nm_negative_base"
    assert all(c.status in ("passed", "skipped") for c in s.reconciliation_checks if c.check_id.startswith("rc_bs"))
    rbi = [n for n in s.news_events if n.category == "Regulatory" and n.highest_tier.value == "T1"]
    assert any("rbi.org.in" in n.evidence.source_url for n in rbi)
    assert s.verification.unverified_tokens_blocked == 0


def test_pine_labs():
    s = _require_verified("pine_labs_nse")
    y = _yaml("pine_labs_nse")
    assert s.entity.is_listed and s.entity.jurisdiction == "IN" and s.entity.listing_date == "2025-11-14"
    rev = {li.fiscal_period: li for li in s.line_items if li.field_key == "revenue"}
    first = sorted(rev)[0]
    assert rev[first].value == _yaml_value(y, "revenue", first)
    if "million" in next(r["unit"] for r in y["line_items"] if r["field_key"] == "revenue").lower():
        assert rev[first].unit.value == "INR_CR"
    periods = sorted(rev)
    if len(periods) >= 2:
        a, b = _yaml_value(y, "revenue", periods[0]), _yaml_value(y, "revenue", periods[1])
        assert _metric(s, f"met_rev_yoy_{periods[1].lower()}").value == (b - a) / a
    pat0 = _yaml_value(y, "pat", periods[0])
    if pat0 < 0:
        assert _metric(s, f"met_cfo_to_pat_{periods[0].lower()}").status == "nm_negative_base"
    assert s.verification.unverified_tokens_blocked == 0


def test_block():
    s = _require_verified("block_nyse")
    y = _yaml("block_nyse")
    r23, r24 = _yaml_value(y, "revenue", "FY2023"), _yaml_value(y, "revenue", "FY2024")
    assert _metric(s, "met_rev_yoy_fy2024").value == (r24 - r23) / r23
    capex = abs(_yaml_value(y, "purchase_of_ppe", "FY2024"))
    if any(r["field_key"] == "purchase_of_intangibles" and str(r["period"]) == "FY2024" for r in y["line_items"]):
        capex += abs(_yaml_value(y, "purchase_of_intangibles", "FY2024"))
    cfo = _yaml_value(y, "cfo", "FY2024")
    rev = _yaml_value(y, "revenue", "FY2024")
    assert _metric(s, "met_fcf_fy2024").value == cfo - capex
    assert _metric(s, "met_fcf_margin_fy2024").value == (cfo - capex) / rev
    assert s.entity.cik == "0001512673"
    assert all("sec.gov" in li.evidence.source_url for li in s.line_items if not li.is_derived)
    assert s.verification.unverified_tokens_blocked == 0


# ---------------------------------------------------------------- replay timing (Section 10.3)

@pytest.mark.parametrize("speed, total_ms", [(1.0, 14000), (2.0, 7000), (5.0, 2800), ("instant", 0)])
def test_replay_duration_is_fixed(speed, total_ms):
    fx = load_fixture(GOLDEN / "sample_synthetic.jsonl.gz")
    delays = schedule_delays_ms(fx.events, speed)
    assert len(delays) == fx.header["total_events"]
    assert sum(delays) == pytest.approx(total_ms, abs=1e-6)


def test_manifest_lists_every_fixture():
    import json

    manifest = json.loads((GOLDEN / "manifest.json").read_text(encoding="utf-8"))
    for entry in manifest["replays"]:
        assert Path(GOLDEN / entry["file"]).exists()
        assert load_fixture(GOLDEN / entry["file"]).header["total_events"] == entry["total_events"]
