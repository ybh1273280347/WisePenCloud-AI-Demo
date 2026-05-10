from pathlib import Path

from chat.application.document_parse.text_utils import normalize_text


class TextExtractor:
    """PDF 文字层文本提取器。"""

    def extract_page_text(self, path: Path, *, page_index: int) -> str:
        import fitz

        with fitz.open(str(path)) as doc:
            page = doc.load_page(page_index)
            text = page.get_text("text", sort=True)

        return normalize_text(text)