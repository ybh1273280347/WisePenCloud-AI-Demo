from dataclasses import dataclass
from pathlib import Path

from .errors import ExportOutputError
from .path_safety import is_path_within_root, sanitize_path_segment


@dataclass(frozen=True, slots=True)
class ResolvedDownloadFile:
    session_id: str
    file_path: Path
    file_name: str


class DocumentDownloadResolver:
    def __init__(self, *, output_root: Path):
        self.output_root = output_root

    def resolve(self, *, download_ref: str) -> ResolvedDownloadFile:
        if not download_ref:
            raise ExportOutputError("Missing download ref.")

        parts = download_ref.split("/")
        if len(parts) != 2:
            raise ExportOutputError("Invalid download ref format.")

        session_id, file_name = parts
        if not session_id or not file_name:
            raise ExportOutputError("Invalid download ref format.")

        safe_session = sanitize_path_segment(session_id, fallback="")
        safe_name = sanitize_path_segment(file_name, fallback="")

        if safe_session != session_id or safe_name != file_name:
            raise ExportOutputError("Invalid download ref path segment.")

        file_path = (self.output_root / safe_session / safe_name).resolve(
            strict=False
        )
        if not is_path_within_root(file_path, self.output_root):
            raise ExportOutputError("Resolved download path escapes output root.")

        return ResolvedDownloadFile(
            session_id=safe_session,
            file_path=file_path,
            file_name=safe_name,
        )
