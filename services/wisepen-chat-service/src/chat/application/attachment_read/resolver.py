from abc import ABC, abstractmethod
from typing import List

from .models import ResolvedAttachment


class AttachmentResolver(ABC):
    @abstractmethod
    async def resolve_many(
        self,
        *,
        session_id: str,
        user_id: str,
        attachment_refs: List[str],
    ) -> List[ResolvedAttachment]:
        ...


class StubAttachmentResolver(AttachmentResolver):
    async def resolve_many(
        self,
        *,
        session_id: str,
        user_id: str,
        attachment_refs: List[str],
    ) -> List[ResolvedAttachment]:
        raise NotImplementedError(
            "AttachmentResolver is not implemented. Inject a concrete resolver from the attachment system."
        )
