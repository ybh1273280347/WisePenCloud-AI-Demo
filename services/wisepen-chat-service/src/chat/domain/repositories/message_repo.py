from abc import ABC, abstractmethod
from typing import List, Optional, Tuple
from datetime import datetime
from chat.domain.entities import ChatMessage


class MessageRepository(ABC):
    """冷数据：历史消息仓储接口 (MongoDB)"""

    @abstractmethod
    async def save_many(self, messages: List[ChatMessage]) -> None: pass

    @abstractmethod
    async def get_by_session(
        self,
        session_id: str,
        after: datetime = None,
        limit: int = 50,
    ) -> List[ChatMessage]:
        pass

    @abstractmethod
    async def get_page_by_session(
        self,
        session_id: str,
        page: int,
        size: int,
    ) -> Tuple[List[ChatMessage], int]:
        """
        分页拉取会话消息，仅返回 user / assistant / tool_calls 类消息，过滤 system 和 tool 结果。
        按 created_at 正序排列，返回 (当页列表, 总数)。
        """
        pass

    @abstractmethod
    async def full_text_search(
        self,
        keyword: str,
        session_id: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 10,
    ) -> List[ChatMessage]:
        """对 content 字段进行全文检索（依赖 MongoDB Text Index）"""
        pass

    @abstractmethod
    async def get_after_time(
        self,
        session_id: str,
        after: datetime,
        limit: int,
    ) -> List[ChatMessage]:
        pass
