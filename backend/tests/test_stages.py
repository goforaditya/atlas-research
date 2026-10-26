"""Stage-level tests with a scripted LLM and fake source clients (no network)."""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest

from backend.config import get_settings
from backend.pipeline.graph import EventEmitter, RunContext, emit_result
from backend.pipeline.llm_client import LLMError, ScriptedLLM
from backend.pipeline.stages import (
    s01_entity_resolution as s01,
)
from backend.pipeline.stages import (
    s02_research_planning as s02,
)
from backend.pipeline.stages import (
    s03_filing_retrieval as s03,
)
from backend.pipeline.stages import (
    s04_statement_extraction as s04,
)
from backend.pipeline.stages import (
    s05_metrics_engine as s05,
)
from backend.pipeline.stages import (
    s06_red_flag_engine as s06,
)
from backend.schemas.common import CurrencyUnit, SourceTier, StageId, StageStatus
from backend.schemas.events import StageEvent
from backend.schemas.state import ResearchRunState, ResolvedEntity
from backend.sources import SourceError
from backend.sources.india_filings import FilingDoc, ScripMatch
from backend.sources.sec_edgar import TickerMatch

# ---------------------------------------------------------------- fakes


class FakeIndia:
    def __init__(self, scrips=(), docs=()):
        self.scrips, self.docs, self.queries = list(scrips), list(docs), []

    async def search_scrips(self, query):
        self.queries.append(query)
        q = query.lower()
        return [s for s in self.scrips if q in s.name.lower()]

    async def fetch_filings(self, scrip_code, symbol, years=5, today=None):
        return self.docs, [], []


class FakeSec:
    def __init__(self, tickers=(), submissions=None, facts=None, fail=False):
        self.tickers, self.submissions, self.facts, self.fail = list(tickers), submissions, facts, fail

    async def search_tickers(self, query):
        if self.fail:
            raise SourceError("sec.gov returned 503")
        q = query.lower()
        return [t for t in self.tickers if q in t.title.lower()]

    async def fetch_submissions(self, cik):
        return self.submissions

    async def fetch_company_facts(self, cik):
        return self.facts


def make_ctx(tmp_path, llm, **kw):
    return RunContext(settings=get_settings(), llm=llm, emitter=EventEmitter("run_test"), work_dir=tmp_path / "work", **kw)


def assert_valid_event(ctx, stage, result):
    ev = emit_result(ctx, stage, result)
    StageEvent.model_validate_json(ev.model_dump_json())
    assert ev.explain_panel is not None and ev.explain_panel.stage_id == stage
    return ev


def apply(state: dict, *events: StageEvent) -> ResearchRunState:
    """Fold state patches the way the client does and validate the result."""
    base = {"run_id": "run_test", "mode": "live", "company_query": "Nexora", "status": "running", "started_at_ms": 0}
    for ev in events:
        for k, v in ev.state_patch.items():
            if k == "evidence_registry":
                base.setdefault(k, {}).update(v)
            elif k == "stated_gaps":
                base[k] = base.get(k, []) + v
            else:
                base[k] = v
    return ResearchRunState.model_validate(base)


NEXORA = ResolvedEntity(query="Nexora", legal_name="Nexora Payments Limited", ticker="NXRA", exchange="BSE", jurisdiction="IN",
                        is_listed=True, bse_scrip_code="543999", sector="Fintech", headquarters="Bengaluru",
                        fy_end_month="March", currency=CurrencyUnit.INR_CR)

# ---------------------------------------------------------------- s01


def _entity_pick(ticker, name, jurisdiction="IN", currency="INR_CR"):
    return {"legal_name": name, "ticker": ticker, "exchange": "BSE", "jurisdiction": jurisdiction, "is_listed": True,
            "sector": "Fintech", "headquarters": "Bengaluru", "fy_end_month": "March", "currency": currency,
            "rejected_candidates": ["Nexora Finserv Pvt Ltd: unlisted subsidiary"], "decision_rationale": "Listed parent"}


SCRIPS = [ScripMatch("543999", "NXRA", "Nexora Payments Limited", "INE000X01010", "Fintech"),
          ScripMatch("500001", "NXFS", "Nexora Finserv Limited", "INE000X01011", "NBFC")]


async def test_s01_resolves_listed_parent_and_sets_currency_by_code(tmp_path):
    llm = ScriptedLLM({"s01": _entity_pick("NXRA", "Nexora Payments Limited", currency="USD_M")})
    ctx = make_ctx(tmp_path, llm, india=FakeIndia(SCRIPS), sec=FakeSec())
    res = await s01.run(ctx, {"company_query": "Nexora"})
    e = res.patch["entity"]
    assert res.status == StageStatus.COMPLETED
    assert (e.ticker, e.bse_scrip_code, e.jurisdiction, e.currency) == ("NXRA", "543999", "IN", CurrencyUnit.INR_CR)
    assert "Nexora Finserv Limited" in llm.calls[0]["user"]
    tools = [c.tool_name for c in res.panel.tool_calls]
    assert tools[:3] == ["search_indian_exchanges", "search_sec_edgar_tickers", "search_opencorporates_and_mca"]
    assert_valid_event(ctx, StageId.S01_ENTITY, res)


