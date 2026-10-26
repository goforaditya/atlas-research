"""Cell parsing and unit normalisation to INR_CR / USD_M (Section 4, Stage 4). Code only."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from backend.schemas.common import CurrencyUnit


class UnitHeaderError(ValueError):
    """Raised when no unit header is detected or it does not match the target unit. The stage never guesses."""


@dataclass(frozen=True)
class UnitRule:
    target: CurrencyUnit
    multiplier: Decimal  # value_in_target = value * multiplier
    label: str


_INR_MARK = re.compile(r"(₹|\brs\.?|\binr\b|\brupees?\b)", re.I)
_USD_MARK = re.compile(r"(\$|\busd\b|\bus\$|\bdollars?\b)", re.I)


def detect_unit(header: str | None) -> UnitRule:
    """Map a printed unit header to its target unit and factor (the table in Stage 4)."""
    if not header or not header.strip():
        raise UnitHeaderError("No unit header detected")
    h = header.strip().lower()
    if h == "usd":  # XBRL facts are in plain units
        return UnitRule(CurrencyUnit.USD_M, Decimal(1) / Decimal(1_000_000), "XBRL USD units")
    is_inr = bool(_INR_MARK.search(h)) or "crore" in h or "lakh" in h or bool(re.search(r"\blacs?\b", h))
    if is_inr:
        if "crore" in h:
            return UnitRule(CurrencyUnit.INR_CR, Decimal(1), "₹ crore")
        if "lakh" in h or re.search(r"\blacs?\b", h):
            return UnitRule(CurrencyUnit.INR_CR, Decimal(1) / Decimal(100), "₹ lakh")
        if "million" in h or re.search(r"\bmn\b", h):
            return UnitRule(CurrencyUnit.INR_CR, Decimal(1) / Decimal(10), "₹ million")
        if "thousand" in h:
            return UnitRule(CurrencyUnit.INR_CR, Decimal(1) / Decimal(10_000), "₹ thousand")
        raise UnitHeaderError(f"Unrecognised INR unit header: {header!r}")
    if "thousand" in h:
        return UnitRule(CurrencyUnit.USD_M, Decimal(1) / Decimal(1000), "US$ thousands")
    if "million" in h:
        return UnitRule(CurrencyUnit.USD_M, Decimal(1), "US$ millions")
    if _USD_MARK.search(h):
        raise UnitHeaderError(f"USD header without a scale: {header!r}")
    raise UnitHeaderError(f"Unrecognised unit header: {header!r}")


def normalise_units(value: Decimal | None, unit_header: str | None, target_unit: CurrencyUnit | str) -> Decimal | None:
    """Convert a parsed value from its printed unit to the target unit. None passes through."""
    rule = detect_unit(unit_header)
    target = CurrencyUnit(target_unit)
    if rule.target is not target:
        raise UnitHeaderError(f"Header {unit_header!r} is {rule.target.value}, expected {target.value}")
    if value is None:
        return None
    return value * rule.multiplier


_BLANKS = {"", "-", "—", "–", "nil", "n/a", "na"}


def parse_cell_decimal(raw_cell_text: str | None) -> Decimal | None:
    """'1,234.5' -> 1234.5; '(1,234)' and '-1,234' -> -1234; '-', '—' and blank -> None."""
    if raw_cell_text is None:
        return None
    text = raw_cell_text.strip().replace("−", "-")
    text = re.sub(r"[₹$\s]", "", text)
    if text.lower() in _BLANKS:
        return None
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1]
    if text.startswith("-"):
        negative = not negative
        text = text[1:]
    text = text.replace(",", "")
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        raise ValueError(f"Not a numeric cell: {raw_cell_text!r}")
    try:
        val = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - guarded by the regex
        raise ValueError(f"Not a numeric cell: {raw_cell_text!r}") from exc
    return -val if negative else val
