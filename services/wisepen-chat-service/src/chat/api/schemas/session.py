import asyncio
import mimetypes
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from chat.api.schemas.chat_file import UploadedChatFileResponse
from chat.application.api_service.document_export import DocumentExportDownloadService
from chat.application.infra.document_temp_files.path import (
    sanitize_document_filename,
    session_root_for,
)
from chat.application.tools.document.services.document_export.errors import ExportOutputError
from chat.container import Container
from chat.domain.repositories import SessionRepository
from common.core.domain import R
from common.logger import log_event
from common.security import require_login

router = APIRouter()

_UPLOAD_ROOT = Path(tempfile.gettempdir()) / "wisepen-chat-upload-files"
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_FILE_ID_RE = re.compile(r"[a-fA-F0-9]{32}")


# ── Session ownership guard ───────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class _Session:
    user_id: str
    session_id: str


@inject
async def _verified_session(
    session_id: str = Query(..., min_length=1),
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
) -> _Session:
    """验证 session 归属，向下游端点透传 (user_id, session_id)。"""
    await session_repo.get_by_id_and_user(session_id, user_id)
    return _Session(user_id=user_id, session_id=session_id)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/uploadChatFile", response_model=R[UploadedChatFileResponse], status_code=200)
async def upload_chat_file(
    request: Request,
    file_name: str = Query(..., min_length=1),
    ctx: _Session = Depends(_verified_session),
):
    # Content-Length 预检（best-effort；流式写入仍以实际字节数为准）
    raw_cl = request.headers.get("content-length", "")
    if raw_cl.isdigit() and int(raw_cl) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Uploaded file is too large.")

    safe_name = sanitize_document_filename(file_name, default="upload.bin")
    file_id = uuid.uuid4().hex

    target_dir = session_root_for(
        temp_root=_UPLOAD_ROOT, user_id=ctx.user_id, session_id=ctx.session_id
    )
    await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)

    target_path = target_dir / f"{file_id}-{safe_name}"
    tmp_path = target_dir / f".{file_id}.tmp"
    size = 0

    try:
        with tmp_path.open("wb") as fh:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Uploaded file is too large.")
                await asyncio.to_thread(fh.write, chunk)

        if size == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        await asyncio.to_thread(tmp_path.replace, target_path)
    finally:
        await asyncio.to_thread(tmp_path.unlink, missing_ok=True)

    content_type = _content_type(
        explicit=request.headers.get("content-type"), file_name=safe_name
    )
    preview_url = _preview_url(ctx.session_id, file_id)
    download_url = _download_url(ctx.session_id, file_id)

    log_event(
        "chat file uploaded",
        session_id=ctx.session_id,
        file_id=file_id,
        file_name=safe_name,
        content_type=content_type,
        size_bytes=size,
    )

    return R.success(
        data=UploadedChatFileResponse(
            file_id=file_id,
            file_ref=str(target_path.resolve(strict=False)),
            file_name=safe_name,
            content_type=content_type,
            size_bytes=size,
            preview_url=preview_url,
            download_url=download_url,
        )
    )


@router.get("/previewChatFile")
async def preview_chat_file(
    file_id: str = Query(..., min_length=1),
    ctx: _Session = Depends(_verified_session),
):
    file_path = _resolve_uploaded_file(
        user_id=ctx.user_id, session_id=ctx.session_id, file_id=file_id
    )
    if file_path is None:
        raise HTTPException(status_code=404, detail="Uploaded file not found.")

    file_name = _strip_file_id(file_path.name, file_id)
    return FileResponse(
        path=file_path,
        media_type=_content_type(explicit=None, file_name=file_name),
        filename=file_name,
        content_disposition_type="inline",
    )


