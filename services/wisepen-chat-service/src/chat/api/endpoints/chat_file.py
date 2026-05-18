import asyncio
import mimetypes
import re
import uuid
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from chat.api.schemas.chat_file import UploadedChatFileResponse
from chat.application.document_export import (
    DocumentDownloadResolver,
    ExportOutputError,
    document_export_output_path,
    guess_export_content_type,
)
from chat.application.tools.services.document_file import (
    DOCUMENT_TEMP_FILE_MAX_BYTES,
    DOCUMENT_TEMP_FILE_ROOT,
    DocumentTempFileCleanupService,
)
from chat.application.tools.services.document_file.pathing import (
    sanitize_document_filename,
    sanitize_scope_component,
)
from chat.container import Container
from chat.domain.repositories import SessionRepository
from common.core.domain import R
from common.logger import log_event
from common.security import require_login
from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

router = APIRouter()

_UPLOAD_ROOT = DOCUMENT_TEMP_FILE_ROOT
_MAX_UPLOAD_BYTES = DOCUMENT_TEMP_FILE_MAX_BYTES


@router.post("/upload", response_model=R[UploadedChatFileResponse], status_code=200)
@inject
async def upload_chat_file(
    request: Request,
    session_id: str = Query(..., min_length=1),
    file_name: str = Query(..., min_length=1),
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    await session_repo.get_by_id_and_user(session_id, user_id)

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413, detail="Uploaded file is too large."
                )
        except ValueError:
            pass

    _cleanup_expired_uploads()

    safe_user_id = sanitize_scope_component(user_id, default="user")
    safe_session_id = sanitize_scope_component(session_id, default="session")
    safe_file_name = sanitize_document_filename(file_name, default="upload.bin")
    file_id = uuid.uuid4().hex

    target_dir = _UPLOAD_ROOT / safe_user_id / safe_session_id
    await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)

    target_path = target_dir / f"{file_id}-{safe_file_name}"
    tmp_path = target_dir / f".{file_id}.tmp"
    size = 0

    try:
        with tmp_path.open("wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413, detail="Uploaded file is too large."
                    )
                await asyncio.to_thread(handle.write, chunk)

        if size <= 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        await asyncio.to_thread(tmp_path.replace, target_path)
    finally:
        await asyncio.to_thread(tmp_path.unlink, missing_ok=True)

    content_type = _content_type(
        explicit=request.headers.get("content-type"),
        file_name=safe_file_name,
    )
    file_ref = str(target_path.resolve(strict=False))
    preview_url = (
        f"/chat/file/preview?session_id={quote(session_id)}&file_id={quote(file_id)}"
    )
    download_url = f"{preview_url}&download=true"

    log_event(
        "chat file uploaded",
        session_id=session_id,
        file_id=file_id,
        file_name=safe_file_name,
        content_type=content_type,
        size_bytes=size,
    )

    return R.success(
        data=UploadedChatFileResponse(
            file_id=file_id,
            file_ref=file_ref,
            file_name=safe_file_name,
            content_type=content_type,
            size_bytes=size,
            preview_url=preview_url,
            download_url=download_url,
        )
    )


