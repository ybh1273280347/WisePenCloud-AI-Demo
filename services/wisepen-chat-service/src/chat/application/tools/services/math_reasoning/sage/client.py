import asyncio
from threading import Lock
from typing import Optional

import httpx
from chat.application.tools.services.math_reasoning.config import MATH_SAGE_TIMEOUT_SECONDS
from chat.core.config.app_settings import settings
from common.logger import log_error, log_event

from .models import SageComputeRequest, SageComputeResponse


class SageClientError(Exception):
    pass


class SageClient:
    def __init__(self) -> None:
        self._base_url = settings.MATH_SAGE_WORKER_URL.rstrip("/")
        self._timeout = MATH_SAGE_TIMEOUT_SECONDS
        self._client: Optional[httpx.AsyncClient] = None
        self._client_lock = asyncio.Lock()
        self._sync_client: Optional[httpx.Client] = None
        self._sync_client_lock = Lock()

    @property
    def enabled(self) -> bool:
        return settings.MATH_SAGE_ENABLED and bool(self._base_url)

    def compute(self, request: SageComputeRequest) -> SageComputeResponse:
        if not self.enabled:
            return SageComputeResponse(
                status="error",
                task=request.task,
                error="SageMath worker is not enabled.",
            )

        try:
            client = self._get_sync_client()
            response = client.post(
                f"{self._base_url}/compute", json=request.model_dump()
            )
            response.raise_for_status()
            data = response.json()
            result = SageComputeResponse(**data)
            log_event("sage_compute", task=request.task, status=result.status)
            return result
        except httpx.TimeoutException:
            log_error("sage", "timeout", task=request.task)
            return SageComputeResponse(
                status="error",
                task=request.task,
                error=f"SageMath worker timed out after {self._timeout}s.",
            )
        except Exception as e:
            log_error("sage", e)
            return SageComputeResponse(
                status="error",
                task=request.task,
                error=f"SageMath worker error: {e}",
            )

    async def compute_async(self, request: SageComputeRequest) -> SageComputeResponse:
        if not self.enabled:
            return SageComputeResponse(
                status="error",
                task=request.task,
                error="SageMath worker is not enabled.",
            )

        try:
            client = await self._get_client()
            response = await client.post(
                f"{self._base_url}/compute",
                json=request.model_dump(),
            )
            response.raise_for_status()
            data = response.json()
            result = SageComputeResponse(**data)
            log_event("sage_compute", task=request.task, status=result.status)
            return result
        except httpx.TimeoutException:
            log_error("sage", "timeout", task=request.task)
            return SageComputeResponse(
                status="error",
                task=request.task,
                error=f"SageMath worker timed out after {self._timeout}s.",
            )
        except Exception as e:
            log_error("sage", e)
            return SageComputeResponse(
                status="error",
                task=request.task,
                error=f"SageMath worker error: {e}",
            )

    async def close(self) -> None:
        client = self._client
        sync_client = self._sync_client
        self._client = None
        self._sync_client = None
        if client is not None:
            await client.aclose()
        if sync_client is not None:
            sync_client.close()
        log_event(
            "SageClient 关闭",
            async_client_closed=client is not None,
            sync_client_closed=sync_client is not None,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client

        async with self._client_lock:
            if self._client is not None and not self._client.is_closed:
                return self._client

            self._client = httpx.AsyncClient(timeout=self._timeout)
            return self._client

    def _get_sync_client(self) -> httpx.Client:
        if self._sync_client is not None and not self._sync_client.is_closed:
            return self._sync_client

        with self._sync_client_lock:
            if self._sync_client is not None and not self._sync_client.is_closed:
                return self._sync_client

            self._sync_client = httpx.Client(timeout=self._timeout)
            return self._sync_client