async def test_s01_discards_invented_ticker(tmp_path):
    llm = ScriptedLLM({"s01": _entity_pick("FAKE", "Nexora Payments Limited")})
    ctx = make_ctx(tmp_path, llm, india=FakeIndia(SCRIPS))
    res = await s01.run(ctx, {"company_query": "Nexora"})
    e = res.patch["entity"]
    assert e.ticker == "NXRA"  # matched by exact legal name; the invented ticker is not used
    assert any("FAKE" in a.action_summary and "discarded" in a.action_summary for a in res.panel.actions)


async def test_s01_unmatched_choice_is_a_warning_with_no_identifiers(tmp_path):
    llm = ScriptedLLM({"s01": _entity_pick("FAKE", "Something Else Ltd")})
    ctx = make_ctx(tmp_path, llm, india=FakeIndia(SCRIPS))
    res = await s01.run(ctx, {"company_query": "Nexora"})
    e = res.patch["entity"]
    assert res.status == StageStatus.WARNING and e.ticker is None and e.bse_scrip_code is None and not e.is_listed


async def test_s01_retries_without_suffix_then_fails_cleanly(tmp_path):
    india = FakeIndia(SCRIPS)
    llm = ScriptedLLM({"s01": _entity_pick("NXRA", "Nexora Payments Limited")})
    ctx = make_ctx(tmp_path, llm, india=india, sec=FakeSec(fail=True))
    res = await s01.run(ctx, {"company_query": "Nexora Payments Pvt Ltd"})
    assert india.queries == ["Nexora Payments Pvt Ltd", "Nexora"]
    assert res.patch["entity"].ticker == "NXRA"
    assert any(c.status == "error" for c in res.panel.tool_calls)  # SEC outage is logged, not fatal

    ctx2 = make_ctx(tmp_path, ScriptedLLM({}), india=FakeIndia([]))
    res2 = await s01.run(ctx2, {"company_query": "Zzyzx Holdings Ltd"})
    assert res2.status == StageStatus.FAILED and res2.patch == {}
    assert_valid_event(ctx2, StageId.S01_ENTITY, res2)


def test_strip_suffixes():
    assert s01.strip_suffixes("Paytm (One 97 Communications Ltd.)") == "Paytm (One 97 )"
    assert s01.strip_suffixes("Stripe, Inc.") == "Stripe,"


# ---------------------------------------------------------------- s02

QUESTIONS = [f"Question {w} about cash conversion?" for w in
             ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]]


async def test_s02_accepts_twelve_questions_and_routes_numbers_to_filings(tmp_path):
    llm = ScriptedLLM({"s02": {"sub_questions": QUESTIONS, "routing_rationale": "x"}})
    ctx = make_ctx(tmp_path, llm)
    res = await s02.run(ctx, {"entity": NEXORA})
    assert res.status == StageStatus.COMPLETED and res.patch["sub_questions"] == QUESTIONS
    routing = json.loads(res.panel.tool_calls[-1].raw_output_sample)
    assert routing["numerical"] == ["T1", "T2"]
    assert "Nexora Payments Limited (Fintech, IN)" in llm.calls[0]["system"]
    assert_valid_event(ctx, StageId.S02_PLAN, res)


@pytest.mark.parametrize("bad", [
    {"sub_questions": QUESTIONS[:11], "routing_rationale": "x"},
    {"sub_questions": QUESTIONS[:11] + ["Will revenue grow 20% next year?"], "routing_rationale": "x"},
    LLMError("s02: TimeoutError"),
])
async def test_s02_falls_back_to_template(tmp_path, bad):
    ctx = make_ctx(tmp_path, ScriptedLLM({"s02": bad}))
    res = await s02.run(ctx, {"entity": NEXORA})
    assert res.status == StageStatus.WARNING and res.patch["sub_questions"] == s02.TEMPLATE
    assert len(s02.TEMPLATE) == 12 and s02.valid_questions(s02.TEMPLATE)


# ---------------------------------------------------------------- PDF builders (India)

PNL = [("Revenue from operations", "revenue"), ("Other income", "other_income"), ("Finance costs", "finance_cost"),
       ("Depreciation and amortisation expense", "d_and_a"), ("Profit before tax", "pbt"), ("Total tax expense", "tax_expense"),
       ("Profit for the year", "pat_before_nci"), ("Profit attributable to owners of the Company", "pat")]
