from __future__ import annotations

from typing import Any, Dict, Optional

from chat.application.translation import (
    TranslationAssistError,
    TranslationAssistService,
)
from chat.application.translation.formatting import (
    format_translation_error,
    format_translation_result,
)
from chat.domain.interfaces.tool import BaseTool

_TOOL_DESCRIPTION = (
    "Provides open-source Chinese-English translation assistance using OPUS-MT / MarianMT. "
    "Use this tool for Chinese-English machine-translation baseline, bilingual comparison, "
    "long-text segmentation, and terminology consistency checks.\n\n"
    "This tool only supports zh <-> en in v1. "
    "It provides translation support, not necessarily the final translation. "
    "The assistant should produce the final translation with context, tone, formatting, and glossary constraints. "
    "If this tool fails or the requested language pair is unsupported, the assistant should translate directly using its own language ability."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {
            "type": "string",
            "minLength": 1,
            "description": "Text to translate or check.",
        },
        "source_language": {
            "type": "string",
            "enum": ["zh", "en"],
            "description": "Source language. Supported in v1: zh, en.",
        },
        "target_language": {
            "type": "string",
            "enum": ["zh", "en"],
            "description": "Target language. Supported in v1: zh, en.",
        },
        "mode": {
            "type": "string",
            "enum": ["baseline", "bilingual_segments", "terminology_check"],
            "default": "bilingual_segments",
            "description": "Translation assistance mode.",
        },
        "glossary": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                },
                "required": ["source", "target"],
                "additionalProperties": False,
            },
            "description": "Optional terminology mapping.",
        },
    },
    "required": ["text", "source_language", "target_language"],
    "additionalProperties": False,
}


class TranslationAssistTool(BaseTool):
    def __init__(self, service: Optional[TranslationAssistService] = None) -> None:
        self._service = service or TranslationAssistService()

    @property
    def name(self) -> str:
        return "translation_assist"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs: Any) -> str:
        try:
            result = await self._service.assist_async(
                text=kwargs.get("text"),
                source_language=kwargs.get("source_language"),
                target_language=kwargs.get("target_language"),
                mode=kwargs.get("mode", "bilingual_segments"),
                glossary=kwargs.get("glossary"),
            )
            return format_translation_result(result)
        except TranslationAssistError as e:
            return format_translation_error(str(e))
        except Exception as e:
            return format_translation_error(f"runtime error: {e}")

    async def close(self) -> None:
        await self._service.close()
