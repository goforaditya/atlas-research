"""Code-only derived line items (Section 4, Stage 4). The LLM never maps these keys.

| Key              | Formula                                                   |
| interest_expense | finance_cost                                              |
| ebit             | pbt + finance_cost - other_income (Ind AS); operating_income (US GAAP) |
| ebitda           | ebit + d_and_a                                            |
| gross_debt       | borrowings_current + borrowings_noncurrent                |
| capex            | purchase_of_ppe + purchase_of_intangibles (stored positive) |
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal, Optional

from backend.schemas.common import CurrencyUnit
from backend.schemas.financials import LineItem

from .formatters import format_currency

Gaap = Literal["ind_as", "us_gaap"]

FIELD_LABELS: dict[str, str] = {
    "finance_cost": "Finance cost",
    "pbt": "PBT",
    "other_income": "Other income",
    "operating_income": "Operating income",
    "ebit": "EBIT",
    "d_and_a": "D&A",
    "borrowings_current": "Current borrowings",
    "borrowings_noncurrent": "Non-current borrowings",
    "purchase_of_ppe": "Purchase of PP&E",
    "purchase_of_intangibles": "Purchase of intangibles",
}


def item_id_for(period: str, field_key: str) -> str:
    return f"li_{period.lower()}_{field_key}"


def _recipes(gaap: Gaap) -> list[tuple[str, list[tuple[str, int]]]]:
    """(derived key, [(input key, sign)]) in dependency order."""
    ebit = [("operating_income", 1)] if gaap == "us_gaap" else [("pbt", 1), ("finance_cost", 1), ("other_income", -1)]
    return [
        ("interest_expense", [("finance_cost", 1)]),
        ("ebit", ebit),
        ("ebitda", [("ebit", 1), ("d_and_a", 1)]),
        ("gross_debt", [("borrowings_current", 1), ("borrowings_noncurrent", 1)]),
        ("capex", [("purchase_of_ppe", 1), ("purchase_of_intangibles", 1)]),
    ]


def _label(parts: list[tuple[str, int]]) -> str:
    out = ""
    for i, (key, sign) in enumerate(parts):
        name = FIELD_LABELS.get(key, key)
        if i == 0:
            out = name
        else:
            out += f" {'+' if sign > 0 else '-'} {name}"
    return out


def compute_derived_fields(
    line_items: list[LineItem], gaap: Optional[Gaap] = None
) -> tuple[list[LineItem], list[str]]:
    """Return (derived line items, stated gaps). Any None input makes the derived value None with a gap.

    `capex` inputs are taken as absolute outflows: cash-flow statements print purchases as negatives,
    so their magnitude is used and capex is stored positive.
    """
    annual = [li for li in line_items if not li.is_derived and li.field_key != "segment_revenue"]
    by_key: dict[tuple[str, str], LineItem] = {(li.fiscal_period, li.field_key): li for li in annual}
    periods = sorted({li.fiscal_period for li in annual}, key=lambda p: line_items_period_order(p))
    derived: list[LineItem] = []
    gaps: list[str] = []
    for period in periods:
        unit = next(li.unit for li in annual if li.fiscal_period == period)
        use_gaap: Gaap = gaap or ("us_gaap" if unit is CurrencyUnit.USD_M else "ind_as")
        for key, parts in _recipes(use_gaap):
            inputs = [by_key.get((period, k)) for k, _ in parts]
            present = [li for li in inputs if li is not None]
            if not present:
                gaps.append(f"{period} {key} not derived: no inputs ({', '.join(k for k, _ in parts)}) in filing")
                continue
            missing = [k for (k, _), li in zip(parts, inputs) if li is None or li.value is None]
            value: Optional[Decimal] = None
            if missing:
                gaps.append(f"{period} {key} unavailable: missing {', '.join(missing)}")
            else:
                total = Decimal(0)
                for (k, sign), li in zip(parts, inputs):
                    v = li.value  # type: ignore[union-attr]
                    if key == "capex":
                        v = abs(v)
                    total += sign * v
                value = total
            item = LineItem(
                item_id=item_id_for(period, key),
                fiscal_period=period,
                field_key=key,
                reported_label=_label(parts),
                value=value,
                formatted_value=format_currency(value, unit),
                unit=unit,
                is_restated=any(li.is_restated for li in present),
                is_derived=True,
                derived_from_item_ids=[li.item_id for li in present],
                evidence=present[0].evidence,
            )
            derived.append(item)
            by_key[(period, key)] = item
    return derived, gaps


def line_items_period_order(period: str) -> tuple[int, int]:
    """Sort key for fiscal labels: 'FY24' < 'FY25'; 'FY2024' < 'FY2025'; 'Q1FY27' sorts within FY27."""
    m = re.search(r"(?:Q([1-4]))?FY(\d{2,4})", period.upper())
    if not m:
        return (0, 0)
    year = int(m.group(2))
    if year < 100:
        year += 2000
    return (year, int(m.group(1) or 0))
