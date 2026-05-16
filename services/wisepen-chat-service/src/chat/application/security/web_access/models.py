from dataclasses import dataclass, field
from typing import List


@dataclass(slots=True)
class PageBlockDetection:
    kind: str = "normal"
    confidence: float = 0.0
    score: int = 0
    signals: List[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.kind != "normal"
