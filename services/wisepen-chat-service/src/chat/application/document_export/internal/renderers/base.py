from abc import ABC, abstractmethod

from ...models import ExportRequest


class DocumentRenderer(ABC):
    target_format: str

    @abstractmethod
    async def render(self, request: ExportRequest) -> None:
        pass
