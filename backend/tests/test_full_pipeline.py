"""Full 12-stage StateGraph run: scripted LLM, real source clients against recorded HTTP responses (respx)."""
from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
import respx

from backend.config import get_settings
from backend.pipeline.graph import (
    STAGE_META,
    EventEmitter,
    PipelineHalted,
    RunContext,
    StageResult,
    execute,
    stage_runners,
)
from backend.pipeline.llm_client import ScriptedLLM
from backend.schemas.common import StageId, StageStatus
from backend.schemas.events import StageEvent
from backend.schemas.state import ResearchRunState
from backend.sources.india_filings import BSE_ANNOUNCEMENTS_URL, BSE_SCRIPS_URL, IndiaFilingsClient
from backend.sources.news_fetcher import GDELT_DOC, NewsFetcher
from backend.sources.prices import FINNHUB_QUOTE_URL, PriceFetcher
from backend.sources.rate_limiter import DomainLimiter
from backend.sources.sec_edgar import FACTS_URL, SUBMISSIONS_URL, TICKERS_URL, SecEdgarClient
from backend.sources.sentiment_fetcher import HN_SEARCH_URL, SentimentFetcher

TODAY = date(2026, 10, 1)
UA = "AtlasResearchBot/1.0 (compliance@atlasresearch.demo)"

# ---------------------------------------------------------------- recorded responses

TAGS = {  # field -> (us-gaap tag, instant?)
    "rev": ("Revenues", False), "op": ("OperatingIncomeLoss", False), "int": ("InterestExpense", False),
    "dna": ("DepreciationDepletionAndAmortization", False),
    "pbt": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", False),
    "tax": ("IncomeTaxExpenseBenefit", False), "pl": ("ProfitLoss", False), "ni": ("NetIncomeLoss", False),
    "cfo": ("NetCashProvidedByUsedInOperatingActivities", False), "ppe": ("PaymentsToAcquirePropertyPlantAndEquipment", False),
    "assets": ("Assets", True), "liab": ("Liabilities", True),
    "equity": ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", True),
    "ltd": ("LongTermDebtNoncurrent", True), "std": ("LongTermDebtCurrent", True),
    "cash": ("CashAndCashEquivalentsAtCarryingValue", True), "ar": ("AccountsReceivableNetCurrent", True),
    "ca": ("AssetsCurrent", True), "cl": ("LiabilitiesCurrent", True),
}


def companyfacts(cik: int, prefix: str, values: dict[str, list[int]], years: list[int], eps: float) -> dict:
    out: dict = {}
    for key, series in values.items():
        tag, instant = TAGS[key]
        rows = []
        for y, v in zip(years, series, strict=True):
            f = {"val": v * 1_000_000, "end": f"{y}-12-31", "form": "10-K", "filed": f"{y + 1}-02-20", "fp": "FY", "fy": y,
                 "accn": f"{prefix}-{str(y + 1)[2:]}-000001"}
            if not instant:
                f["start"] = f"{y}-01-01"
            rows.append(f)
        out[tag] = {"units": {"USD": rows}}
    y = years[-1]
    out["EarningsPerShareDiluted"] = {"units": {"USD/shares": [
        {"val": eps, "start": f"{y}-01-01", "end": f"{y}-12-31", "form": "10-K", "filed": f"{y + 1}-02-20"}]}}
    return {"cik": cik, "facts": {"us-gaap": out}}


