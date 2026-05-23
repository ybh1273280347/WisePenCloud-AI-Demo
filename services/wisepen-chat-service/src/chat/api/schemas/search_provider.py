from typing import Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from chat.application.web_search.search_provider_config import (
    MODES,
    PROVIDERS,
)


class SearchProviderConfigResponse(BaseModel):
    """API 响应：用户搜索源配置"""

    mode: str = Field(..., description="搜索模式：default/custom")
    provider: Optional[str] = Field(default=None, description="custom 模式下选择的搜索服务商")
    key_prefix4: Optional[str] = Field(default=None, description="API Key 前四位")
    key_last4: Optional[str] = Field(default=None, description="API Key 后四位")
    status: str = Field(..., description="状态")
    last_verified_at: Optional[str] = Field(default=None, description="最近验证时间")
    last_error_code: Optional[str] = Field(default=None, description="最近错误码")


class SetSearchProviderModeRequest(BaseModel):
    """API 请求：设置搜索模式"""

    mode: StrictStr = Field(..., description="搜索模式：default/custom")

    model_config = ConfigDict(extra="forbid")

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        if value not in MODES:
            raise ValueError("mode must be default or custom")
        return value


class SetCustomSearchProviderRequest(BaseModel):
    """API 请求：设置 custom 搜索源"""

    provider: StrictStr = Field(
        ...,
        description="搜索服务商：serper/tavily/brave/serpapi/exa/perplexity/anysearch",
    )
    api_key: StrictStr = Field(..., description="用户提供的 API Key")

    model_config = ConfigDict(extra="forbid")

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value not in PROVIDERS:
            raise ValueError("provider is not supported")
        return value

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("api_key must not contain leading or trailing whitespace")
        return value

    @model_validator(mode="after")
    def validate_provider_api_key(self) -> "SetCustomSearchProviderRequest":
        if self.provider != "anysearch" and not self.api_key:
            raise ValueError("api_key is required")
        return self
