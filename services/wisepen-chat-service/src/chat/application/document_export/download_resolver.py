from dataclasses import dataclass
from pathlib import Path

from .errors import ExportOutputError
from .internal.path_safety import is_path_within_root, sanitize_path_segment


@dataclass(frozen=True, slots=True)
class ResolvedDownloadFile:
    user_id: str
    session_id: str
    file_path: Path
    file_name: str
    storage_file_name: str


class DocumentDownloadResolver:
    def __init__(self, *, output_root: Path):
        self.output_root = output_root

    def resolve(self, *, download_ref: str, user_id: str) -> ResolvedDownloadFile:
        if not download_ref:
            raise ExportOutputError("Missing download ref.")

        parts = download_ref.split("/")
        if len(parts) != 3:
            raise ExportOutputError("Invalid download ref format.")

        ref_user_id, session_id, storage_file_name = parts
        if not ref_user_id or not session_id or not storage_file_name:
            raise ExportOutputError("Invalid download ref format.")

        safe_user = sanitize_path_segment(ref_user_id, fallback="")
        expected_user = sanitize_path_segment(user_id, fallback="")
        safe_session = sanitize_path_segment(session_id, fallback="")
        safe_name = sanitize_path_segment(storage_file_name, fallback="")

        if (
            safe_user != ref_user_id
            or expected_user != ref_user_id
            or safe_session != session_id
            or safe_name != storage_file_name
        ):
            raise ExportOutputError("Invalid download ref path segment.")

        file_path = (
            self.output_root / safe_user / safe_session / "outputs" / safe_name
        ).resolve(strict=False)
        if not is_path_within_root(file_path, self.output_root):
            raise ExportOutputError("Resolved download path escapes output root.")

        return ResolvedDownloadFile(
            user_id=safe_user,
            session_id=safe_session,
            file_path=file_path,
            file_name=_display_name_from_storage_name(safe_name),
            storage_file_name=safe_name,
        )


def _display_name_from_storage_name(storage_file_name: str) -> str:
    prefix, sep, rest = storage_file_name.partition("-")
    if sep and len(prefix) == 32 and all(c in "0123456789abcdef" for c in prefix):
        return rest or storage_file_name
    return storage_file_name
