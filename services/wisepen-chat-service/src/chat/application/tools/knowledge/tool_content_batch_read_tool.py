from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from chat.application.tools.common.tool_content_store import tool_content_store
from chat.core.content_store import ContentWindow
from chat.domain.interfaces.tool import BaseTool

TOOL_DESCRIPTION = (
    "Reads multiple cached tool-content chunk windows in one call. "
    "Use this after evidence_rank when multiple ranked chunk evidence items are thematically related "
    "but located in different chunks.\n\n"
    "Always pass all selected ranked chunks in one items array.\n"
    "Each item MUST provide content_id and chunk_index.\n"
    "Use before_chunks and after_chunks only to expand context around the target chunk.\n"
    "Do not use this tool for sequential scanning, blind exploration, or as a replacement for evidence_rank."
)

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "content_id": {
                        "type": "string",
                        "minLength": 1,
                        "description": "cnt_* ToolContent identifier. Do not pass file_ref values.",
                    },
                    "chunk_index": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Chunk index returned by evidence_rank.",
                    },
                    "before_chunks": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 3,
                        "default": 1,
                    },
                    "after_chunks": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 3,
                        "default": 1,
                    },
                },
                "required": ["content_id", "chunk_index"],
                "additionalProperties": False,
            },
        },
        "max_total_chars": {
            "type": "integer",
            "minimum": 1000,
            "maximum": 30000,
            "default": 12000,
            "description": "Maximum total output size. Output stops at window boundary.",
        },
    },
    "required": ["items"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class _BatchReadItem:
    content_id: str
    chunk_index: int
    before_chunks: int
    after_chunks: int


@dataclass(frozen=True, slots=True)
class _SkippedBatchItem:
    index: int
    content_id: str
    chunk_index: int
    reason: str


@dataclass(frozen=True, slots=True)
class _TargetChunkStructure:
    heading_path: Tuple[str, ...] = ()
    page_number: Optional[int] = None
    section_type: str = ""


@dataclass(frozen=True, slots=True)
class _ValidationResult:
    items: Tuple[_BatchReadItem, ...] = ()
    error: str = ""


class ToolContentBatchReadTool(BaseTool):
    @property
    def name(self) -> str:
        return "tool_content_batch_read"

    @property
    def description(self) -> str:
        return TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        validation = _validate_items(kwargs.get("items"))
        if validation.error:
            return validation.error

        max_total_chars = kwargs.get("max_total_chars", 12000)
        if type(max_total_chars) is not int:
            return "[Tool Error] max_total_chars must be an integer."
        if max_total_chars < 1000 or max_total_chars > 30000:
            return "[Tool Error] max_total_chars must be between 1000 and 30000."

        blocks: List[str] = []
        skipped_items: List[_SkippedBatchItem] = []
        total_chars = 0

        for index, item in enumerate(validation.items, 1):
            window = tool_content_store.read_chunk_window(
                content_id=item.content_id,
                session_id=session_id,
                chunk_index=item.chunk_index,
                before_chunks=item.before_chunks,
                after_chunks=item.after_chunks,
            )
            if window is None:
                block = _format_missing_window(index=index, item=item)
            else:
                structure = _extract_target_chunk_structure(
                    session_id=session_id,
                    content_id=item.content_id,
                    chunk_index=item.chunk_index,
                )
                block = _format_batch_window(
                    index=index,
                    item=item,
                    window=window,
                    structure=structure,
                )

            if total_chars + len(block) > max_total_chars:
                for skipped_index, skipped_item in enumerate(
                    validation.items[index - 1 :],
                    start=index,
                ):
                    skipped_items.append(
                        _SkippedBatchItem(
                            index=skipped_index,
                            content_id=skipped_item.content_id,
                            chunk_index=skipped_item.chunk_index,
                            reason="max_total_chars_exceeded",
                        )
                    )
                break

            blocks.append(block)
            total_chars += len(block)

        lines = [
            "[Tool Result] Batch Tool Content Windows",
            f"Requested: {len(validation.items)} item(s)",
            f"Returned: {len(blocks)} window(s)",
            f"Skipped: {len(skipped_items)} item(s)",
        ]
        if skipped_items:
            lines.append("Skip reason: max_total_chars_exceeded")
        lines.append("")
        lines.extend(blocks)
        lines.extend(_format_skipped_items(skipped_items))
        return "\n".join(lines).rstrip()


def _validate_items(raw_items: Any) -> _ValidationResult:
    if not isinstance(raw_items, list):
        return _ValidationResult(error="[Tool Error] items must be a list.")
    if not raw_items:
        return _ValidationResult(
            error="[Tool Error] items must contain at least one item."
        )
    if len(raw_items) > 8:
        return _ValidationResult(error="[Tool Error] items must contain at most 8 items.")

    seen: Set[Tuple[str, int]] = set()
    validated: List[_BatchReadItem] = []
    for index, raw_item in enumerate(raw_items, 1):
        if not isinstance(raw_item, dict):
            return _ValidationResult(
                error=f"[Tool Error] items[{index}] must be an object."
            )

        content_id = raw_item.get("content_id")
        if type(content_id) is not str:
            return _ValidationResult(
                error=f"[Tool Error] items[{index}].content_id must be a string."
            )
        if not content_id:
            return _ValidationResult(
                error=f"[Tool Error] items[{index}].content_id must be a non-empty string."
            )
        if content_id.strip() != content_id:
            return _ValidationResult(
                error=(
                    f"[Tool Error] items[{index}].content_id must not contain leading "
                    "or trailing whitespace."
                )
            )
        if content_id.startswith("file_ref"):
            return _ValidationResult(
                error=(
                    f"[Tool Error] items[{index}].content_id must be a cnt_* value, "
                    "not a file_ref value."
                )
            )

        chunk_index = raw_item.get("chunk_index")
        if type(chunk_index) is not int:
            return _ValidationResult(
                error=f"[Tool Error] items[{index}].chunk_index must be an integer."
            )
        if chunk_index < 0:
            return _ValidationResult(
                error=(
                    f"[Tool Error] items[{index}].chunk_index must be greater than or equal to 0."
                )
            )

        before_chunks = raw_item.get("before_chunks", 1)
        if type(before_chunks) is not int:
            return _ValidationResult(
                error=f"[Tool Error] items[{index}].before_chunks must be an integer."
            )
        if before_chunks < 0 or before_chunks > 3:
            return _ValidationResult(
                error=f"[Tool Error] items[{index}].before_chunks must be between 0 and 3."
            )

        after_chunks = raw_item.get("after_chunks", 1)
        if type(after_chunks) is not int:
            return _ValidationResult(
                error=f"[Tool Error] items[{index}].after_chunks must be an integer."
            )
        if after_chunks < 0 or after_chunks > 3:
            return _ValidationResult(
                error=f"[Tool Error] items[{index}].after_chunks must be between 0 and 3."
            )

        key = (content_id, chunk_index)
        if key in seen:
            return _ValidationResult(
                error="[Tool Error] duplicate content_id + chunk_index items are not allowed."
            )
        seen.add(key)

        validated.append(
            _BatchReadItem(
                content_id=content_id,
                chunk_index=chunk_index,
                before_chunks=before_chunks,
                after_chunks=after_chunks,
            )
        )

    return _ValidationResult(items=tuple(validated))


def _extract_target_chunk_structure(
    *,
    session_id: str,
    content_id: str,
    chunk_index: int,
) -> _TargetChunkStructure:
    stored = tool_content_store.get(
        content_id=content_id,
        session_id=session_id,
    )
    if stored is None:
        return _TargetChunkStructure()

    if chunk_index < 0 or chunk_index >= len(stored.chunks):
        return _TargetChunkStructure()

    metadata = stored.chunks[chunk_index].metadata
    if not isinstance(metadata, dict):
        return _TargetChunkStructure()

    return _TargetChunkStructure(
        heading_path=_read_heading_path(metadata.get("heading_path")),
        page_number=_read_page_number(metadata.get("page_number")),
        section_type=_read_section_type(metadata.get("section_type")),
    )


def _read_heading_path(value: object) -> Tuple[str, ...]:
    if value is None:
        return ()

    if not isinstance(value, (list, tuple)):
        return ()

    headings: List[str] = []
    for item in value:
        if type(item) is not str:
            return ()
        if item:
            headings.append(item)

    return tuple(headings)


def _read_page_number(value: object) -> Optional[int]:
    if value is None:
        return None

    if type(value) is not int:
        return None

    return value


def _read_section_type(value: object) -> str:
    if value is None:
        return ""

    if type(value) is not str:
        return ""

    if not value:
        return ""

    if value.strip() != value:
        return ""

    return value


def _format_missing_window(*, index: int, item: _BatchReadItem) -> str:
    return "\n".join(
        [
            f"[{index}]",
            f"content_id: {item.content_id}",
            f"chunk_index: {item.chunk_index}",
            "error: cached tool content not found, expired, or inaccessible.",
        ]
    )


def _format_batch_window(
    *,
    index: int,
    item: _BatchReadItem,
    window: ContentWindow,
    structure: _TargetChunkStructure,
) -> str:
    lines = [
        f"[{index}]",
        f"content_id: {item.content_id}",
        f"chunk_index: {item.chunk_index}",
        f"before_chunks: {item.before_chunks}",
        f"after_chunks: {item.after_chunks}",
    ]

    if structure.heading_path:
        lines.append("target_heading_path: " + " > ".join(structure.heading_path))

    if structure.page_number is not None:
        lines.append(f"target_page_number: {structure.page_number}")

    if structure.section_type:
        lines.append(f"target_section_type: {structure.section_type}")

    lines.extend(
        [
            f"returned_length: {window.returned_length}",
            f"truncated: {str(window.truncated).lower()}",
        ]
    )

    if window.error:
        lines.append(f"error: {window.error}")

    lines.append("")
    lines.append("[Content]")
    lines.append(window.text)
    return "\n".join(lines)


def _format_skipped_items(skipped_items: List[_SkippedBatchItem]) -> List[str]:
    if not skipped_items:
        return []

    lines = ["", "Skipped items:"]
    for item in skipped_items:
        lines.extend(
            [
                f"[{item.index}]",
                f"content_id: {item.content_id}",
                f"chunk_index: {item.chunk_index}",
                f"reason: {item.reason}",
            ]
        )
    return lines