ACME_FACTS = companyfacts(1234567, "0001234567", {
    "rev": [10000, 11000, 12500], "op": [1000, 1200, 1500], "int": [50, 60, 70], "dna": [300, 320, 350],
    "pbt": [950, 1140, 1430], "tax": [200, 240, 300], "pl": [750, 900, 1130], "ni": [740, 890, 1120],
    "cfo": [900, 1000, 1200], "ppe": [400, 420, 450], "assets": [20000, 22000, 25000], "liab": [12000, 13000, 15000],
    "equity": [8000, 9000, 10000], "ltd": [3000, 3100, 3200], "std": [200, 200, 250], "cash": [1500, 1600, 1800],
    "ar": [1200, 1400, 1700], "ca": [6000, 6500, 7000], "cl": [4000, 4200, 4500],
}, [2023, 2024, 2025], 5.60)
BETA_FACTS = companyfacts(2222222, "0002222222", {
    "rev": [4000, 4600], "op": [400, 500], "dna": [100, 110], "ni": [300, 360], "pl": [300, 360],
    "equity": [3000, 3300], "assets": [6000, 6600], "liab": [3000, 3300],
}, [2024, 2025], 2.40)
GAMA_FACTS = companyfacts(3333333, "0003333333", {
    "rev": [2000, 2100], "op": [100, 90], "dna": [60, 70], "ni": [50, -20], "pl": [50, -20],
    "equity": [1500, 1480], "assets": [3000, 3100], "liab": [1500, 1620],
}, [2024, 2025], -0.30)
TICKERS = {"0": {"cik_str": 1234567, "ticker": "ACME", "title": "Acme Corp"},
           "1": {"cik_str": 2222222, "ticker": "BETA", "title": "Beta Payments Inc"},
           "2": {"cik_str": 3333333, "ticker": "GAMA", "title": "Gamma Pay Corp"}}
SUBMISSIONS = {"cik": "1234567", "filings": {"recent": {
    "form": ["8-K", "10-K", "10-K"], "accessionNumber": ["0001234567-26-000009", "0001234567-26-000001", "0001234567-25-000001"],
    "primaryDocument": ["acme-8k.htm", "acme-10k.htm", "acme-10k24.htm"],
    "filingDate": ["2026-05-04", "2026-02-20", "2025-02-20"], "reportDate": ["2026-05-01", "2025-12-31", "2024-12-31"],
    "items": ["5.02", "", ""]}}}
