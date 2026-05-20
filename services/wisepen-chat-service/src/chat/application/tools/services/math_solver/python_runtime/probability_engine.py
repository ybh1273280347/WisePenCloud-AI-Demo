from typing import Any

import sympy as sp

from chat.application.tools.services.math_solver.errors import MathSolverValidationError
from chat.application.tools.services.math_solver.python_runtime.sympy_engine import (
    parse_bound,
    parse_expression,
    parse_variable,
)


def compute_combinatorics_or_probability(task: str, data: Any) -> Any:
    if task == "factorial":
        n = _require_non_negative_int(data.n, "n")
        return sp.factorial(n)
    if task == "binomial":
        n = _require_non_negative_int(data.n, "n")
        k = _require_non_negative_int(data.k, "k")
        return sp.binomial(n, k)
    if task == "permutation":
        n = _require_non_negative_int(data.n, "n")
        k = _require_non_negative_int(data.k, "k")
        if k > n:
            raise MathSolverValidationError("n and k must satisfy 0 <= k <= n.")
        return sp.factorial(n) / sp.factorial(n - k)
    if task == "binomial_probability":
        n = _require_non_negative_int(data.n, "n")
        k = _require_non_negative_int(data.k, "k")
        probability = parse_expression(data.probability, [])
        return sp.binomial(n, k) * probability**k * (1 - probability) ** (n - k)
    if task == "expectation":
        return _finite_uniform_moment(data, power=1)
    if task == "variance":
        mean = _finite_uniform_moment(data, power=1)
        second = _finite_uniform_moment(data, power=2)
        return sp.simplify(second - mean**2)

    raise MathSolverValidationError(f"unsupported probability task: {task}")


def _require_non_negative_int(value: int | None, name: str) -> int:
    if value is None:
        raise MathSolverValidationError(f"{name} is required.")
    if value < 0:
        raise MathSolverValidationError(f"{name} must be non-negative.")
    return value


def _finite_uniform_moment(data: Any, *, power: int) -> sp.Expr:
    variable = parse_variable(data.variable)
    variable_name = str(variable)
    lower = int(parse_bound(data.lower, "lower", [variable_name]))
    upper = int(parse_bound(data.upper, "upper", [variable_name]))
    if upper < lower:
        raise MathSolverValidationError("upper must be greater than or equal to lower.")
    expression = parse_expression(data.expression, [variable_name])
    count = upper - lower + 1
    return sp.simplify(sp.summation(expression**power, (variable, lower, upper)) / count)
