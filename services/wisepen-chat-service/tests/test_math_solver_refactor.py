import asyncio
from dataclasses import is_dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from chat.application.tools.services.math_solver.models import MathSolverResult
from chat.application.tools.services.math_solver.python_runtime.service import PythonMathSolverService
from chat.application.tools.services.math_solver.sage_runtime.client import SageRuntimeClient
from chat.application.tools.services.math_solver.sage_runtime.schemas import SageComputeResponse
from chat.application.tools.services.math_solver.sage_runtime.service import SageMathSolverService
from chat.application.tools.math_solver.python_math_solver_tool import PythonMathSolverTool
from chat.application.tools.math_solver.sage_math_solver_tool import SageMathSolverTool
from chat.application.tools.math_solver.schemas import (
    PythonMathSolverInput,
    SageMathSolverInput,
)


def test_python_math_solver_expand_success() -> None:
    tool = PythonMathSolverTool(service=PythonMathSolverService())
    try:
        result = asyncio.run(
            tool.execute(
                {},
                task="expand",
                expression="(x + 1)^2",
                variable="x",
            )
        )
    finally:
        asyncio.run(tool.close())

    assert "[Tool Result] python_math_solver" in result
    assert "Backend: python" in result
    assert "Task: expand" in result
    assert "x**2 + 2*x + 1" in result


def test_python_math_solver_differentiate_success() -> None:
    tool = PythonMathSolverTool(service=PythonMathSolverService())
    try:
        result = asyncio.run(
            tool.execute(
                {},
                task="differentiate",
                expression="x^3",
                variable="x",
            )
        )
    finally:
        asyncio.run(tool.close())

    assert "Task: differentiate" in result
    assert "3*x**2" in result


def test_python_math_solver_definite_integral_success() -> None:
    tool = PythonMathSolverTool(service=PythonMathSolverService())
    try:
        result = asyncio.run(
            tool.execute(
                {},
                task="definite_integral",
                expression="x",
                variable="x",
                lower_bound="0",
                upper_bound="2",
            )
        )
    finally:
        asyncio.run(tool.close())

    assert "Task: definite_integral" in result
    assert "Exact result: 2" in result
    assert "Numeric result: 2" in result


def test_python_math_solver_matrix_rank_success() -> None:
    tool = PythonMathSolverTool(service=PythonMathSolverService())
    try:
        result = asyncio.run(
            tool.execute(
                {},
                task="matrix_rank",
                matrix=[[1, 2], [2, 4]],
            )
        )
    finally:
        asyncio.run(tool.close())

    assert "Task: matrix_rank" in result
    assert "Exact result: 1" in result


def test_python_math_solver_numeric_root_success() -> None:
    tool = PythonMathSolverTool(service=PythonMathSolverService())
    try:
        result = asyncio.run(
            tool.execute(
                {},
                task="numeric_root",
                expression="x^2 - 4",
                variable="x",
                lower="0",
                upper="3",
            )
        )
    finally:
        asyncio.run(tool.close())

    assert "Task: numeric_root" in result
    assert "Exact result: 2" in result


def test_python_math_solver_rejects_sage_task() -> None:
    tool = PythonMathSolverTool(service=PythonMathSolverService())
    try:
        result = asyncio.run(tool.execute({}, task="finite_field_basic", field="GF(5)"))
    finally:
        asyncio.run(tool.close())

    assert "[Tool Error] python_math_solver failed" in result
    assert "Use sage_math_solver" in result
    assert "Retryable: false" in result


def test_sage_math_solver_rejects_python_task() -> None:
    tool = SageMathSolverTool(service=_FakeSageService())
    result = asyncio.run(tool.execute({}, task="expand", expression="(x + 1)^2"))

    assert "[Tool Error] sage_math_solver failed" in result
    assert "Use python_math_solver" in result
    assert "Retryable: false" in result


def test_sage_math_solver_service_forwards_high_level_tasks() -> None:
    client = _FakeSageClient()
    service = SageMathSolverService(client=client)

    requests = [
        SageMathSolverInput(task="finite_field_basic", field="GF(5)"),
        SageMathSolverInput(task="modular_inverse", base=3, modulus=11),
        SageMathSolverInput(
            task="polynomial_factor_over_field",
            polynomial="x^2 + 1",
            field="GF(5)",
        ),
        SageMathSolverInput(task="matrix_smith_form", matrix=[[2, 4], [6, 8]]),
    ]

    for request in requests:
        result = asyncio.run(service.solve(request))
        assert result.backend == "sage"
        assert result.task == request.task

    assert [request.task for request in client.requests] == [
        "finite_field_basic",
        "modular_inverse",
        "polynomial_factor_over_field",
        "matrix_smith_form",
    ]


def test_tool_schema_rejects_unknown_field_and_explicit_null() -> None:
    with pytest.raises(ValidationError):
        PythonMathSolverInput.model_validate(
            {"task": "expand", "expression": "x + 1", "unknown": "x"}
        )

    with pytest.raises(ValidationError):
        SageMathSolverInput.model_validate({"task": "finite_field_basic", "field": None})


def test_tool_schema_rejects_numeric_coercion_and_bool_int() -> None:
    with pytest.raises(ValidationError):
        SageMathSolverInput.model_validate({"task": "modular_inverse", "base": "5", "modulus": 7})

    with pytest.raises(ValidationError):
        SageMathSolverInput.model_validate({"task": "modular_inverse", "base": 5.0, "modulus": 7})

    with pytest.raises(ValidationError):
        SageMathSolverInput.model_validate({"task": "modular_inverse", "base": True, "modulus": 7})


def test_static_boundaries() -> None:
    root = Path(__file__).resolve().parents[1]
    tool_dir = root / "src" / "chat" / "application" / "tools" / "math_solver"
    service_dir = (
        root / "src" / "chat" / "application" / "tools" / "services" / "math_solver"
    )

    assert "httpx" not in (tool_dir / "python_math_solver_tool.py").read_text()
    assert "httpx" not in (tool_dir / "sage_math_solver_tool.py").read_text()
    assert "SageRuntimeClient" not in (
        service_dir / "python_runtime" / "service.py"
    ).read_text()
    assert "SAGE_MATH_WORKER_URL" not in (
        service_dir / "python_runtime" / "service.py"
    ).read_text()
    assert ".rstrip(" not in (service_dir / "sage_runtime" / "client.py").read_text()
    assert is_dataclass(MathSolverResult)


def test_sage_client_rejects_trailing_slash_without_stripping() -> None:
    with pytest.raises(ValueError):
        SageRuntimeClient(
            base_url="http://sage-math-worker:8000/",
            timeout_seconds=10,
        )


class _FakeSageClient:
    def __init__(self) -> None:
        self.requests = []

    async def compute_async(self, request):
        self.requests.append(request)
        return SageComputeResponse(
            status="ok",
            task=request.task,
            exact_result="ok",
            numeric_result=None,
            latex_result="ok",
        )

    async def close(self) -> None:
        return None


class _FakeSageService:
    async def solve(self, request):
        return MathSolverResult(task=request.task, backend="sage", exact_result="ok")

    async def close(self) -> None:
        return None
