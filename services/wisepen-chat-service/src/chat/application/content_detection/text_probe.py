import string
from typing import Optional

from chat.application.content_detection.models import (
    ContentDetection,
    ContentKind,
    DetectionConfidence,
)

_ENCODINGS = ("utf-8", "utf-8-sig", "gbk", "latin-1")
_CONTROL_WHITELIST = {"\n", "\r", "\t", "\f", "\b"}


class TextProbe:
    def detect_bytes(self, content: bytes) -> Optional[ContentDetection]:
        if not content:
            return None

        sample = content[:65536]
        if self._nul_ratio(sample) > 0.01:
            return None

        for encoding in _ENCODINGS:
            try:
                text = sample.decode(encoding)
            except UnicodeDecodeError:
                continue

            if self._looks_like_text(text):
                return ContentDetection(
                    kind=ContentKind.TEXT,
                    mime_type="text/plain",
                    extension=".txt",
                    confidence=DetectionConfidence.TEXT,
                    reason=f"text_probe_{encoding}",
                    detector="text_probe",
                )

        return None

    def _nul_ratio(self, sample: bytes) -> float:
        if not sample:
            return 0.0
        return sample.count(0) / len(sample)

    def _looks_like_text(self, text: str) -> bool:
        if not text:
            return False

        control_count = 0
        printable_count = 0
        for char in text:
            if char in _CONTROL_WHITELIST:
                printable_count += 1
            elif char in string.printable or char.isprintable():
                printable_count += 1
            elif ord(char) < 32 or ord(char) == 127:
                control_count += 1

        total = len(text)
        if control_count / total > 0.02:
            return False

        if printable_count / total < 0.85:
            return False

        return "\n" in text or "\r" in text or total >= 16
