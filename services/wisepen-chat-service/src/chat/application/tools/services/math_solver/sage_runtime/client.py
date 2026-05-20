from typing import Optional

import httpx
from chat.application.tools.services.math_solver.sage_runtime.schemas import (
    SageComputeRequest,
    SageComputeResponse,
)
from chat.core.config.app_settings import settings
from common.logger import log_error, log_event


class SageRuntimeClient:
    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[int | float] = None,
    ) -> None:
        self._base_url = (
            base_url
            if base_url is not None
            else settings.SAGE_MATH_WORKER_URL
        )
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.SAGE_MATH_WORKER_TIMEOUT_SECONDS
        )
        self._client: Optional[httpx.AsyncClient] = None
        self._validate_config()

    async def compute_async(self, request: SageComputeRequest) -> SageComputeResponse:
        try:
            client = self._get_client()
            response = await client.post(
                f"{self._base_url}/compute",
                json=request.model_dump(exclude_none=True),
            )
            response.raise_for_status()
            result = SageComputeResponse.model_validate(response.json())
            log_event("sage_math_solver_compute", task=request.task, status=result.status)
            return result
        except httpx.TimeoutException:
            log_error("sage_math_solver", "timeout", task=request.task)
            return SageComputeResponse(
                status="error",
                task=request.task,
                error=f"SageMath worker timed out after {self._timeout_seconds}s.",
            )
        except Exception as exc:
            log_error("sage_math_solver", exc, task=request.task)
            return SageComputeResponse(
                status="error",
                task=request.task,
                error=f"SageMath worker request failed: {exc}",
            )

    async def close(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            await client.aclose()
        log_event("SageRuntimeClient closed", closed=client is not None)

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout_seconds)
        return self._client

    def _validate_config(self) -> None:
        if not isinstance(self._base_url, str) or not self._base_url:
            raise ValueError("SAGE_MATH_WORKER_URL must be a non-empty string.")
        if self._base_url.endswith("/"):
            raise ValueError("SAGE_MATH_WORKER_URL must not end with '/'.")
        if isinstance(self._timeout_seconds, bool) or not isinstance(
            self._timeout_seconds,
            (int, float),
        ):
            raise ValueError("SAGE_MATH_WORKER_TIMEOUT_SECONDS must be a number.")
