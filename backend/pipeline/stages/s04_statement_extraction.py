"""Stage 4: statement line items. The LLM returns table coordinates only; code reads the cell, parses it,
normalises the unit, derives EBIT/EBITDA/debt/capex and runs the reconciliation suite."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

import pymupdf as fitz

from backend.engine.derived_fields import compute_derived_fields, item_id_for, line_items_period_order
from backend.engine.formatters import format_currency
from backend.engine.reconciliation import run_reconciliation_suite
from backend.engine.unit_normaliser import UnitHeaderError, normalise_units, parse_cell_decimal
from backend.pipeline.graph import RunContext, StageResult, Timer, panel, trace
from backend.pipeline.llm_client import LLMError
from backend.pipeline.prompts import (
    STATEMENT_ROW_MAPPING_SYSTEM,
    STATEMENT_ROW_MAPPING_USER,
    RowMapping,
    RowMappingOut,
)
from backend.pipeline.stages.s03_filing_retrieval import now_iso, slug_for
from backend.schemas.common import (
    ActorBadge,
    CurrencyUnit,
    EvidenceCitation,
    SourceTier,
    StageId,
    StageStatus,
)
from backend.schemas.financials import LineItem
from backend.schemas.state import ResolvedEntity
from backend.sources import TIER_WEIGHTS
from backend.sources.pdf_extractor import RawTable, extract_raw_table, locate_statement_pages
from backend.sources.sec_edgar import XbrlLineItem, extract_annual_line_items, extract_quarterly_line_items

S = StageId.S04_EXTRACT
C, L = ActorBadge.CODE, ActorBadge.LLM
GUARDS = ["The model returns row/column indices only; code rejects a mapping whose label does not match column 0",
          "No unit header detected: the table is rejected with a stated gap rather than guessing the scale",
          "Failed identity: retry with the other filing's comparative column, then blank the items and state a gap",
          "Derived keys (EBIT, EBITDA, gross debt, capex) are never mapped by the model"]

STATEMENT_FIELDS: dict[str, list[str]] = {
    "profit_and_loss": ["revenue", "other_income", "finance_cost", "d_and_a", "pbt", "tax_expense", "pat_before_nci", "pat"],
    "balance_sheet": ["total_assets", "total_liabilities", "total_equity", "non_controlling_interest", "borrowings_current",
                      "borrowings_noncurrent", "cash_and_equivalents", "trade_receivables", "current_assets",
                      "current_liabilities"],
    "cash_flow": ["cfo", "purchase_of_ppe", "purchase_of_intangibles"],
}
QUARTERLY_FIELDS = ["revenue", "other_income", "finance_cost", "d_and_a", "pbt", "tax_expense", "pat"]
RESULTS_TITLE = re.compile(r"consolidated[^\n]{0,60}results", re.I)
SEGMENT_TITLE = re.compile(r"segment (information|reporting|revenue)", re.I)
IDENTITY_FIELDS = {"rc_bs": ("total_assets", "total_liabilities", "total_equity"),
                   "rc_np": ("pbt", "tax_expense", "pat_before_nci")}


@dataclass
class Extracted:
    field_key: str
    period: str
    value: Optional[Decimal]
    raw: str
    label: str
    evidence: EvidenceCitation
    segment_name: Optional[str] = None


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def validate_mappings(table: RawTable, mappings: list[RowMapping], fields: list[str], periods: list[str],
                      allow_segments: bool = False) -> tuple[list[tuple[RowMapping, str]], list[str]]:
    """Keep a mapping only when it names a requested field and period, points inside the grid at a non-empty numeric
    column, and its reported_label matches the row's column-0 text."""
    grid = table.grid
    ok, rejected = [], []
    seen: set[tuple[str, str, Optional[str]]] = set()
    for m in mappings:
        if m.row_idx is None or m.col_idx is None:
            continue
        key = (m.field_key, m.fiscal_period, m.segment_name if allow_segments else None)
        why = None
        if m.field_key not in fields or m.fiscal_period not in periods:
            why = "not requested"
        elif m.field_key == "segment_revenue" and not (allow_segments and m.segment_name):
            why = "segment row without a segment name"
        elif not (0 <= m.row_idx < len(grid)) or not (1 <= m.col_idx < len(grid[m.row_idx])):
            why = "coordinates outside the table"
        elif not grid[m.row_idx][m.col_idx]:
            why = "empty cell"
        elif _norm(m.reported_label) != _norm(grid[m.row_idx][0]):
            why = f"label {m.reported_label!r} does not match row text {grid[m.row_idx][0]!r}"
        elif key in seen:
            why = "duplicate mapping"
        if why:
            rejected.append(f"{m.field_key} {m.fiscal_period}: {why}")
            continue
        seen.add(key)
        ok.append((m, grid[m.row_idx][m.col_idx]))
    return ok, rejected


