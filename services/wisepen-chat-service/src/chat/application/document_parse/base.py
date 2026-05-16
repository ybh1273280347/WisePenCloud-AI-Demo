from abc import ABC, abstractmethod
from pathlib import Path
from typing import Tuple

from chat.application.document_parse.models import DocumentParseResult


class BaseDocumentParser(ABC):
    supported_extensions: Tuple[str, ...]

    @abstractmethod
    async def parse(self, path: Path) -> DocumentParseResult: ...
