import asyncio
import importlib.util
import re
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

import chat.application.chat_context_assembler as assembler_module
from chat.api.schemas.chat import ChatRequest
from chat.application.chat_context_assembler import ChatContextAssembler
from chat.application.prompt_context.current_date import (
    DEFAULT_TIMEZONE_NAME,
    build_current_date_context,
    get_current_datetime,
)
from chat.application.runtime_context import RUNTIME_CONTEXT_KEY, RuntimeContext
from chat.application.tools.services.temporal import (
    FreshnessPolicy,
    TimeResolutionMode,
    TimeResolveError,
    resolve_time_text,
)
from chat.application.tools.web.web_search_tool import WebSearchTool
from chat.application.web_search import (
    SearchManyResult,
    SearchResponse,
    SearchResult,
)
from chat.application.web_search.search_provider_config.constants import MODE_CUSTOM
from chat.application.web_search.search_provider_config.service import (
    RuntimeSearchProviderContext,
)


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


class _EmptyMessageRepository:
    pass


class _EmptySessionRepository:
    pass


class _EmptyHotContextRepository:
    pass


class _PromptMessage:
    def __init__(self, **kwargs) -> None:
        self.session_id = kwargs.get("session_id")
        self.role = kwargs.get("role")
        self.content = kwargs.get("content")
        self.tool_calls = kwargs.get("tool_calls")
        self.tool_call_id = kwargs.get("tool_call_id")


def test_current_date_context_uses_asia_shanghai_date_only() -> None:
    now = get_current_datetime()
    context = build_current_date_context()
    service_root = Path(__file__).resolve().parents[1]
    current_date_source = (
        service_root
        / "src"
        / "chat"
        / "application"
        / "prompt_context"
        / "current_date.py"
    ).read_text(encoding="utf-8")

    assert now.tzinfo is not None
    assert DEFAULT_TIMEZONE_NAME == "Asia/Shanghai"
    assert "Current date:" in context
    assert "timezone baseline: Asia/Shanghai" in context
    assert re.search(r"Current date: \d{4}-\d{2}-\d{2} \([A-Za-z]+\)", context)
    assert now.strftime("%H:%M") not in context
    assert not re.search(r"\d{2}:\d{2}:\d{2}", context)
    assert "pytz" not in current_date_source
    assert "strftime" not in current_date_source

    weekdays = (
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    )
    expected_weekday = weekdays[now.weekday()]
    assert f"({expected_weekday})" in context


def test_system_prompt_appends_current_date_context_to_base_prompt(monkeypatch) -> None:
    monkeypatch.setattr(assembler_module, "ChatMessage", _PromptMessage)
    assembler = ChatContextAssembler(
        message_repo=_EmptyMessageRepository(),
        session_repo=_EmptySessionRepository(),
        hot_context_repo=_EmptyHotContextRepository(),
    )

    messages = assembler.assemble_prompt(
        session_id="session-1",
        user_query="今天有什么安排？",
        windowed_messages=[],
        relevant_facts=[],
        session_summary=None,
    )

    assert messages[0].role.value == "system"
    system_prompt = messages[0].content
    assert "# Role" in system_prompt
    assert "Current date:" in system_prompt
    assert system_prompt.index("# Role") < system_prompt.index("Current date:")
    assert not re.search(r"\d{2}:\d{2}:\d{2}", system_prompt)

    user_messages = [message for message in messages if message.role.value == "user"]
    assert len(user_messages) == 1
    assert user_messages[0].content == "今天有什么安排？"
    assert "Current date:" not in user_messages[0].content


def test_chat_request_rejects_removed_runtime_fields() -> None:
    removed_fields = ("client_context", "timezone", "locale", "current_time")
    for field_name in removed_fields:
        with pytest.raises(ValidationError):
            ChatRequest(
                session_id="session-1",
                query="hello",
                **{field_name: "x"},
            )


def test_user_preferences_api_is_not_registered() -> None:
    service_root = Path(__file__).resolve().parents[1]
    router_source = (service_root / "src" / "chat" / "api" / "router.py").read_text(
        encoding="utf-8"
    )

    assert "user_preferences" not in router_source
    assert "preferences/timezone" not in router_source
    assert "preferences/locale" not in router_source


def test_user_preferences_modules_are_removed() -> None:
    removed_modules = (
        "chat.api.schemas.user_preferences",
        "chat.api.endpoints.user_preferences",
        "chat.application.user_preferences",
        "chat.domain.entities.user_preferences",
    )

    for module_name in removed_modules:
        assert importlib.util.find_spec(module_name) is None


def test_runtime_context_contains_search_config_only() -> None:
    fields = set(RuntimeContext.__dataclass_fields__)

    assert "user_id" in fields
    assert "search_config" in fields
    assert "timezone" not in fields
    assert "locale" not in fields


def test_web_search_uses_runtime_search_config_and_no_locale_hint() -> None:
    coordinator = _CapturingSearchCoordinator()
    tool = WebSearchTool(coordinator=coordinator)
    runtime_context = RuntimeContext(
        user_id="user-1",
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
    assert coordinator.request.language is None
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


def test_resolve_time_default_timezone_and_valid_timezone() -> None:
    current_time_result = resolve_time_text(text="现在几点", timezone_name=None)
    default_result = resolve_time_text(text="今天几号", timezone_name=None)
    ny_result = resolve_time_text(
        text="纽约现在几点",
        timezone_name="America/New_York",
    )

    assert current_time_result.timezone == "Asia/Shanghai"
    assert default_result.timezone == "Asia/Shanghai"
    assert ny_result.timezone == "America/New_York"


def test_resolve_time_timezone_is_strict() -> None:
    invalid_timezones = ("", "Shanghai", "北京时间", "PST", " Asia/Shanghai ")

    for timezone_name in invalid_timezones:
        with pytest.raises(TimeResolveError):
            resolve_time_text(text="today", timezone_name=timezone_name)


def test_resolve_time_no_temporal_expression_returns_no_constraint() -> None:
    result = resolve_time_text(text="介绍一下 Transformer", timezone_name=None)

    assert result.mode == TimeResolutionMode.NO_CONSTRAINT
    assert result.freshness_policy == FreshnessPolicy.ANY
    assert result.start is None
    assert result.end is None
    assert result.order_by_time_desc is False
    assert result.limit is None
    assert (
        result.explanation
        == "No temporal expression was detected; no time filter should be applied."
    )


def test_current_date_context_date_matches_iso_format() -> None:
    context = build_current_date_context()
    match = re.search(r"Current date: (\d{4}-\d{2}-\d{2})", context)

    assert match is not None
    datetime.fromisoformat(match.group(1))
