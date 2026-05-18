from chat.application.document_export import (
    GeneratedDocumentFile,
    build_download_ref,
    build_download_url,
)

_DOWNLOAD_REF_USAGE_NOTE = (
    "download_ref is for user download and preview only. Do not pass download_ref to "
    "web_fetch, document_parse, attachment_read, evidence_rank, or tool_content_read."
)


def format_generated_document_result(
    *,
    user_id: str,
    session_id: str,
    generated: GeneratedDocumentFile,
) -> str:
    download_ref = build_download_ref(
        user_id=user_id,
        session_id=session_id,
        storage_file_name=generated.storage_file_name,
    )
    download_url = build_download_url(download_ref=download_ref)
    return (
        "[Generated Document]\n"
        f"- download_ref: {download_ref}\n"
        f"- download_url: {download_url}\n"
        f"- file_name: {generated.file_name}\n"
        f"- target_format: {generated.target_format}\n"
        f"- content_type: {generated.content_type}\n"
        f"- size_bytes: {generated.size_bytes}\n"
        f"{_DOWNLOAD_REF_USAGE_NOTE}"
    )
