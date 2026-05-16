from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from threading import Lock
from typing import Any, Dict, Optional, Tuple

from .models import TranslationAssistError


_OPUS_MODEL_MAP = {
    ("zh", "en"): "Helsinki-NLP/opus-mt-zh-en",
    ("en", "zh"): "Helsinki-NLP/opus-mt-en-zh",
}


class OpusMtTranslationEngine:
    def __init__(self, device: str = "auto") -> None:
        self._cache: Dict[str, Tuple[Any, Any]] = {}
        self._requested_device = device
        self._device: Optional[str] = None
        self._torch: Optional[Any] = None
        self._load_lock = Lock()
        self._inference_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="translation-opusmt",
        )

    def translate(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        model_name = _OPUS_MODEL_MAP.get(
            (source_language.lower(), target_language.lower())
        )
        if not model_name:
            raise TranslationAssistError(
                f"Unsupported translation language pair: {source_language}->{target_language}"
            )

        tokenizer, model = self._load(model_name)
        torch = self._ensure_torch()
        device = self._resolve_device()

        inputs = tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=512,
        ).to(device)

        with torch.inference_mode():
            output = model.generate(
                **inputs,
                num_beams=4,
                max_new_tokens=512,
            )

        return str(tokenizer.decode(output[0], skip_special_tokens=True))

    async def translate_async(
        self,
        *,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._inference_executor,
            partial(
                self.translate,
                text=text,
                source_language=source_language,
                target_language=target_language,
            ),
        )

    def close(self) -> None:
        self._inference_executor.shutdown(wait=False, cancel_futures=True)

    def _load(self, model_name: str) -> Tuple[Any, Any]:
        if model_name in self._cache:
            return self._cache[model_name]

        with self._load_lock:
            if model_name in self._cache:
                return self._cache[model_name]

            try:
                from transformers import MarianMTModel, MarianTokenizer
            except Exception as e:
                raise TranslationAssistError(f"backend unavailable: {e}") from e

            device = self._resolve_device()
            try:
                tokenizer = MarianTokenizer.from_pretrained(model_name)
                model = MarianMTModel.from_pretrained(model_name)
                model.to(device)
                model.eval()
            except Exception as e:
                raise TranslationAssistError(
                    f"Translation model unavailable: {model_name}"
                ) from e

            self._cache[model_name] = (tokenizer, model)
            return tokenizer, model

    def _ensure_torch(self) -> Any:
        if self._torch is not None:
            return self._torch
        try:
            import torch
        except Exception as e:
            raise TranslationAssistError(f"backend unavailable: {e}") from e
        self._torch = torch
        return torch

    def _resolve_device(self) -> str:
        if self._device is not None:
            return self._device

        torch = self._ensure_torch()
        device = self._requested_device
        if device == "auto":
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            return self._device
        if device not in {"cpu", "cuda"}:
            raise TranslationAssistError(f"Unsupported translation device: {device}")
        if device == "cuda" and not torch.cuda.is_available():
            raise TranslationAssistError("Unsupported translation device: cuda is unavailable")
        self._device = device
        return self._device
