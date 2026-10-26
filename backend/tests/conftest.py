"""Shared pytest fixtures: sample evidence, line items and mock LLM responses."""
from __future__ import annotations

from decimal import Decimal

import pytest

from backend.schemas import CurrencyUnit, EvidenceCitation, LineItem, SourceTier


def make_evidence(evidence_id: str = "ev_test", page: int | None = 112) -> EvidenceCitation:
    return EvidenceCitation(
        evidence_id=evidence_id,
        document_id="doc_test_ar",
        document_title="AR FY26",
        source_url="https://example.invalid/ar_fy26.pdf",
        source_tier=SourceTier.T1,
        trust_weight=1.0,
        page_number=page,
        verbatim_snippet="Revenue from operations 3,810.00",
        extracted_value_raw="3,810.00",
        source_unit="₹ in crore",
    )


def make_item(
    field_key: str,
    period: str,
    value: str | Decimal | None,
    unit: CurrencyUnit = CurrencyUnit.INR_CR,
) -> LineItem:
    from backend.engine.formatters import format_currency

    val = None if value is None else Decimal(str(value))
    return LineItem(
        item_id=f"li_{period.lower()}_{field_key}",
        fiscal_period=period,
        field_key=field_key,
        reported_label=field_key,
        value=val,
        formatted_value=format_currency(val, unit),
        unit=unit,
        evidence=make_evidence(f"ev_{period.lower()}_{field_key}"),
    )


@pytest.fixture
def evidence() -> EvidenceCitation:
    return make_evidence()


@pytest.fixture
def item_factory():
    return make_item
