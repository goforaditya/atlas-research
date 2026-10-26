"""Contract tests: schema round-trips (Task 1). REST + SSE contract tests are added in Task 5."""
from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest
from pydantic import ValidationError

from backend.schemas import (
    ActorBadge,
    CodeVsLlmAction,
    ContradictionRecord,
    CurrencyUnit,
    EvidenceCitation,
    ExecutiveSummary,
    ExplainStagePanelData,
    InsightCard,
    LineItem,
    MetricInputRef,
    MetricLedgerRow,
    NewsEvent,
    PeerCompRow,
    ReconciliationCheck,
    RedFlagResult,
    ResearchRunState,
    ResolvedEntity,
    SentimentAggregate,
    SentimentItem,
    SourceTier,
    StageEvent,
    ToolCallTrace,
    VerificationGateReport,
)

EVIDENCE = {
    "evidence_id": "ev_ar26_p112_rev",
    "document_id": "doc_ar26",
    "document_title": "AR FY26",
    "source_url": "https://example.invalid/ar26.pdf",
    "source_tier": "T1",
    "trust_weight": 1.0,
    "page_number": 112,
    "bbox": [72.0, 400.5, 520.0, 412.0],
    "page_size": [595.0, 842.0],
    "verbatim_snippet": "Revenue from operations 3,810.00",
    "extracted_value_raw": "3,810.00",
    "source_unit": "₹ in crore",
}

LINE_ITEM = {
    "item_id": "li_fy26_revenue",
    "fiscal_period": "FY26",
    "field_key": "revenue",
    "reported_label": "Revenue from operations",
    "value": "3810.00",
    "formatted_value": "₹3,810 cr",
    "unit": "INR_CR",
    "evidence": EVIDENCE,
}

METRIC = {
    "metric_id": "met_dso_fy26",
    "metric_key": "dso",
    "metric_label": "DSO FY26",
    "fiscal_period": "FY26",
    "formula_template": "(Receivables_t / Revenue_t) * 365",
    "formula_substituted": "(880 / 3,810) * 365",
    "inputs": [
        {"symbol": "Receivables_FY26", "line_item_id": "li_fy26_trade_receivables", "fiscal_period": "FY26",
         "value": "880", "evidence_id": "ev_x"},
    ],
    "value": "84.30446194225721784776902887",
    "formatted_value": "84.3 d",
    "unit": "days",
    "status": "computed",
    "primary_source_ref": "AR FY26 p.116",
}

PANEL = {
    "stage_id": "s05_metrics_engine",
    "stage_title": "Deterministic Metrics Engine",
    "purpose_one_sentence": "Compute all metrics in Decimal.",
    "inputs_summary": ["24 line items"],
    "tool_calls": [{
        "call_id": "tc_1", "tool_name": "compute_metrics", "actor": "CODE",
        "arguments": {"n": 24}, "duration_ms": 12, "status": "ok", "raw_output_sample": "{}",
    }],
    "actions": [{"actor": "CODE", "action_summary": "Computed 38 ratios"}],
    "decision_made": "All computed",
    "decision_rationale": "No missing inputs",
    "failure_modes_and_guards": ["Zero denominators become N/M"],
}

EVENT = {
    "event_seq": 1,
    "run_id": "run_01J9Z8K4M2P6X8R0000000000",
    "timestamp_ms": 1760000000000,
    "elapsed_ms": 0,
    "stage_id": "s05_metrics_engine",
    "stage_status": "completed",
    "progress_pct": 42,
    "log_line": "computed 38 metrics",
    "explain_panel": PANEL,
    "state_patch": {"metrics_ledger": [METRIC]},
}


@pytest.mark.parametrize(
    "model, payload",
    [
        (EvidenceCitation, EVIDENCE),
        (LineItem, LINE_ITEM),
        (MetricLedgerRow, METRIC),
        (ExplainStagePanelData, PANEL),
        (StageEvent, EVENT),
    ],
)
def test_schema_roundtrip(model, payload):
    obj = model.model_validate(payload)
    dumped = obj.model_dump_json()
    again = model.model_validate_json(dumped)
    assert again == obj


def test_schema_roundtrip_decimal_is_json_string():
    item = LineItem.model_validate(LINE_ITEM)
    assert item.value == Decimal("3810.00")
    wire = json.loads(item.model_dump_json())
    assert wire["value"] == "3810.00"
    assert isinstance(wire["evidence"]["bbox"], list)


def test_schema_roundtrip_derived_key_allowed_and_unknown_rejected():
    LineItem.model_validate({**LINE_ITEM, "field_key": "ebitda", "is_derived": True})
    with pytest.raises(ValidationError):
        LineItem.model_validate({**LINE_ITEM, "field_key": "made_up_key"})


