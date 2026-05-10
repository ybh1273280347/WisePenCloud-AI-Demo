from typing import Any

from chat.application.document_parse import DocumentParseService
from chat.application.document_parse.epub import EpubParser
from chat.application.document_parse.ocr import OcrImageAdapter
from chat.application.document_parse.office import (
    OfficeFallbackParser,
    OfficeNativeParser,
    OfficeParser,
    OfficePrimaryParser,
)
from chat.application.document_parse.pdf import (
    PageClassifier,
    PageRenderer,
    PdfParser,
    ScannedTableExtractor,
    TableExtractor,
    TextExtractor,
)
from chat.application.document_parse.spreadsheet import SpreadsheetParser
from chat.core.config.app_settings import settings


def build_document_parse_service(*, local_ocr_processor: Any) -> DocumentParseService:
    office_fallback_parser = OfficeFallbackParser()

    pdf_parser = PdfParser(
        classifier=PageClassifier(),
        text_extractor=TextExtractor(),
        page_renderer=PageRenderer(
            dpi=settings.OCR_RENDER_DPI,
            max_image_pixels=settings.OCR_MAX_IMAGE_PIXELS,
        ),
        table_extractor=TableExtractor(),
        ocr_adapter=OcrImageAdapter(local_ocr_processor=local_ocr_processor),
        scanned_table_extractor=ScannedTableExtractor(lang=settings.OCR_LANGUAGE),
    )

    office_parser = OfficeParser(
        primary_parser=OfficePrimaryParser(),
        fallback_parser=office_fallback_parser,
        native_parser=OfficeNativeParser(),
    )

    return DocumentParseService(
        pdf_parser=pdf_parser,
        office_parser=office_parser,
        epub_parser=EpubParser(),
        spreadsheet_parser=SpreadsheetParser(),
    )
