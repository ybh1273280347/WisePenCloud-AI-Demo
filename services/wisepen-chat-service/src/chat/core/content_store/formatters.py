from typing import Any, Dict

from .models import ContentWindow


def format_tool_content_window(window: ContentWindow) -> str:
    next_offset = "" if window.next_offset is None else str(window.next_offset)
    chunk_count = str(window.chunk_count)

    metadata_lines = [
        "[ToolContent Metadata]",
        f"content_id: {window.content_id}",
        f"content_cached: {str(window.cached).lower()}",
    ]

    if window.cache_error:
        metadata_lines.append(f"cache_error: {window.cache_error}")

    metadata_lines.extend([
        f"tool_name: {window.producer}",
        f"source: {window.source}",
        f"content_type: {window.content_type}",
        f"original_length: {window.original_length}",
        f"chunk_index: {window.chunk_index}",
        f"chunk_count: {chunk_count}",
        f"offset: {window.offset}",
        f"returned_length: {window.returned_length}",
        f"truncated: {str(window.truncated).lower()}",
        f"next_offset: {next_offset}",
    ])

    if window.error:
        metadata_lines.append(f"error: {window.error}")

    if window.warning:
        metadata_lines.append(f"warning: {window.warning}")

    return "\n".join(metadata_lines) + "\n\n[Content]\n" + window.text


def content_window_to_dict(window: ContentWindow) -> Dict[str, Any]:
    # This dict intentionally uses legacy tool-content field names for compatibility.
    return {
        "content_id": window.content_id,
        "content_cached": window.cached,
        "cache_error": window.cache_error,
        "tool_name": window.producer,
        "source": window.source,
        "content_type": window.content_type,
        "original_length": window.original_length,
        "chunk_index": window.chunk_index,
        "chunk_count": window.chunk_count,
        "offset": window.offset,
        "returned_length": window.returned_length,
        "truncated": window.truncated,
        "next_offset": window.next_offset,
        "error": window.error,
        "warning": window.warning,
        "metadata": dict(window.metadata),
    }