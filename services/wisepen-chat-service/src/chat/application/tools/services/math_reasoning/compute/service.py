import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Dict, List, Optional

import sympy as sp

from common.logger import log_event, log_error

from ..common.errors import MathEngineError, MathValidationError
from ..common.expression_parser import MathParseError
from ..sage import SageClient, SageComputeRequest
from .models import MathComputeResult
from .scipy_engine import compute_scipy
from .sympy_engine import compute_sympy


_SAGE_TASKS = frozenset({
    "modular_arithmetic",
    "polynomial_factor_over_field",
    "finite_field_basic",
})

_MATRIX_TASKS = frozenset({
    "determinant", "rank", "inverse", "eigen", "diagonalize", "rref",
    "linear_solve", "matrix_multiply",
})


def _to_latex(exact: Any) -> Optional[str]:
    if exact is None:
        return None
    try:
        return sp.latex(exact)
    except Exception:
        return None


def _to_float_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return f"{float(value):.12g}"
    except (TypeError, ValueError):
        return None


class MathComputeService:
    def __init__(
        self,
        sage_client: Optional[SageClient] = None,
        *,
        max_workers: int = 2,
    ) -> None:
        self._sage = sage_client or SageClient()
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="math-compute",
        )

    def compute(self, task: str, **kwargs: Any) -> MathComputeResult:
        log_event("math_compute_service", task=task)

        if task in _SAGE_TASKS:
            return self._compute_sage(task, **kwargs)

        return self._compute_local(task, kwargs)

    async def compute_async(self, task: str, **kwargs: Any) -> MathComputeResult:
        log_event("math_compute_service", task=task)

        if task in _SAGE_TASKS:
            return await self._compute_sage_async(task, **kwargs)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._executor,
            partial(self._compute_local, task, dict(kwargs)),
        )

    async def close(self) -> None:
        await self._sage.close()
        self._executor.shutdown(wait=False, cancel_futures=True)
        log_event("MathComputeService 关闭")

    def _compute_local(self, task: str, kwargs: Dict[str, Any]) -> MathComputeResult:
        try:
            if task in _MATRIX_TASKS or task in {
                "simplify", "expand", "factor", "solve", "limit",
                "differentiate", "integrate", "series", "numeric",
                "linear_solve", "matrix_multiply", "factorial",
                "combination", "permutation", "summation",
                "binomial_probability", "expectation", "variance",
            }:
                exact = compute_sympy(task, kwargs)
                return MathComputeResult(
                    task=task,
                    exact_result=exact,
                    numeric_result=_to_float_str(exact),
                    latex_result=_to_latex(exact),
                )

            exact, numeric = compute_scipy(task, kwargs)
            return MathComputeResult(
                task=task,
                exact_result=exact,
                numeric_result=_to_float_str(numeric),
                latex_result=_to_latex(exact),
            )
        except MathValidationError as e:
            raise MathEngineError(str(e)) from e
        except MathParseError as e:
            raise MathEngineError(str(e)) from e
        except Exception as e:
            log_error("math_compute_local", e, task=task)
            raise MathEngineError(f"Computation failed: {e}") from e

    def _compute_sage(self, task: str, **kwargs: Any) -> MathComputeResult:
        if not self._sage.enabled:
            raise MathEngineError(
                "SageMath worker is not enabled. "
                "Set MATH_SAGE_ENABLED=true and MATH_SAGE_WORKER_URL to use Sage tasks."
            )

        request = SageComputeRequest(
            task=task,
            base=kwargs.get("base"),
            exponent=kwargs.get("exponent"),
            modulus=kwargs.get("modulus"),
            polynomial=kwargs.get("polynomial"),
            field=kwargs.get("field"),
        )

        try:
            response = self._sage.compute(request)
        except Exception as e:
            log_error("sage_invoke", e, task=task)
            raise MathEngineError(f"SageMath client error: {e}") from e

        if response.status != "ok":
            raise MathEngineError(response.error or f"Sage task {task} failed.")

        return MathComputeResult(
            task=task,
            exact_result=response.exact_result,
            numeric_result=response.numeric_result,
            latex_result=response.latex_result,
            notes=List(response.warnings),
        )

    async def _compute_sage_async(self, task: str, **kwargs: Any) -> MathComputeResult:
        if not self._sage.enabled:
            raise MathEngineError(
                "SageMath worker is not enabled. "
                "Set MATH_SAGE_ENABLED=true and MATH_SAGE_WORKER_URL to use Sage tasks."
            )

        request = SageComputeRequest(
            task=task,
            base=kwargs.get("base"),
            exponent=kwargs.get("exponent"),
            modulus=kwargs.get("modulus"),
            polynomial=kwargs.get("polynomial"),
            field=kwargs.get("field"),
        )

        try:
            response = await self._sage.compute_async(request)
        except Exception as e:
            log_error("sage_invoke", e, task=task)
            raise MathEngineError(f"SageMath client error: {e}") from e

        if response.status != "ok":
            raise MathEngineError(response.error or f"Sage task {task} failed.")

        return MathComputeResult(
            task=task,
            exact_result=response.exact_result,
            numeric_result=response.numeric_result,
            latex_result=response.latex_result,
            notes=List(response.warnings),
        )
