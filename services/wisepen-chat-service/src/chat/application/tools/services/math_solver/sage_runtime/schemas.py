from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, model_validator


MatrixEntry = StrictInt | StrictFloat | StrictStr


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


class SageComputeRequest(RejectExplicitNullMixin, BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    task: StrictStr

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


class SageComputeResponse(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    status: StrictStr
    task: StrictStr
    exact_result: Optional[StrictStr] = None
    numeric_result: Optional[StrictStr] = None
    latex_result: Optional[StrictStr] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[StrictStr] = Field(default_factory=list)
    error: Optional[StrictStr] = None

