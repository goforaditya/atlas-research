"""Contract tests: schema round-trips (Task 1). REST + SSE contract tests are added in Task 5."""
from __future__ import annotations

import json
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
