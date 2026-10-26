"""Deterministic reconciliation suite (Section 4, Stage 4)."""
from __future__ import annotations

from decimal import Decimal

from backend.schemas.financials import LineItem, ReconciliationCheck

from .derived_fields import line_items_period_order
from .formatters import format_plain_number

BS_TOLERANCE = Decimal("0.01")
OP_TOLERANCE = Decimal("0.01")
NP_TOLERANCE = Decimal("0.02")


def _skipped(check_id: str, period: str, name: str, formula: str, tol: Decimal, reason: str) -> ReconciliationCheck:
    return ReconciliationCheck(
        check_id=check_id, fiscal_period=period, check_name=name, formula_desc=formula,
        lhs_value=None, rhs_value=None, discrepancy=None, tolerance_pct=tol,
        status="skipped", passed=False, skip_reason=reason,
    )


def _judge(
    check_id: str, period: str, name: str, formula: str, tol: Decimal,
    lhs: Decimal, rhs: Decimal, base: Decimal, base_label: str,
) -> ReconciliationCheck:
    discrepancy = lhs - rhs
    limit = tol * abs(base)
    ok = abs(discrepancy) <= limit
    reason = None
    if not ok:
        reason = (
            f"|{format_plain_number(lhs)} - {format_plain_number(rhs)}| = {format_plain_number(abs(discrepancy))} "
            f"exceeds {format_plain_number(tol * 100)}% of {base_label} ({format_plain_number(limit)})"
        )
    return ReconciliationCheck(
        check_id=check_id, fiscal_period=period, check_name=name, formula_desc=formula,
        lhs_value=lhs, rhs_value=rhs, discrepancy=discrepancy, tolerance_pct=tol,
        status="passed" if ok else "failed", passed=ok, failure_reason=reason,
    )


def check_balance_sheet(period: str, values: dict[str, Decimal | None]) -> ReconciliationCheck:
    """|Assets - (Liabilities + Equity)| <= 1% of Assets. Total liabilities must be printed, never back-solved."""
    cid, name = f"rc_bs_{period.lower()}", "Balance Sheet Identity: Assets = Liabilities + Equity"
    formula = "|Total assets - (Total liabilities + Total equity incl. NCI)| <= 1% x Total assets"
    assets, liabs, equity = values.get("total_assets"), values.get("total_liabilities"), values.get("total_equity")
    if liabs is None:
        return _skipped(cid, period, name, formula, BS_TOLERANCE,
                        "Filing prints only Total equity and liabilities; Total liabilities is not printed")
    if assets is None or equity is None:
        missing = [k for k, v in (("total_assets", assets), ("total_equity", equity)) if v is None]
        return _skipped(cid, period, name, formula, BS_TOLERANCE, f"Missing {', '.join(missing)}")
    return _judge(cid, period, name, formula, BS_TOLERANCE, assets, liabs + equity, assets, "Total assets")


def check_operating_profit(
    period: str, values: dict[str, Decimal | None], printed_ebitda: Decimal | None
) -> ReconciliationCheck:
    """|EBITDA_printed - D&A - EBIT| <= 1% of Revenue; only when the filing itself prints EBITDA."""
    cid, name = f"rc_op_{period.lower()}", "Operating Profit Cross-Foot: EBITDA - D&A = EBIT"
    formula = "|EBITDA (printed) - D&A - EBIT| <= 1% x Revenue"
    if printed_ebitda is None:
        return _skipped(cid, period, name, formula, OP_TOLERANCE,
                        "Filing does not print an EBITDA / operating profit line")
    dna, ebit, rev = values.get("d_and_a"), values.get("ebit"), values.get("revenue")
    missing = [k for k, v in (("d_and_a", dna), ("ebit", ebit), ("revenue", rev)) if v is None]
    if missing:
        return _skipped(cid, period, name, formula, OP_TOLERANCE, f"Missing {', '.join(missing)}")
    return _judge(cid, period, name, formula, OP_TOLERANCE, printed_ebitda - dna, ebit, rev, "Revenue")  # type: ignore[operator]


def check_net_profit(period: str, values: dict[str, Decimal | None]) -> ReconciliationCheck:
    """|PBT - Tax - PAT before NCI| <= 2% of |PBT|."""
    cid, name = f"rc_np_{period.lower()}", "Net Profit Cross-Foot: PBT - Tax = PAT"
    formula = "|PBT - Tax expense - PAT (before NCI)| <= 2% x |PBT|"
    pbt, tax, pat = values.get("pbt"), values.get("tax_expense"), values.get("pat_before_nci")
    missing = [k for k, v in (("pbt", pbt), ("tax_expense", tax), ("pat_before_nci", pat)) if v is None]
    if missing:
        return _skipped(cid, period, name, formula, NP_TOLERANCE, f"Missing {', '.join(missing)}")
    return _judge(cid, period, name, formula, NP_TOLERANCE, pbt - tax, pat, pbt, "|PBT|")  # type: ignore[operator]


def run_reconciliation_suite(
    line_items: list[LineItem], printed_ebitda: dict[str, Decimal] | None = None
) -> list[ReconciliationCheck]:
    """Run all three checks for every annual period present in line_items."""
    printed_ebitda = printed_ebitda or {}
    periods = sorted({li.fiscal_period for li in line_items}, key=line_items_period_order)
    checks: list[ReconciliationCheck] = []
    for period in periods:
        values = {li.field_key: li.value for li in line_items if li.fiscal_period == period and li.field_key != "segment_revenue"}
        checks.append(check_balance_sheet(period, values))
        checks.append(check_operating_profit(period, values, printed_ebitda.get(period)))
        checks.append(check_net_profit(period, values))
    return checks
