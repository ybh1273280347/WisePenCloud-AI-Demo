from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from marker.converters.pdf import PdfConverter
from marker.output import text_from_rendered
from marker.renderers.markdown import MarkdownOutput

from chat.application.tools.document.services.document_parse.utils.text import normalize_text


@dataclass(slots=True)
class MarkerPdfResult:
    """表示当前组件。"""
    text: str
    metadata: Dict[str, Any] = field(default_factory=dict)


class MarkerPdfExtractor:

    def __init__(self, converter: PdfConverter):
        self.converter = converter

    def extract(self, path: Path) -> MarkerPdfResult:
        # PdfConverter 默认输出 MarkdownOutput：
        # - markdown: str
        # - images: dict
        # - metadata: dict
        """提取当前流程。"""
        rendered: MarkdownOutput = self.converter(str(path))

        # text_from_rendered(MarkdownOutput) 返回：
        # - text: markdown 文本
        # - ext: "md"
        # - images: 图片字典
        text, _, images = text_from_rendered(rendered)

        metadata = dict(rendered.metadata)
        metadata["image_count"] = len(images)

        return MarkerPdfResult(
            text=normalize_text(text),
            metadata=metadata,
        )