async def map_table(ctx: RunContext, table: RawTable, doc_title: str, fields: list[str], periods: list[str],
                    calls: list) -> list[RowMapping]:
    t = Timer()
    try:
        res = await ctx.llm.structured(
            stage="s04", schema=RowMappingOut,
            system=STATEMENT_ROW_MAPPING_SYSTEM.format(page_num=table.page, doc_title=doc_title),
            user=STATEMENT_ROW_MAPPING_USER.format(fields_json=json.dumps(fields), periods_json=json.dumps(periods),
                                                   unit_header=table.unit_header, grid_json=json.dumps(table.grid)))
    except LLMError as exc:
        calls.append(trace(ctx, "map_table_rows_to_schema", L, {"doc": doc_title, "page": table.page}, t.ms, str(exc), "error"))
        return []
    calls.append(trace(ctx, "map_table_rows_to_schema", L, {"doc": doc_title, "page": table.page, "fields": len(fields)},
                       res.duration_ms, [m.model_dump(exclude_none=True) for m in res.parsed.mappings[:6]]))
    return res.parsed.mappings


def _cell_at(table: RawTable, m: RowMapping):
    return table.rows[m.row_idx][m.col_idx]


async def extract_table(ctx: RunContext, report: dict[str, Any], page: int, fields: list[str], periods: list[str],
                        currency: CurrencyUnit, calls: list, gaps: list[str], suffix: str = "",
                        allow_segments: bool = False) -> list[Extracted]:
    t = Timer()
    table = extract_raw_table(report["path"], page)
    calls.append(trace(ctx, "extract_raw_tables_from_pdf", C, {"doc": report["title"], "page": page}, t.ms,
                       f"{len(table.rows)} rows; unit header {table.unit_header!r}"))
    if not table.unit_header:
        gaps.append(f"{report['title']} p.{page}: no unit header printed; table not used")
        return []
    try:
        normalise_units(None, table.unit_header, currency)
    except UnitHeaderError as exc:
        gaps.append(f"{report['title']} p.{page}: {exc}; table not used")
        return []
    mappings = await map_table(ctx, table, report["title"], fields, periods, calls)
    ok, rejected = validate_mappings(table, mappings, fields, periods, allow_segments)
    if rejected:
        calls.append(trace(ctx, "validate_row_mappings", C, {"doc": report["title"], "page": page}, 0,
                           "; ".join(rejected[:6]), "fallback"))
    out = []
    tier = SourceTier(report.get("tier", "T1"))
    for m, raw in ok:
        cell = _cell_at(table, m)
        value = normalise_units(parse_cell_decimal(raw), table.unit_header, currency)
        name = m.field_key + (f"_{_seg_slug(m.segment_name)}" if m.segment_name and allow_segments else "")
        ev_id = f"ev_{report['doc_id']}_p{page}_{name}_{m.fiscal_period.lower()}{suffix}"
        row_text = " ".join(c for c in table.grid[m.row_idx] if c)
        ev = EvidenceCitation(
            evidence_id=ev_id, document_id=report["doc_id"], document_title=report["title"], source_url=report["url"],
            source_tier=tier, trust_weight=TIER_WEIGHTS[tier], page_number=page, table_id=f"p{page}",
            bbox=cell.bbox if cell else None, page_size=table.page_size, verbatim_snippet=row_text,
            extracted_value_raw=raw, source_unit=table.unit_header, retrieved_at=now_iso())
        out.append(Extracted(m.field_key, m.fiscal_period, value, raw, table.grid[m.row_idx][0], ev,
                             m.segment_name if allow_segments else None))
    return out


