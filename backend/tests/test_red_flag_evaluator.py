"""All 8 default rules firing, clear and insufficient_data, plus load-time rejection of unsafe YAML."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from backend.config import get_settings
from backend.engine.red_flag_evaluator import evaluate_red_flag_rules, load_rules, parse_rules
from backend.engine.safe_expr import MISSING, UnsafeExpressionError, compile_expression
from backend.schemas.financials import MetricLedgerRow

RULES = load_rules(get_settings().atlas_rules_path)
UNITS = {
    "dso": "days", "receivables_yoy": "pct", "rev_yoy": "pct", "cfo_to_pat": "multiple", "fcf": "currency",
    "gross_debt_yoy": "pct", "interest_cover": "multiple", "cfo_yoy": "pct", "current_ratio": "multiple",
    "ebitda_margin": "pct", "ebitda_margin_chg_bps": "bps",
}
PERIODS = ["FY24", "FY25", "FY26"]


def metric(key, period, value):
    from backend.engine.formatters import format_metric_value

    val = None if value is None else D(str(value))
    return MetricLedgerRow(
        metric_id=f"met_{key}_{period.lower()}", metric_key=key, metric_label=f"{key} {period}",
        fiscal_period=period, formula_template="x", formula_substituted="x", inputs=[], value=val,
        formatted_value=format_metric_value(val, UNITS[key], metric_key=key),
        unit=UNITS[key], status="computed" if val is not None else "missing_input", primary_source_ref="AR",
    )


def ledger(**series):
    """series: key=[FY24, FY25, FY26] values (None allowed)."""
    rows = []
    for key, values in series.items():
        for period, v in zip(PERIODS, values):
            rows.append(metric(key, period, v))
    return rows


def run(rows, items=()):
    return {r.rule_id: r for r in evaluate_red_flag_rules(rows, list(items), RULES)}


def test_yaml_has_eight_rules():
    assert [r.rule_id for r in RULES] == [
        "RF_01_RECEIVABLES_STRETCH", "RF_02_LOW_CASH_CONVERSION", "RF_03_NEGATIVE_FCF",
        "RF_04_DEBT_OUTGROWING_REV", "RF_05_WEAK_INTEREST_COVERAGE", "RF_06_PROFIT_CASH_DIVERGENCE",
        "RF_07_LIQUIDITY_SQUEEZE", "RF_08_MARGIN_COMPRESSION",
    ]


# rule id -> (firing ledger, clear ledger, insufficient ledger, line items needed as (key, value) for FY26)
CASES = {
    "RF_01_RECEIVABLES_STRETCH": (
        dict(dso=[58.7356, 70, 84.3044], receivables_yoy=[0, 0.44, 0.44], rev_yoy=[0, 0.18, 0.176]),
        dict(dso=[60, 62, 65], receivables_yoy=[0, 0.1, 0.12], rev_yoy=[0, 0.1, 0.1]),
        dict(dso=[None, 62, 65], receivables_yoy=[0, 0.1, None], rev_yoy=[0, 0.1, 0.1]),
    ),
    "RF_02_LOW_CASH_CONVERSION": (
        dict(cfo_to_pat=[1.1, 0.9, 0.67]), dict(cfo_to_pat=[1.1, 0.9, 0.95]), dict(cfo_to_pat=[1.1, 0.9, None]),
    ),
    "RF_03_NEGATIVE_FCF": (dict(fcf=[10, 122, -12]), dict(fcf=[10, 20, 122]), dict(fcf=[10, 20, None])),
    "RF_04_DEBT_OUTGROWING_REV": (
        dict(gross_debt_yoy=[0, 0, 0.677], rev_yoy=[0, 0, 0.176]),
        dict(gross_debt_yoy=[0, 0, 0.2], rev_yoy=[0, 0, 0.176]),
        dict(gross_debt_yoy=[0, 0, None], rev_yoy=[0, 0, 0.176]),
    ),
    "RF_05_WEAK_INTEREST_COVERAGE": (
        dict(interest_cover=[12, 11.97, 9.42]), dict(interest_cover=[12, 10, 9.4]), dict(interest_cover=[12, 10, None]),
    ),
    "RF_06_PROFIT_CASH_DIVERGENCE": (
        dict(rev_yoy=[0, 0, 0.176], cfo_yoy=[0, 0, -0.244]),
        dict(rev_yoy=[0, 0, 0.176], cfo_yoy=[0, 0, 0.05]),
        dict(rev_yoy=[0, 0, 0.176], cfo_yoy=[0, 0, None]),
    ),
    "RF_07_LIQUIDITY_SQUEEZE": (
        dict(current_ratio=[1.4, 1.2, 1.05]), dict(current_ratio=[1.4, 1.2, 1.32]), dict(current_ratio=[1.4, 1.2, None]),
    ),
    "RF_08_MARGIN_COMPRESSION": (
        dict(ebitda_margin=[0.16, 0.16, 0.13], ebitda_margin_chg_bps=[0, 0, -300]),
        dict(ebitda_margin=[0.16, 0.16, 0.15], ebitda_margin_chg_bps=[0, 0, -100]),
        dict(ebitda_margin=[0.16, 0.16, 0.15], ebitda_margin_chg_bps=[0, 0, None]),
    ),
}


def _items(item_factory, rule_id, pat=296):
    if rule_id == "RF_02_LOW_CASH_CONVERSION":
        return [item_factory("pat", "FY26", pat)]
    if rule_id == "RF_03_NEGATIVE_FCF":
        return [item_factory("cfo", "FY26", 198), item_factory("capex", "FY26", 210)]
    return []


@pytest.mark.parametrize("rule_id", list(CASES))
def test_rule_fires(rule_id, item_factory):
    res = run(ledger(**CASES[rule_id][0]), _items(item_factory, rule_id))[rule_id]
    assert res.status == "fired" and res.fired
    assert "{" not in res.explanation
    assert res.supporting_metric_ids


@pytest.mark.parametrize("rule_id", list(CASES))
def test_rule_clear(rule_id, item_factory):
    res = run(ledger(**CASES[rule_id][1]), _items(item_factory, rule_id))[rule_id]
    assert res.status == "clear" and not res.fired


@pytest.mark.parametrize("rule_id", list(CASES))
def test_rule_insufficient_data(rule_id, item_factory):
    res = run(ledger(**CASES[rule_id][2]), _items(item_factory, rule_id))[rule_id]
    assert res.status == "insufficient_data" and not res.fired
    assert res.missing_inputs
    assert res.explanation == f"Not evaluated: {', '.join(res.missing_inputs)} unavailable."


def test_explanations_use_formatted_values(item_factory):
    res = run(ledger(**CASES["RF_01_RECEIVABLES_STRETCH"][0]))["RF_01_RECEIVABLES_STRETCH"]
    assert res.explanation == (
        "DSO rose from 58.7 d to 84.3 d over two years; receivables grew 44.0% vs revenue growth of 17.6%."
    )
    fcf = run(ledger(**CASES["RF_03_NEGATIVE_FCF"][0]), _items(item_factory, "RF_03_NEGATIVE_FCF"))["RF_03_NEGATIVE_FCF"]
    assert fcf.explanation == "CFO ₹198 cr minus capex ₹210 cr resulted in negative free cash flow of -₹12 cr."


def test_or_with_one_true_side_still_fires():
    # DSO stretch alone decides RF_01 even when receivables growth is missing
    rows = ledger(dso=[50, 60, 80], receivables_yoy=[0, 0, None], rev_yoy=[0, 0, 0.1])
    res = run(rows)["RF_01_RECEIVABLES_STRETCH"]
    assert res.status == "fired"
    assert res.missing_inputs == ["receivables_yoy_t"]


def test_net_loss_makes_cash_conversion_insufficient(item_factory):
    rows = ledger(cfo_to_pat=[None, None, None])
    res = run(rows, [item_factory("pat", "FY26", -80)])["RF_02_LOW_CASH_CONVERSION"]
    assert res.status == "insufficient_data"


@pytest.mark.parametrize("expr", [
    "__import__('os').system('x')", "dso_t.real > 1", "abs(dso_t) > 1", "dso_t * 2 > 1",
    "[1][0] > 0", "dso_t if 1 else 2", "lambda: 1", "dso_t > 'abc'", "dso_t is None",
])
def test_unsafe_expressions_rejected_at_load(expr):
    with pytest.raises(UnsafeExpressionError):
        parse_rules({"rules": [{
            "rule_id": "X", "rule_name": "x", "severity": "low", "threshold_desc": "x",
            "expression": expr, "template_fired": "", "template_clear": "",
        }]})


def test_safe_expr_semantics():
    e = compile_expression("-a + 1 >= 0.5 and not (b == 2)")
    assert e.evaluate({"a": D("0.5"), "b": D(3)}) is True
    assert e.evaluate({"a": None, "b": D(3)}) is MISSING
    assert compile_expression("a < '0.80'").evaluate({"a": D("0.79")}) is True
