from abc import ABC, abstractmethod
from typing import Optional

from chat.application.tools.services.web_fetch.models import FetchedDocument


class BaseFetcher(ABC):
    name: str

    @abstractmethod
    async def fetch(self, url: str) -> Optional[str | FetchedDocument]: ...
