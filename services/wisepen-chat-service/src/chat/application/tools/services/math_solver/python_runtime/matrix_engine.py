from typing import Any, Optional

import sympy as sp

from chat.application.tools.services.math_solver.errors import MathSolverValidationError


MAX_MATRIX_SIZE = 20


def _parse_matrix(value: Optional[list[list[Any]]], name: str) -> sp.Matrix:
    if value is None:
        raise MathSolverValidationError(f"{name} is required.")
    if not value:
        raise MathSolverValidationError(f"{name} must be a non-empty matrix.")
    if len(value) > MAX_MATRIX_SIZE:
        raise MathSolverValidationError(f"{name} exceeds maximum matrix size.")

    width: Optional[int] = None
    rows: list[list[Any]] = []
    for row in value:
        if not row:
            raise MathSolverValidationError(f"{name} rows must not be empty.")
        if width is None:
            width = len(row)
        if len(row) != width:
            raise MathSolverValidationError(f"{name} rows must have the same length.")
        if len(row) > MAX_MATRIX_SIZE:
            raise MathSolverValidationError(f"{name} exceeds maximum matrix size.")
        rows.append([sp.sympify(item) for item in row])
    return sp.Matrix(rows)


def _parse_vector(value: Optional[list[Any]], name: str) -> sp.Matrix:
    if value is None:
        raise MathSolverValidationError(f"{name} is required.")
    if not value:
        raise MathSolverValidationError(f"{name} must be non-empty.")
    if len(value) > MAX_MATRIX_SIZE:
        raise MathSolverValidationError(f"{name} exceeds maximum vector size.")
    return sp.Matrix([sp.sympify(item) for item in value])


def compute_matrix(task: str, data: Any) -> Any:
    matrix = _parse_matrix(data.matrix, "matrix")

    if task == "matrix_determinant":
        return matrix.det()
    if task == "matrix_trace":
        return matrix.trace()
    if task == "matrix_rank":
        return matrix.rank()
    if task == "matrix_inverse":
        if matrix.det() == 0:
            raise MathSolverValidationError("matrix is not invertible.")
        return matrix.inv()
    if task == "matrix_rref":
        reduced, pivots = matrix.rref()
        return {"matrix": reduced, "pivots": list(pivots)}
    if task == "matrix_eigenvalues":
        return matrix.eigenvals()
    if task == "matrix_multiply":
        return matrix * _parse_matrix(data.matrix_b, "matrix_b")
    if task == "matrix_solve":
        if data.vector is not None:
            rhs = _parse_vector(data.vector, "vector")
        else:
            rhs = _parse_matrix(data.matrix_b, "matrix_b")
        return matrix.gauss_jordan_solve(rhs)[0]

    raise MathSolverValidationError(f"unsupported matrix task: {task}")
