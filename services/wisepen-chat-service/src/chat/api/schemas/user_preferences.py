from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from chat.application.user_preferences import (
    ALLOWED_LOCALES,
    validate_locale,
    validate_timezone,
)


class UserPreferencesResponse(BaseModel):
    timezone: str = Field(..., description="IANA timezone")
    locale: str = Field(..., description="用户语言/区域偏好")


class UpdateTimezoneRequest(BaseModel):
    timezone: StrictStr = Field(..., description="IANA timezone")

    model_config = ConfigDict(extra="forbid")

    @field_validator("timezone")
    @classmethod
    def validate_timezone_value(cls, value: str) -> str:
        return validate_timezone(value)


class UpdateLocaleRequest(BaseModel):
    locale: StrictStr = Field(
        ...,
        description="用户语言/区域偏好",
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("locale")
    @classmethod
    def validate_locale_value(cls, value: str) -> str:
        if value not in ALLOWED_LOCALES:
            raise ValueError("locale is not supported")
        return validate_locale(value)