TENK_URL = "https://www.sec.gov/Archives/edgar/data/1234567/000123456726000001/acme-10k.htm"
TENK_HTML = """<html><body><p>Item 1. Business ..... 3</p>
<p>Item 1. Business</p><p>Acme Corp builds payment terminals for retailers. It operates in North America and Europe.</p>
<p>Item 1A. Risk Factors</p><h3>Information About Our Executive Officers</h3>
<table><tr><th>Name</th><th>Age</th><th>Position</th></tr>
<tr><td>Jane Doe</td><td>52</td><td>Chief Executive Officer</td></tr>
<tr><td>John Roe</td><td>47</td><td>Chief Financial Officer</td></tr></table></body></html>"""
RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Acme Corp CFO John Roe steps down - Reuters</title><link>https://www.reuters.com/acme-cfo</link>
<pubDate>Sat, 02 May 2026 08:00:00 GMT</pubDate><source url="https://www.reuters.com">Reuters</source></item>
<item><title>Acme Corp CFO John Roe steps down abruptly - Bloomberg</title><link>https://www.bloomberg.com/acme</link>
<pubDate>Sat, 02 May 2026 10:00:00 GMT</pubDate><source url="https://www.bloomberg.com">Bloomberg</source></item>
<item><title>Acme Corp launches a new countertop terminal line - CNBC</title><link>https://www.cnbc.com/acme-terminal</link>
<pubDate>Wed, 10 Jun 2026 09:00:00 GMT</pubDate><source url="https://www.cnbc.com">CNBC</source></item>
</channel></rss>"""
HN_HITS = [{"objectID": str(i), "author": a, "comment_text": t, "created_at_i": int(datetime(2026, 8, i + 1, tzinfo=UTC).timestamp())}
           for i, (a, t) in enumerate([
               ("merchant_ops", "Our Acme terminals keep crashing during the lunch rush checkout"),
               ("cafe_owner", "Acme card readers crash every few days and support takes ages to respond"),
               ("retail_dev", "The Acme terminal firmware crashes when the receipt printer is attached"),
               ("newbie", "acme is great buy now"),
           ])]
KARMA = {"merchant_ops": 420, "cafe_owner": 88, "retail_dev": 1500, "newbie": 3}
PRICES = {"ACME": 112.0, "BETA": 50.4, "GAMA": 30.0}


def mock_http(router: respx.MockRouter) -> None:
    router.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=TICKERS))
    router.get(SUBMISSIONS_URL.format(cik="0001234567")).mock(return_value=httpx.Response(200, json=SUBMISSIONS))
    for cik, facts in (("0001234567", ACME_FACTS), ("0002222222", BETA_FACTS), ("0003333333", GAMA_FACTS)):
        router.get(FACTS_URL.format(cik=cik)).mock(return_value=httpx.Response(200, json=facts))
    router.get(TENK_URL).mock(return_value=httpx.Response(200, text=TENK_HTML))
    router.get(url__startswith=BSE_SCRIPS_URL).mock(return_value=httpx.Response(200, json=[]))
    router.get(url__startswith=BSE_ANNOUNCEMENTS_URL).mock(return_value=httpx.Response(200, json={"Table": []}))
    router.get(url__startswith="https://news.google.com/rss/search").mock(return_value=httpx.Response(200, text=RSS))
    router.get(url__startswith=GDELT_DOC).mock(return_value=httpx.Response(200, json={"articles": []}))
    router.get(url__startswith=HN_SEARCH_URL).mock(return_value=httpx.Response(200, json={"hits": HN_HITS}))
    router.get(url__regex=r"https://hn\.algolia\.com/api/v1/users/.*").mock(
        side_effect=lambda req: httpx.Response(200, json={"karma": KARMA[req.url.path.rsplit("/", 1)[-1]]}))
    router.get(url__startswith=FINNHUB_QUOTE_URL).mock(side_effect=lambda req: httpx.Response(200, json={
        "c": PRICES[req.url.params["symbol"]], "t": int(datetime(2026, 9, 30, 20, tzinfo=UTC).timestamp())}))


# ---------------------------------------------------------------- scripted model (copies only what it is given)


def section(user: str, name: str):
    m = re.search(rf"^{name}: (.*)$", user, re.M)
    return json.loads(m.group(1))


def news_model(user: str) -> dict:
    clusters = section(user, "ARTICLE_CLUSTERS")
    filings = section(user, "T1_T2_TITLES_AND_SNIPPETS")
    events = []
    for c in clusters:
        head = c["articles"][0]["headline"]
        cfo = "steps down" in head
        events.append({"event_id": f"e{len(events)}", "event_date": "May 2026" if cfo else "Jun 2026",
                       "category": "Management" if cfo else "Product", "headline": head,
                       "summary": "The finance chief left the company." if cfo else "A new terminal range was launched.",
                       "polarity": "negative" if cfo else "positive", "source_names": [], "highest_tier": "T3",
                       "corroborated_by_filing": cfo, "article_count_merged": len(c["articles"]),
                       "primary_article_id": c["primary_article_id"]})
    events.append({"event_id": "ghost", "event_date": "Jan 2026", "category": "Regulatory", "headline": "Made up",
                   "summary": "Not in any source.", "polarity": "negative", "source_names": [], "highest_tier": "T1",
                   "corroborated_by_filing": True, "article_count_merged": 1, "primary_article_id": "gn_99"})
    assert filings and filings[0]["title"] == "Form 8-K items 5.02"
    return {"events": events}


def sentiment_model(user: str) -> dict:
    posts = section(user, "CLEAN_POSTS")
    return {"audience_breakdown": [{"audience": "Customers", "dominant_tone": "negative",
                                    "trend_description": "Merchants report repeated terminal crashes."}],
            "items": [{"post_id": p["post_id"], "platform": "Hacker News", "audience": "Customer",
                       "quote_text": "paraphrased by the model", "polarity": "negative", "verdict": "signal",
                       "verdict_reasons": ["Recurring crash reports from independent merchants"], "corroborating_ids": []}
                      for p in posts]}


def positioning_model(user: str) -> dict:
    table = section(user, "PEER_TABLE_WITH_MEDIANS")
    t = next(r for r in table if r["is_target"])["cells"]
    m = next(r for r in table if r["is_median"])["cells"]

    def cell(c, k):
        return f"{c[k]['formatted_value']} {c[k]['token']}"

    return {"market_advantages": [f"Revenue growth of {cell(t, 'rev_growth_pct')} is above the peer median of "
                                  f"{cell(m, 'rev_growth_pct')}."],
            "market_disadvantages": [f"Return on equity of {cell(t, 'roe_pct')} compares with a peer median of "
                                     f"{cell(m, 'roe_pct')}."],
            "market_positioning_prose": f"Acme's EBITDA margin of {cell(t, 'ebitda_margin_pct')} compares with a peer "
                                        f"median of {cell(m, 'ebitda_margin_pct')}."}


def contradiction_model(user: str) -> dict:
    t1 = section(user, "T1_METRICS_AND_ITEMS")
    claims = section(user, "T2_T3_T4_CLAIMS")
    rev = next(m for m in t1["metrics"] if m["id"].startswith("met_rev_yoy_"))
    news = next(c for c in claims if c.get("headline", "").startswith("Acme Corp launches"))
    return {"candidate_contradictions": [
        {"contradiction_id": "x1", "topic": "Growth narrative", "source_a_claim": f"Revenue grew {rev['value']}",
         "source_a_evidence_id": rev["id"], "source_b_claim": news["headline"], "source_b_evidence_id": news["evidence_id"],
         "conflict_type": "NARRATIVE_VS_METRIC", "explanation_note": "The launch is framed as a growth driver."},
        {"contradiction_id": "x2", "topic": "Invented", "source_a_claim": "Revenue fell 40%", "source_a_evidence_id": rev["id"],
         "source_b_claim": "x", "source_b_evidence_id": news["evidence_id"], "conflict_type": "NUMERIC_DISCREPANCY",
         "explanation_note": ""},
    ]}


def synthesis_model(extra_sentence: str = ""):
    def model(user: str) -> dict:
        metrics = {m["metric_id"]: m for m in section(user, "METRICS_LEDGER")}
        items = {i["item_id"]: i for i in section(user, "LINE_ITEMS")}
        news = section(user, "NEWS_EVENTS")
        business = section(user, "BUSINESS_EVIDENCE")
        rev = items["li_fy2025_revenue"]
        dso_id = next(k for k in metrics if k.startswith("met_dso_fy2025"))
        dso = metrics[dso_id]
        cfo_exit = next(n for n in news if n["headline"].startswith("Acme Corp CFO"))
        return {"insights": [
            {"insight_id": "a", "title": "Revenue scale", "body": f"Revenue reached {rev['formatted_value']} [[{rev['item_id']}]].",
             "classification": "fact", "polarity": "positive", "supporting_item_ids": [rev["item_id"], "li_bogus"]},
            {"insight_id": "b", "title": "Collections are slowing",
             "body": f"DSO stands at {dso['formatted_value']} [[{dso_id}]] while merchants report terminal crashes.",
             "classification": "inference", "polarity": "negative", "supporting_metric_ids": [dso_id, "met_bogus"]},
            {"insight_id": "c", "title": "Finance leadership change",
             "body": "The CFO left without a stated reason." + extra_sentence,
             "classification": "fact", "polarity": "negative", "supporting_news_ids": [cfo_exit["event_id"]]},
        ], "executive_summary": {
            "verdict_headline": "Growing, with collection and leadership questions",
            "verdict_prose": f"Revenue reached {rev['formatted_value']} [[{rev['item_id']}]].",
            "business_overview": business[0]["text"] if business else "", "revenue_mix_summary": "Segment data is not available.",
            "management_summary": "Jane Doe is Chief Executive Officer.", "governance_watch_note": None,
            "opportunities": ["New terminal range."], "risks": ["Terminal reliability."],
            "quarterly_direction_note": "Quarterly data is limited.", "analyst_follow_up_notes": ["Ask about the CFO search."]}}
    return model


def script(extra_sentence: str = "") -> dict:
    return {
        "s01": {"legal_name": "Acme Corp", "ticker": "ACME", "exchange": "NYSE", "jurisdiction": "US", "is_listed": True,
                "sector": "Payments hardware", "headquarters": "Austin", "fy_end_month": "December", "currency": "USD_M",
                "rejected_candidates": [], "decision_rationale": "Only listed match"},
        "s02": {"sub_questions": [f"Which question {w}?" for w in "abcdefghijkl"], "routing_rationale": "x"},
        "s07": news_model, "s08": sentiment_model,
        "s09_candidates": {"names": ["Beta Payments", "Gamma Pay", "Zeta Unknown Holdings"]},
        "s09_select": {"peers": [{"ticker": "BETA", "company_name": "Beta Payments Inc", "reason": "Same model"},
                                 {"ticker": "GAMA", "company_name": "Gamma Pay Corp", "reason": "Same region"},
                                 {"ticker": "FAKE", "company_name": "Invented", "reason": "x"}]},
        "s09_positioning": positioning_model, "s10": contradiction_model, "s11": synthesis_model(extra_sentence),
    }


async def _no_sleep(_s: float) -> None:
    return None


@pytest.fixture
def run_pipeline(tmp_path):
    async def go(llm_script: dict, query: str = "Acme Corp", runners=None):
        events: list[StageEvent] = []

        async def on_event(ev: StageEvent) -> None:
            events.append(ev)

        limiter = DomainLimiter(sleep=_no_sleep, rate_override=10_000)
        with respx.mock(assert_all_called=False) as router:
            mock_http(router)
            async with httpx.AsyncClient() as client:
                ctx = RunContext(
                    settings=get_settings(), llm=ScriptedLLM(llm_script), emitter=EventEmitter("run_full"),
                    sec=SecEdgarClient(client, limiter, UA), india=IndiaFilingsClient(client, limiter),
                    news=NewsFetcher(client, limiter),
                    sentiment=SentimentFetcher(client, limiter, play_search=lambda *a, **k: [], play_reviews=lambda *a, **k: []),
                    download=lambda url: _download(client, url), close_price=PriceFetcher(client, limiter, "test-key"),
                    work_dir=tmp_path / "work")
                final = None
                error = None
                try:
                    final = await execute(ctx, query, on_event, today=TODAY, runners=runners)
                except PipelineHalted as exc:
                    error = exc
        return events, final, error, ctx

    return go


async def _download(client: httpx.AsyncClient, url: str) -> bytes:
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.content


def fold(events: list[StageEvent]) -> ResearchRunState:
    """The client's shallow-merge rule: each patch key replaces the previous value."""
    state = {"run_id": "run_full", "mode": "live", "company_query": "Acme Corp", "status": "running", "started_at_ms": 0}
    for ev in events:
        state.update(ev.state_patch)
    return ResearchRunState.model_validate(state)


