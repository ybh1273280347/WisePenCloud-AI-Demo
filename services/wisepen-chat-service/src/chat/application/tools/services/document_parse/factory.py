from chat.application.tools.services.document_parse.document_parse_service import DocumentParseService
from chat.application.tools.services.document_parse.epub.parser import EpubParser
from chat.application.tools.services.document_parse.office.fallback_parser import OfficeFallbackParser
from chat.application.tools.services.document_parse.office.parser import OfficeParser
from chat.application.tools.services.document_parse.office.primary_parser import OfficePrimaryParser
from chat.application.tools.services.document_parse.pdf.marker_extractor import (
    MarkerPdfExtractor,
)
from chat.application.tools.services.document_parse.pdf.page_classifier import PageClassifier
from chat.application.tools.services.document_parse.pdf.page_renderer import PageRenderer
from chat.application.tools.services.document_parse.pdf.parser import PdfParser
from chat.application.tools.services.document_parse.pdf.text_extractor import TextExtractor
from chat.application.tools.services.document_parse.spreadsheet.parser import SpreadsheetParser
from chat.application.tools.common.ocr import OcrImageAdapter, OcrProcessor
from common.logger import log_event


def build_document_parse_service(
    *, local_ocr_processor: OcrProcessor
) -> DocumentParseService:
    log_event(
        "document_parse service 构建开始",
        ocr_processor_class=type(local_ocr_processor).__name__,
    )

    office_fallback_parser = OfficeFallbackParser()
    office_primary_parser = OfficePrimaryParser()

    pdf_parser = PdfParser(
        classifier=PageClassifier(),
        text_extractor=TextExtractor(),
        page_renderer=PageRenderer(),
        marker_extractor=MarkerPdfExtractor(),
        ocr_adapter=OcrImageAdapter(local_ocr_processor=local_ocr_processor),
    )

    office_parser = OfficeParser(
        primary_parser=office_primary_parser,
        fallback_parser=office_fallback_parser,
    )

    epub_parser = EpubParser()
    spreadsheet_parser = SpreadsheetParser()

    log_event(
        "document_parse service 构建完成",
        pdf_parser=type(pdf_parser).__name__,
        office_parser=type(office_parser).__name__,
        office_primary_parser=type(office_primary_parser).__name__,
        office_fallback_parser=type(office_fallback_parser).__name__,
        epub_parser=type(epub_parser).__name__,
        spreadsheet_parser=type(spreadsheet_parser).__name__,
    )

    return DocumentParseService(
        pdf_parser=pdf_parser,
        office_parser=office_parser,
        epub_parser=epub_parser,
        spreadsheet_parser=spreadsheet_parser,
    )
