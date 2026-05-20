import re
from typing import Any

from chat.application.tools.services.math_solver.errors import (
    MathSolverError,
    MathSolverValidationError,
)
from chat.application.tools.services.math_solver.models import MathSolverResult
from chat.application.tools.services.math_solver.sage_runtime.client import SageRuntimeClient
from chat.application.tools.services.math_solver.sage_runtime.schemas import SageComputeRequest


_FIELD_PATTERN = re.compile(r"^GF\((\d+)\)$")

_NUMBER_THEORY_INTEGER_TASKS = frozenset({
    "prime_factorization",
    "is_prime",
    "next_prime",
    "euler_phi",
    "divisors",
    "sigma",
    "moebius",
})

_POLYNOMIAL_FIELD_TASKS = frozenset({
    "polynomial_factor_over_field",
    "polynomial_roots_over_field",
    "polynomial_is_irreducible_over_field",
})

_POLYNOMIAL_TWO_FIELD_TASKS = frozenset({
    "polynomial_gcd_over_field",
})

_POLYNOMIAL_TWO_TASKS = frozenset({
    "polynomial_resultant",
    "polynomial_quotient_remainder",
})

_POLYNOMIAL_ONE_TASKS = frozenset({
    "polynomial_discriminant",
    "polynomial_squarefree_decomposition",
})

_MATRIX_TASKS = frozenset({
    "matrix_smith_form",
    "matrix_hermite_form",
    "matrix_minimal_polynomial",
    "matrix_characteristic_polynomial",
    "matrix_kernel",
    "matrix_image",
})


class SageMathSolverService:
    def __init__(self, client: SageRuntimeClient | None = None) -> None:
        self._client = client or SageRuntimeClient()

    async def solve(self, request: Any) -> MathSolverResult:
        self._validate_request(request)
        payload = request.model_dump(exclude_none=True)
        sage_request = SageComputeRequest.model_validate(payload)
        response = await self._client.compute_async(sage_request)

        if response.status != "ok":
            raise MathSolverError(response.error or "SageMath worker error.")

        notes = list(response.warnings)
        if response.metadata:
            notes.append(f"metadata: {response.metadata}")

        return MathSolverResult(
            task=request.task,
            backend="sage",
            exact_result=response.exact_result,
            numeric_result=response.numeric_result,
            latex_result=response.latex_result,
            notes=notes,
        )

    async def close(self) -> None:
        await self._client.close()

    def _validate_request(self, request: Any) -> None:
        if request.field is not None:
            _validate_field_or_ring(request.field, "field")
        if request.ring is not None:
            _validate_field_or_ring(request.ring, "ring")

        task = request.task
        if task == "modular_arithmetic":
            _require(request, "base", "exponent", "modulus")
        elif task == "modular_inverse":
            _require(request, "base", "modulus")
        elif task in {"gcd", "lcm"}:
            _require(request, "integers")
        elif task == "xgcd":
            _require(request, "integers")
            if len(request.integers) != 2:
                raise MathSolverValidationError("integers must contain exactly two values.")
        elif task in _NUMBER_THEORY_INTEGER_TASKS:
            _require(request, "integer")
        elif task == "crt":
            _require(request, "residues", "moduli")
            if len(request.residues) != len(request.moduli):
                raise MathSolverValidationError(
                    "residues and moduli must have the same length."
                )
        elif task == "finite_field_basic":
            _require(request, "field")
        elif task == "finite_field_operation":
            _validate_finite_field_operation(request)
        elif task in _POLYNOMIAL_FIELD_TASKS:
            _require(request, "polynomial", "field")
        elif task in _POLYNOMIAL_TWO_FIELD_TASKS:
            _require(request, "polynomial_a", "polynomial_b", "field")
        elif task in _POLYNOMIAL_TWO_TASKS:
            _require(request, "polynomial_a", "polynomial_b")
        elif task in _POLYNOMIAL_ONE_TASKS:
            _require(request, "polynomial")
        elif task in _MATRIX_TASKS:
            _require(request, "matrix")
        else:
            raise MathSolverValidationError(
                f"unsupported sage math task: {task}",
                retryable=False,
            )


def _require(request: Any, *field_names: str) -> None:
    missing = [name for name in field_names if getattr(request, name) is None]
    if missing:
        raise MathSolverValidationError(f"{', '.join(missing)} is required.")


def _validate_finite_field_operation(request: Any) -> None:
    _require(request, "field", "operation")
    if request.operation not in {"add", "sub", "mul", "div", "pow", "neg"}:
        raise MathSolverValidationError("operation must be add, sub, mul, div, pow, or neg.")
    if request.operation == "neg":
        _require(request, "element")
    else:
        _require(request, "element_a", "element_b")


def _validate_field_or_ring(value: str, name: str) -> None:
    if value in {"ZZ", "QQ"}:
        return
    match = _FIELD_PATTERN.match(value)
    if match and int(match.group(1)) > 1:
        return
    raise MathSolverValidationError(f"{name} must be ZZ, QQ, or GF(q).")
