import os
from pathlib import Path

from chat.application.document_temp_files import DOCUMENT_TEMP_FILE_ROOT

_SERVICE_ROOT = Path(__file__).resolve().parents[4]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(1, parsed)


DOCUMENT_EXPORT_OUTPUT_DIR = os.getenv(
    "DOCUMENT_EXPORT_OUTPUT_DIR",
    str(DOCUMENT_TEMP_FILE_ROOT),
)
DOCUMENT_EXPORT_MAX_PDF_CONTEXTS = _env_int("DOCUMENT_EXPORT_MAX_PDF_CONTEXTS", 8)
DOCUMENT_EXPORT_PANDOC_BIN = os.getenv("DOCUMENT_EXPORT_PANDOC_BIN", "pandoc")
DOCUMENT_EXPORT_PLAYWRIGHT_DISABLE_SANDBOX = _env_bool(
    "DOCUMENT_EXPORT_PLAYWRIGHT_DISABLE_SANDBOX",
    False,
)


def document_export_output_path() -> Path:
    path = Path(DOCUMENT_EXPORT_OUTPUT_DIR)
    if path.is_absolute():
        return path
    return (_SERVICE_ROOT / path).resolve()
