import re
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import Optional

from chat.application.tools.services.document_file.cleanup import (
    DocumentTempFileCleanupService,
)
from chat.application.tools.services.document_file.config import (
    DOCUMENT_TEMP_FILE_GRACE_SECONDS,
    DOCUMENT_TEMP_FILE_ROOT,
)
from chat.application.tools.services.document_file.pathing import (
    sanitize_document_filename,
    session_root_for,
)
from common.logger import log_event

from .errors import FileHandoffInvalidSuffixError, FileHandoffWriteError
from .models import FileHandoffResult

DEFAULT_HANDOFF_TTL_SECONDS = 6 * 60 * 60
DEFAULT_HANDOFF_ROOT = DOCUMENT_TEMP_FILE_ROOT

_ALLOWED_HANDOFF_SUFFIXES = frozenset(
    {
        ".pdf",
        ".md",
        ".markdown",
        ".txt",
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
        grace_seconds: int = DOCUMENT_TEMP_FILE_GRACE_SECONDS,
    ):
        self._root_dir = root_dir
        self._ttl_seconds = ttl_seconds
        self._grace_seconds = grace_seconds

    def write_bytes(
        self,
        *,
        user_id: str,
        session_id: str,
        filename: str,
        content: bytes,
        canonical_suffix: str,
        content_type: Optional[str] = None,
    ) -> FileHandoffResult:
        suffix = _validate_suffix(canonical_suffix)
        target = self._build_target_path(
            user_id=user_id,
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
            user_id=user_id,
            session_id=session_id,
            original_file_name=filename,
            content_type=content_type,
        )

    def copy_file(
        self,
        *,
        user_id: str,
        session_id: str,
        source_path: Path,
        filename: str,
        canonical_suffix: str,
        content_type: Optional[str] = None,
    ) -> FileHandoffResult:
        suffix = _validate_suffix(canonical_suffix)
        target = self._build_target_path(
            user_id=user_id,
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
            user_id=user_id,
            session_id=session_id,
            original_file_name=filename,
            content_type=content_type,
        )

    def cleanup_expired(self) -> None:
        DocumentTempFileCleanupService(
            temp_root=self._root_dir,
            ttl_seconds=self._ttl_seconds,
            grace_seconds=self._grace_seconds,
        ).cleanup()

    def _build_target_path(
        self,
        *,
        user_id: str,
        session_id: str,
        filename: str,
        canonical_suffix: str,
    ) -> Path:
        self.cleanup_expired()
        if not user_id or not str(user_id).strip():
            raise FileHandoffWriteError("user_id is required for temporary file handoff.")
        if not session_id or not str(session_id).strip():
            raise FileHandoffWriteError(
                "session_id is required for temporary file handoff."
            )

        root = self._root_dir.resolve(strict=False)
        safe_name = sanitize_document_filename(
            _safe_stem_with_suffix(filename=filename, canonical_suffix=canonical_suffix)
        )
        target_dir = session_root_for(
            temp_root=root,
            user_id=user_id,
            session_id=session_id,
        ).resolve(strict=False)
        try:
            target_dir.relative_to(root)
        except ValueError as exc:
            raise FileHandoffWriteError(
                "Resolved temporary file directory escaped document temp root."
            ) from exc

        target_dir.mkdir(parents=True, exist_ok=True)
        target = (target_dir / f"{uuid.uuid4().hex}-{safe_name}").resolve(strict=False)
        try:
            target.relative_to(target_dir)
        except ValueError as exc:
            raise FileHandoffWriteError(
                "Resolved temporary file path escaped document session root."
            ) from exc

        log_event(
            "document file handoff target",
            user_id=user_id,
            session_id=session_id,
            target_path=str(target),
            original_file_name=filename,
        )
        return target


def is_allowed_handoff_suffix(suffix: Optional[str]) -> bool:
    if not suffix:
        return False
    return suffix.lower() in _ALLOWED_HANDOFF_SUFFIXES


def _validate_suffix(suffix: str) -> str:
    normalized = (suffix or "").lower()
    if not normalized.startswith(".") or normalized not in _ALLOWED_HANDOFF_SUFFIXES:
        raise FileHandoffInvalidSuffixError(suffix)
    return normalized


def _safe_stem(filename: str) -> str:
    base = PurePosixPath(str(filename).replace("\\", "/")).name
    stem = PurePosixPath(base).stem
    stem_path = PurePosixPath(stem)
    if stem_path.suffix.lower() in _DANGEROUS_INNER_SUFFIXES:
        stem = stem_path.stem
    safe = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", stem).strip("._-")
    return safe or "document"


def _safe_stem_with_suffix(*, filename: str, canonical_suffix: str) -> str:
    return f"{_safe_stem(filename)}{canonical_suffix}"
