"""All 32 worked examples from Section 5 plus None / zero / negative cases for every metric (80 tests)."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from backend.engine import metrics_calculator as mc
from backend.schemas.common import CurrencyUnit

U = {
    "rev_yoy": "pct", "rev_cagr": "pct", "ebitda_margin": "pct", "ebitda_margin_chg_bps": "bps",
    "pat_margin": "pct", "roe": "pct", "dso": "days", "receivables_yoy": "pct", "cfo_to_pat": "multiple",
    "cfo_yoy": "pct", "fcf": "currency", "fcf_margin": "pct", "net_debt_to_ebitda": "multiple",
    "gross_debt_yoy": "pct", "interest_cover": "multiple", "current_ratio": "multiple",
}


def run(key, *args):
    return getattr(mc, f"calc_{key}")(*args)


def fmt(key, outcome):
    return mc.format_outcome(key, U[key], outcome, CurrencyUnit.INR_CR)


def cagr_value(end, start, n):
    return ((D(end) / D(start)).ln() / D(n)).exp() - 1


# (id, metric key, args, expected unrounded value, expected formatted, expected status)
WORKED = [
    ("rev_yoy_1", "rev_yoy", (D(3810), D(3240)), (D(3810) - D(3240)) / D(3240), "17.6%", "computed"),
    ("rev_yoy_2", "rev_yoy", (D(500), D(0)), None, "N/M (Zero Base)", "nm_zero_div"),
    ("rev_cagr_1", "rev_cagr", (D(3810), D(1420), 4), cagr_value(3810, 1420, 4), "28.0%", "computed"),
    ("rev_cagr_2", "rev_cagr", (D(1500), D(2000), 4), cagr_value(1500, 2000, 4), "-6.9%", "computed"),
    ("ebitda_margin_1", "ebitda_margin", (D(571), D(3810)), D(571) / D(3810), "15.0%", "computed"),
    ("ebitda_margin_2", "ebitda_margin", (D(-180), D(1200)), D("-0.15"), "-15.0%", "computed"),
    ("ebitda_chg_1", "ebitda_margin_chg_bps", (D(571), D(3810), D(518), D(3240)), D(-100), "-100 bps", "computed"),
    ("ebitda_chg_2", "ebitda_margin_chg_bps", (D(25), D(1000), D(-50), D(1000)), D(750), "+750 bps", "computed"),
    ("pat_margin_1", "pat_margin", (D(296), D(3810)), D(296) / D(3810), "7.8%", "computed"),
    ("pat_margin_2", "pat_margin", (D(-45), D(900)), D("-0.05"), "-5.0%", "computed"),
    ("roe_1", "roe", (D(296), D(1540)), D(296) / D(1540), "19.2%", "computed"),
    ("roe_2", "roe", (D(-120), D(-500)), None, "N/M (Neg Equity)", "nm_negative_base"),
    ("dso_1", "dso", (D(880), D(3810)), D(880) / D(3810) * 365, "84.3 d", "computed"),
    ("dso_2", "dso", (D(420), D(2610)), D(420) / D(2610) * 365, "58.7 d", "computed"),
    ("recv_yoy_1", "receivables_yoy", (D(880), D(610)), (D(880) - D(610)) / D(610), "44.3%", "computed"),
    ("recv_yoy_2", "receivables_yoy", (D(450), D(500)), D("-0.1"), "-10.0%", "computed"),
    ("cfo_to_pat_1", "cfo_to_pat", (D(198), D(296)), D(198) / D(296), "0.67x", "computed"),
    ("cfo_to_pat_2", "cfo_to_pat", (D(150), D(-80)), None, "N/M (Net Loss)", "nm_negative_base"),
    ("cfo_yoy_1", "cfo_yoy", (D(198), D(262)), (D(198) - D(262)) / D(262), "-24.4%", "computed"),
    ("cfo_yoy_2", "cfo_yoy", (D(120), D(-50)), None, "N/M (Neg Base)", "nm_negative_base"),
    ("fcf_1", "fcf", (D(198), D(210)), D(-12), "-₹12 cr", "computed"),
    ("fcf_2", "fcf", (D(262), D(140)), D(122), "₹122 cr", "computed"),
    ("fcf_margin_1", "fcf_margin", (D(198), D(210), D(3810)), D(-12) / D(3810), "-0.3%", "computed"),
    ("fcf_margin_2", "fcf_margin", (D(262), D(140), D(3240)), D(122) / D(3240), "3.8%", "computed"),
    ("nd_ebitda_1", "net_debt_to_ebitda", (D(520), D(430), D(571)), D(90) / D(571), "0.16x", "computed"),
    ("nd_ebitda_2", "net_debt_to_ebitda", (D(310), D(520), D(518)), D(-210) / D(518), "-0.41x", "computed"),
    ("debt_yoy_1", "gross_debt_yoy", (D(520), D(310)), D(210) / D(310), "67.7%", "computed"),
    ("debt_yoy_2", "gross_debt_yoy", (D(0), D(0)), D(0), "0.0%", "computed"),
    ("int_cover_1", "interest_cover", (D(490), D(52)), D(490) / D(52), "9.4x", "computed"),
    ("int_cover_2", "interest_cover", (D(455), D(38)), D(455) / D(38), "12.0x", "computed"),
    ("current_ratio_1", "current_ratio", (D(1980), D(1500)), D("1.32"), "1.32x", "computed"),
    ("current_ratio_2", "current_ratio", (D(1240), D(860)), D(1240) / D(860), "1.44x", "computed"),
]


@pytest.mark.parametrize("case_id, key, args, value, formatted, status", WORKED, ids=[w[0] for w in WORKED])
def test_worked_examples(case_id, key, args, value, formatted, status):
    out = run(key, *args)
    assert out.status == status
    assert out.value == value
    assert fmt(key, out) == formatted


# None input, zero denominator, negative denominator for every metric (48 cases).
EDGE = [
    # rev_yoy
    ("rev_yoy", (None, D(100)), "missing_input", "—"),
    ("rev_yoy", (D(100), D(0)), "nm_zero_div", "N/M (Zero Base)"),
    ("rev_yoy", (D(100), D(-50)), "nm_negative_base", "N/M (Neg Base)"),
    # rev_cagr (zero / negative start are not meaningful; < 3 periods gets its own label)
    ("rev_cagr", (D(100), None, 2), "missing_input", "—"),
    ("rev_cagr", (D(100), D(0), 2), "nm_negative_base", "N/M (Neg Base)"),
    ("rev_cagr", (D(100), D(-10), 2), "nm_negative_base", "N/M (Neg Base)"),
    # ebitda_margin
    ("ebitda_margin", (D(10), None), "missing_input", "—"),
    ("ebitda_margin", (D(10), D(0)), "nm_zero_div", "N/M (Zero Base)"),
    ("ebitda_margin", (D(10), D(-100)), "nm_negative_base", "N/M (Neg Base)"),
    # ebitda_margin_chg_bps: a margin that is not computable makes the change missing
    ("ebitda_margin_chg_bps", (None, D(100), D(10), D(100)), "missing_input", "—"),
    ("ebitda_margin_chg_bps", (D(10), D(0), D(10), D(100)), "missing_input", "—"),
    ("ebitda_margin_chg_bps", (D(10), D(100), D(10), D(-100)), "missing_input", "—"),
    # pat_margin
    ("pat_margin", (None, D(100)), "missing_input", "—"),
    ("pat_margin", (D(5), D(0)), "nm_zero_div", "N/M (Zero Base)"),
    ("pat_margin", (D(5), D(-100)), "nm_negative_base", "N/M (Neg Base)"),
    # roe
    ("roe", (D(5), None), "missing_input", "—"),
    ("roe", (D(5), D(0)), "nm_zero_div", "N/M (Zero Base)"),
    ("roe", (D(5), D(-100)), "nm_negative_base", "N/M (Neg Equity)"),
    # dso
    ("dso", (None, D(100)), "missing_input", "—"),
    ("dso", (D(10), D(0)), "nm_zero_div", "N/M (Zero Base)"),
    ("dso", (D(10), D(-100)), "nm_negative_base", "N/M (Neg Base)"),
    # receivables_yoy
    ("receivables_yoy", (D(10), None), "missing_input", "—"),
    ("receivables_yoy", (D(10), D(0)), "nm_zero_div", "N/M (Zero Base)"),
    ("receivables_yoy", (D(10), D(-5)), "nm_negative_base", "N/M (Neg Base)"),
    # cfo_to_pat
    ("cfo_to_pat", (None, D(10)), "missing_input", "—"),
    ("cfo_to_pat", (D(10), D(0)), "nm_zero_div", "N/M (Zero Base)"),
    ("cfo_to_pat", (D(10), D(-10)), "nm_negative_base", "N/M (Net Loss)"),
    # cfo_yoy
    ("cfo_yoy", (D(10), None), "missing_input", "—"),
    ("cfo_yoy", (D(10), D(0)), "nm_zero_div", "N/M (Zero Base)"),
    ("cfo_yoy", (D(10), D(-10)), "nm_negative_base", "N/M (Neg Base)"),
    # fcf has no denominator: zero and negative inputs compute normally
    ("fcf", (D(10), None), "missing_input", "—"),
    ("fcf", (D(0), D(0)), "computed", "₹0 cr"),
    ("fcf", (D(-30), D(15)), "computed", "-₹45 cr"),
    # fcf_margin
    ("fcf_margin", (D(10), D(5), None), "missing_input", "—"),
    ("fcf_margin", (D(10), D(5), D(0)), "nm_zero_div", "N/M (Zero Base)"),
    ("fcf_margin", (D(10), D(5), D(-100)), "nm_negative_base", "N/M (Neg Base)"),
    # net_debt_to_ebitda
    ("net_debt_to_ebitda", (D(10), None, D(10)), "missing_input", "—"),
    ("net_debt_to_ebitda", (D(10), D(5), D(0)), "nm_zero_div", "N/M (Zero Base)"),
    ("net_debt_to_ebitda", (D(10), D(5), D(-10)), "nm_negative_base", "N/M (Neg Base)"),
    # gross_debt_yoy
    ("gross_debt_yoy", (None, D(10)), "missing_input", "—"),
    ("gross_debt_yoy", (D(10), D(0)), "nm_zero_div", "N/M (Zero Base)"),
    ("gross_debt_yoy", (D(10), D(-10)), "nm_negative_base", "N/M (Neg Base)"),
    # interest_cover
    ("interest_cover", (D(10), None), "missing_input", "—"),
    ("interest_cover", (D(10), D(0)), "nm_zero_div", "N/M (No Int Exp)"),
    ("interest_cover", (D(10), D(-2)), "nm_negative_base", "N/M (Neg Base)"),
    # current_ratio
    ("current_ratio", (None, D(10)), "missing_input", "—"),
    ("current_ratio", (D(10), D(0)), "nm_zero_div", "N/M (Zero Base)"),
    ("current_ratio", (D(10), D(-10)), "nm_negative_base", "N/M (Neg Base)"),
]


@pytest.mark.parametrize("key, args, status, formatted", EDGE)
def test_edge_cases(key, args, status, formatted):
    out = run(key, *args)
    assert out.status == status
    if status != "computed":
        assert out.value is None
    assert fmt(key, out) == formatted


def test_cagr_short_span_label():
    out = mc.calc_rev_cagr(D(100), D(80), 1)
    assert out.status == "missing_input"
    assert fmt("rev_cagr", out) == "N/M (< 3 yrs)"


def test_bps_stored_as_integer():
    out = mc.calc_ebitda_margin_chg_bps(D(571), D(3810), D(518), D(3240))
    assert out.value == D(-100) and out.value.as_tuple().exponent == 0


def _ledger_items(make_item):
    data = {
        "FY24": dict(revenue=2610, trade_receivables=420, current_assets=1240, current_liabilities=860),
        "FY25": dict(revenue=3240, ebitda=518, trade_receivables=610, cfo=262, capex=140, gross_debt=310,
                     cash_and_equivalents=520, ebit=455, interest_expense=38),
        "FY26": dict(revenue=3810, ebitda=571, pat=296, total_equity=1540, trade_receivables=880, cfo=198,
                     capex=210, gross_debt=520, cash_and_equivalents=430, ebit=490, interest_expense=52,
                     current_assets=1980, current_liabilities=1500),
    }
    return [make_item(k, p, v) for p, row in data.items() for k, v in row.items()]


def test_ledger_rows_and_formula_substitution(item_factory):
    ledger = {r.metric_id: r for r in mc.compute_all_metrics(_ledger_items(item_factory))}
    dso = ledger["met_dso_fy26"]
    assert dso.metric_label == "DSO FY26"
    assert dso.formula_substituted == "(880 / 3,810) * 365"
    assert dso.formatted_value == "84.3 d"
    assert [i.symbol for i in dso.inputs] == ["Receivables_FY26", "Revenue_FY26"]
    assert dso.primary_source_ref == "AR FY26 p.112"
    assert ledger["met_rev_yoy_fy26"].formatted_value == "17.6%"
    assert ledger["met_ebitda_margin_chg_bps_fy26"].formatted_value == "-100 bps"
    assert ledger["met_fcf_fy26"].formatted_value == "-₹12 cr"
    assert ledger["met_net_debt_to_ebitda_fy26"].formatted_value == "0.16x"
    assert ledger["met_current_ratio_fy24"].formatted_value == "1.44x"
    cagr = ledger["met_rev_cagr_fy26"]
    assert cagr.metric_label == "Revenue CAGR (FY24–FY26)"
    assert cagr.status == "computed" and cagr.value == cagr_value(3810, 2610, 2)
    assert cagr.formatted_value == "20.8%"
    assert cagr.formula_substituted == "(3,810 / 2,610) ^ (1 / 2) - 1"
    assert "met_rev_yoy_fy24" not in ledger  # no prior period
    assert ledger["met_roe_fy25"].status == "missing_input"
