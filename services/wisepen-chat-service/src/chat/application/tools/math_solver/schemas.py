from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, StrictFloat, StrictInt, StrictStr, model_validator


MatrixEntry = StrictInt | StrictFloat | StrictStr


PYTHON_MATH_TASKS = (
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
    "matrix_determinant",
    "matrix_trace",
    "matrix_rank",
    "matrix_inverse",
    "matrix_rref",
    "matrix_eigenvalues",
    "matrix_solve",
    "matrix_multiply",
    "factorial",
    "binomial",
    "permutation",
    "summation",
    "binomial_probability",
    "poisson_probability",
    "normal_cdf",
    "expectation",
    "variance",
    "numeric_root",
    "numeric_minimize",
)

SAGE_MATH_TASKS = (
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
    "finite_field_basic",
    "finite_field_operation",
    "polynomial_factor_over_field",
    "polynomial_roots_over_field",
    "polynomial_gcd_over_field",
    "polynomial_is_irreducible_over_field",
    "polynomial_resultant",
    "polynomial_discriminant",
    "polynomial_squarefree_decomposition",
    "polynomial_quotient_remainder",
    "matrix_smith_form",
    "matrix_hermite_form",
    "matrix_minimal_polynomial",
    "matrix_characteristic_polynomial",
    "matrix_kernel",
    "matrix_image",
)


class RejectExplicitNullMixin:
    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        null_keys = sorted(key for key, value in data.items() if value is None)
        if null_keys:
            raise ValueError(f"arguments must not be null: {', '.join(null_keys)}")

        return data


class PythonMathSolverInput(RejectExplicitNullMixin, BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    task: Literal[
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
        "matrix_determinant",
        "matrix_trace",
        "matrix_rank",
        "matrix_inverse",
        "matrix_rref",
        "matrix_eigenvalues",
        "matrix_solve",
        "matrix_multiply",
        "factorial",
        "binomial",
        "permutation",
        "summation",
        "binomial_probability",
        "poisson_probability",
        "normal_cdf",
        "expectation",
        "variance",
        "numeric_root",
        "numeric_minimize",
    ]

    expression: Optional[StrictStr] = None
    equation: Optional[StrictStr] = None
    equations: Optional[list[StrictStr]] = None
    variable: Optional[StrictStr] = None
    variables: Optional[list[StrictStr]] = None
    point: Optional[StrictStr] = None
    order: Optional[StrictInt] = None
    lower_bound: Optional[StrictStr] = None
    upper_bound: Optional[StrictStr] = None

    matrix: Optional[list[list[MatrixEntry]]] = None
    matrix_b: Optional[list[list[MatrixEntry]]] = None
    vector: Optional[list[MatrixEntry]] = None

    n: Optional[StrictInt] = None
    k: Optional[StrictInt] = None
    probability: Optional[StrictStr] = None

    lower: Optional[StrictStr] = None
    upper: Optional[StrictStr] = None


class SageMathSolverInput(RejectExplicitNullMixin, BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    task: Literal[
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
        "finite_field_basic",
        "finite_field_operation",
        "polynomial_factor_over_field",
        "polynomial_roots_over_field",
        "polynomial_gcd_over_field",
        "polynomial_is_irreducible_over_field",
        "polynomial_resultant",
        "polynomial_discriminant",
        "polynomial_squarefree_decomposition",
        "polynomial_quotient_remainder",
        "matrix_smith_form",
        "matrix_hermite_form",
        "matrix_minimal_polynomial",
        "matrix_characteristic_polynomial",
        "matrix_kernel",
        "matrix_image",
    ]

    integer: Optional[StrictInt] = None
    integers: Optional[list[StrictInt]] = None

    base: Optional[StrictInt] = None
    exponent: Optional[StrictInt] = None
    modulus: Optional[StrictInt] = None

    residues: Optional[list[StrictInt]] = None
    moduli: Optional[list[StrictInt]] = None

    polynomial: Optional[StrictStr] = None
    polynomial_a: Optional[StrictStr] = None
    polynomial_b: Optional[StrictStr] = None
    variable: Optional[StrictStr] = None
    field: Optional[StrictStr] = None
    ring: Optional[StrictStr] = None

    matrix: Optional[list[list[MatrixEntry]]] = None

    operation: Optional[StrictStr] = None
    element: Optional[StrictStr] = None
    element_a: Optional[StrictStr] = None
    element_b: Optional[StrictStr] = None

