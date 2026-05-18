from pathlib import Path

from chat.application.tools.common.errors.document_parse import OcrProcessingError
from chat.application.tools.common.ocr.processor import OcrProcessor


class OcrImageAdapter:
    """本地图片 OCR 适配器。"""

    def __init__(self, *, local_ocr_processor: OcrProcessor):
        self.local_ocr_processor = local_ocr_processor

    async def extract_text(self, image_path: Path) -> str:
        result = await self.local_ocr_processor.recognize_image(image_path)
        if not result.ok:
            raise OcrProcessingError(result.error or "OCR recognition failed")

        return result.text.strip()
