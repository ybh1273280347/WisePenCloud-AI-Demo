from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from common.logger import log_error


@contextmanager
def document_processing_scope(session_root: Path) -> Iterator[None]:
    marker_dir = session_root / ".in_progress"
    marker_path = marker_dir / f"{uuid4().hex}.lock"
    try:
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker_path.write_text("", encoding="utf-8")
    except OSError as exc:
        log_error(
            "document temp processing marker create",
            exc,
            session_root=str(session_root),
            marker_path=str(marker_path),
        )

    try:
        yield
    finally:
        try:
            marker_path.unlink(missing_ok=True)
            marker_dir.rmdir()
        except OSError:
            pass
