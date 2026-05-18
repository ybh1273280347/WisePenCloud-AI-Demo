import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from common.logger import log_event

from .config import DOCUMENT_TEMP_FILE_MAX_BYTES, DOCUMENT_TEMP_FILE_ROOT
from .errors import (
    DocumentFilePermissionError,
    DocumentFileScopeError,
    DocumentFileSymlinkEscapeError,
    DocumentSessionRootMissingError,
    DocumentTempRootMissingError,
    InvalidDocumentRefError,
    UnreadableDocumentRefError,
)
from .models import ResolvedDocumentSource
from .pathing import ensure_relative_to, session_root_for


@dataclass(frozen=True, slots=True)
class DocumentTempFileResolver:
    temp_root: Path = DOCUMENT_TEMP_FILE_ROOT
    max_file_size_bytes: Optional[int] = DOCUMENT_TEMP_FILE_MAX_BYTES

    def resolve(
        self,
        *,
        file_ref: str,
        user_id: str,
        session_id: str,
        content_type: Optional[str] = None,
    ) -> ResolvedDocumentSource:
        if not file_ref or not str(file_ref).strip():
            raise InvalidDocumentRefError("document file_ref is required")
        if not user_id or not str(user_id).strip():
            raise InvalidDocumentRefError("document user_id is required")
        if not session_id or not str(session_id).strip():
            raise InvalidDocumentRefError("document session_id is required")

        root = self._resolve_root()
        session_root = self._candidate_session_root(
            root=root,
            user_id=user_id,
            session_id=session_id,
        )
        candidate = Path(file_ref)
        if not candidate.is_absolute():
            candidate = session_root / candidate

        try:
            lexical_path = Path(os.path.abspath(candidate))
        except OSError as exc:
            raise UnreadableDocumentRefError("document file_ref cannot be resolved") from exc

        try:
            ensure_relative_to(lexical_path, session_root)
        except ValueError as exc:
            raise DocumentFileScopeError() from exc

        self._ensure_session_root_exists(session_root)

        try:
            resolved_path = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise UnreadableDocumentRefError("document file_ref does not exist") from exc
        except OSError as exc:
            raise UnreadableDocumentRefError("document file_ref cannot be resolved") from exc

        try:
            ensure_relative_to(resolved_path, session_root)
        except ValueError as exc:
            if candidate.is_symlink() or lexical_path != resolved_path:
                raise DocumentFileSymlinkEscapeError() from exc
            raise DocumentFileScopeError() from exc

        try:
            if resolved_path.is_dir():
                raise UnreadableDocumentRefError("document file_ref is a directory")
            if not resolved_path.is_file():
                raise UnreadableDocumentRefError(
                    "document file_ref is not a regular file"
                )
            if not os.access(resolved_path, os.R_OK):
                raise DocumentFilePermissionError()
            size_bytes = resolved_path.stat().st_size
        except UnreadableDocumentRefError:
            raise
        except OSError as exc:
            raise UnreadableDocumentRefError("document file_ref stat failed") from exc

        if (
            self.max_file_size_bytes is not None
            and size_bytes > self.max_file_size_bytes
        ):
            raise UnreadableDocumentRefError(
                "document file_ref exceeds maximum file size"
            )

        log_event(
            "document_file resolved",
            user_id=user_id,
            session_id=session_id,
            path=str(resolved_path),
            size_bytes=size_bytes,
        )
        return ResolvedDocumentSource(
            path=resolved_path,
            user_id=user_id,
            session_id=session_id,
            source_file_name=resolved_path.name,
            size_bytes=size_bytes,
            content_type=content_type,
        )

    def session_root(self, *, user_id: str, session_id: str) -> Path:
        root = self._resolve_root()
        session_root = self._candidate_session_root(
            root=root,
            user_id=user_id,
            session_id=session_id,
        )
        self._ensure_session_root_exists(session_root)
        return session_root

    def _resolve_root(self) -> Path:
        try:
            root = self.temp_root.resolve(strict=False)
        except OSError as exc:
            raise DocumentTempRootMissingError("document temp root cannot be resolved") from exc

        if not root.exists():
            raise DocumentTempRootMissingError()
        if not root.is_dir():
            raise DocumentTempRootMissingError("document temp root is not a directory")
        return root

    def _candidate_session_root(
        self,
        *,
        root: Path,
        user_id: str,
        session_id: str,
    ) -> Path:
        try:
            session_root = session_root_for(
                temp_root=root,
                user_id=user_id,
                session_id=session_id,
            ).resolve(strict=False)
        except OSError as exc:
            raise DocumentSessionRootMissingError(
                "document session root cannot be resolved"
            ) from exc

        try:
            ensure_relative_to(session_root, root)
        except ValueError as exc:
            raise InvalidDocumentRefError(
                "document session root escapes document temp root"
            ) from exc

        return session_root

    def _ensure_session_root_exists(self, session_root: Path) -> None:
        if not session_root.exists():
            raise DocumentSessionRootMissingError()
        if not session_root.is_dir():
            raise DocumentSessionRootMissingError(
                "document session root is not a directory"
            )
        return session_root