BS = [("Total assets", "total_assets"), ("Total liabilities", "total_liabilities"), ("Total equity", "total_equity"),
      ("Non-controlling interests", "non_controlling_interest"), ("Current borrowings", "borrowings_current"),
      ("Non-current borrowings", "borrowings_noncurrent"), ("Cash and cash equivalents", "cash_and_equivalents"),
      ("Trade receivables", "trade_receivables"), ("Total current assets", "current_assets"),
      ("Total current liabilities", "current_liabilities")]
CF = [("Net cash from operating activities", "cfo"), ("Purchase of property, plant and equipment", "purchase_of_ppe"),
      ("Purchase of intangible assets", "purchase_of_intangibles")]
LABELS = {k: lbl for lbl, k in PNL + BS + CF}

YEAR = {
    "FY26": dict(revenue="3,810.00", other_income="120.00", finance_cost="95.00", d_and_a="210.00", pbt="520.00",
                 tax_expense="130.00", pat_before_nci="390.00", pat="380.00", total_assets="6,200.00",
                 total_liabilities="2,600.00", total_equity="3,600.00", non_controlling_interest="40.00",
                 borrowings_current="150.00", borrowings_noncurrent="400.00", cash_and_equivalents="600.00",
                 trade_receivables="880.00", current_assets="2,900.00", current_liabilities="1,700.00", cfo="310.00",
                 purchase_of_ppe="(180.00)", purchase_of_intangibles="(60.00)"),
    "FY25": dict(revenue="3,240.00", other_income="100.00", finance_cost="80.00", d_and_a="180.00", pbt="450.00",
                 tax_expense="112.00", pat_before_nci="338.00", pat="330.00", total_assets="5,400.00",
                 total_liabilities="2,300.00", total_equity="3,100.00", non_controlling_interest="35.00",
                 borrowings_current="120.00", borrowings_noncurrent="380.00", cash_and_equivalents="520.00",
                 trade_receivables="610.00", current_assets="2,500.00", current_liabilities="1,500.00", cfo="360.00",
                 purchase_of_ppe="(150.00)", purchase_of_intangibles="(45.00)"),
    "FY24": dict(revenue="2,800.00", other_income="80.00", finance_cost="70.00", d_and_a="150.00", pbt="380.00",
                 tax_expense="95.00", pat_before_nci="285.00", pat="280.00", total_assets="5,000.00",
                 total_liabilities="2,000.00", total_equity="2,500.00", non_controlling_interest="30.00",
                 borrowings_current="100.00", borrowings_noncurrent="350.00", cash_and_equivalents="450.00",
                 trade_receivables="500.00", current_assets="2,100.00", current_liabilities="1,300.00", cfo="330.00",
                 purchase_of_ppe="(140.00)", purchase_of_intangibles="(40.00)"),
}


def _table_page(doc, title, rows, cur, prev, unit="(Rs. in crore)"):
    page = doc.new_page()
    y = 72
    lines = [(title, None, None)] + ([(unit, None, None)] if unit else []) + [("Particulars", None, None)] + rows
    for label, a, b in lines:
        page.insert_text((60, y), label, fontsize=9)
        for x, v in ((380, a), (480, b)):
            if v:
                page.insert_text((x - pymupdf.get_text_length(v, fontsize=9), y), v, fontsize=9)
        y += 16
    return page


def build_ar(path: Path, fy: str, overrides: dict | None = None, unit="(Rs. in crore)", kmp=True, segment=False):
    prev = f"FY{int(fy[2:]) - 1}"
    cur_v, prev_v = dict(YEAR[fy]), dict(YEAR[prev])
    for (period, key), v in (overrides or {}).items():
        (cur_v if period == fy else prev_v)[key] = v
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Contents\nConsolidated Balance Sheet ..... 4\nCorporate Overview ..... 2")
    doc.new_page().insert_text((72, 72), "Corporate Overview\nNexora provides a payment gateway, in-store POS and banking "
                               "APIs to merchants. It serves about 1.9 million active merchants.", fontsize=9)
    if kmp:
        doc.new_page().insert_text((72, 72), "Key Managerial Personnel\nAnita Verghese - Managing Director & CEO\n"
                                   "Rahul Menon - Chief Financial Officer\nPriya Shah - Company Secretary", fontsize=9)
    for title, spec in (("Consolidated Statement of Profit and Loss for the year ended 31 March", PNL),
                        ("Consolidated Balance Sheet as at 31 March", BS),
                        ("Consolidated Statement of Cash Flows for the year ended 31 March", CF)):
        _table_page(doc, f"{title} 20{fy[2:]}", [(lbl, cur_v[k], prev_v[k]) for lbl, k in spec], cur_v, prev_v, unit)
    if segment:
        _table_page(doc, "Note 41: Segment information", [("Payments", "2,900.00", None), ("Lending", "700.00", None),
                                                          ("Banking APIs", "210.00", None)], {}, {})
    doc.save(path)
    return path.read_bytes()


