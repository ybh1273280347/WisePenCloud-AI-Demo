from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import Lock
from typing import Optional, Protocol, List

from chat.application.translation.config import (
    TRANSLATION_MAX_SEGMENT_CHARS,
    TRANSLATION_MAX_TOTAL_CHARS,
)
from chat.core.config.app_settings import settings
from common.logger import log_event
from .glossary import check_glossary, parse_glossary
from .models import TranslationAssistError, TranslationAssistResult, TranslationSegment
from .segmenter import split_text_for_translation


TRANSLATION_DEVICE = settings.TRANSLATION_DEVICE

SUPPORTED_LANGUAGE_PAIRS = {
    ("zh", "en"),
    ("en", "zh"),
}

SUPPORTED_MODES = {"baseline", "bilingual_segments", "terminology_check"}


class TranslationEngine(Protocol):
    def translate(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str: ...


class TranslationAssistService:
    def __init__(
        self,
        engine: Optional[TranslationEngine] = None,
        *,
        max_segment_chars: int = TRANSLATION_MAX_SEGMENT_CHARS,
        max_total_chars: int = TRANSLATION_MAX_TOTAL_CHARS,
        device: str = TRANSLATION_DEVICE,
    ) -> None:
        self._engine = engine
        self._max_segment_chars = max_segment_chars
        self._max_total_chars = max_total_chars
        self._device = device
        self._engine_lock = Lock()
        self._fallback_executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="translation-assist",
        )

    def assist(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
        mode: str = "bilingual_segments",
        glossary: object = None,
    ) -> TranslationAssistResult:
        source_language = _normalize_language(source_language, "source_language")
        target_language = _normalize_language(target_language, "target_language")
        mode = mode or "bilingual_segments"
        if mode not in SUPPORTED_MODES:
            raise TranslationAssistError(f"Unsupported translation mode: {mode}")

        if not isinstance(text, str) or not text.strip():
            raise TranslationAssistError("text must be a non-empty string.")

        warnings: List[str] = []

        if source_language == target_language:
            warnings.append("source_language and target_language are the same.")
        elif (source_language, target_language) not in SUPPORTED_LANGUAGE_PAIRS:
            raise TranslationAssistError("Unsupported translation language pair.")

        normalized_text = text.strip()
        if len(normalized_text) > self._max_total_chars:
            normalized_text = normalized_text[: self._max_total_chars]
            warnings.append(
                f"Input text exceeded {self._max_total_chars} characters and was truncated."
            )

        terms = parse_glossary(glossary)
        raw_segments = split_text_for_translation(
            normalized_text,
            max_chars=self._max_segment_chars,
        )
        if not raw_segments:
            raise TranslationAssistError("text produced no translatable segments.")

        engine = self._get_engine()
        segments: List[TranslationSegment] = []
        for index, segment in enumerate(raw_segments, 1):
            if source_language == target_language:
                translated = segment
            else:
                translated = engine.translate(
                    text=segment,
                    source_language=source_language,
                    target_language=target_language,
                )
            segments.append(
                TranslationSegment(
                    index=index,
                    source=segment,
                    baseline_translation=translated,
                )
            )

        source_joined = "\n".join(segment.source for segment in segments)
        translated_joined = "\n".join(segment.baseline_translation for segment in segments)
        terminology = check_glossary(
            source_text=source_joined,
            translated_text=translated_joined,
            glossary=terms,
        )

        if mode == "terminology_check" and not terms:
            warnings.append("terminology_check mode was requested without glossary terms.")

        warnings.append(
            "This is a machine-translation baseline. The assistant should polish the final translation according to context."
        )

        return TranslationAssistResult(
            source_language=source_language,
            target_language=target_language,
            mode=mode,
            backend="opus_mt",
            segments=segments,
            terminology=terminology,
            warnings=warnings,
        )

    async def assist_async(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
        mode: str = "bilingual_segments",
        glossary: object = None,
    ) -> TranslationAssistResult:
        source_language = _normalize_language(source_language, "source_language")
        target_language = _normalize_language(target_language, "target_language")
        mode = mode or "bilingual_segments"
        if mode not in SUPPORTED_MODES:
            raise TranslationAssistError(f"Unsupported translation mode: {mode}")

        if not isinstance(text, str) or not text.strip():
            raise TranslationAssistError("text must be a non-empty string.")

        warnings: List[str] = []

        if source_language == target_language:
            warnings.append("source_language and target_language are the same.")
        elif (source_language, target_language) not in SUPPORTED_LANGUAGE_PAIRS:
            raise TranslationAssistError("Unsupported translation language pair.")

        normalized_text = text.strip()
        if len(normalized_text) > self._max_total_chars:
            normalized_text = normalized_text[: self._max_total_chars]
            warnings.append(
                f"Input text exceeded {self._max_total_chars} characters and was truncated."
            )

        terms = parse_glossary(glossary)
        raw_segments = split_text_for_translation(
            normalized_text,
            max_chars=self._max_segment_chars,
        )
        if not raw_segments:
            raise TranslationAssistError("text produced no translatable segments.")

        engine = self._get_engine()
        segments: List[TranslationSegment] = []
        for index, segment in enumerate(raw_segments, 1):
            if source_language == target_language:
                translated = segment
            else:
                translated = await self._translate_segment_async(
                    engine,
                    text=segment,
                    source_language=source_language,
                    target_language=target_language,
                )
            segments.append(
                TranslationSegment(
                    index=index,
                    source=segment,
                    baseline_translation=translated,
                )
            )

        source_joined = "\n".join(segment.source for segment in segments)
        translated_joined = "\n".join(segment.baseline_translation for segment in segments)
        terminology = check_glossary(
            source_text=source_joined,
            translated_text=translated_joined,
            glossary=terms,
        )

        if mode == "terminology_check" and not terms:
            warnings.append("terminology_check mode was requested without glossary terms.")

        warnings.append(
            "This is a machine-translation baseline. The assistant should polish the final translation according to context."
        )

        return TranslationAssistResult(
            source_language=source_language,
            target_language=target_language,
            mode=mode,
            backend="opus_mt",
            segments=segments,
            terminology=terminology,
            warnings=warnings,
        )

    async def close(self) -> None:
        engine = self._engine
        if engine is not None:
            close = getattr(engine, "close", None)
            if close is not None:
                close()
        self._fallback_executor.shutdown(wait=False, cancel_futures=True)
        log_event("TranslationAssistService 关闭", engine_loaded=engine is not None)

    def _get_engine(self) -> TranslationEngine:
        if self._engine is None:
            with self._engine_lock:
                if self._engine is None:
                    from .opus_mt_engine import OpusMtTranslationEngine

                    self._engine = OpusMtTranslationEngine(device=self._device)
        return self._engine

    async def _translate_segment_async(
        self,
        engine: TranslationEngine,
        *,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        translate_async = getattr(engine, "translate_async", None)
        if translate_async is not None:
            return await translate_async(
                text=text,
                source_language=source_language,
                target_language=target_language,
            )

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._fallback_executor,
            partial(
                engine.translate,
                text=text,
                source_language=source_language,
                target_language=target_language,
            ),
        )


def _normalize_language(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TranslationAssistError(f"{field_name} is required.")
    language = value.strip().lower()
    if language not in {"zh", "en"}:
        raise TranslationAssistError(f"Unsupported language: {language}")
    return language
