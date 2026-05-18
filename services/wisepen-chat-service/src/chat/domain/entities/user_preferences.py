from datetime import datetime

from beanie import Document
from pydantic import Field
from pymongo import ASCENDING, IndexModel

_DEFAULT_TIMEZONE = "Asia/Shanghai"
_DEFAULT_LOCALE = "zh-CN"


class UserPreferences(Document):
    """用户长期通用偏好。搜索源和凭据不属于此模型。"""

    user_id: str = Field(..., description="用户 ID")
    timezone: str = Field(default=_DEFAULT_TIMEZONE, description="IANA timezone")
    locale: str = Field(default=_DEFAULT_LOCALE, description="用户语言/区域偏好")
    created_at: datetime = Field(default_factory=datetime.utcnow, description="创建时间")
    updated_at: datetime = Field(default_factory=datetime.utcnow, description="更新时间")

    class Settings:
        name = "user_preferences"
        indexes = [
            IndexModel(
                [("user_id", ASCENDING)],
                unique=True,
                name="uniq_user_preferences_user_id",
            )
        ]
