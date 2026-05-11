from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field


class SearchOverride(BaseModel):
    force_deep_search: Optional[bool] = Field(default=None, description="强制深度搜索")
    force_image_search: Optional[bool] = Field(default=None, description="强制图片搜索")


class ChatRequest(BaseModel):
    """
    [DTO] 聊天请求传输对象。
    """
    session_id: str = Field(..., description="会话ID")

    query: str = Field(..., description="用户问题")

    model: Optional[int] = Field(default=None, description="模型ID")

    states: Optional[List[Dict[str, Any]]] = Field(default=None, description="上下文状态列表")

    search_override: Optional[SearchOverride] = Field(default=None, description="搜索模式覆盖")

    model_config = {"extra": "ignore"}
