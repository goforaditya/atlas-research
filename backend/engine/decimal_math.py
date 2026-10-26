"""Decimal helpers shared by every deterministic engine.

The global context is set exactly once here: prec = 28, ROUND_HALF_UP. Values stay unrounded;
quantising happens only in formatters.py when building formatted_value strings.
"""
from __future__ import annotations

import decimal
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

decimal.getcontext().prec = 28
decimal.getcontext().rounding = ROUND_HALF_UP

ZERO = Decimal(0)
ONE = Decimal(1)
HUNDRED = Decimal(100)
TEN_THOUSAND = Decimal(10000)
DAYS_IN_YEAR = Decimal(365)

DivStatus = Literal["computed", "nm_zero_div", "nm_negative_base", "missing_input"]


def to_decimal(x: object) -> Decimal | None:
    """Convert int / str / Decimal to Decimal. Floats go through str() so 0.15 stays 0.15."""
    if x is None:
        return None
    if isinstance(x, Decimal):
        return x
    if isinstance(x, bool):
        raise TypeError("bool is not a number")
    if isinstance(x, float):
        return Decimal(str(x))
    return Decimal(x)


def safe_div(
    num: Decimal | None, den: Decimal | None, *, negative_base_nm: bool = True
) -> tuple[Decimal | None, DivStatus]:
    """num / den with the global edge-case rules of Section 5.1."""
    if num is None or den is None:
        return None, "missing_input"
    if den == 0:
        return None, "nm_zero_div"
    if negative_base_nm and den < 0:
        return None, "nm_negative_base"
    return num / den, "computed"


def growth(cur: Decimal | None, prev: Decimal | None) -> tuple[Decimal | None, DivStatus]:
    """(cur - prev) / prev; zero base -> nm_zero_div, negative base -> nm_negative_base."""
    if cur is None or prev is None:
        return None, "missing_input"
    return safe_div(cur - prev, prev)


def cagr(end: Decimal | None, start: Decimal | None, n: int) -> tuple[Decimal | None, DivStatus]:
    """(end / start) ** (1/n) - 1, root computed as exp(ln(ratio) / n) in Decimal."""
    if end is None or start is None or n < 2:
        return None, "missing_input"
    if start <= 0 or end <= 0:
        return None, "nm_negative_base"
    ratio = end / start
    return (ratio.ln() / Decimal(n)).exp() - ONE, "computed"


def median(values: Iterable[Decimal | None]) -> Decimal | None:
    """Exact statistical median of the non-None values; None when there are none."""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / Decimal(2)


def quantize(value: Decimal, places: int) -> Decimal:
    """ROUND_HALF_UP to a fixed number of decimal places."""
    exp = Decimal(1).scaleb(-places) if places > 0 else Decimal(1)
    return value.quantize(exp, rounding=ROUND_HALF_UP)
