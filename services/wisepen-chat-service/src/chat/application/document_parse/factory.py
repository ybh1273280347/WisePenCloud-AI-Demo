from chat.application.document_parse.document_parse_service import DocumentParseService
from chat.application.document_parse.epub.parser import EpubParser
from chat.application.document_parse.ocr.image_adapter import OcrImageAdapter
from chat.application.document_parse.office.parser import OfficeParser
from chat.application.document_parse.office.primary_parser import OfficePrimaryParser
from chat.application.document_parse.office.fallback_parser import OfficeFallbackParser
from chat.application.document_parse.office.native_parser import OfficeNativeParser
from chat.application.document_parse.pdf.parser import PdfParser
from chat.application.document_parse.pdf.page_classifier import PageClassifier
from chat.application.document_parse.pdf.text_extractor import TextExtractor
from chat.application.document_parse.pdf.page_renderer import PageRenderer
from chat.application.document_parse.pdf.table_extractor import TableExtractor
from chat.application.document_parse.pdf.scanned_table_extractor import ScannedTableExtractor
from chat.application.document_parse.spreadsheet.parser import SpreadsheetParser
from chat.application.document_parse.ocr.processor import OcrProcessor
from chat.core.config.app_settings import settings
from common.logger import log_event, log_ok


def build_document_parse_service(*, local_ocr_processor: OcrProcessor) -> DocumentParseService:
    log_event(
        "DocumentParse service build start",
        ocr_processor_class=type(local_ocr_processor).__name__,
    )

    office_fallback_parser = OfficeFallbackParser()
    office_primary_parser = OfficePrimaryParser()
    office_native_parser = OfficeNativeParser()

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
        primary_parser=office_primary_parser,
        fallback_parser=office_fallback_parser,
        native_parser=office_native_parser,
    )

    epub_parser = EpubParser()
    spreadsheet_parser = SpreadsheetParser()

    log_ok(
        "DocumentParse service build",
        pdf_parser=type(pdf_parser).__name__,
        office_parser=type(office_parser).__name__,
        office_primary_parser=type(office_primary_parser).__name__,
        office_fallback_parser=type(office_fallback_parser).__name__,
        office_native_parser=type(office_native_parser).__name__,
        epub_parser=type(epub_parser).__name__,
        spreadsheet_parser=type(spreadsheet_parser).__name__,
    )

    return DocumentParseService(
        pdf_parser=pdf_parser,
        office_parser=office_parser,
        epub_parser=epub_parser,
        spreadsheet_parser=spreadsheet_parser,
    )
