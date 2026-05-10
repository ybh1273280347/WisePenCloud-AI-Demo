from tavily import AsyncTavilyClient

from chat.application.web_search.models import (
    SearchResponse,
    TavilySearchRequest,
    map_tavily_response,
)
from common.logger import log_ok, log_fail


class TavilySearcher:
    name = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 15.0,
    ) -> None:
        api_key = api_key.strip()

        self._client = AsyncTavilyClient(api_key=api_key)
        self._timeout = timeout

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse:
        request = TavilySearchRequest(
            query=query,
            max_results=max_results,
            with_images=with_images,
        )

        payload = request.to_payload()
        payload["timeout"] = self._timeout

        safe_payload = {
            key: value
            for key, value in payload.items()
            if key not in {"api_key"}
        }

        try:
            raw_response = await self._client.search(**payload)
        except Exception as e:
            raise RuntimeError(
                "Tavily search failed: "
                f"payload={safe_payload}, "
                f"error={type(e).__name__}: {e}"
            ) from e

        return map_tavily_response(raw_response, max_results=max_results)
