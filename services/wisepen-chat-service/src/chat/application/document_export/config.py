from pathlib import Path

_SERVICE_ROOT = Path(__file__).resolve().parents[4]

DOCUMENT_EXPORT_OUTPUT_DIR = "dev_fixtures/generated_documents"
DOCUMENT_EXPORT_MAX_PDF_CONTEXTS = 8
DOCUMENT_EXPORT_PANDOC_BIN = "pandoc"
DOCUMENT_EXPORT_PLAYWRIGHT_DISABLE_SANDBOX = False


def document_export_output_path() -> Path:
    path = Path(DOCUMENT_EXPORT_OUTPUT_DIR)
    if path.is_absolute():
        return path
    return (_SERVICE_ROOT / path).resolve()