@router.get("/preview")
@inject
async def preview_chat_file(
    session_id: str = Query(..., min_length=1),
    file_id: str = Query(..., min_length=1),
    download: bool = Query(default=False),
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    await session_repo.get_by_id_and_user(session_id, user_id)
    file_path = _resolve_uploaded_file(
        user_id=user_id,
        session_id=session_id,
        file_id=file_id,
    )
    if file_path is None:
        raise HTTPException(status_code=404, detail="Uploaded file not found.")

    filename = _filename_without_id(file_path.name, file_id=file_id)
    content_type = _content_type(explicit=None, file_name=filename)
    disposition = "attachment" if download else "inline"

    return FileResponse(
        path=file_path,
        media_type=content_type,
        filename=filename,
        content_disposition_type=disposition,
    )


@router.get("/list", response_model=R[list[UploadedChatFileResponse]], status_code=200)
@inject
async def list_chat_files(
    session_id: str = Query(..., min_length=1),
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    await session_repo.get_by_id_and_user(session_id, user_id)
    _cleanup_expired_uploads()

    safe_user_id = sanitize_scope_component(user_id, default="user")
    safe_session_id = sanitize_scope_component(session_id, default="session")
    base_dir = (_UPLOAD_ROOT / safe_user_id / safe_session_id).resolve(strict=False)
    if not base_dir.is_dir():
        return R.success(data=[])

    files: List[UploadedChatFileResponse] = []
    for file_path in sorted(
        base_dir.glob("*-*"), key=lambda path: path.stat().st_mtime
    ):
        if not file_path.is_file():
            continue
        file_id, _, _ = file_path.name.partition("-")
        if not re.fullmatch(r"[a-fA-F0-9]{32}", file_id):
            continue

        file_name = _filename_without_id(file_path.name, file_id=file_id)
        preview_url = f"/chat/file/preview?session_id={quote(session_id)}&file_id={quote(file_id)}"
        files.append(
            UploadedChatFileResponse(
                file_id=file_id,
                file_ref=str(file_path.resolve(strict=False)),
                file_name=file_name,
                content_type=_content_type(explicit=None, file_name=file_name),
                size_bytes=file_path.stat().st_size,
                preview_url=preview_url,
                download_url=f"{preview_url}&download=true",
            )
        )

    return R.success(data=files)


@router.get("/generated")
@inject
async def preview_generated_file(
    download_ref: str = Query(..., min_length=1),
    download: bool = Query(default=False),
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    output_root = document_export_output_path()
    resolver = DocumentDownloadResolver(output_root=output_root)
    try:
        resolved = resolver.resolve(download_ref=download_ref, user_id=user_id)
    except ExportOutputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await session_repo.get_by_id_and_user(resolved.session_id, user_id)

    if not resolved.file_path.is_file():
        raise HTTPException(status_code=404, detail="Generated file not found.")

    content_type = guess_export_content_type(file_path=resolved.file_path)
    disposition = "attachment" if download else "inline"
    return FileResponse(
        path=resolved.file_path,
        media_type=content_type,
        filename=resolved.file_name,
        content_disposition_type=disposition,
    )


@router.delete("/delete", response_model=R, status_code=200)
@inject
async def delete_chat_file(
    session_id: str = Query(..., min_length=1),
    file_id: str = Query(..., min_length=1),
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    await session_repo.get_by_id_and_user(session_id, user_id)
    file_path = _resolve_uploaded_file(
        user_id=user_id,
        session_id=session_id,
        file_id=file_id,
    )
    if file_path is not None:
        await asyncio.to_thread(file_path.unlink, missing_ok=True)
    return R.success()


def _resolve_uploaded_file(
    *, user_id: str, session_id: str, file_id: str
) -> Optional[Path]:
    safe_user_id = sanitize_scope_component(user_id, default="user")
    safe_session_id = sanitize_scope_component(session_id, default="session")
    base_dir = (_UPLOAD_ROOT / safe_user_id / safe_session_id).resolve(strict=False)

    if not file_id or not re.fullmatch(r"[a-fA-F0-9]{32}", file_id):
        return None

    for candidate in base_dir.glob(f"{file_id}-*"):
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(base_dir)
        except ValueError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _cleanup_expired_uploads() -> None:
    DocumentTempFileCleanupService(temp_root=_UPLOAD_ROOT).cleanup()


def _filename_without_id(name: str, *, file_id: str) -> str:
    prefix = f"{file_id}-"
    if name.startswith(prefix):
        return name[len(prefix) :] or "upload.bin"
    return name


def _content_type(*, explicit: Optional[str], file_name: str) -> str:
    if explicit:
        return explicit.split(";", 1)[0].strip() or "application/octet-stream"
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"
