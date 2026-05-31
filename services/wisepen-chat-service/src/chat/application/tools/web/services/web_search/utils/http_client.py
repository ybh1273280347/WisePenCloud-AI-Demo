import asyncio
from typing import Any, Dict, Optional

import httpx

from chat.application.tools.web.services.web_search.errors import (
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
)


async def fetch_search_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    query: str,
    timeout_seconds: float,
    semaphore: asyncio.Semaphore,
    provider_name: str,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """异步执行搜索引擎 HTTP 请求并返回 JSON 响应。

    根据 params/payload 参数自动选择 GET/POST，经信号量限流后发起请求，
    将下层 httpx 异常转译为业务层结构化错误类型。

    Args:
        client: httpx 异步客户端实例。
        url: 搜索引擎 API 端点 URL。
        query: 原始搜索查询（仅用于异常信息携带）。
        timeout_seconds: 整体超时秒数。
        semaphore: 并发控制信号量。
        provider_name: 搜索引擎名称（用于错误标记）。
        headers: 可选的自定义 HTTP 头部。
        params: 可选，GET 请求的查询参数（与 payload 二选一）。
        payload: 可选，POST 请求的 JSON 体（与 params 二选一）。

    Returns:
        搜索引擎返回的 JSON 字典。

    Raises:
        ValueError: params 和 payload 同时传入或同时缺失。
        SearchTimeoutError: 请求超时。
        SearchRateLimitError: 429 频率限制。
        SearchProviderError: 其他 HTTP / 网络错误。
    """
    if params is not None and payload is not None:
        raise ValueError("params and payload are mutually exclusive")
    if params is None and payload is None:
        raise ValueError("params or payload is required")

    timeout = httpx.Timeout(
        timeout=timeout_seconds,
        connect=min(3.0, timeout_seconds),
        read=timeout_seconds,
    )

    try:
        # get / post 分流
        async with semaphore:
            if params is not None:
                response = await client.get(
                    url=url,
                    params=params,
                    headers=headers,
                    timeout=timeout
                )
            else:
                response = await client.post(
                    url=url,
                    json=payload,
                    headers=headers,
                    timeout=timeout
                )

        # HTTP 状态码审查
        response.raise_for_status()

        # 非预期重定向强力熔断
        if response.is_redirect:
            raise SearchProviderError(
                provider=provider_name,
                status_code=response.status_code,
                reason=f"Unexpected redirect to {response.headers.get('location', '')}",
            )

        # 非 JSON 返回体
        try:
            return response.json()
        except (ValueError, TypeError) as e:
            raise SearchProviderError(
                provider=provider_name,
                status_code=response.status_code,
                reason=f"response_is_not_valid_json: {str(e)}",
            )

    # 下层错误向上转译
    except httpx.TimeoutException as e:
        raise SearchTimeoutError(
            provider=provider_name,
            queries=[query],
            timeout=timeout_seconds,
        ) from e

    except httpx.HTTPStatusError as e:
        status_code = e.response.status_code

        # 429 频率超限特殊关照
        if status_code == 429:
            try:
                retry_after = int(e.response.headers.get("retry-after", "0"))
            except ValueError:
                retry_after = 0
            raise SearchRateLimitError(
                provider=provider_name,
                retry_after=retry_after
            ) from e

        raise SearchProviderError(
            provider=provider_name,
            status_code=status_code,
            reason="http_status_error",
        ) from e

    except httpx.RequestError as e:
        # 物理网络崩溃或 DNS 解析失败
        raise SearchProviderError(
            provider=provider_name,
            status_code=0,
            reason=f"connection_failed: {str(e)}",
        ) from e
