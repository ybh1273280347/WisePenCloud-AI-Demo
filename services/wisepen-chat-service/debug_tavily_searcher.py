import asyncio
import os

import httpx

from chat.application.tools.web.services.web_search.searcher.tavily import TavilySearcher
from chat.core.config.app_settings import settings


async def main() -> None:
    async with httpx.AsyncClient() as client:
        searcher = TavilySearcher(client=client, api_key="xxx", base_url=settings.TAVILY_BASE_URL)
        response = await searcher.search("DeepSeek", max_results=5, timeout_seconds=20)
        print(response)


asyncio.run(main())