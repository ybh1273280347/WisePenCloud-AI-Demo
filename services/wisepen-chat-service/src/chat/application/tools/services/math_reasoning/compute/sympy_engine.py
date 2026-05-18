from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional, Sequence

import sympy as sp

from chat.application.tools.services.math_reasoning.common.errors import MathValidationError
from chat.application.tools.services.math_reasoning.common.expression_parser import parse_math_expr
from chat.application.tools.services.math_reasoning.config import MATH_REASONING_MAX_MATRIX_SIZE


MAX_MATRIX_SIZE = MATH_REASONING_MAX_MATRIX_SIZE


@dataclass(frozen=True, slots=True)
class _IntegralCall:
    integrand: str
    variable: Optional[str] = None
    lower: Optional[str] = None
    upper: Optional[str] = None


def parse_variable(name: Any, default: str = "x") -> sp.Symbol:
    variable = name or default
    if not isinstance(variable, str) or not variable.isidentifier():
        raise MathValidationError("variable must be a valid identifier.")
    return sp.Symbol(variable)


def parse_variables(names: Any, fallback: Sequence[str] = ("x",)) -> List[sp.Symbol]:
    raw = names if isinstance(names, list) and names else list(fallback)
    if not all(isinstance(name, str) and name.isidentifier() for name in raw):
        raise MathValidationError("variables must be valid identifiers.")
    return [sp.Symbol(name) for name in raw]


def parse_matrix(value: Any, *, name: str = "matrix") -> sp.Matrix:
    if not isinstance(value, list) or not value:
        raise MathValidationError(f"{name} must be a non-empty array.")
    if len(value) > MAX_MATRIX_SIZE:
        raise MathValidationError(f"{name} exceeds maximum matrix size.")

    rows: List[List[Any]] = []
    width: Optional[int] = None
    for row in value:
        row_values = row if isinstance(row, list) else [row]
        if width is None:
            width = len(row_values)
        if len(row_values) != width:
            raise MathValidationError(f"{name} rows must have the same length.")
        if width > MAX_MATRIX_SIZE:
            raise MathValidationError(f"{name} exceeds maximum matrix size.")
        rows.append([sp.sympify(item) for item in row_values])
    return sp.Matrix(rows)


def compute_sympy(task: str, kwargs: dict[str, Any]) -> Any:
    variables = kwargs.get("variables")
    variable_names = variables if isinstance(variables, list) else None
    variable = parse_variable(kwargs.get("variable"), default="x")
    expression = kwargs.get("expression")

    if task == "integrate" and isinstance(expression, str):
        integral_call = _parse_integral_call(expression)
        if integral_call is not None:
            expression = integral_call.integrand
            if integral_call.variable and kwargs.get("variable") is None:
                variable = parse_variable(integral_call.variable, default="x")
            if integral_call.lower is not None and integral_call.upper is not None:
                kwargs = dict(kwargs)
                kwargs.setdefault("lower", integral_call.lower)
                kwargs.setdefault("upper", integral_call.upper)

    if task in {"simplify", "expand", "factor", "solve", "limit", "differentiate", "integrate", "series", "numeric"}:
        if not isinstance(expression, str):
            raise MathValidationError("expression is required.")
        parse_vars = variable_names or [str(variable)]
        expr = parse_math_expr(expression, parse_vars)

    if task == "simplify":
        return sp.simplify(expr)
    if task == "expand":
        return sp.expand(expr)
    if task == "factor":
        return sp.factor(expr)
    if task == "solve":
        solve_vars = parse_variables(variable_names, fallback=(str(variable),))
        result = sp.solve(expr, solve_vars, dict=True)
        if not result and len(solve_vars) == 1:
            return sp.solve(expr, solve_vars[0])
        return result
    if task == "limit":
        if kwargs.get("point") is None:
            raise MathValidationError("point is required for limit.")
        point = parse_math_expr(str(kwargs["point"]), [str(variable)])
        return sp.limit(expr, variable, point)
    if task == "differentiate":
        return sp.diff(expr, variable)
    if task == "integrate":
        lower = kwargs.get("lower")
        upper = kwargs.get("upper")
        if lower is not None and upper is not None:
            lo = parse_math_expr(str(lower), [str(variable)])
            hi = parse_math_expr(str(upper), [str(variable)])
            return sp.integrate(expr, (variable, lo, hi))
        return sp.integrate(expr, variable)
    if task == "series":
        point = parse_math_expr(str(kwargs.get("point", "0")), [str(variable)])
        order = _coerce_int(kwargs.get("order", 6), "order")
        if order < 1 or order > 20:
            raise MathValidationError("order must be between 1 and 20.")
        return sp.series(expr, variable, point, order)
    if task == "numeric":
        return sp.N(expr)

    if task in {"determinant", "rank", "inverse", "eigen", "diagonalize", "rref"}:
        matrix = parse_matrix(kwargs.get("matrix"))
        if task == "determinant":
            return matrix.det()
        if task == "rank":
            return matrix.rank()
        if task == "inverse":
            if matrix.det() == 0:
                raise MathValidationError("matrix is not invertible.")
            return matrix.inv()
        if task == "eigen":
            return matrix.eigenvals()
        if task == "diagonalize":
            try:
                transition, diagonal = matrix.diagonalize()
            except Exception as e:
                raise MathValidationError(f"matrix is not diagonalizable: {e}") from e
            return {"P": transition, "D": diagonal}
        if task == "rref":
            reduced, pivots = matrix.rref()
            return {"matrix": reduced, "pivots": pivots}

    if task == "linear_solve":
        matrix = parse_matrix(kwargs.get("matrix"))
        rhs = parse_matrix(kwargs.get("matrix_b"), name="matrix_b")
        return matrix.gauss_jordan_solve(rhs)[0]

    if task == "matrix_multiply":
        matrix = parse_matrix(kwargs.get("matrix"))
        rhs = parse_matrix(kwargs.get("matrix_b"), name="matrix_b")
        return matrix * rhs

    if task == "factorial":
        n = _coerce_int(kwargs.get("n"), "n")
        if n < 0:
            raise MathValidationError("n must be non-negative.")
        return sp.factorial(n)

    if task == "combination":
        n = _coerce_int(kwargs.get("n"), "n")
        k = _coerce_int(kwargs.get("k"), "k")
        return sp.binomial(n, k)

    if task == "permutation":
        n = _coerce_int(kwargs.get("n"), "n")
        k = _coerce_int(kwargs.get("k"), "k")
        if k < 0 or n < 0 or k > n:
            raise MathValidationError("n and k must satisfy 0 <= k <= n.")
        return sp.factorial(n) / sp.factorial(n - k)

    if task == "summation":
        if not isinstance(expression, str):
            raise MathValidationError("expression is required.")
        expr = parse_math_expr(expression, variable_names or [str(variable)])
        lower = parse_math_expr(str(kwargs.get("lower", "1")), [str(variable)])
        upper = parse_math_expr(str(kwargs.get("upper", kwargs.get("n", "n"))), [str(variable)])
        return sp.summation(expr, (variable, lower, upper))

    if task == "binomial_probability":
        n = _coerce_int(kwargs.get("n"), "n")
        k = _coerce_int(kwargs.get("k"), "k")
        p = parse_math_expr(str(kwargs.get("probability")), [])
        return sp.binomial(n, k) * p**k * (1 - p) ** (n - k)

    if task == "expectation":
        return _finite_uniform_moment(kwargs, power=1)

    if task == "variance":
        mean = _finite_uniform_moment(kwargs, power=1)
        second = _finite_uniform_moment(kwargs, power=2)
        return sp.simplify(second - mean**2)

    raise MathValidationError(f"unsupported math task: {task}")


