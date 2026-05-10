from pathlib import Path
from typing import Any


class OcrImageAdapter:
    """本地图片 OCR 适配器。"""

    def __init__(self, *, local_ocr_processor: Any):
        self.local_ocr_processor = local_ocr_processor

    async def extract_text(self, image_path: Path) -> str:
        result = await self.local_ocr_processor.recognize_image(image_path)
        if not result.ok:
            raise RuntimeError(result.error or "OCR recognition failed")

        return result.text.strip()