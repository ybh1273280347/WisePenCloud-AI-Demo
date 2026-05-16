import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Optional

from .errors import FileHandoffInvalidSuffixError, FileHandoffWriteError
from .models import FileHandoffResult

DEFAULT_HANDOFF_TTL_SECONDS = 6 * 60 * 60
DEFAULT_HANDOFF_ROOT = Path(tempfile.gettempdir()) / "wisepen-file-handoff"

_ALLOWED_HANDOFF_SUFFIXES = frozenset(
    {
        ".pdf",
        ".docx",
        ".docm",
        ".pptx",
        ".pptm",
        ".epub",
        ".xlsx",
        ".xls",
        ".xlsm",
        ".ods",
    }
)
_SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")
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


class TemporaryFileHandoffStore:
    def __init__(
        self,
        *,
        root_dir: Path,
        ttl_seconds: int,
    ):
        self._root_dir = root_dir
        self._ttl_seconds = ttl_seconds

    def write_bytes(
        self,
        *,
        session_id: str,
        filename: str,
        content: bytes,
        canonical_suffix: str,
    ) -> FileHandoffResult:
        suffix = _validate_suffix(canonical_suffix)
        target = self._build_target_path(
            session_id=session_id,
            filename=filename,
            canonical_suffix=suffix,
        )

        try:
            target.write_bytes(content)
        except OSError as exc:
            raise FileHandoffWriteError("Failed to write temporary file handoff.") from exc

        return FileHandoffResult(
            file_ref=str(target.resolve(strict=False)),
            local_path=target,
            filename=target.name,
            size_bytes=len(content),
        )

    def copy_file(
        self,
        *,
        session_id: str,
        source_path: Path,
        filename: str,
        canonical_suffix: str,
    ) -> FileHandoffResult:
        suffix = _validate_suffix(canonical_suffix)
        target = self._build_target_path(
            session_id=session_id,
            filename=filename,
            canonical_suffix=suffix,
        )

        try:
            shutil.copyfile(source_path, target)
            size_bytes = target.stat().st_size
        except OSError as exc:
            raise FileHandoffWriteError("Failed to copy temporary file handoff.") from exc

        return FileHandoffResult(
            file_ref=str(target.resolve(strict=False)),
            local_path=target,
            filename=target.name,
            size_bytes=size_bytes,
        )

    def cleanup_expired(self) -> None:
        if not self._root_dir.is_dir():
            return

        threshold = time.time() - self._ttl_seconds
        for session_dir in list(self._root_dir.iterdir()):
            if not session_dir.is_dir():
                continue

            for file_path in list(session_dir.iterdir()):
                if not file_path.is_file():
                    continue
                try:
                    if file_path.stat().st_mtime < threshold:
                        file_path.unlink(missing_ok=True)
                except OSError:
                    continue

            try:
                next(session_dir.iterdir())
            except StopIteration:
                try:
                    session_dir.rmdir()
                except OSError:
                    pass

    def _build_target_path(
        self,
        *,
        session_id: str,
        filename: str,
        canonical_suffix: str,
    ) -> Path:
        self.cleanup_expired()
        safe_session_id = _safe_component(session_id, default="session")
        safe_stem = _safe_stem(filename)
        target_dir = self._root_dir / safe_session_id
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / f"{uuid.uuid4().hex[:16]}-{safe_stem}{canonical_suffix}"


def is_allowed_handoff_suffix(suffix: Optional[str]) -> bool:
    if not suffix:
        return False
    return suffix.lower() in _ALLOWED_HANDOFF_SUFFIXES


def _validate_suffix(suffix: str) -> str:
    normalized = (suffix or "").lower()
    if not normalized.startswith(".") or normalized not in _ALLOWED_HANDOFF_SUFFIXES:
        raise FileHandoffInvalidSuffixError(suffix)
    return normalized


def _safe_component(value: str, *, default: str) -> str:
    raw = PurePosixPath(str(value).replace("\\", "/")).name
    safe = _SAFE_NAME_PATTERN.sub("_", raw).strip("._-")
    return safe or default


def _safe_stem(filename: str) -> str:
    base = PurePosixPath(str(filename).replace("\\", "/")).name
    stem = PurePosixPath(base).stem
    stem_path = PurePosixPath(stem)
    if stem_path.suffix.lower() in _DANGEROUS_INNER_SUFFIXES:
        stem = stem_path.stem
    return _safe_component(stem, default="document")
