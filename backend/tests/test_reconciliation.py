"""Balance-sheet identity, operating profit and net profit cross-foot checks."""
from __future__ import annotations

from decimal import Decimal as D

from backend.engine.reconciliation import (
    check_balance_sheet,
    check_net_profit,
    check_operating_profit,
    run_reconciliation_suite,
)


def test_balance_sheet_passes_within_one_percent():
    c = check_balance_sheet("FY26", {"total_assets": D(1000), "total_liabilities": D(600), "total_equity": D(395)})
    assert c.status == "passed" and c.passed
    assert c.discrepancy == D(5)
    assert c.failure_reason is None


def test_balance_sheet_fails_at_two_and_a_half_percent():
    c = check_balance_sheet("FY26", {"total_assets": D(1000), "total_liabilities": D(600), "total_equity": D(375)})
    assert c.status == "failed" and not c.passed
    assert c.discrepancy == D(25)
    assert "exceeds 1% of Total assets" in c.failure_reason


def test_balance_sheet_skipped_when_only_total_equity_and_liabilities_printed():
    c = check_balance_sheet("FY26", {"total_assets": D(1000), "total_equity": D(400)})
    assert c.status == "skipped" and not c.passed
    assert "Total equity and liabilities" in c.skip_reason


def test_operating_profit_cross_foot():
    vals = {"d_and_a": D(81), "ebit": D(490), "revenue": D(3810)}
    ok = check_operating_profit("FY26", vals, printed_ebitda=D(575))
    assert ok.status == "passed"  # |575 - 81 - 490| = 4 <= 38.1
    bad = check_operating_profit("FY26", vals, printed_ebitda=D(700))
    assert bad.status == "failed" and bad.failure_reason
    skipped = check_operating_profit("FY26", vals, printed_ebitda=None)
    assert skipped.status == "skipped"


def test_net_profit_cross_foot():
    ok = check_net_profit("FY26", {"pbt": D(400), "tax_expense": D(104), "pat_before_nci": D(300)})
    assert ok.status == "passed"  # |296 - 300| = 4 <= 8
    bad = check_net_profit("FY26", {"pbt": D(400), "tax_expense": D(104), "pat_before_nci": D(286)})
    assert bad.status == "failed"  # 10 > 8 (2.5%)
    neg = check_net_profit("FY25", {"pbt": D(-600), "tax_expense": D(10), "pat_before_nci": D(-612)})
    assert neg.status == "passed"  # tolerance uses |PBT|
    missing = check_net_profit("FY25", {"pbt": D(1)})
    assert missing.status == "skipped" and "tax_expense" in missing.skip_reason


def test_suite_runs_three_checks_per_period(item_factory):
    items = [
        item_factory("total_assets", "FY25", 900), item_factory("total_liabilities", "FY25", 500),
        item_factory("total_equity", "FY25", 400), item_factory("total_assets", "FY26", 1000),
    ]
    checks = run_reconciliation_suite(items)
    assert [c.check_id for c in checks] == [
        "rc_bs_fy25", "rc_op_fy25", "rc_np_fy25", "rc_bs_fy26", "rc_op_fy26", "rc_np_fy26",
    ]
    assert checks[0].status == "passed"
    assert checks[3].status == "skipped"
