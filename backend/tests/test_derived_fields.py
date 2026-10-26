"""Derived line items, None propagation and unit normalisation."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from backend.engine.derived_fields import compute_derived_fields
from backend.engine.unit_normaliser import UnitHeaderError, detect_unit, normalise_units, parse_cell_decimal
from backend.schemas.common import CurrencyUnit


def _by_key(items):
    return {(i.fiscal_period, i.field_key): i for i in items}


def test_ind_as_derivations(item_factory):
    raw = [
        item_factory("pbt", "FY26", 400), item_factory("finance_cost", "FY26", 52),
        item_factory("other_income", "FY26", 30), item_factory("d_and_a", "FY26", 81),
        item_factory("borrowings_current", "FY26", 200), item_factory("borrowings_noncurrent", "FY26", 320),
        item_factory("purchase_of_ppe", "FY26", -150), item_factory("purchase_of_intangibles", "FY26", -60),
    ]
    derived, gaps = compute_derived_fields(raw)
    d = _by_key(derived)
    assert gaps == []
    assert d[("FY26", "interest_expense")].value == D(52)
    assert d[("FY26", "ebit")].value == D(422)
    assert d[("FY26", "ebit")].reported_label == "PBT + Finance cost - Other income"
    assert d[("FY26", "ebitda")].value == D(503)
    assert d[("FY26", "ebitda")].derived_from_item_ids == ["li_fy26_ebit", "li_fy26_d_and_a"]
    assert d[("FY26", "gross_debt")].value == D(520)
    assert d[("FY26", "capex")].value == D(210)  # stored positive
    assert d[("FY26", "capex")].formatted_value == "₹210 cr"
    assert all(i.is_derived for i in derived)
    assert d[("FY26", "ebit")].evidence.evidence_id == "ev_fy26_pbt"


def test_us_gaap_ebit_is_operating_income(item_factory):
    raw = [
        item_factory("operating_income", "FY2024", 1197, CurrencyUnit.USD_M),
        item_factory("d_and_a", "FY2024", 300, CurrencyUnit.USD_M),
    ]
    d = _by_key(compute_derived_fields(raw)[0])
    assert d[("FY2024", "ebit")].value == D(1197)
    assert d[("FY2024", "ebitda")].value == D(1497)
    assert d[("FY2024", "ebitda")].formatted_value == "$1,497 M"


def test_none_propagates_with_gap(item_factory):
    raw = [
        item_factory("pbt", "FY25", 100), item_factory("finance_cost", "FY25", 10),
        item_factory("other_income", "FY25", None), item_factory("d_and_a", "FY25", 5),
        item_factory("borrowings_current", "FY25", 50),
    ]
    derived, gaps = compute_derived_fields(raw)
    d = _by_key(derived)
    assert d[("FY25", "ebit")].value is None
    assert d[("FY25", "ebit")].formatted_value == "—"
    assert d[("FY25", "ebitda")].value is None  # depends on ebit
    assert d[("FY25", "gross_debt")].value is None
    assert "FY25 ebit unavailable: missing other_income" in gaps
    assert "FY25 ebitda unavailable: missing ebit" in gaps
    assert "FY25 gross_debt unavailable: missing borrowings_noncurrent" in gaps
    assert ("FY25", "capex") not in d
    assert any(g.startswith("FY25 capex not derived") for g in gaps)


@pytest.mark.parametrize("header, raw, target, expected", [
    ("₹ in crore", "3,810.00", "INR_CR", D("3810.00")),
    ("Rs. crore", "100", "INR_CR", D(100)),
    ("₹ in lakh", "1,23,456", "INR_CR", D("1234.56")),
    ("Rs. lakhs", "500", "INR_CR", D(5)),
    ("₹ in million", "17,695.5", "INR_CR", D("1769.55")),
    ("INR million", "22,742.7", "INR_CR", D("2274.27")),
    ("₹ in thousand", "12,345,678", "INR_CR", D("1234.5678")),
    ("in thousands", "24,121,000", "USD_M", D(24121)),
    ("(In millions, except per share data)", "24,121", "USD_M", D(24121)),
    ("USD", "24121000000", "USD_M", D(24121)),
])
def test_unit_conversion(header, raw, target, expected):
    assert normalise_units(parse_cell_decimal(raw), header, target) == expected


def test_unit_header_required_and_must_match():
    with pytest.raises(UnitHeaderError):
        detect_unit("")
    with pytest.raises(UnitHeaderError):
        detect_unit("Particulars")
    with pytest.raises(UnitHeaderError):
        normalise_units(D(1), "₹ in crore", "USD_M")


@pytest.mark.parametrize("raw, expected", [
    ("1,234.5", D("1234.5")), ("(1,234)", D(-1234)), ("-1,234", D(-1234)), ("−56", D(-56)),
    ("-", None), ("—", None), ("", None), ("  ", None), ("₹ 3,810", D(3810)),
])
def test_parse_cell_decimal(raw, expected):
    assert parse_cell_decimal(raw) == expected


def test_parse_cell_rejects_text():
    with pytest.raises(ValueError):
        parse_cell_decimal("Note 12")
