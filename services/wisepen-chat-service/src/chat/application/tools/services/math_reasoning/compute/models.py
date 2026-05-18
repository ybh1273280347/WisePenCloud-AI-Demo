from typing import Any, List, Optional

from pydantic import BaseModel


class MathComputeResult(BaseModel):
    task: str
    exact_result: Optional[Any] = None
    numeric_result: Optional[str] = None
    latex_result: Optional[str] = None
    notes: List[str] = []
