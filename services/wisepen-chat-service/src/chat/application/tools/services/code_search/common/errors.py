from __future__ import annotations

from typing import Mapping, Optional


class VerticalSearchError(RuntimeError):
    pass


class VerticalSearchHttpError(VerticalSearchError):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        headers: Optional[Mapping[str, str]] = None,
        body_preview: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.body_preview = body_preview
