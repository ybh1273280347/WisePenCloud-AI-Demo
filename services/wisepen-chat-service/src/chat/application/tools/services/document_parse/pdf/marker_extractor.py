from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from chat.application.tools.services.document_parse.text_utils import normalize_text


@dataclass(slots=True)
class MarkerPdfResult:
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class MarkerPdfExtractor:
    def extract(self, path: Path) -> MarkerPdfResult:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        from marker.output import text_from_rendered

        converter = PdfConverter(
            artifact_dict=create_model_dict(),
        )
        rendered = converter(str(path))
        text, _, images = text_from_rendered(rendered)
        metadata = self._metadata_from_rendered(rendered)
        metadata["image_count"] = len(images or {})
        return MarkerPdfResult(text=normalize_text(text), metadata=metadata)

    def _metadata_from_rendered(self, rendered) -> Dict[str, Any]:
        metadata = getattr(rendered, "metadata", None)
        if isinstance(metadata, dict):
            return dict(metadata)
        if metadata is not None:
            return {"metadata": metadata}
        return {}
