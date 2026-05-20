from typing import Any, Optional, Sequence

import sympy as sp

from chat.application.tools.services.math_solver.errors import MathSolverValidationError
from chat.application.tools.services.math_solver.python_runtime.expression_parser import (
    MathParseError,
    parse_math_expr,
)


def parse_variable(name: Optional[str], default: str = "x") -> sp.Symbol:
    variable = name or default
    if not variable.isidentifier():
        raise MathSolverValidationError("variable must be a valid identifier.")
    return sp.Symbol(variable)


def parse_variables(
    names: Optional[list[str]],
    fallback: Sequence[str] = ("x",),
) -> list[sp.Symbol]:
    raw = names if names else list(fallback)
    if not all(name.isidentifier() for name in raw):
        raise MathSolverValidationError("variables must be valid identifiers.")
    return [sp.Symbol(name) for name in raw]


def parse_expression(expression: Optional[str], variables: Optional[list[str]] = None) -> sp.Expr:
    if expression is None:
        raise MathSolverValidationError("expression is required.")
    try:
        return parse_math_expr(expression, variables)
    except MathParseError as exc:
        raise MathSolverValidationError(str(exc)) from exc


def parse_bound(value: Optional[str], name: str, variables: Optional[list[str]] = None) -> sp.Expr:
    if value is None:
        raise MathSolverValidationError(f"{name} is required.")
    return parse_expression(value, variables)


def split_equation(equation: Optional[str], variables: Optional[list[str]]) -> sp.Equality:
    if equation is None:
        raise MathSolverValidationError("equation is required.")
    if "=" not in equation:
        raise MathSolverValidationError("equation must contain '='.")
    left, right = equation.split("=", 1)
    return sp.Eq(parse_expression(left, variables), parse_expression(right, variables))


def compute_symbolic(task: str, data: Any) -> Any:
    variable = parse_variable(data.variable)
    variable_names = data.variables or [str(variable)]

    if task == "simplify":
        return sp.simplify(parse_expression(data.expression, variable_names))
    if task == "expand":
        return sp.expand(parse_expression(data.expression, variable_names))
    if task == "factor":
        return sp.factor(parse_expression(data.expression, variable_names))
    if task == "numeric":
        return sp.N(parse_expression(data.expression, variable_names))
    if task == "differentiate":
        return sp.diff(parse_expression(data.expression, variable_names), variable)
    if task == "integrate":
        return sp.integrate(parse_expression(data.expression, variable_names), variable)
    if task == "definite_integral":
        lower = parse_bound(data.lower_bound, "lower_bound", variable_names)
        upper = parse_bound(data.upper_bound, "upper_bound", variable_names)
        return sp.integrate(
            parse_expression(data.expression, variable_names),
            (variable, lower, upper),
        )
    if task == "limit":
        point = parse_bound(data.point, "point", variable_names)
        return sp.limit(parse_expression(data.expression, variable_names), variable, point)
    if task == "taylor_series":
        if data.order is None:
            raise MathSolverValidationError("order is required.")
        if data.order < 1 or data.order > 50:
            raise MathSolverValidationError("order must be between 1 and 50.")
        point = parse_bound(data.point, "point", variable_names)
        return sp.series(
            parse_expression(data.expression, variable_names),
            variable,
            point,
            data.order,
        )
    if task == "solve_equation":
        equation = split_equation(data.equation, variable_names)
        return sp.solve(equation, variable)
    if task == "solve_system":
        if data.equations is None:
            raise MathSolverValidationError("equations is required.")
        if data.variables is None:
            raise MathSolverValidationError("variables is required.")
        symbols = parse_variables(data.variables)
        equations = [split_equation(equation, data.variables) for equation in data.equations]
        return sp.solve(equations, symbols, dict=True)
    if task == "summation":
        lower = parse_bound(data.lower, "lower", variable_names)
        upper = parse_bound(data.upper, "upper", variable_names)
        return sp.summation(
            parse_expression(data.expression, variable_names),
            (variable, lower, upper),
        )

    raise MathSolverValidationError(f"unsupported python math task: {task}")
