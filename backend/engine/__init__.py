"""Deterministic compute engines (zero LLM)."""
from .decimal_math import cagr, growth, median, quantize, safe_div, to_decimal
from .derived_fields import compute_derived_fields
from .formatters import (
    format_bps,
    format_currency,
    format_days,
    format_metric_value,
    format_multiple,
    format_pct,
    format_plain_number,
)
from .metrics_calculator import compute_all_metrics
from .reconciliation import run_reconciliation_suite
from .unit_normaliser import detect_unit, normalise_units, parse_cell_decimal

__all__ = [name for name in dir() if not name.startswith("_")]
