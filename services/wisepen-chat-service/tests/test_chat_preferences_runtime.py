import asyncio

import pytest
from pydantic import ValidationError

from chat.api.schemas.chat import ChatRequest
from chat.api.schemas.user_preferences import UserPreferencesResponse
from chat.application.runtime_context import RUNTIME_CONTEXT_KEY, RuntimeContext
from chat.application.tools.services.temporal import TimeResolveError, resolve_time_text
from chat.application.tools.web.web_search_tool import WebSearchTool
from chat.application.user_preferences.constants import (
    DEFAULT_LOCALE,
    DEFAULT_TIMEZONE,
)
from chat.application.user_preferences.service import (
    UserPreferencesService,
    validate_locale,
    validate_timezone,
)
from chat.application.web_search import (
    SearchManyResult,
    SearchResponse,
    SearchResult,
)
from chat.application.web_search.search_provider_config.constants import MODE_CUSTOM
from chat.application.web_search.search_provider_config.service import (
    RuntimeSearchProviderContext,
)
from chat.domain.entities.user_preferences import UserPreferences


class _EmptyPreferencesRepository:
    async def get_by_user_id(self, user_id: str):
        return None


class _CapturingSearchCoordinator:
    def __init__(self) -> None:
        self.request = None

    async def search_many(self, request):
        self.request = request
        return SearchManyResult(
            response=SearchResponse(
                query="python typing",
                results=(
                    SearchResult(
                        title="Python typing",
                        url="https://example.com/python-typing",
                        snippet="Python typing reference.",
                    ),
                ),
            ),
        )


def test_chat_request_rejects_removed_search_fields() -> None:
    with pytest.raises(ValidationError):
        ChatRequest(
            session_id="session-1",
            query="hello",
            web_search_provider_mode="custom",
        )


def test_user_preferences_schema_contains_no_search_fields() -> None:
    fields = set(UserPreferencesResponse.model_fields)

    assert fields == {"timezone", "locale"}
    assert "search" not in fields
    assert "provider" not in fields
    assert "credential_id" not in fields
    assert "api_key" not in fields


def test_user_preferences_defaults_without_record() -> None:
    service = UserPreferencesService(repository=_EmptyPreferencesRepository())

    preferences = asyncio.run(service.get_preferences(user_id="user-1"))

    assert preferences.timezone == DEFAULT_TIMEZONE
    assert preferences.locale == DEFAULT_LOCALE


def test_user_preferences_validation_is_strict() -> None:
    assert validate_timezone("Asia/Shanghai") == "Asia/Shanghai"
    assert validate_locale("zh-CN") == "zh-CN"

    with pytest.raises(ValueError):
        validate_timezone("")
    with pytest.raises(ValueError):
        validate_timezone("Shanghai")
    with pytest.raises(ValueError):
        validate_timezone(" Asia/Shanghai ")
    with pytest.raises(ValueError):
        validate_locale("zh_cn")
    with pytest.raises(ValueError):
        validate_locale("en-us")


def test_user_preferences_document_has_no_search_fields() -> None:
    fields = set(UserPreferences.model_fields)

    assert {"user_id", "timezone", "locale", "created_at", "updated_at"} <= fields
    assert not {
        "search",
        "provider",
        "credential_id",
        "api_key",
    } & fields


def test_web_search_uses_runtime_search_config_and_ignores_legacy_context() -> None:
    coordinator = _CapturingSearchCoordinator()
    tool = WebSearchTool(coordinator=coordinator)
    runtime_context = RuntimeContext(
        user_id="user-1",
        timezone="Asia/Shanghai",
        locale="en-US",
        search_config=RuntimeSearchProviderContext(
            mode=MODE_CUSTOM,
            custom_providers=[
                {
                    "provider": "serper",
                    "api_key": "saved-key",
                    "enabled": True,
                }
            ],
        ),
    )

    result = asyncio.run(
        tool.execute(
            {
                "session_id": "session-1",
                "user_id": "user-1",
                RUNTIME_CONTEXT_KEY: runtime_context,
                "web_search_provider_mode": "default",
                "web_search_custom_providers": [
                    {
                        "provider": "tavily",
                        "api_key": "legacy-key",
                        "enabled": True,
                    }
                ],
            },
            queries=["python typing best practices", "Python typing guide"],
            mode="fast",
        )
    )

    assert "Python typing reference." in result
    assert coordinator.request is not None
    assert coordinator.request.provider_mode == MODE_CUSTOM
    assert coordinator.request.language == "en"
    assert coordinator.request.custom_provider_params[0].provider == "serper"
    assert coordinator.request.custom_provider_params[0].api_key == "saved-key"


def test_web_search_requires_ascii_english_query() -> None:
    coordinator = _CapturingSearchCoordinator()
    tool = WebSearchTool(coordinator=coordinator)

    result = asyncio.run(
        tool.execute(
            {"session_id": "session-1", "user_id": "user-1"},
            queries=["Python typing 最佳实践", "typing guía"],
            mode="fast",
        )
    )

    assert "requires at least one pure English query" in result
    assert "ASCII English words" in result
    assert coordinator.request is None


def test_resolve_time_timezone_is_strict_and_locale_is_optional() -> None:
    result = resolve_time_text(
        text="today",
        timezone_name="America/New_York",
        locale="en-US",
    )

    assert result.timezone == "America/New_York"

    with pytest.raises(TimeResolveError):
        resolve_time_text(text="today", timezone_name="")
    with pytest.raises(TimeResolveError):
        resolve_time_text(text="today", timezone_name="PST")
    with pytest.raises(TimeResolveError):
        resolve_time_text(text="today", timezone_name=" Asia/Shanghai ")
