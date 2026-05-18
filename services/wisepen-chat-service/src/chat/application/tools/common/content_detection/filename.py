import re
from pathlib import PurePosixPath
from typing import Set

_DANGEROUS_FILENAME_SUFFIXES: Set[str] = {
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


def sanitize_download_filename(name: str) -> str:
    normalized = name.replace("\\", "/")
    base = PurePosixPath(normalized).name.strip()
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()

    if base in {"", ".", "..", "~"}:
        return "download"

    return base


def drop_dangerous_inner_suffix(name: str) -> str:
    path_name = PurePosixPath(name)
    supported_suffix = path_name.suffix
    stem_path = PurePosixPath(path_name.stem)

    if stem_path.suffix.lower() not in _DANGEROUS_FILENAME_SUFFIXES:
        return path_name.name

    safe_stem = stem_path.stem or path_name.stem
    return f"{safe_stem}{supported_suffix}"