def test_schema_roundtrip_full_state():
    ev = EvidenceCitation.model_validate(EVIDENCE)
    state = ResearchRunState(
        run_id="run_01J9Z8K4M2P6X8R0000000000",
        mode="replay",
        company_query="One97 Communications Ltd",
        status="completed",
        started_at_ms=1,
        entity=ResolvedEntity(
            query="paytm", legal_name="One97 Communications Ltd", ticker="PAYTM", exchange="NSE",
            jurisdiction="IN", is_listed=True, sector="Fintech", headquarters="Noida",
            fy_end_month="March", currency=CurrencyUnit.INR_CR,
        ),
        line_items=[LineItem.model_validate(LINE_ITEM)],
        reconciliation_checks=[ReconciliationCheck(
            check_id="rc_bs_fy26", fiscal_period="FY26", check_name="Balance Sheet Identity",
            formula_desc="A = L + E", lhs_value=Decimal("10"), rhs_value=Decimal("10"),
            discrepancy=Decimal("0"), status="passed", passed=True,
        )],
        metrics_ledger=[MetricLedgerRow.model_validate(METRIC)],
        red_flags=[RedFlagResult(
            rule_id="RF_01", rule_name="x", severity="high", threshold_desc="t",
            observed_value_formatted="o", status="clear", fired=False, explanation="e",
            supporting_metric_ids=["met_dso_fy26"],
        )],
        news_events=[NewsEvent(
            event_id="nw_1", event_date="Mar 2026", category="Regulatory", headline="h", summary="s",
            polarity="negative", source_names=["RBI"], highest_tier=SourceTier.T1,
            corroborated_by_filing=True, article_count_merged=3, evidence=ev,
        )],
        sentiment=SentimentAggregate(
            total_posts_fetched=10, bots_and_duplicates_removed=3, posts_classified=7,
            positive_pct=20, neutral_pct=30, negative_pct=50, audience_breakdown=[],
            items=[SentimentItem(
                post_id="p1", platform="Reddit", audience="Customer", quote_text="q",
                polarity="negative", verdict="signal", verdict_reasons=["r"], evidence=ev,
            )],
        ),
        peer_comps=[PeerCompRow(company_name="A", ticker="A", rev_growth_pct=Decimal("0.1"))],
        contradictions=[ContradictionRecord(
            contradiction_id="ct_1", topic="t", source_a_claim="a", source_a_tier="T3",
            source_a_weight=0.65, source_a_evidence=ev, source_b_claim="b", source_b_tier="T1",
            source_b_weight=1.0, source_b_evidence=ev, winning_source="B", resolution_rationale="r",
        )],
        insights=[InsightCard(
            insight_id="in_1", title="t", body="b [[met_dso_fy26]]", classification="fact",
            polarity="negative", supporting_metric_ids=["met_dso_fy26"], supporting_news_ids=[],
            supporting_sentiment_ids=[],
        )],
        summary=ExecutiveSummary(
            verdict_headline="v", verdict_prose="p", fact_count=1, inference_count=0, hypothesis_count=0,
            business_overview="b", revenue_mix_summary="r", management_summary="m", opportunities=[],
            risks=[], quarterly_direction_note="q", analyst_follow_up_notes=[],
        ),
        verification=VerificationGateReport(
            total_numeric_tokens_scanned=1, verified_against_ledger=1, unverified_tokens_blocked=0,
            claims_downgraded_to_hypothesis=[], passed=True,
        ),
        evidence_registry={ev.evidence_id: ev},
        events=[StageEvent.model_validate(EVENT)],
    )
    again = ResearchRunState.model_validate_json(state.model_dump_json())
    assert again == state
    # unused imports kept honest
    assert ToolCallTrace and CodeVsLlmAction and MetricInputRef and ActorBadge


# ---------------------------------------------------------------- Task 5: REST + SSE contracts

def _app(tmp_path):
    from fastapi.testclient import TestClient

    from backend.config import Settings
    from backend.main import create_app

    settings = Settings(atlas_db_path=tmp_path / "atlas_state.db", gemini_api_key="")
    return TestClient(create_app(settings))


def _sse(text: str) -> list[dict]:
    """Parse an SSE body into [{event, id, data, retry}] messages (comments dropped)."""
    out, cur = [], {}
    for line in text.splitlines():
        if not line.strip():
            if cur:
                out.append(cur)
            cur = {}
            continue
        if line.startswith(":"):
            continue
        key, _, val = line.partition(":")
        val = val[1:] if val.startswith(" ") else val
        cur[key] = cur.get(key, "") + ("\n" if key in cur else "") + val
    if cur:
        out.append(cur)
    return out


def test_health_and_replays(tmp_path):
    with _app(tmp_path) as client:
        h = client.get("/api/v1/health").json()
        assert h["status"] == "ok" and h["sqlite_wal"] is True and h["llm_configured"] is False
        assert "sample_synthetic" in h["golden_runs_loaded"]
        replays = client.get("/api/v1/replays").json()["replays"]
        sample = next(r for r in replays if r["replay_id"] == "sample_synthetic")
        assert sample["synthetic"] is True and sample["total_events"] == 24
        assert sample["headline_verdict"] == "Growth is real, cash quality is deteriorating"


