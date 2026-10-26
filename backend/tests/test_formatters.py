"""Every formatted_value style in Section 5.2, including signs, zero and grouping."""
from __future__ import annotations

from decimal import Decimal as D

import pytest

from backend.engine.formatters import (
    format_bps,
    format_currency,
    format_days,
    format_metric_value,
    format_multiple,
    format_pct,
    format_plain_number,
)
from backend.schemas.common import CurrencyUnit


@pytest.mark.parametrize("value, expected", [
    (D("0.175925"), "17.6%"), (D("-0.06939"), "-6.9%"), (D(0), "0.0%"), (D("-0.0004"), "0.0%"),
    (D("0.00049"), "0.0%"), (D("0.00050"), "0.1%"), (D("12.3456"), "1,234.6%"), (None, "—"),
])
def test_pct(value, expected):
    assert format_pct(value) == expected


@pytest.mark.parametrize("value, expected", [
    (D(-100), "-100 bps"), (D(750), "+750 bps"), (D(0), "0 bps"), (D("-0.4"), "0 bps"),
    (D("1250.5"), "+1,251 bps"), (None, "—"),
])
def test_bps(value, expected):
    assert format_bps(value) == expected


@pytest.mark.parametrize("value, expected", [
    (D("84.30446"), "84.3 d"), (D("58.75"), "58.8 d"), (D(0), "0.0 d"), (D("-3.21"), "-3.2 d"), (None, "—"),
])
def test_days(value, expected):
    assert format_days(value) == expected


@pytest.mark.parametrize("value, places, expected", [
    (D("0.66891"), 2, "0.67x"), (D("-0.4054"), 2, "-0.41x"), (D("9.42307"), 1, "9.4x"),
    (D("11.9736"), 1, "12.0x"), (D("1.32"), 2, "1.32x"), (D(0), 2, "0.00x"), (None, 2, "—"),
])
def test_multiple(value, places, expected):
    assert format_multiple(value, places) == expected


@pytest.mark.parametrize("value, unit, expected", [
    (D(3810), CurrencyUnit.INR_CR, "₹3,810 cr"), (D(-12), CurrencyUnit.INR_CR, "-₹12 cr"),
    (D("1769.55"), CurrencyUnit.INR_CR, "₹1,770 cr"), (D("0.4"), CurrencyUnit.INR_CR, "₹0 cr"),
    (D("-0.4"), CurrencyUnit.INR_CR, "₹0 cr"), (D("-12.5"), CurrencyUnit.INR_CR, "-₹13 cr"),
    (D(24121), CurrencyUnit.USD_M, "$24,121 M"), (D(-45), CurrencyUnit.USD_M, "-$45 M"),
    (D(1234567), CurrencyUnit.USD_M, "$1,234,567 M"), (None, CurrencyUnit.USD_M, "—"),
])
def test_currency_and_line_items(value, unit, expected):
    assert format_currency(value, unit) == expected


def test_interest_cover_uses_one_decimal():
    assert format_metric_value(D("9.42307"), "multiple", metric_key="interest_cover") == "9.4x"
    assert format_metric_value(D("9.42307"), "multiple", metric_key="cfo_to_pat") == "9.42x"
    assert format_metric_value(D(-12), "currency", currency="INR_CR") == "-₹12 cr"


@pytest.mark.parametrize("value, expected", [
    (D("3810.00"), "3,810"), (D("1769.55"), "1,769.55"), (D(-12), "-12"), (D("0.150"), "0.15"),
    (D(0), "0"), (None, "—"),
])
def test_plain_number(value, expected):
    assert format_plain_number(value) == expected
