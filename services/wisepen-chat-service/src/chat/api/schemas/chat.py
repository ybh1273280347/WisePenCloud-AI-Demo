from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """
    [DTO] 聊天请求传输对象。
    """

    session_id: str = Field(..., description="会话ID")

    query: str = Field(..., description="用户问题")

    model: Optional[int] = Field(default=None, description="模型ID")

    states: Optional[List[Dict[str, Any]]] = Field(
        default=None, description="上下文状态列表"
    )

    web_search_provider_mode: Optional[Literal["default", "custom"]] = Field(
        default=None, description="web_search 搜索源模式"
    )

    web_search_custom_provider: Optional[
        Literal["serper", "tavily", "brave", "serpapi", "exa", "perplexity"]
    ] = Field(default=None, description="本次请求使用的临时 custom 搜索服务商")

    web_search_custom_api_key: Optional[str] = Field(
        default=None, description="本次请求使用的临时 custom 搜索服务商 API Key"
    )

    web_search_use_saved_custom_key: bool = Field(
        default=False, description="是否使用已保存的 custom 搜索服务商凭据"
    )

    model_config = {"extra": "ignore"}
