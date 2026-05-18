import os
import tempfile
from pathlib import Path
from typing import Optional

DEFAULT_DOCUMENT_TEMP_FILE_ROOT = Path(tempfile.gettempdir()) / "wisepen-chat-upload-files"
DEFAULT_DOCUMENT_TEMP_FILE_TTL_SECONDS = 6 * 60 * 60
DEFAULT_DOCUMENT_TEMP_FILE_GRACE_SECONDS = 10 * 60
DEFAULT_DOCUMENT_TEMP_FILE_MAX_BYTES = 50 * 1024 * 1024

DOCUMENT_TEMP_FILE_ROOT = Path(
    os.getenv("DOCUMENT_TEMP_FILE_ROOT", str(DEFAULT_DOCUMENT_TEMP_FILE_ROOT))
)


def _env_int(name: str, default: int) -> int:
    value: Optional[str] = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return max(1, parsed)


DOCUMENT_TEMP_FILE_TTL_SECONDS = _env_int(
    "DOCUMENT_TEMP_FILE_TTL_SECONDS",
    DEFAULT_DOCUMENT_TEMP_FILE_TTL_SECONDS,
)
DOCUMENT_TEMP_FILE_GRACE_SECONDS = _env_int(
    "DOCUMENT_TEMP_FILE_GRACE_SECONDS",
    DEFAULT_DOCUMENT_TEMP_FILE_GRACE_SECONDS,
)
DOCUMENT_TEMP_FILE_MAX_BYTES = _env_int(
    "DOCUMENT_TEMP_FILE_MAX_BYTES",
    DEFAULT_DOCUMENT_TEMP_FILE_MAX_BYTES,
)
