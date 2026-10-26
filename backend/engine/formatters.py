"""The only place formatted_value strings are produced (Section 5.2).

All rounding is ROUND_HALF_UP on the unrounded value. The frontend never re-formats these strings.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional

from backend.schemas.common import CurrencyUnit

from .decimal_math import HUNDRED, quantize

MISSING = "—"
MetricUnit = Literal["pct", "multiple", "days", "bps", "currency"]

# Default N/M labels (Section 5.1); individual metrics may override them.
NM_ZERO_LABEL = "N/M (Zero Base)"
NM_NEG_LABEL = "N/M (Neg Base)"


def _strip_neg_zero(q: Decimal) -> Decimal:
    return q.copy_abs() if q == 0 else q


def _group(q: Decimal, places: int) -> str:
    """Western thousands grouping of an already-quantised Decimal (sign handled by callers)."""
    return f"{q.copy_abs():,.{places}f}"


def format_pct(value: Optional[Decimal]) -> str:
    """Fraction -> '17.6%'. Minus sign only when the rounded value is negative."""
    if value is None:
        return MISSING
    q = _strip_neg_zero(quantize(value * HUNDRED, 1))
    return f"{'-' if q < 0 else ''}{_group(q, 1)}%"


def format_bps(value: Optional[Decimal]) -> str:
    """Integer, always signed except zero: '-100 bps', '+750 bps', '0 bps'."""
    if value is None:
        return MISSING
    q = _strip_neg_zero(quantize(value, 0))
    if q == 0:
        return "0 bps"
    return f"{'+' if q > 0 else '-'}{_group(q, 0)} bps"


def format_days(value: Optional[Decimal]) -> str:
    if value is None:
        return MISSING
    q = _strip_neg_zero(quantize(value, 1))
    return f"{'-' if q < 0 else ''}{_group(q, 1)} d"


def format_multiple(value: Optional[Decimal], places: int = 2) -> str:
    if value is None:
        return MISSING
    q = _strip_neg_zero(quantize(value, places))
    return f"{'-' if q < 0 else ''}{_group(q, places)}x"


def format_currency(value: Optional[Decimal], unit: CurrencyUnit | str) -> str:
    """Whole crore / whole millions, sign before symbol: '₹3,810 cr', '-₹12 cr', '$24,121 M'."""
    if value is None:
        return MISSING
    unit = CurrencyUnit(unit)
    q = _strip_neg_zero(quantize(value, 0))
    sign = "-" if q < 0 else ""
    if unit is CurrencyUnit.INR_CR:
        return f"{sign}₹{_group(q, 0)} cr"
    return f"{sign}${_group(q, 0)} M"


# metric_key -> decimal places for 'multiple' metrics (Section 5.2)
MULTIPLE_PLACES: dict[str, int] = {"interest_cover": 1}


def format_metric_value(
    value: Optional[Decimal],
    unit: MetricUnit,
    *,
    metric_key: str = "",
    currency: CurrencyUnit | str = CurrencyUnit.INR_CR,
) -> str:
    if unit == "pct":
        return format_pct(value)
    if unit == "bps":
        return format_bps(value)
    if unit == "days":
        return format_days(value)
    if unit == "multiple":
        return format_multiple(value, MULTIPLE_PLACES.get(metric_key, 2))
    return format_currency(value, currency)


def format_plain_number(value: Optional[Decimal]) -> str:
    """Unrounded input as written in formula_substituted: grouped, trailing zeros dropped ('3,810', '1,769.55')."""
    if value is None:
        return MISSING
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    neg = text.startswith("-")
    digits = text.lstrip("-")
    whole, _, frac = digits.partition(".")
    grouped = f"{int(whole):,}"
    out = grouped + (f".{frac}" if frac else "")
    return f"-{out}" if neg and out.strip("0.,") else out
