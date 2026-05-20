import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Optional

import sympy as sp

from chat.application.tools.services.math_solver.errors import (
    MathSolverError,
    MathSolverValidationError,
)
from chat.application.tools.services.math_solver.models import MathSolverResult
from chat.application.tools.services.math_solver.python_runtime.matrix_engine import compute_matrix
from chat.application.tools.services.math_solver.python_runtime.probability_engine import (
    compute_combinatorics_or_probability,
)
from chat.application.tools.services.math_solver.python_runtime.scipy_engine import compute_numeric
from chat.application.tools.services.math_solver.python_runtime.sympy_engine import (
    compute_symbolic,
)
from common.logger import log_error, log_event


_SYMBOLIC_TASKS = frozenset({
    "simplify",
    "expand",
    "factor",
    "solve_equation",
    "solve_system",
    "differentiate",
    "integrate",
    "definite_integral",
    "limit",
    "taylor_series",
    "numeric",
    "summation",
})

_MATRIX_TASKS = frozenset({
    "matrix_determinant",
    "matrix_trace",
    "matrix_rank",
    "matrix_inverse",
    "matrix_rref",
    "matrix_eigenvalues",
    "matrix_solve",
    "matrix_multiply",
})

_COMBINATORICS_PROBABILITY_TASKS = frozenset({
    "factorial",
    "binomial",
    "permutation",
    "binomial_probability",
    "expectation",
    "variance",
})

_NUMERIC_TASKS = frozenset({
    "poisson_probability",
    "normal_cdf",
    "numeric_root",
    "numeric_minimize",
})


class PythonMathSolverService:
    def __init__(self, *, max_workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="python-math-solver",
        )

    async def solve(self, request: Any) -> MathSolverResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            partial(self._solve_sync, request),
        )

    async def close(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        log_event("PythonMathSolverService closed")

    def _solve_sync(self, request: Any) -> MathSolverResult:
        log_event("python_math_solver_service", task=request.task)
        try:
            exact: Any
            numeric_source: Optional[Any] = None

            if request.task in _SYMBOLIC_TASKS:
                exact = compute_symbolic(request.task, request)
                numeric_source = exact
            elif request.task in _MATRIX_TASKS:
                exact = compute_matrix(request.task, request)
                numeric_source = exact
            elif request.task in _COMBINATORICS_PROBABILITY_TASKS:
                exact = compute_combinatorics_or_probability(request.task, request)
                numeric_source = exact
            elif request.task in _NUMERIC_TASKS:
                exact, numeric_source = compute_numeric(request.task, request)
            else:
                raise MathSolverValidationError(
                    f"unsupported python math task: {request.task}",
                    retryable=False,
                )

            return MathSolverResult(
                task=request.task,
                backend="python",
                exact_result=exact,
                numeric_result=_to_float_str(numeric_source),
                latex_result=_to_latex(exact),
            )
        except MathSolverError:
            raise
        except Exception as exc:
            log_error("python_math_solver", exc, task=request.task)
            raise MathSolverError(f"Computation failed: {exc}") from exc


def _to_latex(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return sp.latex(value)
    except Exception:
        return None


def _to_float_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return f"{float(value):.12g}"
    except (TypeError, ValueError):
        return None