# ---------------------------------------------------------------- tests


async def test_full_pipeline_runs_twelve_stages_in_topology_order(run_pipeline):
    events, final, error, ctx = await run_pipeline(script())
    assert error is None
    assert [e.event_seq for e in events] == list(range(1, len(events) + 1))
    assert len(events) == 24
    for e in events:
        StageEvent.model_validate_json(e.model_dump_json())
        assert (e.explain_panel is None) == (e.stage_status == StageStatus.RUNNING)
    terminal = {e.stage_id: e for e in events if e.stage_status != StageStatus.RUNNING}
    assert set(terminal) == set(StageId)
    assert all(t.stage_status in (StageStatus.COMPLETED, StageStatus.WARNING) for t in terminal.values())
    seq = {(e.stage_id, e.stage_status == StageStatus.RUNNING): e.event_seq for e in events}

    def start(s):
        return seq[(s, True)]

    def end(s):
        return seq[(s, False)]

    for before, after in [(StageId.S02_PLAN, StageId.S03_RETRIEVE), (StageId.S02_PLAN, StageId.S07_NEWS),
                          (StageId.S05_METRICS, StageId.S06_RED_FLAGS), (StageId.S05_METRICS, StageId.S09_MARKET),
                          (StageId.S05_METRICS, StageId.S08_SENTIMENT), (StageId.S07_NEWS, StageId.S08_SENTIMENT),
                          (StageId.S06_RED_FLAGS, StageId.S10_CONTRADICTION), (StageId.S08_SENTIMENT, StageId.S10_CONTRADICTION),
                          (StageId.S09_MARKET, StageId.S10_CONTRADICTION), (StageId.S11_SYNTHESIS, StageId.S12_VERIFY)]:
        assert end(before) < start(after), (before, after)
    assert start(StageId.S07_NEWS) < start(StageId.S04_EXTRACT)  # S7 shares a superstep with S3
    assert events[-1].progress_pct == 100