def _seg_slug(name: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def to_line_item(x: Extracted, unit: CurrencyUnit, restated: bool = False) -> LineItem:
    key = x.field_key if not x.segment_name else f"segment_revenue_{_seg_slug(x.segment_name)}"
    return LineItem(item_id=item_id_for(x.period, key), fiscal_period=x.period, field_key=x.field_key,
                    reported_label=x.label, value=x.value, formatted_value=format_currency(x.value, unit), unit=unit,
                    is_restated=restated, segment_name=x.segment_name, evidence=x.evidence)


def merge_newest_wins(per_report: list[list[Extracted]]) -> tuple[dict[tuple[str, str], Extracted], set[tuple[str, str]],
                                                                   dict[tuple[str, str], list[Extracted]]]:
    """Reports are given oldest first. A later filing's comparative column replaces the earlier value; a changed
    value marks the item restated. All candidates are kept for the reconciliation retry."""
    chosen: dict[tuple[str, str], Extracted] = {}
    restated: set[tuple[str, str]] = set()
    candidates: dict[tuple[str, str], list[Extracted]] = {}
    for items in per_report:
        for x in items:
            k = (x.period, x.field_key if not x.segment_name else f"segment:{x.segment_name}")
            prior = chosen.get(k)
            if prior is not None and prior.value != x.value:
                restated.add(k)
            chosen[k] = x
            candidates.setdefault(k, []).insert(0, x)
    return chosen, restated, candidates


def reconcile(items: list[LineItem], candidates: dict[tuple[str, str], list[Extracted]], unit: CurrencyUnit,
              gaap: str) -> tuple[list[LineItem], list, list[str], list[str]]:
    """Run the suite; on a failed identity, retry that period with the other filing's values; if it still fails,
    blank the identity's items. Returns (raw items, checks, gaps, actions)."""
    gaps, actions = [], []
    by_key = {(li.fiscal_period, li.field_key): li for li in items if li.field_key != "segment_revenue"}

    def suite(raw: list[LineItem]):
        derived, _ = compute_derived_fields(raw, gaap)  # type: ignore[arg-type]
        return run_reconciliation_suite(raw + derived)

    checks = suite(items)
    for chk in [c for c in checks if c.status == "failed"]:
        prefix = chk.check_id.rsplit("_", 1)[0]
        fields = IDENTITY_FIELDS.get(prefix)
        if not fields:
            continue
        period = chk.fiscal_period
        alt = {f: candidates.get((period, f), [])[1:2] for f in fields}
        if any(alt.values()):
            trial = dict(by_key)
            for f, xs in alt.items():
                if xs:
                    trial[(period, f)] = to_line_item(xs[0], unit)
            retry = [c for c in suite(list(trial.values()) + [li for li in items if li.field_key == "segment_revenue"])
                     if c.check_id == chk.check_id][0]
            if retry.status == "passed":
                by_key = trial
                actions.append((C, f"{chk.check_name} {period}: passed using the other filing's comparative column"))
                continue
        for f in fields:
            li = by_key.get((period, f))
            if li is not None:
                by_key[(period, f)] = li.model_copy(update={"value": None, "formatted_value": format_currency(None, unit)})
        gaps.append(f"{period} {chk.check_name} failed ({chk.failure_reason or 'outside tolerance'}); "
                    f"{', '.join(fields)} discarded")
    final = list(by_key.values()) + [li for li in items if li.field_key == "segment_revenue"]
    return final, suite(final), gaps, actions


def xbrl_to_line_item(x: XbrlLineItem, slug: str, suffix: str = "") -> LineItem:
    unit = CurrencyUnit.USD_M
    ev = EvidenceCitation(
        evidence_id=f"ev_{slug}_xbrl_{x.field_key}_{x.fiscal_period.lower()}{suffix}",
        document_id=f"sec_{x.accession}" if x.accession else f"sec_{slug}_companyfacts",
        document_title=f"{x.form or '10-K'} {x.fiscal_period}", source_url=x.source_url or "https://data.sec.gov/api/xbrl/",
        source_tier=SourceTier.T1, trust_weight=1.0,
        verbatim_snippet=f"us-gaap:{x.tag} = {x.raw_value} USD for the period ending {x.period_end} ({x.form} filed {x.filed})",
        extracted_value_raw=x.raw_value, source_unit="USD", retrieved_at=now_iso())
    return LineItem(item_id=item_id_for(x.fiscal_period, x.field_key), fiscal_period=x.fiscal_period, field_key=x.field_key,
                    reported_label=f"us-gaap:{x.tag}", value=x.value, formatted_value=format_currency(x.value, unit),
                    unit=unit, is_restated=x.is_restated, evidence=ev)


def find_page(path: str, pattern: re.Pattern[str]) -> Optional[int]:
    with fitz.open(path) as doc:
        for i, page in enumerate(doc, start=1):
            if pattern.search(page.get_text()[:800]):
                return i
    return None


def prior_fy(label: str) -> str:
    return f"FY{int(label[2:]) - 1:02d}"


def year_ago_quarter(label: str) -> str:
    m = re.match(r"(Q[1-4]FY)(\d+)", label)
    return f"{m.group(1)}{int(m.group(2)) - 1:0{len(m.group(2))}d}" if m else label


async def _india(ctx: RunContext, entity: ResolvedEntity):
    calls, actions, gaps = [], [], []
    unit = entity.currency
    reports = ctx.scratch.get("annual_reports", [])
    per_report: list[list[Extracted]] = []
    for rep in reports:
        t = Timer()
        pages = locate_statement_pages(rep["path"])
        located = {k: (v.page if v else None) for k, v in pages.best.items()}
        runner = {k: (v.page if v else None) for k, v in pages.runner_up.items()}
        calls.append(trace(ctx, "locate_statement_pages", C, {"doc": rep["title"]}, t.ms,
                           f"best {located}; runner-up {runner}"))
        got: list[Extracted] = []
        periods = [rep["fiscal_year"], prior_fy(rep["fiscal_year"])]
        for stmt, fields in STATEMENT_FIELDS.items():
            page = located.get(stmt)
            if page is None:
                gaps.append(f"{rep['title']}: consolidated {stmt.replace('_', ' ')} page not found")
                continue
            got += await extract_table(ctx, rep, page, fields, periods, unit, calls, gaps)
        per_report.append(got)
    if reports:
        latest = reports[-1]
        seg_page = find_page(latest["path"], SEGMENT_TITLE)
        if seg_page:
            per_report.append(await extract_table(ctx, latest, seg_page, ["segment_revenue"], [latest["fiscal_year"]], unit,
                                                  calls, gaps, allow_segments=True))
        else:
            gaps.append(f"{latest['title']}: segment note not found; revenue mix unavailable")
    chosen, restated, candidates = merge_newest_wins(per_report)
    raw = [to_line_item(x, unit, k in restated) for k, x in chosen.items()]
    actions.append((L, "Mapped statement rows and columns to standard field keys (coordinates only)"))
    actions.append((C, f"Parsed {len(raw)} cell(s), normalised units to {unit.value}; newer filings override comparatives"))

    q_per: list[list[Extracted]] = []
    for qr in ctx.scratch.get("quarterly_reports", []):
        page = find_page(qr["path"], RESULTS_TITLE)
        if page is None:
            gaps.append(f"{qr['title']}: consolidated results table not found")
            continue
        q_per.append(await extract_table(ctx, qr, page, QUARTERLY_FIELDS, [qr["label"], year_ago_quarter(qr["label"])],
                                         unit, calls, gaps, suffix="_q"))
    q_chosen, q_restated, _ = merge_newest_wins(q_per)
    quarterly = [to_line_item(x, unit, k in q_restated) for k, x in q_chosen.items()]
    return raw, quarterly, calls, actions, gaps, candidates


async def _us(ctx: RunContext, entity: ResolvedEntity):
    calls, actions, gaps = [], [], []
    facts = ctx.scratch.get("xbrl_facts") or {}
    slug = slug_for(entity)
    t = Timer()
    xs, xgaps = extract_annual_line_items(facts, 5)
    qs = extract_quarterly_line_items(facts, 5)
    calls.append(trace(ctx, "extract_xbrl_companyfacts", C, {"cik": entity.cik, "years": 5}, t.ms,
                       f"{len(xs)} annual facts, {len(qs)} quarterly facts"))
    gaps += xgaps
    gaps.append("Segment revenue is not in XBRL companyfacts (dimensional facts); revenue mix unavailable")
    raw = [xbrl_to_line_item(x, slug) for x in xs]
    quarterly = [xbrl_to_line_item(x, slug, "_q") for x in qs]
    actions.append((C, "Selected one us-gaap fact per field and fiscal year from 10-K filings; converted USD to USD_M"))
    candidates = {(li.fiscal_period, li.field_key): [] for li in raw}
    return raw, quarterly, calls, actions, gaps, candidates


async def run(ctx: RunContext, state: dict[str, Any]) -> StageResult:
    entity: ResolvedEntity = state["entity"]
    gaap = "us_gaap" if entity.jurisdiction == "US" else "ind_as"
    if entity.jurisdiction == "US":
        raw, quarterly, calls, actions, gaps, candidates = await _us(ctx, entity)
    else:
        raw, quarterly, calls, actions, gaps, candidates = await _india(ctx, entity)
    if not raw:
        p = panel(S, [f"{entity.legal_name}"], calls, actions or [(C, "No statement table could be read")],
                  "Halted: no line items", "Metrics cannot be computed without statement figures.", GUARDS)
        return StageResult(StageStatus.FAILED, "No statement line items extracted", {"stated_gaps": gaps}, p)

    raw, checks, rgaps, ractions = reconcile(raw, candidates, entity.currency, gaap)
    gaps += rgaps
    actions += ractions
    derived, dgaps = compute_derived_fields(raw, gaap)  # type: ignore[arg-type]
    gaps += dgaps
    q_derived, _ = compute_derived_fields(quarterly, gaap) if quarterly else ([], [])  # type: ignore[arg-type]
    q_derived = [li for li in q_derived if li.field_key in ("ebit", "ebitda", "interest_expense")]
    line_items = sorted(raw + derived, key=lambda li: (line_items_period_order(li.fiscal_period), li.field_key))
    quarterly_items = sorted(quarterly + q_derived, key=lambda li: (line_items_period_order(li.fiscal_period), li.field_key))
    passed = sum(c.status == "passed" for c in checks)
    failed = sum(c.status == "failed" for c in checks)
    actions.append((C, f"Derived EBIT, EBITDA, interest, gross debt and capex; ran {len(checks)} reconciliation checks"))
    calls.append(trace(ctx, "run_reconciliation_suite", C, {"periods": len({c.fiscal_period for c in checks})}, 0,
                       f"{passed} passed, {failed} failed, {len(checks) - passed - failed} skipped"))
    p = panel(S, [f"{len(ctx.scratch.get('annual_reports', [])) or 'XBRL'} filing source(s)"], calls, actions,
              f"{len(line_items)} line items; {passed}/{len(checks)} checks passed",
              "Every figure is a parsed cell with its page and bounding box; identities are checked in code.", GUARDS)
    status = StageStatus.WARNING if failed or rgaps else StageStatus.COMPLETED
    log = f"Extracted {len(line_items)} line items across {len({li.fiscal_period for li in line_items})} years; " \
          f"{passed} reconciliation checks passed"
    return StageResult(status, log, {"line_items": line_items, "quarterly_items": quarterly_items,
                                     "reconciliation_checks": checks, "stated_gaps": gaps}, p)
