import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from common.logger import log_error, log_event, log_fail

from .config import (
    DOCUMENT_TEMP_FILE_GRACE_SECONDS,
    DOCUMENT_TEMP_FILE_ROOT,
    DOCUMENT_TEMP_FILE_TTL_SECONDS,
)
from .pathing import ensure_relative_to


@dataclass(frozen=True, slots=True)
class CleanupResult:
    scanned_session_dirs: int
    removed_session_dirs: int
    failed_session_dirs: int
    skipped_in_progress_dirs: int


@dataclass(frozen=True, slots=True)
class DocumentTempFileCleanupService:
    temp_root: Path = DOCUMENT_TEMP_FILE_ROOT
    ttl_seconds: int = DOCUMENT_TEMP_FILE_TTL_SECONDS
    grace_seconds: int = DOCUMENT_TEMP_FILE_GRACE_SECONDS

    def cleanup(self) -> CleanupResult:
        try:
            root = self.temp_root.resolve(strict=False)
        except OSError as exc:
            log_error(
                "document temp cleanup root resolve",
                exc,
                temp_root=str(self.temp_root),
            )
            return CleanupResult(0, 0, 1, 0)

        if not root.exists():
            log_event("document_temp_cleanup skipped", reason="root_missing", root=str(root))
            return CleanupResult(0, 0, 0, 0)

        if not root.is_dir():
            log_fail("document_temp_cleanup", "root_not_directory", root=str(root))
            return CleanupResult(0, 0, 1, 0)

        cutoff = time.time() - self.ttl_seconds - self.grace_seconds
        scanned = 0
        removed = 0
        failed = 0
        skipped_in_progress = 0

        for user_dir in list(root.iterdir()):
            if not user_dir.is_dir():
                continue
            for session_dir in list(user_dir.iterdir()):
                if not session_dir.is_dir():
                    continue
                scanned += 1
                try:
                    resolved_session_dir = session_dir.resolve(strict=True)
                    ensure_relative_to(resolved_session_dir, root)
                    marker_dir = resolved_session_dir / ".in_progress"
                    if marker_dir.exists() and any(marker_dir.iterdir()):
                        skipped_in_progress += 1
                        continue
                    if resolved_session_dir.stat().st_mtime > cutoff:
                        continue
                    shutil.rmtree(resolved_session_dir)
                    removed += 1
                except Exception as exc:
                    failed += 1
                    log_error(
                        "document temp cleanup session",
                        exc,
                        root=str(root),
                        session_dir=str(session_dir),
                    )

        log_event(
            "document_temp_cleanup completed",
            root=str(root),
            scanned_session_dirs=scanned,
            removed_session_dirs=removed,
            failed_session_dirs=failed,
            skipped_in_progress_dirs=skipped_in_progress,
            ttl_seconds=self.ttl_seconds,
            grace_seconds=self.grace_seconds,
        )
        return CleanupResult(scanned, removed, failed, skipped_in_progress)