async def test_full_pipeline_state_passes_the_gate(run_pipeline):
    events, final, _, ctx = await run_pipeline(script())
    st = fold(events)
    assert st.status == "completed" and st.verification.passed and st.verification.unverified_tokens_blocked == 0
    assert st.verification.verified_against_ledger >= 5
    assert st.entity.cik == "0001234567" and st.fiscal_years == ["FY2023", "FY2024", "FY2025"]
    assert [p.name for p in st.management_roster] == ["Jane Doe", "John Roe"]
    assert any(c.status == "passed" for c in st.reconciliation_checks)

    # News: syndicated copies merged, dates from timestamps, CFO exit corroborated by the 8-K, ghost event dropped.
    by_head = {n.headline: n for n in st.news_events}
    cfo = next(n for h, n in by_head.items() if "steps down" in h)
    assert cfo.article_count_merged == 2 and cfo.corroborated_by_filing and cfo.highest_tier.value == "T2"
    assert cfo.event_date == "May 2026" and "SEC 8-K" in cfo.source_names
    assert len(st.news_events) == 2

    # Sentiment: the low-karma post is removed by code; quotes are the post text, not the model's paraphrase.
    assert st.sentiment.bots_and_duplicates_removed == 1 and st.sentiment.posts_classified == 3
    assert all(i.verdict == "signal" and "paraphrased" not in i.quote_text for i in st.sentiment.items)
    assert st.sentiment.negative_pct == 100

    # Peers: invented names and tickers dropped, P/E from close and diluted EPS, N/M for a loss-maker, medians in code.
    rows = {r.ticker: r for r in st.peer_comps}
    assert set(rows) == {"NYSE: ACME", "BETA", "GAMA", "MEDIAN"}
    assert rows["NYSE: ACME"].formatted["pe_multiple"] == "20.0x" and rows["GAMA"].formatted["pe_multiple"] == "N/M"
    assert rows["BETA"].price_as_of == "2026-09-30"
    assert "[[peer_acme_rev_growth_pct]]" in st.market_advantages[0]
    assert any(k.startswith("ev_beta_xbrl_") for k in st.evidence_registry)

    # Contradictions: winner set by code; the one with an ungrounded number is dropped.
    assert [c.winning_source for c in st.contradictions] == ["BOTH_FRAMED"]

    # Synthesis: unknown ids dropped, counts by code, news-only "fact" downgraded to hypothesis by the gate.
    cards = {c.title: c for c in st.insights}
    assert cards["Revenue scale"].supporting_item_ids == ["li_fy2025_revenue"]
    assert cards["Collections are slowing"].supporting_metric_ids == ["met_dso_fy2025"]
    assert cards["Finance leadership change"].classification.value == "fact"  # T2-corroborated news is hard support
    assert (st.summary.fact_count, st.summary.inference_count, st.summary.hypothesis_count) == (2, 1, 0)
    assert {n.node_type for n in st.provenance_nodes} >= {"line_item", "metric", "news", "insight"}

    assert any("Reddit skipped" in g for g in st.stated_gaps)
    assert st.sources_read_count > 0
    llm_stages = [c["stage"] for c in ctx.llm.calls]
    assert llm_stages.count("s04") == 0  # US filers use XBRL; no row mapping call


