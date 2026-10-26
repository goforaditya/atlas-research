"""Pure Python implementation of all 16 financial metrics (Section 5). Never calls an LLM.

Each `calc_*` function takes Decimals and returns a MetricOutcome holding the unrounded value and status.
`compute_all_metrics` binds them to line items and builds the calculation ledger.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from backend.schemas.common import CurrencyUnit
from backend.schemas.financials import LineItem, MetricInputRef, MetricLedgerRow

from .decimal_math import DAYS_IN_YEAR, TEN_THOUSAND, cagr, growth, quantize, safe_div
from .derived_fields import item_id_for, line_items_period_order
from .formatters import (
    MISSING,
    NM_NEG_LABEL,
    NM_ZERO_LABEL,
    MetricUnit,
    format_metric_value,
    format_plain_number,
)

D = Optional[Decimal]


@dataclass(frozen=True)
class MetricOutcome:
    value: D
    status: str  # computed | nm_negative_base | nm_zero_div | missing_input
    note: str | None = None


def _out(pair: tuple[D, str], note: str | None = None) -> MetricOutcome:
    return MetricOutcome(pair[0], pair[1], note)


# ---------------------------------------------------------------- the 16 metrics

def calc_rev_yoy(rev_t: D, rev_t1: D) -> MetricOutcome:
    return _out(growth(rev_t, rev_t1))


def calc_rev_cagr(rev_t: D, rev_start: D, n: int) -> MetricOutcome:
    if n < 2:
        return MetricOutcome(None, "missing_input", "Fewer than 3 annual periods")
    return _out(cagr(rev_t, rev_start, n))


def calc_ebitda_margin(ebitda_t: D, rev_t: D) -> MetricOutcome:
    return _out(safe_div(ebitda_t, rev_t))


def calc_ebitda_margin_chg_bps(ebitda_t: D, rev_t: D, ebitda_t1: D, rev_t1: D) -> MetricOutcome:
    em_t = calc_ebitda_margin(ebitda_t, rev_t).value
    em_t1 = calc_ebitda_margin(ebitda_t1, rev_t1).value
    if em_t is None or em_t1 is None:
        return MetricOutcome(None, "missing_input", "EBITDA margin unavailable for one of the periods")
    # bps are stored as integers after quantising, computed from unrounded margins
    return MetricOutcome(quantize((em_t - em_t1) * TEN_THOUSAND, 0), "computed")


def calc_pat_margin(pat_t: D, rev_t: D) -> MetricOutcome:
    return _out(safe_div(pat_t, rev_t))


def calc_roe(pat_t: D, equity_t: D) -> MetricOutcome:
    return _out(safe_div(pat_t, equity_t))


def calc_dso(recv_t: D, rev_t: D) -> MetricOutcome:
    if recv_t is not None and recv_t < 0 and rev_t is not None and rev_t != 0:
        return MetricOutcome(None, "nm_negative_base", "Negative receivables")
    ratio, status = safe_div(recv_t, rev_t)
    return MetricOutcome(None if ratio is None else ratio * DAYS_IN_YEAR, status)


def calc_receivables_yoy(recv_t: D, recv_t1: D) -> MetricOutcome:
    return _out(growth(recv_t, recv_t1))


def calc_cfo_to_pat(cfo_t: D, pat_t: D) -> MetricOutcome:
    return _out(safe_div(cfo_t, pat_t))


def calc_cfo_yoy(cfo_t: D, cfo_t1: D) -> MetricOutcome:
    return _out(growth(cfo_t, cfo_t1))


def calc_fcf(cfo_t: D, capex_t: D) -> MetricOutcome:
    if cfo_t is None or capex_t is None:
        return MetricOutcome(None, "missing_input")
    return MetricOutcome(cfo_t - capex_t, "computed")


def calc_fcf_margin(cfo_t: D, capex_t: D, rev_t: D) -> MetricOutcome:
    fcf = calc_fcf(cfo_t, capex_t).value
    if fcf is None:
        return MetricOutcome(None, "missing_input")
    return _out(safe_div(fcf, rev_t))


def calc_net_debt_to_ebitda(debt_t: D, cash_t: D, ebitda_t: D) -> MetricOutcome:
    if debt_t is None or cash_t is None:
        return MetricOutcome(None, "missing_input")
    return _out(safe_div(debt_t - cash_t, ebitda_t))


def calc_gross_debt_yoy(debt_t: D, debt_t1: D) -> MetricOutcome:
    if debt_t is not None and debt_t1 == 0 and debt_t == 0:
        return MetricOutcome(Decimal(0), "computed", "No debt in either period")
    return _out(growth(debt_t, debt_t1))


def calc_interest_cover(ebit_t: D, interest_t: D) -> MetricOutcome:
    return _out(safe_div(ebit_t, interest_t))


def calc_current_ratio(ca_t: D, cl_t: D) -> MetricOutcome:
    return _out(safe_div(ca_t, cl_t))


# ---------------------------------------------------------------- formatting of outcomes

# Metric-specific N/M labels (Section 5); everything else uses the defaults.
NM_LABELS: dict[str, dict[str, str]] = {
    "roe": {"nm_negative_base": "N/M (Neg Equity)"},
    "cfo_to_pat": {"nm_negative_base": "N/M (Net Loss)"},
    "interest_cover": {"nm_zero_div": "N/M (No Int Exp)"},
    "rev_cagr": {"missing_input_short_span": "N/M (< 3 yrs)"},
}


def format_outcome(
    metric_key: str, unit: MetricUnit, outcome: MetricOutcome, currency: CurrencyUnit = CurrencyUnit.INR_CR
) -> str:
    labels = NM_LABELS.get(metric_key, {})
    if outcome.status == "computed":
        return format_metric_value(outcome.value, unit, metric_key=metric_key, currency=currency)
    if outcome.status == "nm_zero_div":
        return labels.get("nm_zero_div", NM_ZERO_LABEL)
    if outcome.status == "nm_negative_base":
        return labels.get("nm_negative_base", NM_NEG_LABEL)
    if metric_key == "rev_cagr" and outcome.note == "Fewer than 3 annual periods":
        return labels["missing_input_short_span"]
    return MISSING


# ---------------------------------------------------------------- ledger construction

@dataclass(frozen=True)
class MetricSpec:
    key: str
    label: str
    unit: MetricUnit
    template: str
    inputs: list[tuple[str, str, int]]  # (symbol, field_key, period offset: 0 = t, 1 = t-1)
    calc: Callable[..., MetricOutcome]


SPECS: list[MetricSpec] = [
    MetricSpec("rev_yoy", "Revenue Growth YoY", "pct", "(Revenue_t - Revenue_t_minus_1) / Revenue_t_minus_1",
               [("Revenue", "revenue", 0), ("Revenue", "revenue", 1)], calc_rev_yoy),
    MetricSpec("ebitda_margin", "EBITDA Margin", "pct", "EBITDA_t / Revenue_t",
               [("EBITDA", "ebitda", 0), ("Revenue", "revenue", 0)], calc_ebitda_margin),
    MetricSpec("ebitda_margin_chg_bps", "EBITDA Margin YoY Change", "bps",
               "(EBITDA_t / Revenue_t - EBITDA_t_minus_1 / Revenue_t_minus_1) * 10000",
               [("EBITDA", "ebitda", 0), ("Revenue", "revenue", 0), ("EBITDA", "ebitda", 1), ("Revenue", "revenue", 1)],
               calc_ebitda_margin_chg_bps),
    MetricSpec("pat_margin", "Net Profit (PAT) Margin", "pct", "PAT_t / Revenue_t",
               [("PAT", "pat", 0), ("Revenue", "revenue", 0)], calc_pat_margin),
    MetricSpec("roe", "Return on Equity (ROE)", "pct", "PAT_t / Equity_t",
               [("PAT", "pat", 0), ("Equity", "total_equity", 0)], calc_roe),
    MetricSpec("dso", "DSO", "days", "(Receivables_t / Revenue_t) * 365",
               [("Receivables", "trade_receivables", 0), ("Revenue", "revenue", 0)], calc_dso),
    MetricSpec("receivables_yoy", "Receivables Growth YoY", "pct",
               "(Receivables_t - Receivables_t_minus_1) / Receivables_t_minus_1",
               [("Receivables", "trade_receivables", 0), ("Receivables", "trade_receivables", 1)], calc_receivables_yoy),
    MetricSpec("cfo_to_pat", "Cash Conversion (CFO / PAT)", "multiple", "CFO_t / PAT_t",
               [("CFO", "cfo", 0), ("PAT", "pat", 0)], calc_cfo_to_pat),
    MetricSpec("cfo_yoy", "Operating Cash Flow YoY", "pct", "(CFO_t - CFO_t_minus_1) / CFO_t_minus_1",
               [("CFO", "cfo", 0), ("CFO", "cfo", 1)], calc_cfo_yoy),
    MetricSpec("fcf", "Free Cash Flow (FCF)", "currency", "CFO_t - Capex_t",
               [("CFO", "cfo", 0), ("Capex", "capex", 0)], calc_fcf),
    MetricSpec("fcf_margin", "FCF Margin", "pct", "(CFO_t - Capex_t) / Revenue_t",
               [("CFO", "cfo", 0), ("Capex", "capex", 0), ("Revenue", "revenue", 0)], calc_fcf_margin),
    MetricSpec("net_debt_to_ebitda", "Net Debt / EBITDA", "multiple", "(Debt_t - Cash_t) / EBITDA_t",
               [("Debt", "gross_debt", 0), ("Cash", "cash_and_equivalents", 0), ("EBITDA", "ebitda", 0)],
               calc_net_debt_to_ebitda),
    MetricSpec("gross_debt_yoy", "Gross Debt Growth YoY", "pct", "(Debt_t - Debt_t_minus_1) / Debt_t_minus_1",
               [("Debt", "gross_debt", 0), ("Debt", "gross_debt", 1)], calc_gross_debt_yoy),
    MetricSpec("interest_cover", "Interest Coverage Ratio", "multiple", "EBIT_t / Interest_t",
               [("EBIT", "ebit", 0), ("Interest", "interest_expense", 0)], calc_interest_cover),
    MetricSpec("current_ratio", "Current Ratio", "multiple", "CA_t / CL_t",
               [("CA", "current_assets", 0), ("CL", "current_liabilities", 0)], calc_current_ratio),
]

SHORT_LABELS: dict[str, str] = {"dso": "DSO"}


def _primary_ref(item: LineItem | None) -> str:
    if item is None:
        return "—"
    ev = item.evidence
    return f"{ev.document_title} p.{ev.page_number}" if ev.page_number else ev.document_title


def _substitute(template: str, values: dict[str, D]) -> str:
    def repl(m: re.Match[str]) -> str:
        return format_plain_number(values.get(m.group(0)))

    names = sorted(values, key=len, reverse=True)
    pattern = re.compile("|".join(rf"\b{re.escape(n)}\b" for n in names))
    return pattern.sub(repl, template)


def _row(
    spec_key: str, label: str, unit: MetricUnit, period: str, template: str, substituted: str,
    refs: list[MetricInputRef], outcome: MetricOutcome, currency: CurrencyUnit, primary: str,
) -> MetricLedgerRow:
    return MetricLedgerRow(
        metric_id=f"met_{spec_key}_{period.lower()}",
        metric_key=spec_key,
        metric_label=label,
        fiscal_period=period,
        formula_template=template,
        formula_substituted=substituted,
        inputs=refs,
        value=outcome.value,
        formatted_value=format_outcome(spec_key, unit, outcome, currency),
        unit=unit,
        status=outcome.status,  # type: ignore[arg-type]
        status_note=outcome.note,
        primary_source_ref=primary,
    )


def annual_periods(line_items: list[LineItem]) -> list[str]:
    return sorted({li.fiscal_period for li in line_items}, key=line_items_period_order)


def compute_all_metrics(line_items: list[LineItem]) -> list[MetricLedgerRow]:
    """Build the full calculation ledger: every metric for every period that has the periods it needs."""
    items = [li for li in line_items if li.field_key != "segment_revenue"]
    if not items:
        return []
    currency = items[0].unit
    lookup = {(li.fiscal_period, li.field_key): li for li in items}
    periods = annual_periods(items)
    ledger: list[MetricLedgerRow] = []

    for idx, period in enumerate(periods):
        for spec in SPECS:
            max_offset = max(off for _, _, off in spec.inputs)
            if idx - max_offset < 0:
                continue  # YoY metrics need a prior period
            symbol_values: dict[str, D] = {}
            refs: list[MetricInputRef] = []
            args: list[D] = []
            first_item: LineItem | None = None
            for symbol, field_key, off in spec.inputs:
                p = periods[idx - off]
                li = lookup.get((p, field_key))
                val = li.value if li else None
                first_item = first_item or li
                tmpl_name = f"{symbol}_t" if off == 0 else f"{symbol}_t_minus_{off}"
                symbol_values[tmpl_name] = val
                args.append(val)
                if not any(r.symbol == f"{symbol}_{p}" for r in refs):
                    refs.append(MetricInputRef(
                        symbol=f"{symbol}_{p}",
                        line_item_id=li.item_id if li else item_id_for(p, field_key),
                        fiscal_period=p,
                        value=val,
                        evidence_id=li.evidence.evidence_id if li else "",
                    ))
            outcome = spec.calc(*args)
            label = f"{SHORT_LABELS.get(spec.key, spec.label)} {period}"
            ledger.append(_row(
                spec.key, label, spec.unit, period, spec.template,
                _substitute(spec.template, symbol_values), refs, outcome, currency, _primary_ref(first_item),
            ))

    # Revenue CAGR over the full available span, latest period only
    latest, first = periods[-1], periods[0]
    n = len(periods) - 1
    end_li, start_li = lookup.get((latest, "revenue")), lookup.get((first, "revenue"))
    end_v, start_v = (end_li.value if end_li else None), (start_li.value if start_li else None)
    outcome = calc_rev_cagr(end_v, start_v, n)
    refs = [
        MetricInputRef(symbol=f"Revenue_{p}", line_item_id=li.item_id if li else item_id_for(p, "revenue"),
                       fiscal_period=p, value=li.value if li else None,
                       evidence_id=li.evidence.evidence_id if li else "")
        for p, li in ((latest, end_li), (first, start_li))
    ]
    template = "(Revenue_t / Revenue_t_minus_n) ^ (1 / n) - 1"
    substituted = (
        f"({format_plain_number(end_v)} / {format_plain_number(start_v)}) ^ (1 / {n}) - 1"
    )
    ledger.append(_row(
        "rev_cagr", f"Revenue CAGR ({first}–{latest})", "pct", latest, template, substituted,
        refs, outcome, currency, _primary_ref(end_li),
    ))
    return ledger
