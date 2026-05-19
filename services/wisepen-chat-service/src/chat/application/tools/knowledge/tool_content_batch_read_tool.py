from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from chat.application.tools.common.tool_content_store import tool_content_store
from chat.core.content_store import ContentWindow
from chat.domain.interfaces.tool import BaseTool

TOOL_DESCRIPTION = (
    "Reads multiple cached tool-content chunk windows in one call. "
    "Use this after evidence_rank when multiple ranked chunk evidence items are thematically related "
    "but located in different chunks. "
    "Each item must provide content_id and chunk_index, with optional before_chunks and after_chunks. "
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
                        "description": "cnt_* content_id returned by evidence_rank.",
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
        total_chars = 0
        skipped = 0

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
                block = _format_batch_window(index=index, item=item, window=window)

            if total_chars + len(block) > max_total_chars:
                skipped = len(validation.items) - index + 1
                break

            blocks.append(block)
            total_chars += len(block)

        lines = [
            "[Tool Result] Batch Tool Content Windows",
            f"Requested: {len(validation.items)} item(s)",
            f"Returned: {len(blocks)} window(s)",
            f"Skipped: {skipped} item(s)",
            "",
        ]
        lines.extend(blocks)
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


def _format_missing_window(*, index: int, item: _BatchReadItem) -> str:
    return "\n".join(
        [
            f"[{index}]",
            f"content_id: {item.content_id}",
            f"chunk_index: {item.chunk_index}",
            f"before_chunks: {item.before_chunks}",
            f"after_chunks: {item.after_chunks}",
            "error: cached tool content not found, expired, or inaccessible",
            "",
        ]
    )


def _format_batch_window(
    *,
    index: int,
    item: _BatchReadItem,
    window: ContentWindow,
) -> str:
    return "\n".join(
        [
            f"[{index}]",
            f"content_id: {item.content_id}",
            f"chunk_index: {item.chunk_index}",
            f"before_chunks: {item.before_chunks}",
            f"after_chunks: {item.after_chunks}",
            f"returned_length: {window.returned_length}",
            f"truncated: {str(window.truncated).lower()}",
            "",
            "[Content]",
            window.text,
            "",
        ]
    )
