from typing import Any, Dict

from pydantic import ValidationError

from chat.application.tools.services.math_solver.errors import MathSolverError
from chat.application.tools.services.math_solver.formatting import (
    format_math_solver_error,
    format_math_solver_result,
)
from chat.application.tools.services.math_solver.sage_runtime import SageMathSolverService
from chat.application.tools.math_solver.schemas import (
    PYTHON_MATH_TASKS,
    SAGE_MATH_TASKS,
    SageMathSolverInput,
)
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_event


_DESCRIPTION = (
    "Performs deterministic advanced exact math through the SageMath worker. Use this only "
    "for number theory, modular arithmetic, finite fields, exact polynomial algebra over "
    "fields, and advanced exact matrix algebra such as Smith or Hermite normal forms. "
    "This tool does not execute arbitrary Sage code and does not handle ordinary symbolic "
    "or numeric tasks; use python_math_solver for ordinary computation."
)


class SageMathSolverTool(BaseTool):
    def __init__(self, service: SageMathSolverService | None = None) -> None:
        self._service = service or SageMathSolverService()

    @property
    def name(self) -> str:
        return "sage_math_solver"

    @property
    def description(self) -> str:
        return _DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return SageMathSolverInput.model_json_schema()

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        task = kwargs.get("task") if isinstance(kwargs.get("task"), str) else "unknown"

        if task in PYTHON_MATH_TASKS:
            return format_math_solver_error(
                self.name,
                task,
                "ordinary symbolic computation is not supported by sage_math_solver. Use python_math_solver.",
                False,
            )

        try:
            request = SageMathSolverInput.model_validate(kwargs)
        except ValidationError as exc:
            return format_math_solver_error(
                self.name,
                task,
                _validation_reason(exc),
                True,
            )

        if request.task not in SAGE_MATH_TASKS:
            return format_math_solver_error(
                self.name,
                request.task,
                f"unsupported sage math task: {request.task}",
                False,
            )

        log_event("sage_math_solver_tool", task=request.task)
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


__all__ = ["SageMathSolverTool", "SAGE_MATH_TASKS"]
