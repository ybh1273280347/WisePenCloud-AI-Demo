from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(slots=True)
class MathSolverResult:
    task: str
    backend: str
    exact_result: Optional[Any] = None
    numeric_result: Optional[str] = None
    latex_result: Optional[str] = None
    notes: list[str] = field(default_factory=list)
    is_formal_proof: bool = False

