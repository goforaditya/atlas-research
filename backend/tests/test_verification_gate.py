"""Prose number blocking, citation resolution and hypothesis downgrade."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from backend.engine.verification_gate import (
    GAP_MARKER,
    extract_numeric_tokens,
    peer_token_id,
    run_verification_gate,
)
from backend.schemas import (
    ClaimClassification,
    ExecutiveSummary,
    InsightCard,
    MetricLedgerRow,
    NewsEvent,
    PeerCompRow,
    ResearchRunState,
)
from backend.tests.conftest import make_evidence, make_item


@pytest.mark.parametrize("text, expected", [
    ("Revenue grew 17.6% last year", ["17.6%"]),
    ("margin fell -100 bps", ["-100 bps"]),
    ("margin rose +750 bps", ["+750 bps"]),
    ("CFO/PAT was 0.67x", ["0.67x"]),
    ("DSO is 84.3 d now", ["84.3 d"]),
    ("revenue of ₹3,810 cr in", ["₹3,810 cr"]),
    ("FCF of -₹12 cr", ["-₹12 cr"]),
    ("Block posted $24,121 M of revenue", ["$24,121 M"]),
    ("a ₹5.39 crore penalty", ["₹5.39 crore"]),
    ("over 365 days", ["365 days"]),
])
def test_tokeniser_covers_every_style(text, expected):
    assert [t for _, t in extract_numeric_tokens(text)] == expected


def test_masking_skips_citations_fiscal_labels_and_dates():
    text = "In FY26 and Q1FY27 [[met_dso_fy26]] on 2026-03-31, in Mar 2026 and on 14 Nov 2025."
    assert extract_numeric_tokens(text) == []


def _metric(metric_id, formatted):
    return MetricLedgerRow(
        metric_id=metric_id, metric_key="x", metric_label="x", fiscal_period="FY26", formula_template="x",
        formula_substituted="x", inputs=[], value=D(1), formatted_value=formatted, unit="pct",
        status="computed", primary_source_ref="AR FY26 p.1",
    )


def _summary(prose: str) -> ExecutiveSummary:
    return ExecutiveSummary(
        verdict_headline="Growth on credit", verdict_prose=prose, fact_count=9, inference_count=9,
        hypothesis_count=9, business_overview="Payments company.", revenue_mix_summary="Mostly payments.",
        management_summary="CEO since 2000.", opportunities=["Expand 3 products."], risks=[],
        quarterly_direction_note="Flat.", analyst_follow_up_notes=[],
    )


def _state(**kw) -> ResearchRunState:
    base = dict(run_id="run_x", mode="replay", company_query="x", status="running", started_at_ms=0)
    base.update(kw)
    return ResearchRunState(**base)


def _insight(insight_id, body, cls="fact", **links):
    return InsightCard(
        insight_id=insight_id, title="t", body=body, classification=cls, polarity="negative",
        supporting_metric_ids=links.get("metrics", []), supporting_item_ids=links.get("items", []),
        supporting_news_ids=links.get("news", []), supporting_sentiment_ids=links.get("sentiment", []),
    )


def test_verified_prose_passes():
    state = _state(
        metrics_ledger=[_metric("met_rev_yoy_fy26", "17.6%"), _metric("met_dso_fy26", "84.3 d")],
        line_items=[make_item("revenue", "FY26", 3810)],
        summary=_summary("Revenue grew 17.6% [[met_rev_yoy_fy26]] to ₹3,810 cr [[li_fy26_revenue]]. DSO hit 84.3 d [[met_dso_fy26]] in FY26."),
        insights=[_insight("in_1", "DSO is 84.3 d [[met_dso_fy26]].", metrics=["met_dso_fy26"])],
    )
    report, new = run_verification_gate(state)
    assert report.passed and report.unverified_tokens_blocked == 0
    assert report.total_numeric_tokens_scanned == report.verified_against_ledger == 4
    assert new.summary.verdict_prose == state.summary.verdict_prose
    assert (new.summary.fact_count, new.summary.inference_count, new.summary.hypothesis_count) == (1, 0, 0)


def test_unverified_number_is_stripped_and_unsupported_claim_downgraded():
    state = _state(
        metrics_ledger=[_metric("met_rev_yoy_fy26", "17.6%")],
        summary=_summary("Strong year. Revenue grew 29.4% to ₹4,100 cr. Margins held."),
        insights=[
            _insight("in_cause", "The CFO exit likely preceded the raise.", cls="inference", sentiment=["p1"]),
            _insight("in_ok", "Revenue grew 17.6% [[met_rev_yoy_fy26]].", metrics=["met_rev_yoy_fy26"]),
        ],
    )
    report, new = run_verification_gate(state)
    assert new.summary.verdict_prose == f"Strong year. {GAP_MARKER} Margins held."
    assert report.unverified_tokens_blocked == 2
    assert not report.passed
    assert report.claims_downgraded_to_hypothesis == ["in_cause"]
    cls = {i.insight_id: i.classification for i in new.insights}
    assert cls == {"in_cause": ClaimClassification.HYPOTHESIS, "in_ok": ClaimClassification.FACT}
    assert (new.summary.fact_count, new.summary.inference_count, new.summary.hypothesis_count) == (1, 0, 1)
    assert new.verification == report


def test_wrong_value_for_citation_is_blocked():
    state = _state(
        metrics_ledger=[_metric("met_rev_yoy_fy26", "17.6%")],
        insights=[_insight("in_1", "Revenue grew 17.5% [[met_rev_yoy_fy26]].", metrics=["met_rev_yoy_fy26"])],
    )
    report, new = run_verification_gate(state)
    assert report.unverified_tokens_blocked == 1
    assert new.insights[0].body == GAP_MARKER


def test_hallucinated_number_injection_drill():
    """Drill 3: a synthetic 99.9% injected into synthesis output is blocked before completion."""
    state = _state(insights=[_insight("in_1", "Take rate is 99.9% and rising.", metrics=["met_x"])])
    report, _ = run_verification_gate(state)
    assert report.unverified_tokens_blocked == 1


def test_evidence_snippet_and_peer_citations():
    ev = make_evidence("ev_rbi_penalty")
    ev = ev.model_copy(update={"verbatim_snippet": "RBI imposed a monetary penalty of ₹5.39 crore on the bank."})
    peer = PeerCompRow(company_name="PB Fintech", ticker="NSE: POLICYBZR", rev_growth_pct=D("0.31"),
                       formatted={"rev_growth_pct": "31.0%"})
    median = PeerCompRow(company_name="Peer median", ticker="MEDIAN", is_median_row=True,
                         formatted={"rev_growth_pct": "22.0%"})
    state = _state(
        evidence_registry={ev.evidence_id: ev},
        peer_comps=[peer, median],
        market_positioning_prose=(
            f"Peer growth was 31.0% [[{peer_token_id(peer, 'rev_growth_pct')}]] against a median of "
            f"22.0% [[{peer_token_id(median, 'rev_growth_pct')}]]."
        ),
        news_events=[NewsEvent(
            event_id="nw_1", event_date="Mar 2026", category="Regulatory", headline="RBI penalty",
            summary="A penalty of ₹5.39 crore [[ev_rbi_penalty]] was imposed.", polarity="negative",
            source_names=["RBI"], highest_tier="T1", corroborated_by_filing=True, article_count_merged=1, evidence=ev,
        )],
        insights=[_insight("in_news", "Regulator acted.", cls="fact", news=["nw_1"])],
    )
    report, new = run_verification_gate(state)
    assert report.passed, new.market_positioning_prose
    assert report.verified_against_ledger == 3
    assert report.claims_downgraded_to_hypothesis == []  # T1 news counts as hard support


@pytest.mark.parametrize("prose", ["Watching 3 peers.", "Listed in 2025.", "Over 12 quarters."])
def test_exemptions(prose):
    report, _ = run_verification_gate(_state(market_positioning_prose=prose))
    assert report.passed and report.total_numeric_tokens_scanned == 0


@pytest.mark.parametrize("prose", ["Watching 13 peers.", "Up 3%.", "About ₹5 cr."])
def test_non_exempt_small_numbers_need_citations(prose):
    report, _ = run_verification_gate(_state(market_positioning_prose=prose))
    assert report.unverified_tokens_blocked == 1
