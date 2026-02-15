from __future__ import annotations

import ast
import decimal
import re
from typing import Any

from llm_async.models import Tool

from minibot.llm.tools.arg_utils import int_with_default
from minibot.llm.tools.base import ToolBinding, ToolContext
from minibot.llm.tools.schema_utils import nullable_integer, strict_object, string_field

_ALLOWED_CHARS_PATTERN = re.compile(r"^[0-9.()+\-*/%\s]+$")
_TOKEN_PATTERN = re.compile(r"\d+(?:\.\d+)?|\.\d+|\*\*|[+\-*/%()]")


class CalculatorTool:
    def __init__(
        self,
        default_scale: int = 28,
        max_expression_length: int = 200,
        max_exponent_abs: int = 1000,
    ) -> None:
        self._default_scale = max(default_scale, 1)
        self._max_expression_length = max(max_expression_length, 1)
        self._max_exponent_abs = max(max_exponent_abs, 1)

    def bindings(self) -> list[ToolBinding]:
        return [
            ToolBinding(tool=self._calculator_schema(), handler=self._handle),
            ToolBinding(tool=self._schema(), handler=self._handle),
        ]

    def _calculator_schema(self) -> Tool:
        schema = self._schema()
        return Tool(name="calculator", description=schema.description, parameters=schema.parameters)

    def _schema(self) -> Tool:
        return Tool(
            name="calculate_expression",
            description=(
                "Safely evaluate arithmetic expressions with Decimal precision. "
                "Supports +, -, *, /, %, **, parentheses, and unary +/- operators."
            ),
            parameters=strict_object(
                properties={
                    "expression": string_field("Arithmetic expression to evaluate."),
                    "scale": nullable_integer(minimum=1, description="Optional Decimal precision."),
                },
                required=["expression", "scale"],
            ),
        )

    async def _handle(self, payload: dict[str, Any], _: ToolContext) -> dict[str, Any]:
        expression = payload.get("expression")
        if not isinstance(expression, str):
            return {"ok": False, "error": "expression must be a string"}

        scale = self._coerce_scale(payload.get("scale"))
        try:
            normalized = self._validate_expression(expression)
            root = ast.parse(normalized, mode="eval")
            self._validate_ast(root)
            with decimal.localcontext() as ctx:
                ctx.prec = scale
                value = self._evaluate(root.body)
            result = format(value, "f")
            return {
                "ok": True,
                "expression": expression,
                "result": _normalize_decimal_string(result),
                "scale": scale,
            }
        except Exception as exc:
            return {
                "ok": False,
                "expression": expression,
                "error": str(exc),
                "scale": scale,
            }

    def _coerce_scale(self, value: Any) -> int:
        return int_with_default(
            value,
            default=self._default_scale,
            field="scale",
            min_value=1,
            allow_string=True,
            reject_bool=True,
            type_error="scale must be an integer",
            min_error="scale must be >= 1",
        )

    def _validate_expression(self, expression: str) -> str:
        normalized = expression.strip()
        if not normalized:
            raise ValueError("expression cannot be empty")
        if len(normalized) > self._max_expression_length:
            raise ValueError(f"expression exceeds max length {self._max_expression_length}")
        if not _ALLOWED_CHARS_PATTERN.fullmatch(normalized):
            raise ValueError("expression contains invalid characters")
        compact = re.sub(r"\s+", "", normalized)
        self._validate_tokens(compact)
        self._validate_parentheses(compact)
        return compact

    def _validate_tokens(self, compact: str) -> None:
        position = 0
        for match in _TOKEN_PATTERN.finditer(compact):
            if match.start() != position:
                raise ValueError("expression contains invalid token sequence")
            position = match.end()
        if position != len(compact):
            raise ValueError("expression contains invalid token sequence")

    @staticmethod
    def _validate_parentheses(compact: str) -> None:
        depth = 0
        for char in compact:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            if depth < 0:
                raise ValueError("expression has unbalanced parentheses")
        if depth != 0:
            raise ValueError("expression has unbalanced parentheses")

    @staticmethod
    def _validate_ast(node: ast.AST) -> None:
        allowed = (
            ast.Expression,
            ast.BinOp,
            ast.UnaryOp,
            ast.Constant,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.Mod,
            ast.Pow,
            ast.UAdd,
            ast.USub,
        )
        for child in ast.walk(node):
            if not isinstance(child, allowed):
                raise ValueError("expression includes unsupported syntax")
            if isinstance(child, ast.Constant) and not isinstance(child.value, (int, float)):
                raise ValueError("expression must use numeric literals")

    def _evaluate(self, node: ast.AST) -> decimal.Decimal:
        if isinstance(node, ast.Constant):
            return decimal.Decimal(str(node.value))
        if isinstance(node, ast.UnaryOp):
            operand = self._evaluate(node.operand)
            if isinstance(node.op, ast.UAdd):
                return operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise ValueError("unsupported unary operator")
        if isinstance(node, ast.BinOp):
            left = self._evaluate(node.left)
            right = self._evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise ValueError("division by zero")
                return left / right
            if isinstance(node.op, ast.Mod):
                if right == 0:
                    raise ValueError("modulo by zero")
                return left % right
            if isinstance(node.op, ast.Pow):
                return self._pow(left, right)
            raise ValueError("unsupported operator")
        raise ValueError("unsupported expression")

    def _pow(self, base: decimal.Decimal, exponent: decimal.Decimal) -> decimal.Decimal:
        if exponent != exponent.to_integral_value():
            raise ValueError("exponent must be an integer")
        exponent_int = int(exponent)
        if abs(exponent_int) > self._max_exponent_abs:
            raise ValueError(f"absolute exponent exceeds limit {self._max_exponent_abs}")
        return base**exponent_int


def _normalize_decimal_string(value: str) -> str:
    if "." not in value:
        return value
    normalized = value.rstrip("0").rstrip(".")
    return normalized or "0"
