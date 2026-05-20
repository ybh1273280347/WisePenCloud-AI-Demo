from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import httpx
from common.logger import log_event

from .errors import SoftwareEcosystemHttpError


class SoftwareEcosystemHttpClient:
    def __init__(
        self,
        *,
        timeout: float,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._timeout = timeout
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()

    async def get_json(
        self,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        try:
            client = await self._get_client()
            response = await client.get(url, params=params, headers=headers)
        except httpx.HTTPError as e:
            raise SoftwareEcosystemHttpError(f"GET request failed: {e}") from e

        if response.status_code >= 400:
            raise SoftwareEcosystemHttpError(
                f"GET request failed with HTTP {response.status_code}",
                status_code=response.status_code,
                headers=response.headers,
                body_preview=response.text[:500],
            )

        try:
            return response.json()
        except ValueError as e:
            raise SoftwareEcosystemHttpError(
                "GET request returned invalid JSON",
                status_code=response.status_code,
                headers=response.headers,
                body_preview=response.text[:500],
            ) from e

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()
        log_event("SoftwareEcosystemHttpClient 关闭", closed=client is not None)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client

        async with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                return self._client

            self._client = httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            )
            return self._client