async def test_gate_blocks_an_unsourced_number_and_run_still_completes(run_pipeline):
    events, *_ = await run_pipeline(script(" Margins could reach 30% next year."))
    st = fold(events)
    s12 = next(e for e in events if e.stage_id == StageId.S12_VERIFY and e.stage_status != StageStatus.RUNNING)
    assert s12.stage_status == StageStatus.WARNING and st.status == "completed"
    assert st.verification.unverified_tokens_blocked == 1 and not st.verification.passed
    body = next(c.body for c in st.insights if c.title == "Finance leadership change")
    assert "30%" not in body and "[Stated Gap: Unverified numeric claim removed by Verification Gate]" in body


async def test_critical_stage_failure_halts_after_its_failed_event(run_pipeline):
    events, final, error, _ = await run_pipeline(script(), query="Zzyzx Nonexistent Holdings")
    assert isinstance(error, PipelineHalted) and error.stage == StageId.S01_ENTITY
    assert [(e.stage_id, e.stage_status) for e in events] == [(StageId.S01_ENTITY, StageStatus.RUNNING),
                                                             (StageId.S01_ENTITY, StageStatus.FAILED)]
    assert events[-1].explain_panel is not None


async def test_non_critical_stage_error_is_reported_and_the_run_continues(run_pipeline):
    runners = stage_runners()

    async def broken(ctx, state) -> StageResult:
        raise RuntimeError("sentiment source exploded")

    runners[StageId.S08_SENTIMENT] = broken
    events, final, error, _ = await run_pipeline(script(), runners=runners)
    assert error is None
    s8 = next(e for e in events if e.stage_id == StageId.S08_SENTIMENT and e.stage_status != StageStatus.RUNNING)
    assert s8.stage_status == StageStatus.FAILED and "RuntimeError" in s8.explain_panel.decision_rationale
    assert s8.log_line == f"{STAGE_META[StageId.S08_SENTIMENT].title} failed: RuntimeError"
    assert fold(events).verification is not None