def _finite_uniform_moment(kwargs: dict[str, Any], *, power: int) -> sp.Expr:
    expression = kwargs.get("expression")
    if not isinstance(expression, str):
        raise MathValidationError("expression is required.")
    variable = parse_variable(kwargs.get("variable"), default="x")
    if kwargs.get("lower") is None or kwargs.get("upper") is None:
        raise MathValidationError("lower and upper are required for expectation/variance.")
    lower = int(parse_math_expr(str(kwargs["lower"]), [str(variable)]))
    upper = int(parse_math_expr(str(kwargs["upper"]), [str(variable)]))
    if upper < lower:
        raise MathValidationError("upper must be greater than or equal to lower.")
    expr = parse_math_expr(expression, [str(variable)])
    count = upper - lower + 1
    return sp.simplify(sp.summation(expr**power, (variable, lower, upper)) / count)


def _coerce_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or value is None:
        raise MathValidationError(f"{name} must be an integer.")
    try:
        if isinstance(value, float) and not value.is_integer():
            raise ValueError
        return int(value)
    except (TypeError, ValueError):
        raise MathValidationError(f"{name} must be an integer.") from None


def _parse_integral_call(expression: str) -> Optional[_IntegralCall]:
    text = expression.strip()
    for name in ("integrate", "Integral"):
        prefix = f"{name}("
        if text.startswith(prefix) and text.endswith(")"):
            inner = text[len(prefix):-1].strip()
            args = _split_top_level_args(inner)
            if not args:
                return None
            variable = None
            lower = None
            upper = None
            if len(args) >= 2:
                variable_arg = args[1].strip()
                tuple_args = _parse_integral_variable_tuple(variable_arg)
                if tuple_args is not None:
                    variable, lower, upper = tuple_args
                elif variable_arg.isidentifier():
                    variable = variable_arg
            return _IntegralCall(
                integrand=args[0].strip(),
                variable=variable,
                lower=lower,
                upper=upper,
            )
    return None


def _parse_integral_variable_tuple(value: str) -> Optional[tuple[str, str, str]]:
    text = value.strip()
    if not (text.startswith("(") and text.endswith(")")):
        return None
    parts = _split_top_level_args(text[1:-1].strip())
    if len(parts) != 3 or not parts[0].strip().isidentifier():
        return None
    return parts[0].strip(), parts[1].strip(), parts[2].strip()


def _split_top_level_args(value: str) -> List[str]:
    args: List[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(value):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            args.append(value[start:index].strip())
            start = index + 1
    tail = value[start:].strip()
    if tail:
        args.append(tail)
    return args
