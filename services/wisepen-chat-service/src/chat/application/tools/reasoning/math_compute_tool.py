from typing import Any, Dict, Optional, Tuple

import httpx

from chat.application.math_reasoning.common.errors import MathEngineError
from chat.application.math_reasoning.common.formatting import format_math_result
from chat.application.math_reasoning.compute.service import MathComputeService
from chat.application.math_reasoning.config import MATH_SAGE_TIMEOUT_SECONDS
from chat.core.config.app_settings import settings
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_event


_SAGE_TASKS = {
    "symbolic_simplify",
    "symbolic_expand",
    "symbolic_factor",
    "symbolic_collect",
    "symbolic_partial_fraction",
    "symbolic_numerator_denominator",
    "symbolic_substitute",
    "differentiate",
    "integrate",
    "definite_integral",
    "limit",
    "taylor_series",
    "solve_equation",
    "solve_system",
    "matrix_determinant",
    "matrix_trace",
    "matrix_characteristic_polynomial",
    "matrix_minimal_polynomial",
    "matrix_power",
    "matrix_power_entry",
    "matrix_inverse",
    "matrix_rref",
    "matrix_rank",
    "matrix_eigenvalues",
    "matrix_eigenvectors",
    "matrix_kernel",
    "matrix_image",
    "matrix_transpose",
    "matrix_solve",
    "matrix_smith_form",
    "matrix_hermite_form",
    "modular_arithmetic",
    "modular_inverse",
    "gcd",
    "lcm",
    "xgcd",
    "prime_factorization",
    "is_prime",
    "next_prime",
    "euler_phi",
    "divisors",
    "sigma",
    "moebius",
    "crt",
    "polynomial_factor",
    "polynomial_expand",
    "polynomial_derivative",
    "polynomial_integral",
    "polynomial_resultant",
    "polynomial_gcd",
    "polynomial_lcm",
    "polynomial_discriminant",
    "polynomial_roots",
    "polynomial_degree",
    "polynomial_coefficients",
    "polynomial_evaluate",
    "polynomial_quotient_remainder",
    "polynomial_squarefree_decomposition",
    "polynomial_factor_over_field",
    "polynomial_roots_over_field",
    "polynomial_gcd_over_field",
    "polynomial_is_irreducible_over_field",
    "finite_field_basic",
    "finite_field_operation",
    "binomial",
    "factorial",
}

_SAGE_TASK_ALIASES = {
    "simplify": "symbolic_simplify",
    "expand": "symbolic_expand",
    "factor": "symbolic_factor",
    "series": "taylor_series",
    "determinant": "matrix_determinant",
    "inverse": "matrix_inverse",
    "rref": "matrix_rref",
    "rank": "matrix_rank",
    "eigen": "matrix_eigenvalues",
    "linear_solve": "matrix_solve",
    "combination": "binomial",
}

_LOCAL_ONLY_TASKS = {
    "numeric",
    "diagonalize",
    "matrix_multiply",
    "permutation",
    "summation",
    "binomial_probability",
    "poisson_probability",
    "normal_cdf_numeric",
    "expectation",
    "variance",
    "numeric_root",
    "numeric_minimize",
}

_SAGE_PAYLOAD_KEYS = {
    "task",
    "expression",
    "variable",
    "point",
    "order",
    "lower_bound",
    "upper_bound",
    "substitutions",
    "equation",
    "equations",
    "variables",
    "matrix",
    "vector",
    "matrix_power",
    "row_index",
    "column_index",
    "ring",
    "integer",
    "integers",
    "base",
    "exponent",
    "modulus",
    "residues",
    "moduli",
    "n",
    "k",
    "polynomial",
    "polynomial_a",
    "polynomial_b",
    "field",
    "evaluate_at",
    "operation",
    "element",
    "element_a",
    "element_b",
}

_LOCAL_PAYLOAD_KEYS = [
    "expression",
    "variable",
    "variables",
    "point",
    "order",
    "matrix",
    "matrix_b",
    "n",
    "k",
    "probability",
    "lower",
    "upper",
    "base",
    "exponent",
    "modulus",
    "polynomial",
    "field",
]

_ALL_TASKS = [
    "simplify",
    "expand",
    "factor",
    "solve",
    "limit",
    "differentiate",
    "integrate",
    "series",
    "numeric",
    "determinant",
    "rank",
    "inverse",
    "eigen",
    "diagonalize",
    "rref",
    "linear_solve",
    "matrix_multiply",
    "factorial",
    "combination",
    "permutation",
    "summation",
    "binomial_probability",
    "poisson_probability",
    "normal_cdf_numeric",
    "expectation",
    "variance",
    "numeric_root",
    "numeric_minimize",
    *_SAGE_TASKS,
]

