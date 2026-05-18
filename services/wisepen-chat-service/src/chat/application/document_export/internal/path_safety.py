import re
from pathlib import Path

_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
)

_SAFE_CHAR_PATTERN = re.compile(r"[^\w.\- \u4e00-\u9fff]")
_MAX_SEGMENT_LENGTH = 120


def sanitize_path_segment(value: str, *, fallback: str = "document") -> str:
    if not value:
        return fallback

    cleaned = value.strip().strip(". ")

    cleaned = cleaned.replace("/", "_").replace("\\", "_")

    if cleaned in (".", "..", ""):
        return fallback

    if ":" in cleaned:
        prefix = cleaned.split(":")[0]
        if len(prefix) == 1 and prefix.isalpha():
            cleaned = cleaned.split(":", 1)[1].lstrip("\\/")

    cleaned = _SAFE_CHAR_PATTERN.sub("_", cleaned)

    cleaned = cleaned.strip(". ")

    if not cleaned:
        return fallback

    base_name = cleaned.rsplit(".", 1)[0] if "." in cleaned else cleaned
    if base_name.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = cleaned + "_"

    if len(cleaned) > _MAX_SEGMENT_LENGTH:
        cleaned = cleaned[:_MAX_SEGMENT_LENGTH]

    return cleaned


def is_path_within_root(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        resolved_root = root.resolve()
        resolved.relative_to(resolved_root)
        return True
    except (ValueError, OSError):
        return False
