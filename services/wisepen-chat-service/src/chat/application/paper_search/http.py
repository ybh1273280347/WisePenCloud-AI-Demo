from __future__ import annotations

import asyncio
from typing import Optional

import httpx


async def get_json_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict,
    headers: Optional[dict] = None,
    retries: int = 2,
) -> dict:
    last_error: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            response = await client.get(url, params=params, headers=headers)
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    await asyncio.sleep(int(retry_after))
                else:
                    await asyncio.sleep(2**attempt)
                continue

            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                return payload
            raise RuntimeError("response JSON is not an object")
        except Exception as e:
            last_error = e
            if attempt < retries:
                await asyncio.sleep(2**attempt)

    raise RuntimeError(f"request failed after retries: {last_error}")