_MATH_COMPUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "task": {
            "type": "string",
            "enum": sorted(set(_ALL_TASKS)),
            "description": (
                "Mathematical computation task. "
                "Use Sage-specific tasks for advanced symbolic algebra, calculus, matrix algebra, number theory, "
                "finite fields, and exact polynomial arithmetic."
            ),
        },
        "expression": {"type": "string"},
        "variable": {"type": "string"},
        "variables": {"type": "array", "items": {"type": "string"}},
        "point": {"type": "string"},
        "order": {"type": "integer", "minimum": 1, "maximum": 50},
        "lower": {"type": "string"},
        "upper": {"type": "string"},
        "lower_bound": {"type": "string"},
        "upper_bound": {"type": "string"},
        "substitutions": {
            "type": "object",
            "additionalProperties": {"type": "string"},
        },
        "equation": {"type": "string"},
        "equations": {"type": "array", "items": {"type": "string"}},
        "matrix": {
            "type": "array",
            "items": {
                "type": "array",
                "items": {
                    "anyOf": [
                        {"type": "number"},
                        {"type": "string"},
                    ]
                },
            },
        },
        "matrix_b": {
            "type": "array",
            "items": {
                "type": "array",
                "items": {
                    "anyOf": [
                        {"type": "number"},
                        {"type": "string"},
                    ]
                },
            },
        },
        "vector": {
            "type": "array",
            "items": {
                "anyOf": [
                    {"type": "number"},
                    {"type": "string"},
                ]
            },
        },
        "matrix_power": {"type": "integer", "minimum": 0},
        "row_index": {"type": "integer", "minimum": 0},
        "column_index": {"type": "integer", "minimum": 0},
        "ring": {
            "type": "string",
            "description": "Base ring, e.g. ZZ, QQ, or GF(5).",
        },
        "integer": {"type": "integer"},
        "integers": {"type": "array", "items": {"type": "integer"}},
        "base": {"type": "integer"},
        "exponent": {"type": "integer"},
        "modulus": {"type": "integer"},
        "residues": {"type": "array", "items": {"type": "integer"}},
        "moduli": {"type": "array", "items": {"type": "integer"}},
        "n": {"type": "integer"},
        "k": {"type": "integer"},
        "probability": {"type": "string"},
        "polynomial": {"type": "string"},
        "polynomial_a": {"type": "string"},
        "polynomial_b": {"type": "string"},
        "field": {"type": "string"},
        "evaluate_at": {"type": "string"},
        "operation": {
            "type": "string",
            "enum": ["add", "sub", "mul", "div", "pow", "neg"],
        },
        "element": {"type": "string"},
        "element_a": {"type": "string"},
        "element_b": {"type": "string"},
    },
    "required": ["task"],
    "additionalProperties": False,
}

_MATH_COMPUTE_DESCRIPTION = (
    "Performs deterministic mathematical computation using SymPy, SciPy, and SageMath. "
    "Use this tool for symbolic algebra, calculus, equation solving, matrix algebra, exact polynomial arithmetic, "
    "finite fields, number theory, combinatorics, probability/statistics, numerical roots, and numerical optimization.\n\n"
    "This tool computes mathematical results. It is not a formal proof verifier. "
    "For proof questions, use computed results only as supporting evidence and write the reasoning separately.\n\n"
    "The SageMath backend exposes a broad whitelist of CAS tasks, but it does not execute arbitrary Sage or Python code."
)


class MathComputeTool(BaseTool):
    def __init__(self, service: Optional[MathComputeService] = None) -> None:
        self._service = service or MathComputeService()

    @property
    def name(self) -> str:
        return "math_compute"

    @property
    def description(self) -> str:
        return _MATH_COMPUTE_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _MATH_COMPUTE_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        task = kwargs.get("task", "")
        log_event("math_compute_tool", task=task)

        if not isinstance(task, str) or not task:
            return "[Tool Error] math_compute failed: task is required."

        normalized_task = _normalize_sage_task(task, kwargs)

        if _should_route_to_sage(task, normalized_task):
            sage_payload = _build_sage_payload(normalized_task, kwargs)
            return await self._execute_sage_task(sage_payload)

        kwargs_dict = {
            key: kwargs[key]
            for key in _LOCAL_PAYLOAD_KEYS
            if key in kwargs
        }

        try:
            result = await self._service.compute_async(task, **kwargs_dict)
            return format_math_result(result)
        except MathEngineError as e:
            return f"[Tool Error] math_compute {task} failed: {e}"

    async def _execute_sage_task(self, payload: Dict[str, Any]) -> str:
        task = payload["task"]

        enabled, worker_url, timeout = _read_sage_settings()
        if not enabled:
            return (
                f"[Tool Error] math_compute {task} failed: "
                "SageMath worker is not enabled. "
                "Set MATH_SAGE_ENABLED=true and MATH_SAGE_WORKER_URL to use Sage tasks."
            )

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{worker_url}/compute",
                    json=_compact_payload(payload),
                )
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            return (
                f"[Tool Error] math_compute {task} failed: "
                f"SageMath worker request failed: {e}"
            )

        return _format_sage_result(data)

    async def close(self) -> None:
        await self._service.close()


