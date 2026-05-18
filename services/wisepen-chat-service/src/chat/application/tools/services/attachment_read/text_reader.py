import asyncio
from pathlib import Path

from .errors import AttachmentTextReadError

ATTACHMENT_TEXT_MAX_BYTES = 10 * 1024 * 1024
_ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "latin-1")


async def read_text_file(
    *,
    path: Path,
    attachment_ref: str,
) -> str:
    try:
        return await asyncio.to_thread(_read_text_file_sync, path, attachment_ref)
    except AttachmentTextReadError:
        raise
    except Exception as exc:
        raise AttachmentTextReadError(
            attachment_ref, "Failed to read text attachment."
        ) from exc


def _read_text_file_sync(path: Path, attachment_ref: str) -> str:
    try:
        with path.open("rb") as file:
            raw = file.read(ATTACHMENT_TEXT_MAX_BYTES + 1)
    except OSError as exc:
        raise AttachmentTextReadError(attachment_ref, "File cannot be read.") from exc

    truncated = len(raw) > ATTACHMENT_TEXT_MAX_BYTES
    if truncated:
        raw = raw[:ATTACHMENT_TEXT_MAX_BYTES]

    text = _decode_text(raw, attachment_ref)

    if truncated:
        text += "\n\n[Content truncated at attachment text read limit.]"

    return text


def _decode_text(raw: bytes, attachment_ref: str) -> str:
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding).lstrip("\ufeff")
        except UnicodeDecodeError:
            continue

    raise AttachmentTextReadError(attachment_ref, "Unsupported text encoding.")
