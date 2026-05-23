from abc import ABC, abstractmethod
from typing import Optional

from chat.application.tools.services.web_fetch.models import (
    FetchedDocument,
    FetchedPage,
    FetchedRedirect,
)


class BaseFetcher(ABC):
    """Fetchers return normalized markdown, a document handoff, or a markdown page envelope.

    Implementations should not return raw HTML/text to the coordinator. Use
    FetchedPage only when the fetcher has normalized markdown plus metadata such
    as discovered links, final URL, or status code.
    """

    name: str

    @abstractmethod
    async def fetch(
        self, url: str
    ) -> Optional[str | FetchedDocument | FetchedPage | FetchedRedirect]: ...
