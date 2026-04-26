from __future__ import annotations

import json
import math

import pytest

from schemas.errors import CALCULATION_ERROR, TOOL_ARGUMENT_ERROR
from tools.impl.calculator_tool import CalculatorTool


@pytest.fixture
def calc():
    return CalculatorTool()


# ---------------------------------------------------------------------------
# Basic arithmetic
# ---------------------------------------------------------------------------

def test_addition(calc):
    result = calc.run({"expression": "1 + 2"})
    assert result.success
    data = json.loads(result.output)["data"]
    assert data["result"] == pytest.approx(3)


def test_subtraction(calc):
    result = calc.run({"expression": "10 - 4"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(6)


def test_multiplication(calc):
    result = calc.run({"expression": "3 * 7"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(21)


def test_division(calc):
    result = calc.run({"expression": "10 / 4"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(2.5)


def test_floor_division(calc):
    result = calc.run({"expression": "10 // 3"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == 3


def test_modulo(calc):
    result = calc.run({"expression": "10 % 3"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == 1


def test_power(calc):
    result = calc.run({"expression": "2 ** 10"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(1024)


def test_parentheses(calc):
    result = calc.run({"expression": "(1 + 2) * 3"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(9)


def test_unary_minus(calc):
    result = calc.run({"expression": "-5"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(-5)


def test_unary_plus(calc):
    result = calc.run({"expression": "+5"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(5)


# ---------------------------------------------------------------------------
# Math functions
# ---------------------------------------------------------------------------

def test_sqrt(calc):
    result = calc.run({"expression": "sqrt(16)"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(4.0)


def test_sin(calc):
    result = calc.run({"expression": "sin(0)"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(0.0)


def test_cos(calc):
    result = calc.run({"expression": "cos(0)"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(1.0)


def test_log(calc):
    result = calc.run({"expression": "log(1)"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(0.0)


def test_log10(calc):
    result = calc.run({"expression": "log10(100)"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(2.0)


def test_exp(calc):
    result = calc.run({"expression": "exp(0)"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(1.0)


def test_floor(calc):
    result = calc.run({"expression": "floor(3.7)"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == 3


def test_ceil(calc):
    result = calc.run({"expression": "ceil(3.2)"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == 4


def test_abs(calc):
    result = calc.run({"expression": "abs(-42)"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(42)


def test_round(calc):
    result = calc.run({"expression": "round(3.567, 2)"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(3.57)


def test_pow_function(calc):
    result = calc.run({"expression": "pow(2, 8)"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(256)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_pi_constant(calc):
    result = calc.run({"expression": "pi"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(math.pi)


def test_e_constant(calc):
    result = calc.run({"expression": "e"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(math.e)


def test_tau_constant(calc):
    result = calc.run({"expression": "tau"})
    assert result.success
    assert json.loads(result.output)["data"]["result"] == pytest.approx(math.tau)


# ---------------------------------------------------------------------------
# Complex expressions
# ---------------------------------------------------------------------------

def test_complex_expression(calc):
    result = calc.run({"expression": "(1250.5 * 18 + 320 * 6) / 1.13"})
    assert result.success
    expected = (1250.5 * 18 + 320 * 6) / 1.13
    assert json.loads(result.output)["data"]["result"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

def test_empty_expression(calc):
    result = calc.run({"expression": ""})
    assert not result.success
    assert result.error.code == TOOL_ARGUMENT_ERROR


def test_missing_expression_key(calc):
    result = calc.run({})
    assert not result.success
    assert result.error.code == TOOL_ARGUMENT_ERROR


def test_division_by_zero(calc):
    result = calc.run({"expression": "1 / 0"})
    assert not result.success
    assert result.error.code == CALCULATION_ERROR
    assert "zero" in result.error.message.lower()


def test_invalid_syntax(calc):
    result = calc.run({"expression": "1 +"})
    assert not result.success
    assert result.error.code == CALCULATION_ERROR


def test_unknown_identifier(calc):
    result = calc.run({"expression": "unknown_var"})
    assert not result.success
    assert result.error.code == CALCULATION_ERROR


def test_unsupported_function(calc):
    result = calc.run({"expression": "eval('1+1')"})
    assert not result.success
    assert result.error.code == CALCULATION_ERROR


def test_string_constant_not_allowed(calc):
    result = calc.run({"expression": "'hello'"})
    assert not result.success
    assert result.error.code == CALCULATION_ERROR


def test_keyword_arguments_not_allowed(calc):
    result = calc.run({"expression": "round(x=3.5)"})
    assert not result.success
    assert result.error.code == CALCULATION_ERROR


def test_sqrt_negative_raises(calc):
    result = calc.run({"expression": "sqrt(-1)"})
    assert not result.success
    assert result.error.code == CALCULATION_ERROR


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------

def test_tool_name(calc):
    assert calc.name == "calculator"


def test_tool_schema(calc):
    schema = calc.schema()
    assert schema["name"] == "calculator"
    assert "expression" in schema["parameters"]["properties"]