def build_results(path: Path):
    doc = pymupdf.open()
    rows = [("Revenue from operations", "1,020.00", "880.00"), ("Other income", "30.00", "25.00"),
            ("Finance costs", "24.00", "20.00"), ("Depreciation and amortisation expense", "55.00", "48.00"),
            ("Profit before tax", "140.00", "120.00"), ("Total tax expense", "35.00", "30.00"),
            ("Net profit attributable to owners", "103.00", "88.00")]
    _table_page(doc, "Statement of Unaudited Consolidated Financial Results for the quarter ended 30 June 2026", rows, {}, {})
    doc.save(path)
    return path.read_bytes()


def label_mapper(user: str) -> dict:
    """A well-behaved row mapper: finds each requested field by its printed label. Coordinates only."""
    fields = json.loads(re.search(r"REQUESTED_FIELDS: (.*)", user).group(1))
    periods = json.loads(re.search(r"REQUESTED_PERIODS: (.*)", user).group(1))
    grid = json.loads(re.search(r"RAW_TABLE_GRID: (.*)", user, re.S).group(1))
    extra = {"Net profit attributable to owners": "pat"}
    out = []
    for r, row in enumerate(grid):
        label = row[0]
        if fields == ["segment_revenue"]:
            if row[1:] and row[1] and label not in ("Particulars",):
                out.append({"field_key": "segment_revenue", "fiscal_period": periods[0], "reported_label": label,
                            "row_idx": r, "col_idx": 1, "segment_name": label})
            continue
        key = next((k for k, lbl in LABELS.items() if lbl == label), extra.get(label))
        if key in fields:
            for c, period in enumerate(periods, start=1):
                out.append({"field_key": key, "fiscal_period": period, "reported_label": label, "row_idx": r, "col_idx": c})
    return {"mappings": out}


