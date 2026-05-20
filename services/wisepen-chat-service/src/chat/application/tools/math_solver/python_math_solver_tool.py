from typing import Any, Dict

from pydantic import ValidationError

from chat.application.tools.services.math_solver.errors import MathSolverError
from chat.application.tools.services.math_solver.formatting import (
    format_math_solver_error,
    format_math_solver_result,
)
from chat.application.tools.services.math_solver.python_runtime import PythonMathSolverService
from chat.application.tools.math_solver.schemas import (
    PYTHON_MATH_TASKS,
    SAGE_MATH_TASKS,
    PythonMathSolverInput,
)
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_event


_DESCRIPTION = (
    "Performs deterministic ordinary mathematical computation using Python math libraries "
    "such as SymPy, NumPy, SciPy, and mpmath. Use this for ordinary algebra, calculus, "
    "equations, matrices, combinatorics, probability/statistics, numeric roots, and numeric "
    "optimization. This tool does not execute arbitrary Python code, does not use pandas, "
    "does not draw plots, and does not call SageMath."
)


class PythonMathSolverTool(BaseTool):
    def __init__(self, service: PythonMathSolverService | None = None) -> None:
        self._service = service or PythonMathSolverService()

    @property
    def name(self) -> str:
        return "python_math_solver"

    @property
    def description(self) -> str:
        return _DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return PythonMathSolverInput.model_json_schema()

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        task = kwargs.get("task") if isinstance(kwargs.get("task"), str) else "unknown"

        if task in SAGE_MATH_TASKS:
            return format_math_solver_error(
                self.name,
                task,
                "advanced exact math is not supported by python_math_solver. Use sage_math_solver.",
                False,
            )

        try:
            request = PythonMathSolverInput.model_validate(kwargs)
        except ValidationError as exc:
            return format_math_solver_error(
                self.name,
                task,
                _validation_reason(exc),
                True,
            )

        log_event("python_math_solver_tool", task=request.task)
        try:
            result = await self._service.solve(request)
            return format_math_solver_result(self.name, result)
        except MathSolverError as exc:
            return format_math_solver_error(
                self.name,
                request.task,
                exc.message,
                exc.retryable,
            )

    async def close(self) -> None:
        await self._service.close()


def _validation_reason(exc: ValidationError) -> str:
    first = exc.errors()[0]
    loc = ".".join(str(item) for item in first.get("loc", ()))
    message = first.get("msg", "invalid arguments")
    if loc:
        return f"{loc}: {message}"
    return str(message)


__all__ = ["PythonMathSolverTool", "PYTHON_MATH_TASKS"]