def _read_sage_settings() -> Tuple[bool, str, float]:
    if not hasattr(settings, "MATH_SAGE_ENABLED"):
        raise RuntimeError("MATH_SAGE_ENABLED setting is missing.")

    enabled = settings.MATH_SAGE_ENABLED
    worker_url = settings.MATH_SAGE_WORKER_URL
    timeout = MATH_SAGE_TIMEOUT_SECONDS

    if not isinstance(enabled, bool):
        raise RuntimeError("MATH_SAGE_ENABLED must be a boolean setting.")

    if not isinstance(worker_url, str) or not worker_url:
        raise RuntimeError("MATH_SAGE_WORKER_URL config must be a non-empty string.")

    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise RuntimeError("MATH_SAGE_TIMEOUT_SECONDS config must be a number.")

    return enabled, worker_url.rstrip("/"), float(timeout)


def _should_route_to_sage(original_task: str, normalized_task: str) -> bool:
    if original_task in _LOCAL_ONLY_TASKS:
        return False

    return normalized_task in _SAGE_TASKS


def _normalize_sage_task(task: str, kwargs: Dict[str, Any]) -> str:
    if task == "solve":
        if kwargs.get("equations") is not None:
            return "solve_system"
        if kwargs.get("equation") is not None or kwargs.get("expression") is not None:
            return "solve_equation"
        return task

    if task == "integrate":
        if (
            kwargs.get("lower_bound") is not None
            or kwargs.get("upper_bound") is not None
            or kwargs.get("lower") is not None
            or kwargs.get("upper") is not None
        ):
            return "definite_integral"
        return "integrate"

    return _SAGE_TASK_ALIASES.get(task, task)


def _build_sage_payload(task: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"task": task}

    for key in _SAGE_PAYLOAD_KEYS:
        if key == "task":
            continue
        if key in kwargs:
            payload[key] = kwargs[key]

    if task == "definite_integral":
        if "lower_bound" not in payload and kwargs.get("lower") is not None:
            payload["lower_bound"] = kwargs["lower"]
        if "upper_bound" not in payload and kwargs.get("upper") is not None:
            payload["upper_bound"] = kwargs["upper"]

    if task == "solve_equation":
        if "equation" not in payload and kwargs.get("expression") is not None:
            payload["equation"] = f"{kwargs['expression']} = 0"

    return _compact_payload(payload)


def _compact_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None
    }


def _format_sage_result(data: Dict[str, Any]) -> str:
    task = data.get("task", "unknown")
    status = data.get("status", "error")

    if status != "ok":
        error = data.get("error") or "unknown SageMath worker error"
        return f"[Tool Error] math_compute {task} failed: {error}"

    exact_result = data.get("exact_result")
    numeric_result = data.get("numeric_result")
    latex_result = data.get("latex_result")
    metadata = data.get("metadata") or {}
    warnings = data.get("warnings") or []

    lines = [
        "[Tool Result] math_compute",
        "",
        "Backend: sage",
        f"Task: {task}",
    ]

    if exact_result is not None:
        lines.append(f"Exact result: {exact_result}")

    if numeric_result is not None:
        lines.append(f"Numeric result: {numeric_result}")

    if latex_result is not None:
        lines.append(f"LaTeX result: {latex_result}")

    if metadata:
        lines.append(f"Metadata: {metadata}")

    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in warnings:
            lines.append(f"- {warning}")

    lines.extend(
        [
            "",
            "Assistant instructions:",
            "- Use the SageMath result as the authoritative computation result.",
            "- If the result is symbolic, preserve the exact form before giving approximations.",
            "- Explain the mathematical steps briefly if the user asked for reasoning.",
            "- This result is a computation result, not a formal proof certificate.",
        ]
    )

    return "\n".join(lines)
