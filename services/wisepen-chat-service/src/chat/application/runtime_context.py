from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from chat.application.web_search.search_provider_config.service import (
    RuntimeSearchProviderContext,
)
from chat.application.web_search.search_provider_config.constants import MODE_DEFAULT

RUNTIME_CONTEXT_KEY = "runtime_context"


@dataclass(frozen=True, slots=True)
class RuntimeContext:
    user_id: str
    timezone: str
    locale: str
    search_config: RuntimeSearchProviderContext = field(
        default_factory=lambda: RuntimeSearchProviderContext(mode=MODE_DEFAULT)
    )


def get_runtime_context(context: Mapping[str, Any]) -> Optional[RuntimeContext]:
    value = context.get(RUNTIME_CONTEXT_KEY)
    if isinstance(value, RuntimeContext):
        return value
    return None
