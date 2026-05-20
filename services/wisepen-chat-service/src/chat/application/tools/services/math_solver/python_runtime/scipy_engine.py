from typing import Any

import sympy as sp

from chat.application.tools.services.math_solver.errors import MathSolverValidationError
from chat.application.tools.services.math_solver.python_runtime.sympy_engine import (
    parse_bound,
    parse_expression,
    parse_variable,
)


def compute_numeric(task: str, data: Any) -> tuple[Any, float | str]:
    try:
        from scipy import optimize, stats
    except Exception as exc:
        raise MathSolverValidationError(f"SciPy is unavailable: {exc}") from exc

    if task == "poisson_probability":
        if data.n is None or data.k is None:
            raise MathSolverValidationError("n and k are required.")
        numeric = float(stats.poisson.pmf(data.k, data.n))
        return numeric, numeric

    if task == "normal_cdf":
        raw_x = data.point or data.expression
        x = float(parse_bound(raw_x, "point", []))
        numeric = float(stats.norm.cdf(x))
        return numeric, numeric

    if task == "numeric_root":
        variable = parse_variable(data.variable)
        variable_name = str(variable)
        expression = parse_expression(data.expression, [variable_name])
        func = sp.lambdify(variable, expression, modules=["numpy"])
        if data.lower is not None and data.upper is not None:
            root = optimize.root_scalar(
                func,
                bracket=[
                    float(parse_bound(data.lower, "lower", [])),
                    float(parse_bound(data.upper, "upper", [])),
                ],
            )
            if not root.converged:
                raise MathSolverValidationError("numeric root search did not converge.")
            return float(root.root), float(root.root)

        start = float(parse_bound(data.point, "point", []))
        root = optimize.root(lambda values: [func(values[0])], [start])
        if not root.success:
            raise MathSolverValidationError("numeric root search did not converge.")
        return float(root.x[0]), float(root.x[0])

    if task == "numeric_minimize":
        variable = parse_variable(data.variable)
        variable_name = str(variable)
        expression = parse_expression(data.expression, [variable_name])
        func = sp.lambdify(variable, expression, modules=["numpy"])
        lower = float(parse_bound(data.lower, "lower", []))
        upper = float(parse_bound(data.upper, "upper", []))
        result = optimize.minimize_scalar(func, bounds=(lower, upper), method="bounded")
        if not result.success:
            raise MathSolverValidationError("numeric minimization did not converge.")
        exact = {"x": float(result.x), "fun": float(result.fun)}
        return exact, float(result.fun)

    raise MathSolverValidationError(f"unsupported numeric task: {task}")