def test_replay_run_streams_every_event_instantly(tmp_path):
    from backend.replay.player import load_fixture

    total = load_fixture("data/golden_runs/sample_synthetic.jsonl.gz").header["total_events"]
    with _app(tmp_path) as client:
        r = client.post("/api/v1/runs", json={"company_query": "Nexora Payments", "mode": "replay",
                                              "replay_id": "sample_synthetic", "replay_speed": 1.0})
        assert r.status_code == 201
        body = r.json()
        assert re.fullmatch(r"run_[0-9A-HJKMNP-TV-Z]{26}", body["run_id"])
        assert body["stream_url"].startswith(f"/api/v1/runs/{body['run_id']}/stream")
        with client.stream("GET", f"/api/v1/runs/{body['run_id']}/stream?speed=instant") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.headers["cache-control"] == "no-cache"
            assert resp.headers["x-accel-buffering"] == "no"
            msgs = _sse(resp.read().decode())
        assert msgs[0]["event"] == "run_started" and msgs[0]["retry"] == "3000"
        stage = [m for m in msgs if m.get("event") == "stage_event"]
        assert len(stage) == total
        events = [StageEvent.model_validate_json(m["data"]) for m in stage]
        assert [e.event_seq for e in events] == list(range(1, total + 1))
        assert [m["id"] for m in stage] == [str(i) for i in range(1, total + 1)]
        assert all(e.run_id == body["run_id"] for e in events)
        done = msgs[-1]
        assert done["event"] == "run_completed"
        assert json.loads(done["data"])["metrics_count"] > 0
        snap = client.get(body["state_url"]).json()
        state = ResearchRunState.model_validate(snap)
        assert state.status == "completed" and len(state.events) == total


def test_reconnect_resumes_after_last_event_id(tmp_path):
    with _app(tmp_path) as client:
        run = client.post("/api/v1/runs", json={"company_query": "x", "mode": "replay", "replay_id": "sample_synthetic"}).json()
        with client.stream("GET", f"/api/v1/runs/{run['run_id']}/stream?speed=instant",
                           headers={"Last-Event-ID": "20"}) as resp:
            msgs = _sse(resp.read().decode())
        assert not any(m.get("event") == "run_started" for m in msgs)
        assert [m["id"] for m in msgs if m.get("event") == "stage_event"] == ["21", "22", "23", "24"]


def test_stateless_replay_restart_with_unknown_run_id(tmp_path):
    with _app(tmp_path) as client:
        with client.stream("GET", "/api/v1/runs/run_01J9Z8K4M2P6X8R0000000000/stream?speed=instant&replay_id=sample_synthetic") as resp:
            msgs = _sse(resp.read().decode())
        assert msgs[0]["event"] == "run_started"
        assert msgs[-1]["event"] == "run_completed"


def test_error_envelopes(tmp_path):
    with _app(tmp_path) as client:
        r = client.get("/api/v1/runs/run_nope")
        assert r.status_code == 404 and r.json()["error_code"] == "RUN_NOT_FOUND"
        r = client.post("/api/v1/runs", json={"company_query": "x", "mode": "replay", "replay_id": "nope"})
        assert r.status_code == 404 and r.json()["error_code"] == "REPLAY_NOT_FOUND"
        r = client.post("/api/v1/runs", json={"mode": "replay"})
        assert r.status_code == 422 and r.json()["error_code"] == "VALIDATION_ERROR"
        r = client.post("/api/v1/runs", json={"company_query": "Block", "mode": "live"})
        assert r.status_code == 503 and r.json()["error_code"] == "LLM_NOT_CONFIGURED"
        assert "sample_synthetic" in r.json()["details"]["suggest_replay_ids"]
        r = client.get("/api/v1/fixtures/pages/..%2Fsecret.png")
        assert r.status_code == 404
        r = client.get("/api/v1/nope")
        assert r.status_code == 404 and r.json()["error_code"] in ("NOT_FOUND", "RUN_NOT_FOUND")


def test_evidence_endpoint(tmp_path):
    with _app(tmp_path) as client:
        run = client.post("/api/v1/runs", json={"company_query": "x", "mode": "replay", "replay_id": "sample_synthetic"}).json()
        state = client.get(run["state_url"]).json()
        li = next(i for i in state["line_items"] if i["item_id"] == "li_fy26_revenue")
        ev_id = li["evidence"]["evidence_id"]
        r = client.get(f"/api/v1/runs/{run['run_id']}/evidence/{ev_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["evidence"]["evidence_id"] == ev_id
        assert any(i["item_id"] == "li_fy26_revenue" for i in body["linked_line_items"])
        assert all(c["fiscal_period"] == "FY26" for c in body["linked_reconciliation_checks"])
        assert client.get(f"/api/v1/runs/{run['run_id']}/evidence/ev_missing").json()["error_code"] == "EVIDENCE_NOT_FOUND"
