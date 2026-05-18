import re
from pathlib import Path, PurePosixPath

_SAFE_COMPONENT_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")
_MAX_FILENAME_LENGTH = 180
_DANGEROUS_INNER_SUFFIXES = frozenset(
    {
        ".bat",
        ".cmd",
        ".com",
        ".dll",
        ".exe",
        ".jar",
        ".js",
        ".msi",
        ".ps1",
        ".scr",
        ".sh",
        ".vbs",
    }
)


def sanitize_scope_component(value: str, *, default: str) -> str:
    raw = PurePosixPath(str(value).replace("\\", "/")).name
    safe = _SAFE_COMPONENT_PATTERN.sub("_", raw).strip("._-")
    return safe or default


def sanitize_document_filename(filename: str, *, default: str = "document") -> str:
    base = PurePosixPath(str(filename).replace("\\", "/")).name.strip()
    if not base:
        return default

    path = PurePosixPath(base)
    suffix = path.suffix
    stem = path.stem or default
    stem_path = PurePosixPath(stem)
    if stem_path.suffix.lower() in _DANGEROUS_INNER_SUFFIXES:
        stem = stem_path.stem or default

    safe_stem = _SAFE_FILENAME_PATTERN.sub("_", stem).strip("._-") or default
    safe_suffix = _SAFE_FILENAME_PATTERN.sub("", suffix).lower()
    safe = f"{safe_stem}{safe_suffix}"
    return safe[:_MAX_FILENAME_LENGTH] or default


def session_root_for(
    *,
    temp_root: Path,
    user_id: str,
    session_id: str,
) -> Path:
    safe_user_id = sanitize_scope_component(user_id, default="user")
    safe_session_id = sanitize_scope_component(session_id, default="session")
    return temp_root / safe_user_id / safe_session_id


def ensure_relative_to(path: Path, root: Path) -> None:
    path.relative_to(root)