@pytest.fixture
def india_sources(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    blobs = {
        # FY26 report restates FY25 revenue and misprints FY25 total liabilities (the FY25 report has it right).
        "https://www.bseindia.com/bseplus/AnnualReport/543999/ar26.pdf": build_ar(
            src / "ar26.pdf", "FY26", {("FY25", "total_liabilities"): "2,400.00"}, segment=True),
        # FY25 report prints FY25 revenue before restatement and an FY24 balance sheet that does not balance.
        "https://www.bseindia.com/bseplus/AnnualReport/543999/ar25.pdf": build_ar(
            src / "ar25.pdf", "FY25", {("FY25", "revenue"): "3,230.00", ("FY24", "total_liabilities"): "1,500.00"}),
        "https://www.bseindia.com/xml-data/corpfiling/AttachLive/q1.pdf": build_results(src / "q1.pdf"),
    }
    docs = [
        FilingDoc("BSE", "Annual Report 2024-25", "2025-08-01", "https://www.bseindia.com/bseplus/AnnualReport/543999/ar25.pdf",
                  "annual_report", SourceTier.T1, "FY25"),
        FilingDoc("BSE", "Annual Report 2025-26", "2026-08-01", "https://www.bseindia.com/bseplus/AnnualReport/543999/ar26.pdf",
                  "annual_report", SourceTier.T1, "FY26"),
        FilingDoc("BSE", "Financial Results for Q1", "2026-08-10", "https://www.bseindia.com/xml-data/corpfiling/AttachLive/q1.pdf",
                  "results", SourceTier.T2),
        FilingDoc("BSE", "Annual report mirror", "2026-08-02", "https://www.moneycontrol.com/ar26.pdf", "annual_report",
                  SourceTier.T1, "FY26"),
        FilingDoc("BSE", "Resignation of CFO", "2026-05-02", "https://www.bseindia.com/xml-data/corpfiling/AttachLive/r.pdf",
                  "management_change", SourceTier.T2),
    ]

    async def download(url):
        if url not in blobs:
            raise SourceError(f"404 for {url}")
        return blobs[url]

    return FakeIndia(SCRIPS, docs), download


# ---------------------------------------------------------------- s03 (India)


async def test_s03_india_downloads_hashes_and_rejects_aggregators(tmp_path, india_sources):
    india, download = india_sources
    ctx = make_ctx(tmp_path, ScriptedLLM({}), india=india, download=download)
    res = await s03.run(ctx, {"entity": NEXORA})
    assert res.status == StageStatus.COMPLETED
    assert res.patch["fiscal_years"] == ["FY24", "FY25", "FY26"]
    reports = ctx.scratch["annual_reports"]
    assert [r["fiscal_year"] for r in reports] == ["FY25", "FY26"] and all(len(r["sha256"]) == 64 for r in reports)
    assert any("moneycontrol" in c.raw_output_sample for c in res.panel.tool_calls if c.status == "fallback")
    assert [q["label"] for q in ctx.scratch["quarterly_reports"]] == ["Q1FY27"]
    assert any(e["category"] == "management_change" for e in ctx.scratch["filing_events"])
    roster = res.patch["management_roster"]
    assert [(p.name, p.role) for p in roster] == [("Anita Verghese", "Managing Director & CEO"),
                                                  ("Rahul Menon", "Chief Financial Officer"),
                                                  ("Priya Shah", "Company Secretary")]
    reg = res.patch["evidence_registry"]
    assert reg["ev_nxra_business"].page_number == 2 and "1.9 million" in reg["ev_nxra_business"].verbatim_snippet
    assert reg["ev_nxra_kmp"].page_number == 3
    ev = assert_valid_event(ctx, StageId.S03_RETRIEVE, res)
    apply({}, ev)


async def test_s03_roster_fallback_keeps_only_verbatim_rows(tmp_path):
    src = tmp_path / "ar.pdf"
    blob = build_ar(src, "FY26", kmp=False)
    doc = pymupdf.open(src)
    doc.new_page().insert_text((72, 72), "Key Managerial Personnel\nMs. Anita Verghese serves as Managing Director "
                               "and CEO; Rahul Menon is our Chief Financial Officer.", fontsize=9)
    doc.save(tmp_path / "ar2.pdf")
    blob = (tmp_path / "ar2.pdf").read_bytes()
    url = "https://www.bseindia.com/bseplus/AnnualReport/543999/ar.pdf"
    india = FakeIndia(SCRIPS, [FilingDoc("BSE", "AR", "2026-08-01", url, "annual_report", SourceTier.T1, "FY26")])

    async def download(u):
        return blob

    llm = ScriptedLLM({"s03_roster": {"people": [
        {"name": "Anita Verghese", "role": "Managing Director"},
        {"name": "Rahul Menon", "role": "Chief Financial Officer"},
        {"name": "Vikram Rao", "role": "Chief Operating Officer"},  # not in the text
    ]}})
    ctx = make_ctx(tmp_path, llm, india=india, download=download)
    res = await s03.run(ctx, {"entity": NEXORA})
    assert [p.name for p in res.patch["management_roster"]] == ["Anita Verghese", "Rahul Menon"]
    assert res.status == StageStatus.WARNING  # only FY25-FY26 available
    assert any("fewer than 3" in g for g in res.patch["stated_gaps"])


async def test_s03_no_source_fails(tmp_path):
    ent = NEXORA.model_copy(update={"bse_scrip_code": None})
    res = await s03.run(make_ctx(tmp_path, ScriptedLLM({})), {"entity": ent})
    assert res.status == StageStatus.FAILED


# ---------------------------------------------------------------- s04 (India)


async def _india_stage4(tmp_path, india_sources, mapper=label_mapper):
    india, download = india_sources
    ctx = make_ctx(tmp_path, ScriptedLLM({"s04": mapper}), india=india, download=download)
    r3 = await s03.run(ctx, {"entity": NEXORA})
    r4 = await s04.run(ctx, {"entity": NEXORA})
    return ctx, r3, r4


def _item(items, period, key):
    return next(li for li in items if li.fiscal_period == period and li.field_key == key)


async def test_s04_extracts_cells_with_bboxes_and_units(tmp_path, india_sources):
    ctx, r3, r4 = await _india_stage4(tmp_path, india_sources)
    items = r4.patch["line_items"]
    rev = _item(items, "FY26", "revenue")
    assert rev.value == Decimal("3810.00") and rev.formatted_value == "₹3,810 cr" and rev.item_id == "li_fy26_revenue"
    assert rev.evidence.page_number is not None and rev.evidence.bbox is not None and rev.evidence.source_unit == "(Rs. in crore)"
    assert rev.evidence.extracted_value_raw == "3,810.00" and "Revenue from operations" in rev.evidence.verbatim_snippet
    capex = _item(items, "FY26", "capex")
    assert capex.is_derived and capex.value == Decimal("240.00")  # |(180)| + |(60)|
    ebit = _item(items, "FY26", "ebit")
    assert ebit.value == Decimal("495.00")  # 520 + 95 - 120
    segs = [li for li in items if li.field_key == "segment_revenue"]
    assert {s.segment_name for s in segs} == {"Payments", "Lending", "Banking APIs"}
    assert "li_fy26_segment_revenue_banking_apis" in {s.item_id for s in segs}


async def test_s04_newer_filing_wins_and_marks_restatement(tmp_path, india_sources):
    _, _, r4 = await _india_stage4(tmp_path, india_sources)
    rev25 = _item(r4.patch["line_items"], "FY25", "revenue")
    assert rev25.value == Decimal("3240.00") and rev25.is_restated
    assert "ar_fy26" in rev25.evidence.document_id


async def test_s04_reconciliation_retry_and_discard(tmp_path, india_sources):
    _, _, r4 = await _india_stage4(tmp_path, india_sources)
    items, checks, gaps = r4.patch["line_items"], r4.patch["reconciliation_checks"], r4.patch["stated_gaps"]
    by_id = {c.check_id: c for c in checks}
    # FY25: the FY26 report misprints liabilities; the FY25 report's own column balances, so it is used.
    assert by_id["rc_bs_fy25"].status == "passed"
    assert _item(items, "FY25", "total_liabilities").value == Decimal("2300.00")
    # FY24: only one source and it does not balance, so the identity's items are blanked with a gap.
    assert _item(items, "FY24", "total_assets").value is None
    assert any(g.startswith("FY24 Balance Sheet Identity") for g in gaps)
    assert by_id["rc_np_fy26"].status == "passed" and by_id["rc_op_fy26"].status == "skipped"
    assert r4.status == StageStatus.WARNING


async def test_s04_quarterly_items_from_results_pdf(tmp_path, india_sources):
    _, _, r4 = await _india_stage4(tmp_path, india_sources)
    q = r4.patch["quarterly_items"]
    assert _item(q, "Q1FY27", "revenue").value == Decimal("1020.00")
    assert _item(q, "Q1FY26", "revenue").value == Decimal("880.00")
    assert _item(q, "Q1FY27", "ebitda").value == Decimal("189.00")  # 140 + 24 - 30 + 55
    assert _item(q, "Q1FY27", "revenue").evidence.evidence_id.endswith("_q")
    assert not any(li.fiscal_period.startswith("Q") for li in r4.patch["line_items"])


async def test_s04_rejects_mislabelled_and_out_of_range_coordinates(tmp_path, india_sources):
    def sloppy(user):
        out = label_mapper(user)
        for m in out["mappings"]:
            if m["field_key"] == "revenue":
                m["reported_label"] = "Total income"  # wrong row label
            if m["field_key"] == "cfo":
                m["col_idx"] = 9
        out["mappings"].append({"field_key": "pat", "fiscal_period": "FY19", "reported_label": "x", "row_idx": 0, "col_idx": 1})
        return out

    _, _, r4 = await _india_stage4(tmp_path, india_sources, sloppy)
    items = r4.patch["line_items"]
    assert not any(li.field_key == "revenue" for li in items)
    assert not any(li.field_key == "cfo" for li in items)
    rejected = [c for c in r4.panel.tool_calls if c.tool_name == "validate_row_mappings"]
    assert rejected and "does not match row text" in rejected[0].raw_output_sample


async def test_s04_rejects_table_without_unit_header(tmp_path):
    src = tmp_path / "ar.pdf"
    blob = build_ar(src, "FY26", unit=None)
    url = "https://www.bseindia.com/bseplus/AnnualReport/543999/ar.pdf"
    india = FakeIndia(SCRIPS, [FilingDoc("BSE", "AR", "2026-08-01", url, "annual_report", SourceTier.T1, "FY26")])

    async def download(u):
        return blob

    ctx = make_ctx(tmp_path, ScriptedLLM({"s04": label_mapper}), india=india, download=download)
    await s03.run(ctx, {"entity": NEXORA})
    r4 = await s04.run(ctx, {"entity": NEXORA})
    assert r4.status == StageStatus.FAILED
    assert any("no unit header" in g for g in r4.patch["stated_gaps"])
    assert not [c for c in ctx.llm.calls if c["stage"] == "s04"]  # the model is never asked to guess a scale


# ---------------------------------------------------------------- s03/s04 (US, XBRL)


def _fact(val, end, start=None, form="10-K", filed="2026-02-20", fp="FY", fy=2025, accn="0001-26-000001"):
    f = {"val": val, "end": end, "form": form, "filed": filed, "fp": fp, "fy": fy, "accn": accn}
    if start:
        f["start"] = start
    return f


def _annual(tag_vals):
    facts = {}
    for tag, vals in tag_vals.items():
        rows = []
        for year, v in vals.items():
            instant = tag in ("Assets", "Liabilities", "StockholdersEquity", "AccountsReceivableNetCurrent")
            rows.append(_fact(v, f"{year}-12-31", None if instant else f"{year}-01-01", fy=year, filed=f"{year + 1}-02-20"))
        facts[tag] = {"units": {"USD": rows}}
    return facts


US_FACTS = {"cik": 1234567, "facts": {"us-gaap": _annual({
    "Revenues": {2023: 10_000_000_000, 2024: 11_000_000_000, 2025: 12_500_000_000},
    "OperatingIncomeLoss": {2023: 1_000_000_000, 2024: 1_200_000_000, 2025: 1_500_000_000},
    "NetIncomeLoss": {2023: 700_000_000, 2024: 800_000_000, 2025: 1_000_000_000},
    "Assets": {2023: 20_000_000_000, 2024: 22_000_000_000, 2025: 25_000_000_000},
    "Liabilities": {2023: 12_000_000_000, 2024: 13_000_000_000, 2025: 15_000_000_000},
    "StockholdersEquity": {2023: 8_000_000_000, 2024: 9_000_000_000, 2025: 10_000_000_000},
})}}
SUBMISSIONS = {"cik": "1234567", "filings": {"recent": {
    "form": ["10-K", "8-K", "10-K"], "accessionNumber": ["0001-26-000001", "0001-26-000002", "0001-25-000001"],
    "primaryDocument": ["acme-10k.htm", "acme-8k.htm", "acme-10k24.htm"],
    "filingDate": ["2026-02-20", "2026-03-01", "2025-02-20"], "reportDate": ["2025-12-31", "", "2024-12-31"],
    "items": ["", "5.02", ""]}}}
TENK_HTML = """<html><body><p>Table of contents</p><p>Item 1. Business ..... 3</p>
<p>Item 1. Business</p><p>Acme Corp builds payment terminals for retailers. It operates in North America and Europe.</p>
<p>Item 1A. Risk Factors</p>
<h3>Information About Our Executive Officers</h3>
<table><tr><th>Name</th><th>Age</th><th>Position</th></tr>
<tr><td>Jane Doe</td><td>52</td><td>Chief Executive Officer</td></tr>
<tr><td>John Roe</td><td>47</td><td>Chief Financial Officer</td></tr></table></body></html>"""
ACME = ResolvedEntity(query="Acme", legal_name="Acme Corp", ticker="ACME", exchange="NYSE", jurisdiction="US", is_listed=True,
                      cik="0001234567", sector="Payments", headquarters="Austin", fy_end_month="December",
                      currency=CurrencyUnit.USD_M)


async def test_s03_s04_us_xbrl_path(tmp_path):
    async def download(url):
        assert url.endswith("acme-10k.htm")
        return TENK_HTML.encode()

    sec = FakeSec([TickerMatch("0001234567", "ACME", "Acme Corp")], SUBMISSIONS, US_FACTS)
    ctx = make_ctx(tmp_path, ScriptedLLM({}), sec=sec, download=download)
    r3 = await s03.run(ctx, {"entity": ACME})
    assert r3.status == StageStatus.COMPLETED
    assert r3.patch["fiscal_years"] == ["FY2023", "FY2024", "FY2025"]
    assert [(p.name, p.role) for p in r3.patch["management_roster"]] == [("Jane Doe", "Chief Executive Officer"),
                                                                       ("John Roe", "Chief Financial Officer")]
    assert r3.patch["evidence_registry"]["ev_acme_business"].verbatim_snippet.startswith("Acme Corp builds payment terminals")
    assert "ev_acme_10k_fy2025" in r3.patch["evidence_registry"]
    assert ctx.scratch["filing_events"][0]["title"] == "Form 8-K items 5.02"

    r4 = await s04.run(ctx, {"entity": ACME})
    items = r4.patch["line_items"]
    rev = _item(items, "FY2025", "revenue")
    assert rev.value == Decimal("12500") and rev.formatted_value == "$12,500 M" and rev.evidence.source_unit == "USD"
    assert _item(items, "FY2025", "ebit").value == Decimal("1500")  # US GAAP: operating income
    assert by_status(r4.patch["reconciliation_checks"], "rc_bs_fy2025") == "passed"
    assert any("Segment revenue" in g for g in r4.patch["stated_gaps"])
    assert_valid_event(ctx, StageId.S04_EXTRACT, r4)


def by_status(checks, cid):
    return next(c.status for c in checks if c.check_id == cid)


# ---------------------------------------------------------------- s05 / s06 and the folded state


async def test_s05_s06_run_on_extracted_items_and_fold_into_valid_state(tmp_path, india_sources):
    ctx, r3, r4 = await _india_stage4(tmp_path, india_sources)
    e3, e4 = assert_valid_event(ctx, StageId.S03_RETRIEVE, r3), assert_valid_event(ctx, StageId.S04_EXTRACT, r4)
    state = {"entity": NEXORA, **r4.patch}
    r5 = await s05.run(ctx, state)
    ledger = r5.patch["metrics_ledger"]
    dso = next(m for m in ledger if m.metric_id == "met_dso_fy26")
    assert dso.formatted_value == "84.3 d" and dso.inputs[0].evidence_id.startswith("ev_nxra_ar_fy26_p")
    assert r5.patch["quarterly_metrics"]  # QoQ needs two quarters: Q1FY26 and Q1FY27
    e5 = assert_valid_event(ctx, StageId.S05_METRICS, r5)
    assert all(a.actor.value == "CODE" for a in r5.panel.actions)

    r6 = await s06.run(ctx, {**state, **r5.patch})
    flags = r6.patch["red_flags"]
    assert flags and {f.status for f in flags} <= {"fired", "clear", "insufficient_data"}
    assert all(a.actor.value == "CODE" for a in r6.panel.actions)
    e6 = assert_valid_event(ctx, StageId.S06_RED_FLAGS, r6)

    folded = apply({}, e3, e4, e5, e6)
    assert folded.metrics_ledger and folded.red_flags and folded.management_roster
    assert [e.event_seq for e in ctx.emitter.events] == sorted(e.event_seq for e in ctx.emitter.events)


async def test_s05_s06_tolerate_empty_inputs(tmp_path):
    ctx = make_ctx(tmp_path, ScriptedLLM({}))
    r5 = await s05.run(ctx, {"line_items": []})
    assert r5.status == StageStatus.WARNING and r5.patch["metrics_ledger"] == []
    r6 = await s06.run(ctx, {**r5.patch, "line_items": []})
    assert all(f.status == "insufficient_data" for f in r6.patch["red_flags"])


# ---------------------------------------------------------------- s07-s12 rules (the full graph is in test_full_pipeline)


def test_s07_corroboration_needs_a_matching_filing_within_window():
    from datetime import date as d

    from backend.pipeline.stages import s07_news_intelligence as s07

    filings = [{"filing_id": "fl_01", "event_category": "Management", "date": "2026-05-04", "tier": "T2", "source": "SEC 8-K",
                "title": "Form 8-K items 5.02", "url": "https://www.sec.gov/x"}]
    assert s07.corroborating_filing("Management", d(2026, 5, 2), filings) is not None
    assert s07.corroborating_filing("Management", d(2026, 8, 2), filings) is None  # outside 45 days
    assert s07.corroborating_filing("Acquisition", d(2026, 5, 2), filings) is None  # wrong kind


def test_s08_uncorroborated_isolated_signal_becomes_noise():
    from datetime import UTC, datetime

    from backend.pipeline.prompts import SentimentOut
    from backend.pipeline.stages import s08_sentiment_filter as s08
    from backend.sources.sentiment_fetcher import Post

    posts = {f"p{i}": Post(f"p{i}", "Reddit", f"u{i}", f"text {i}", datetime(2026, 9, 1, tzinfo=UTC)) for i in range(4)}

    def item(pid, audience="Customer", ids=()):
        return {"post_id": pid, "platform": "Reddit", "audience": audience, "quote_text": "x", "polarity": "negative",
                "verdict": "signal", "verdict_reasons": ["r"], "corroborating_ids": list(ids)}

    out = SentimentOut.model_validate({"audience_breakdown": [], "items": [
        item("p0", "Employee"), item("p1", ids=["met_dso_fy26", "met_made_up"]), item("p9")]})
    items, notes = s08.build_items(out, posts, {"met_dso_fy26"})
    by = {i.post_id: i for i in items}
    assert set(by) == {"p0", "p1"}  # p9 was never fetched
    assert by["p0"].verdict == "noise" and by["p1"].verdict == "signal" and by["p1"].corroborating_ids == ["met_dso_fy26"]
    assert by["p0"].quote_text == "text 0" and by["p0"].evidence.source_tier == SourceTier.T4


def test_s10_numbers_in_claims_must_come_from_the_source():
    from backend.pipeline.stages.s10_contradiction_resolver import Source, numbers_grounded
    from backend.tests.conftest import make_evidence

    src = Source(make_evidence(), ["84.3 d", "Trade receivables 880.00"])
    assert numbers_grounded("DSO rose to 84.3 days in FY26", src)
    assert numbers_grounded("Receivables of 880.00", src)
    assert not numbers_grounded("DSO rose to 91 days", src)


async def test_s12_gate_on_the_sample_fixture_state(tmp_path):
    from backend.pipeline.stages import s12_verification_gate as s12
    from backend.replay.player import load_fixture, project_final_state

    root = Path(__file__).resolve().parents[2]
    projected = project_final_state(load_fixture(root / "data/golden_runs/sample_synthetic.jsonl.gz"))
    final = ResearchRunState.model_validate({"run_id": "r", "mode": "replay", "company_query": "Nexora", "started_at_ms": 0,
                                             **projected})
    state = {k: getattr(final, k) for k in ResearchRunState.model_fields}
    res = await s12.run(make_ctx(tmp_path, ScriptedLLM({})), state)
    assert res.status == StageStatus.COMPLETED and res.patch["verification"].passed
    assert all(a.actor.value == "CODE" for a in res.panel.actions)
