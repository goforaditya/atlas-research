from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .common import CurrencyUnit, EvidenceCitation

# Raw keys are mapped by the LLM to table cells and parsed by code.
RawFieldKey = Literal[
    "revenue", "other_income", "finance_cost", "d_and_a", "pbt", "tax_expense",
    "pat_before_nci", "pat",                       # pat = attributable to owners of the parent
    "cfo", "purchase_of_ppe", "purchase_of_intangibles",
    "total_assets", "total_liabilities", "total_equity", "non_controlling_interest",
    "borrowings_current", "borrowings_noncurrent", "cash_and_equivalents",
    "trade_receivables", "current_assets", "current_liabilities", "segment_revenue",
    "operating_income",                            # US GAAP 'Operating income (loss)'
]
# Derived keys are NEVER mapped by the LLM; backend/engine/derived_fields.py computes them (Section 4, Stage 4).
DerivedFieldKey = Literal["ebit", "ebitda", "interest_expense", "gross_debt", "capex"]
StandardFieldKey = Literal[RawFieldKey, DerivedFieldKey]


class LineItem(BaseModel):
    item_id: str = Field(..., description="e.g., 'li_fy26_revenue'")
    fiscal_period: str = Field(..., description="e.g., 'FY24', 'FY25', 'FY26' (annual) or 'Q1FY27' (quarterly list only)")
    field_key: StandardFieldKey
    reported_label: str = Field(..., description="Exact label in filing, or the derivation for derived keys")
    value: Optional[Decimal] = Field(None, description="Normalized numeric value; None if missing in filing")
    formatted_value: str = Field(..., description="Display string from formatters.py, '—' when None")
    unit: CurrencyUnit
    is_restated: bool = False
    is_derived: bool = False
    derived_from_item_ids: list[str] = Field(default_factory=list)
    segment_name: Optional[str] = Field(None, description="Only for field_key='segment_revenue'")
    evidence: EvidenceCitation = Field(..., description="For derived items: the citation of the first input")


class ReconciliationCheck(BaseModel):
    check_id: str
    fiscal_period: str
    check_name: str = Field(..., description="e.g., 'Balance Sheet Identity: Assets = Liabilities + Equity'")
    formula_desc: str
    lhs_value: Optional[Decimal]
    rhs_value: Optional[Decimal]
    discrepancy: Optional[Decimal]
    tolerance_pct: Decimal = Decimal("0.01")
    status: Literal["passed", "failed", "skipped"]
    passed: bool = Field(..., description="True only when status == 'passed'")
    failure_reason: Optional[str] = None
    skip_reason: Optional[str] = Field(None, description="e.g. 'Filing prints only Total equity and liabilities'")


class MetricInputRef(BaseModel):
    symbol: str = Field(..., description="Variable symbol in formula, e.g., 'Receivables_FY26'")
    line_item_id: str
    fiscal_period: str
    value: Optional[Decimal]
    evidence_id: str


class MetricLedgerRow(BaseModel):
    metric_id: str = Field(..., description="Unique ID, e.g., 'met_dso_fy26'")
    metric_key: str = Field(..., description="e.g., 'dso', 'cfo_to_pat', 'rev_cagr'")
    metric_label: str = Field(..., description="e.g., 'DSO FY26'")
    fiscal_period: str
    formula_template: str = Field(..., description="e.g., '(Receivables_t / Revenue_t) * 365'")
    formula_substituted: str = Field(..., description="e.g., '(880 / 3,810) * 365'")
    inputs: list[MetricInputRef]
    value: Optional[Decimal] = Field(None, description="Computed Decimal; None if N/M or missing inputs")
    formatted_value: str = Field(..., description="Display string, e.g., '84.3 d', '17.6%', 'N/M (Neg Base)'")
    unit: Literal["pct", "multiple", "days", "bps", "currency"]
    status: Literal["computed", "nm_negative_base", "nm_zero_div", "missing_input"]
    status_note: Optional[str] = None
    primary_source_ref: str = Field(..., description="Short citation, e.g., 'AR FY26 p.116'")


class RedFlagResult(BaseModel):
    rule_id: str = Field(..., description="e.g., 'RF_01_RECEIVABLES_STRETCH'")
    rule_name: str = Field(..., description="e.g., 'Receivables stretching'")
    severity: Literal["high", "medium", "low"]
    threshold_desc: str = Field(..., description="e.g., '2-year DSO increase > 15.0 days'")
    observed_value_formatted: str = Field(..., description="e.g., '+25.5 days (58.7d -> 84.3d)'")
    status: Literal["fired", "clear", "insufficient_data"]
    fired: bool = Field(..., description="True only when status == 'fired'")
    explanation: str = Field(..., description="Deterministic string populated from formatted values")
    missing_inputs: list[str] = Field(default_factory=list, description="Variable names that were None")
    supporting_metric_ids: list[str]
