import time
from urllib.parse import quote

from chat.application.document_export.config import document_export_output_path
from chat.application.document_export.download_resolver import (
    DocumentDownloadResolver,
)
from chat.application.document_export.errors import ExportOutputError
from chat.application.document_export.mime import guess_export_content_type
from chat.domain.repositories import SessionRepository
from common.logger import log_event
from common.security import require_login
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/document-export", tags=["document-export"])


def current_user_id(user_id: str = Depends(require_login)) -> str:
    return user_id


def get_session_repo() -> SessionRepository:
    from chat.container import container

    return container.session_repo()


def _build_content_disposition(*, file_name: str) -> str:
    encoded_name = quote(file_name, safe="")
    return f"attachment; filename*=UTF-8''{encoded_name}"


@router.get("/download")
async def download_generated_document(
    ref: str = Query(default=""),
    user_id: str = Depends(current_user_id),
    session_repo: SessionRepository = Depends(get_session_repo),
) -> FileResponse:
    started = time.monotonic()
    resolver = DocumentDownloadResolver(output_root=document_export_output_path())

    try:
        resolved = resolver.resolve(download_ref=ref)
    except ExportOutputError as exc:
        log_event(
            "document_export_download invalid_ref",
            download_ref=ref,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            success=False,
        )
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session_repo.get_by_id_and_user(resolved.session_id, user_id)

    if not resolved.file_path.is_file():
        log_event(
            "document_export_download not_found",
            download_ref=ref,
            file_name=resolved.file_name,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            success=False,
        )
        raise HTTPException(status_code=404, detail="Generated document not found.")

    content_type = guess_export_content_type(file_path=resolved.file_path)
    size_bytes = resolved.file_path.stat().st_size
    log_event(
        "document_export_download success",
        download_ref=ref,
        file_name=resolved.file_name,
        content_type=content_type,
        size_bytes=size_bytes,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        success=True,
    )

    return FileResponse(
        path=resolved.file_path,
        media_type=content_type,
        headers={
            "Content-Disposition": _build_content_disposition(
                file_name=resolved.file_name
            ),
        },
    )
