from pathlib import Path

from .constants import CONTENT_TYPES

_EXTENSION_CONTENT_TYPES = {
    ".md": CONTENT_TYPES["markdown"],
    ".html": CONTENT_TYPES["html"],
    ".pdf": CONTENT_TYPES["pdf"],
    ".docx": CONTENT_TYPES["docx"],
    ".txt": CONTENT_TYPES["txt"],
}


def guess_export_content_type(*, file_path: Path) -> str:
    return _EXTENSION_CONTENT_TYPES.get(
        file_path.suffix.lower(), "application/octet-stream"
    )
