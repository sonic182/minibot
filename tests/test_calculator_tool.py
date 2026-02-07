from __future__ import annotations

import pytest

from minibot.llm.tools.base import ToolContext
from minibot.llm.tools.calculator import CalculatorTool


def _binding(tool: CalculatorTool):
    return tool.bindings()[0]


@pytest.mark.asyncio
async def test_calculator_evaluates_pow_and_mod() -> None:
    binding = _binding(CalculatorTool())
    result = await binding.handler({"expression": "2 ** 8 % 7", "scale": None}, ToolContext())
    assert result["ok"] is True
    assert result["result"] == "4"


@pytest.mark.asyncio
async def test_calculator_preserves_decimal_precision() -> None:
    binding = _binding(CalculatorTool())
    result = await binding.handler({"expression": "1 / 3", "scale": 10}, ToolContext())
    assert result["ok"] is True
    assert result["result"] == "0.3333333333"


@pytest.mark.asyncio
async def test_calculator_rejects_invalid_regex_input() -> None:
    binding = _binding(CalculatorTool())
    result = await binding.handler({"expression": "__import__('os')", "scale": None}, ToolContext())
    assert result["ok"] is False
    assert "invalid characters" in result["error"]


@pytest.mark.asyncio
async def test_calculator_rejects_unbalanced_parentheses() -> None:
    binding = _binding(CalculatorTool())
    result = await binding.handler({"expression": "(2 + 3", "scale": None}, ToolContext())
    assert result["ok"] is False
    assert "unbalanced parentheses" in result["error"]


@pytest.mark.asyncio
async def test_calculator_rejects_modulo_by_zero() -> None:
    binding = _binding(CalculatorTool())
    result = await binding.handler({"expression": "10 % 0", "scale": None}, ToolContext())
    assert result["ok"] is False
    assert "modulo by zero" in result["error"]


@pytest.mark.asyncio
async def test_calculator_limits_exponent_size() -> None:
    binding = _binding(CalculatorTool(max_exponent_abs=5))
    result = await binding.handler({"expression": "2 ** 9", "scale": None}, ToolContext())
    assert result["ok"] is False
    assert "exceeds limit" in result["error"]