# ---------------------------------------------------------------- Phase 2 recorder


async def test_recorder_compare_and_fixture_round_trip(run_pipeline, tmp_path, monkeypatch):
    from backend.replay.player import load_fixture, project_final_state
    from scripts import record_golden_run as rec

    events, *_ = await run_pipeline(script())
    st = rec.fold("run_full", "Acme Corp", events)
    verified = {"entity": {"currency": "USD_M"}, "line_items": [
        {"field_key": "revenue", "period": "FY2025", "value": "12,500", "unit": "(In millions)", "doc": "acme_10k", "page": 41},
        {"field_key": "cfo", "period": "FY2024", "value": "1,000", "unit": "(In millions)", "doc": "acme_10k", "page": 44},
    ]}
    assert rec.compare_line_items(st, verified) == []
    verified["line_items"][0]["value"] = "12,600"
    verified["line_items"].append({"field_key": "segment_revenue", "segment": "Hardware", "period": "FY2025", "value": "1",
                                   "unit": "(In millions)", "doc": "acme_10k", "page": 50})
    problems = rec.compare_line_items(st, verified)
    assert len(problems) == 2 and "12600" in problems[0] and "missing" in problems[1]

    async def fake_record(query, run_id):
        return events

    monkeypatch.setattr(rec, "record", fake_record)
    out = tmp_path / "golden" / "acme_nyse.jsonl.gz"
    assert await rec.amain(["--query", "Acme Corp", "--output", str(out)]) == 0
    fx = load_fixture(out)
    assert fx.header["built_by"] == "record_golden_run" and fx.header["total_events"] == 24
    assert project_final_state(fx)["verification"]["passed"] is True
    manifest = json.loads((out.parent / "manifest.json").read_text())
    assert manifest["replays"][0]["replay_id"] == "acme_nyse" and manifest["replays"][0]["ticker"] == "NYSE: ACME"


def test_recorder_refuses_unverified_yaml(tmp_path):
    from scripts import record_golden_run as rec

    path = Path(__file__).resolve().parents[2] / "data" / "golden_inputs" / "block_nyse.yaml"
    assert rec.main(["--query", "Block", "--output", str(tmp_path / "x.jsonl.gz"), "--compare", str(path)]) == 1
    assert not (tmp_path / "x.jsonl.gz").exists()
