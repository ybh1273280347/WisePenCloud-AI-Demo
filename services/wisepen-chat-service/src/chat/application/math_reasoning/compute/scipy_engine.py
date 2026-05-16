from __future__ import annotations

from typing import Any, Dict, Tuple

import sympy as sp
from chat.application.math_reasoning.common.errors import MathValidationError
from chat.application.math_reasoning.common.expression_parser import parse_math_expr
from chat.application.math_reasoning.compute.sympy_engine import parse_variable


def compute_scipy(task: str, kwargs: Dict[str, Any]) -> Tuple[Any, float | str]:
    try:
        from scipy import optimize, stats
    except Exception as e:
        raise MathValidationError(f"SciPy is unavailable: {e}") from e

    if task == "normal_cdf_numeric":
        raw_x = kwargs.get("point", kwargs.get("expression"))
        if raw_x is None:
            raise MathValidationError(
                "point or expression is required for normal_cdf_numeric."
            )
        x = float(parse_math_expr(str(raw_x), []))
        numeric = float(stats.norm.cdf(x))
        return numeric, numeric

    if task == "poisson_probability":
        if kwargs.get("n") is None or kwargs.get("k") is None:
            raise MathValidationError(
                "n (lambda) and k are required for poisson_probability."
            )
        lam = float(kwargs["n"])
        k = int(kwargs["k"])
        numeric = float(stats.poisson.pmf(k, lam))
        return numeric, numeric

    if task == "numeric_root":
        variable = parse_variable(kwargs.get("variable"), default="x")
        expression = kwargs.get("expression")
        if not isinstance(expression, str):
            raise MathValidationError("expression is required.")
        expr = parse_math_expr(expression, [str(variable)])
        func = sp.lambdify(variable, expr, modules=["numpy"])
        lower = kwargs.get("lower")
        upper = kwargs.get("upper")
        if lower is not None and upper is not None:
            root = optimize.root_scalar(
                func,
                bracket=[
                    float(parse_math_expr(str(lower), [])),
                    float(parse_math_expr(str(upper), [])),
                ],
            )
            if not root.converged:
                raise MathValidationError("numeric root search did not converge.")
            return root.root, float(root.root)
        start = float(parse_math_expr(str(kwargs.get("point", "0")), []))
        root = optimize.root(lambda values: [func(values[0])], [start])
        if not root.success:
            raise MathValidationError("numeric root search did not converge.")
        return float(root.x[0]), float(root.x[0])

    if task == "numeric_minimize":
        variable = parse_variable(kwargs.get("variable"), default="x")
        expression = kwargs.get("expression")
        if not isinstance(expression, str):
            raise MathValidationError("expression is required.")
        expr = parse_math_expr(expression, [str(variable)])
        func = sp.lambdify(variable, expr, modules=["numpy"])
        lower = float(parse_math_expr(str(kwargs.get("lower", "-10")), []))
        upper = float(parse_math_expr(str(kwargs.get("upper", "10")), []))
        result = optimize.minimize_scalar(func, bounds=(lower, upper), method="bounded")
        if not result.success:
            raise MathValidationError("numeric minimization did not converge.")
        exact = {"x": float(result.x), "fun": float(result.fun)}
        return exact, float(result.fun)

    raise MathValidationError(f"unsupported math task: {task}")
