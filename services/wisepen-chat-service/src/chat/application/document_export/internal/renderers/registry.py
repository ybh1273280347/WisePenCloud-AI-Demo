from dataclasses import dataclass
from typing import Dict, List

from ...errors import DuplicateRendererFormatError, UnsupportedExportFormatError
from .base import DocumentRenderer


@dataclass(frozen=True, slots=True)
class RendererRegistry:
    renderers: Dict[str, DocumentRenderer]

    @classmethod
    def from_renderers(cls, renderers: List[DocumentRenderer]) -> "RendererRegistry":
        mapping: Dict[str, DocumentRenderer] = {}

        for renderer in renderers:
            target_format = renderer.target_format
            if target_format in mapping:
                raise DuplicateRendererFormatError(target_format)
            mapping[target_format] = renderer

        return cls(renderers=mapping)

    def get(self, target_format: str) -> DocumentRenderer:
        renderer = self.renderers.get(target_format)
        if renderer is None:
            raise UnsupportedExportFormatError(target_format)
        return renderer
