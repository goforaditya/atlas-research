"""Whitelisted AST evaluator for red-flag expressions (Section 4, Stage 6). `eval` is never called.

Allowed nodes: Expression, BoolOp (and/or), Compare (<, <=, >, >=, ==), BinOp (+, -),
UnaryOp (-, not), Name, Constant (int / float / numeric str). Anything else raises at load time.
Evaluation is three-valued: a missing (None) variable makes its sub-expression MISSING, and only an
`or` with one fully available true side can still be decided.
"""
from __future__ import annotations

import ast
import operator
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Union


class UnsafeExpressionError(ValueError):
    """Raised when an expression uses anything outside the whitelist."""


class _Missing:
    _inst: _Missing | None = None

    def __new__(cls) -> _Missing:
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __repr__(self) -> str:
        return "MISSING"


MISSING = _Missing()
Value = Union[Decimal, bool, _Missing]

_ALLOWED: tuple[type[ast.AST], ...] = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.Compare, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq,
    ast.BinOp, ast.Add, ast.Sub, ast.UnaryOp, ast.USub, ast.Not, ast.Name, ast.Load, ast.Constant,
)
_CMP = {ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Eq: operator.eq}


@dataclass
class SafeExpression:
    source: str
    tree: ast.Expression
    names: list[str] = field(default_factory=list)

    def evaluate(self, variables: dict[str, Decimal | None]) -> Value:
        return _eval(self.tree.body, variables)


def compile_expression(source: str) -> SafeExpression:
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(f"Invalid expression {source!r}: {exc.msg}") from exc
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise UnsafeExpressionError(f"Disallowed syntax {type(node).__name__} in {source!r}")
        if isinstance(node, ast.Constant):
            _const(node)  # validates
        if isinstance(node, ast.Name) and node.id not in names:
            names.append(node.id)
    return SafeExpression(source, tree, names)


def _const(node: ast.Constant) -> Decimal:
    v = node.value
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise UnsafeExpressionError(f"Disallowed constant {v!r}")
    try:
        return Decimal(str(v))
    except InvalidOperation as exc:
        raise UnsafeExpressionError(f"Non-numeric constant {v!r}") from exc


def _eval(node: ast.AST, env: dict[str, Decimal | None]) -> Value:
    if isinstance(node, ast.Constant):
        return _const(node)
    if isinstance(node, ast.Name):
        v = env.get(node.id)
        return MISSING if v is None else v
    if isinstance(node, ast.UnaryOp):
        x = _eval(node.operand, env)
        if x is MISSING:
            return MISSING
        if isinstance(node.op, ast.Not):
            return not x
        return -x  # type: ignore[operator]
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, env), _eval(node.right, env)
        if a is MISSING or b is MISSING:
            return MISSING
        return a + b if isinstance(node.op, ast.Add) else a - b  # type: ignore[operator]
    if isinstance(node, ast.Compare):
        left = _eval(node.left, env)
        result: Value = True
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, env)
            if left is MISSING or right is MISSING:
                return MISSING
            if not _CMP[type(op)](left, right):
                result = False
            left = right
        return result
    if isinstance(node, ast.BoolOp):
        vals = [_eval(v, env) for v in node.values]
        if isinstance(node.op, ast.Or):
            if any(v is not MISSING and bool(v) for v in vals):
                return True
            return MISSING if any(v is MISSING for v in vals) else False
        if any(v is MISSING for v in vals):
            return MISSING
        return all(bool(v) for v in vals)
    raise UnsafeExpressionError(f"Disallowed syntax {type(node).__name__}")  # pragma: no cover
