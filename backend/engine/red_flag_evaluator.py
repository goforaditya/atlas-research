"""YAML rule parser and deterministic threshold evaluator (Section 6). Zero LLM involvement."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from backend.schemas.financials import LineItem, MetricLedgerRow, RedFlagResult

from .derived_fields import line_items_period_order
from .formatters import MISSING as MISSING_TEXT
from .safe_expr import MISSING, SafeExpression, compile_expression

_VAR = re.compile(r"^(?P<base>[a-z0-9_]+?)_t(?:_minus_(?P<off>[12]))?$")


@dataclass
class RedFlagRule:
    rule_id: str
    rule_name: str
    severity: str
    threshold_desc: str
    expression: SafeExpression
    required_metrics: list[str]
    template_fired: str
    template_clear: str


def load_rules(path: str | Path) -> list[RedFlagRule]:
    """Parse and validate the YAML. Unsafe expressions raise here, at load time."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return parse_rules(data)


def parse_rules(data: dict) -> list[RedFlagRule]:
    rules = []
    for raw in data.get("rules", []):
        rules.append(RedFlagRule(
            rule_id=raw["rule_id"],
            rule_name=raw["rule_name"],
            severity=raw["severity"],
            threshold_desc=raw["threshold_desc"],
            expression=compile_expression(raw["expression"]),
            required_metrics=list(raw.get("required_metrics", [])),
            template_fired=raw["template_fired"],
            template_clear=raw["template_clear"],
        ))
    return rules


@dataclass
class _Binding:
    value: Decimal | None
    formatted: str
    label: str
    metric_id: str | None = None


class _Bindings:
    """Resolves `<metric_key>_t[_minus_N]` and `<field_key>_t` names against the ledger and line items."""

    def __init__(self, ledger: list[MetricLedgerRow], line_items: list[LineItem]):
        annual = [li for li in line_items if li.field_key != "segment_revenue"]
        periods = sorted({r.fiscal_period for r in ledger} | {li.fiscal_period for li in annual},
                         key=line_items_period_order)
        self.periods = periods
        self.metrics = {(r.metric_key, r.fiscal_period): r for r in ledger}
        self.metric_keys = {r.metric_key for r in ledger}
        self.items = {(li.field_key, li.fiscal_period): li for li in annual}
        self.item_keys = {li.field_key for li in annual}

    def resolve(self, name: str) -> _Binding:
        m = _VAR.match(name)
        if not m or not self.periods:
            return _Binding(None, MISSING_TEXT, name)
        base, off = m.group("base"), int(m.group("off") or 0)
        idx = len(self.periods) - 1 - off
        if idx < 0:
            return _Binding(None, MISSING_TEXT, name)
        period = self.periods[idx]
        if base in self.metric_keys or base not in self.item_keys:
            row = self.metrics.get((base, period))
            if row is None:
                return _Binding(None, MISSING_TEXT, f"{base} {period}")
            return _Binding(row.value, row.formatted_value, row.metric_label, row.metric_id)
        li = self.items.get((base, period))
        if li is None:
            return _Binding(None, MISSING_TEXT, f"{base} {period}")
        return _Binding(li.value, li.formatted_value, f"{li.reported_label} {period}")


def _fill(template: str, formatted: dict[str, str]) -> str:
    return re.sub(r"\{([a-z0-9_]+)\}", lambda m: formatted.get(m.group(1), MISSING_TEXT), template)


def evaluate_rule(rule: RedFlagRule, bindings: _Bindings) -> RedFlagResult:
    template_names = re.findall(r"\{([a-z0-9_]+)\}", rule.template_fired + rule.template_clear)
    names = list(dict.fromkeys(rule.expression.names + template_names))
    resolved = {n: bindings.resolve(n) for n in names}
    env = {n: resolved[n].value for n in rule.expression.names}
    formatted = {n: b.formatted for n, b in resolved.items()}
    missing = [n for n in rule.expression.names if env[n] is None]
    supporting = list(dict.fromkeys(b.metric_id for b in resolved.values() if b.metric_id))
    observed = "; ".join(f"{resolved[n].label}: {resolved[n].formatted}" for n in rule.expression.names)

    outcome = rule.expression.evaluate(env)
    if outcome is MISSING:
        status, explanation = "insufficient_data", f"Not evaluated: {', '.join(missing)} unavailable."
    elif outcome:
        status, explanation = "fired", _fill(rule.template_fired, formatted)
    else:
        status, explanation = "clear", _fill(rule.template_clear, formatted)

    return RedFlagResult(
        rule_id=rule.rule_id,
        rule_name=rule.rule_name,
        severity=rule.severity,  # type: ignore[arg-type]
        threshold_desc=rule.threshold_desc,
        observed_value_formatted=observed,
        status=status,  # type: ignore[arg-type]
        fired=status == "fired",
        explanation=explanation,
        missing_inputs=missing,
        supporting_metric_ids=supporting,
    )


def evaluate_red_flag_rules(
    metrics_ledger: list[MetricLedgerRow], line_items: list[LineItem], rules: list[RedFlagRule] | str | Path
) -> list[RedFlagResult]:
    if not isinstance(rules, list):
        rules = load_rules(rules)
    bindings = _Bindings(metrics_ledger, line_items)
    return [evaluate_rule(rule, bindings) for rule in rules]
