from typing import List, Optional

from pydantic import BaseModel, Field


class SageComputeRequest(BaseModel):
    task: str

    base: Optional[int] = None
    exponent: Optional[int] = None
    modulus: Optional[int] = None

    polynomial: Optional[str] = None
    field: Optional[str] = None


class SageComputeResponse(BaseModel):
    status: str
    task: str
    exact_result: Optional[str] = None
    numeric_result: Optional[str] = None
    latex_result: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