@router.get("/downloadChatFile")
async def download_chat_file(
    file_id: str = Query(..., min_length=1),
    ctx: _Session = Depends(_verified_session),
):
    file_path = _resolve_uploaded_file(
        user_id=ctx.user_id, session_id=ctx.session_id, file_id=file_id
    )
    if file_path is None:
        raise HTTPException(status_code=404, detail="Uploaded file not found.")

    file_name = _strip_file_id(file_path.name, file_id)
    return FileResponse(
        path=file_path,
        media_type=_content_type(explicit=None, file_name=file_name),
        filename=file_name,
        content_disposition_type="attachment",
    )


@router.get("/listChatFiles", response_model=R[List[UploadedChatFileResponse]], status_code=200)
async def list_chat_files(ctx: _Session = Depends(_verified_session)):
    base_dir = session_root_for(
        temp_root=_UPLOAD_ROOT, user_id=ctx.user_id, session_id=ctx.session_id
    ).resolve(strict=False)

    if not base_dir.is_dir():
        return R.success(data=[])

    # stat() 调用一次，同时用于排序和 size_bytes
    entries = sorted(
        ((p, p.stat()) for p in base_dir.glob("*-*") if p.is_file()),
        key=lambda t: t[1].st_mtime,
    )

    files: List[UploadedChatFileResponse] = []
    for file_path, st in entries:
        file_id, _, _ = file_path.name.partition("-")
        if not _FILE_ID_RE.fullmatch(file_id):
            continue
        file_name = _strip_file_id(file_path.name, file_id)
        files.append(
            UploadedChatFileResponse(
                file_id=file_id,
                file_ref=str(file_path.resolve(strict=False)),
                file_name=file_name,
                content_type=_content_type(explicit=None, file_name=file_name),
                size_bytes=st.st_size,
                preview_url=_preview_url(ctx.session_id, file_id),
                download_url=_download_url(ctx.session_id, file_id),
            )
        )

    return R.success(data=files)


@router.get("/generated")
@inject
async def preview_generated_file(
    download_ref: str = Query(..., min_length=1),
    user_id: str = Depends(require_login),
    download_service: DocumentExportDownloadService = Depends(
        Provide[Container.document_export_download_service]
    ),
):
    try:
        resolved = await download_service.resolve_existing_file(
            download_ref=download_ref, user_id=user_id
        )
    except ExportOutputError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return FileResponse(
        path=resolved.file_path,
        media_type=resolved.content_type,
        filename=resolved.file_name,
        content_disposition_type="inline",
    )


@router.delete("/delete", response_model=R, status_code=200)
async def delete_chat_file(
    file_id: str = Query(..., min_length=1),
    ctx: _Session = Depends(_verified_session),
):
    file_path = _resolve_uploaded_file(
        user_id=ctx.user_id, session_id=ctx.session_id, file_id=file_id
    )
    if file_path is not None:
        await asyncio.to_thread(file_path.unlink, missing_ok=True)
    return R.success()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _preview_url(session_id: str, file_id: str) -> str:
    return f"/chat/file/preview?session_id={quote(session_id)}&file_id={quote(file_id)}"


def _download_url(session_id: str, file_id: str) -> str:
    return f"/chat/file/download?session_id={quote(session_id)}&file_id={quote(file_id)}"


def _resolve_uploaded_file(
    *, user_id: str, session_id: str, file_id: str
) -> Optional[Path]:
    if not _FILE_ID_RE.fullmatch(file_id):
        return None
    base_dir = session_root_for(
        temp_root=_UPLOAD_ROOT, user_id=user_id, session_id=session_id
    ).resolve(strict=False)
    for candidate in base_dir.glob(f"{file_id}-*"):
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(base_dir)
        except ValueError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _strip_file_id(name: str, file_id: str) -> str:
    prefix = f"{file_id}-"
    return name[len(prefix):] or "upload.bin" if name.startswith(prefix) else name


def _content_type(*, explicit: Optional[str], file_name: str) -> str:
    if explicit:
        return explicit.split(";", 1)[0].strip() or "application/octet-stream"
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or "application/octet-stream"