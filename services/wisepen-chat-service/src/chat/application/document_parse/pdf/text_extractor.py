from pathlib import Path

from chat.application.document_parse.text_utils import normalize_text


class TextExtractor:
    def extract_page_text(self, path: Path, *, page_index: int) -> str:
        import fitz

        with fitz.open(str(path)) as doc:
            page = doc.load_page(page_index)
            return self.extract_page_text_from_page(page)

    def extract_page_text_from_page(self, page) -> str:
        text = page.get_text("text", sort=True)
        return normalize_text(text)
