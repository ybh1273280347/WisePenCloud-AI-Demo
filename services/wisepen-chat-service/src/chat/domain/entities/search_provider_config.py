from datetime import datetime
from typing import Optional

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel


class UserSearchProviderConfig(Document):
    """
    用户搜索源配置。

    V1 仅支持：
    - default 模式：平台搜索源
    - custom 模式：用户选择一个 provider 并保存一个 API Key

    不保存明文 API Key。
    """

    user_id: str = Field(..., description="用户 ID")
    mode: str = Field(default="default", description="搜索模式：default/custom")

    provider: Optional[str] = Field(
        default=None,
        description="custom 模式下选择的搜索服务商：serper/tavily/brave/serpapi/exa/perplexity/anysearch",
    )

    encrypted_api_key: Optional[str] = Field(
        default=None,
        description="加密后的 API Key",
    )
    encryption_key_id: Optional[str] = Field(
        default=None,
        description="加密主密钥 ID，用于后续密钥轮换",
    )
    key_fingerprint: Optional[str] = Field(
        default=None,
        description="API Key HMAC 指纹，用于去重，不可反推出原 key",
    )
    key_prefix4: Optional[str] = Field(
        default=None,
        description="API Key 前四位，仅用于脱敏展示",
    )
    key_last4: Optional[str] = Field(
        default=None,
        description="API Key 后四位，仅用于脱敏展示",
    )

    status: str = Field(
        default="unset",
        description="状态：unset/untested/valid/invalid/quota_exhausted/rate_limited/provider_error",
    )
    last_verified_at: Optional[datetime] = Field(
        default=None,
        description="最近验证时间",
    )
    last_error_code: Optional[str] = Field(
        default=None,
        description="最近标准化错误码，不保存原始错误文本",
    )

    created_at: datetime = Field(default_factory=datetime.utcnow, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="更新时间")

    class Settings:
        name = "user_search_provider_configs"
        indexes = [
            IndexModel(
                [("user_id", ASCENDING)],
                unique=True,
                name="uniq_user_search_provider_configs_user_id",
            )
        ]
