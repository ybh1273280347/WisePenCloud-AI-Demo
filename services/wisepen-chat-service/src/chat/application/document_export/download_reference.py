from urllib.parse import quote

from .path_safety import sanitize_path_segment

_DOCUMENT_EXPORT_DOWNLOAD_PATH = "/api/document-export/download"


def build_download_ref(*, session_id: str, file_name: str) -> str:
    safe_session = sanitize_path_segment(session_id, fallback="default")
    safe_name = sanitize_path_segment(file_name, fallback="document")
    return f"{safe_session}/{safe_name}"


def build_download_url(*, download_ref: str) -> str:
    encoded_ref = quote(download_ref, safe="")
    return f"{_DOCUMENT_EXPORT_DOWNLOAD_PATH}?ref={encoded_ref}"
