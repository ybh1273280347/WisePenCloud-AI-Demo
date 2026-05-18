from abc import ABC, abstractmethod

from chat.application.web_search.models.common import SearchResponse


class BaseSearcher(ABC):
    name: str

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
        with_images: bool = False,
    ) -> SearchResponse: ...
