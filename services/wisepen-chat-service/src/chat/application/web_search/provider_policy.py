from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

CUSTOM_PROVIDER_NAMES = frozenset(
    {"serper", "tavily", "brave", "serpapi", "exa", "perplexity"}
)

_DEFAULT_CUSTOM_MAX_RESULTS = 10


@dataclass(frozen=True, slots=True)
class CustomProviderCredential:
    provider: str
    api_key: str
    enabled: bool = True
    max_results: int = _DEFAULT_CUSTOM_MAX_RESULTS
    allow_secondary: bool = False


def parse_custom_provider_credentials(
    raw_items: Any,
) -> Tuple[CustomProviderCredential, ...]:
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        return ()

    credentials: List[CustomProviderCredential] = []

    for item in raw_items:
        credential = _parse_custom_provider_credential(item)
        if credential is not None:
            credentials.append(credential)

    return tuple(credentials)


def _parse_custom_provider_credential(item: Any) -> Optional[CustomProviderCredential]:
    if isinstance(item, CustomProviderCredential):
        if not _is_supported_enabled_provider(
            item.provider, item.api_key, item.enabled
        ):
            return None
        return item

    if not isinstance(item, Mapping):
        return None

    provider = item.get("provider")
    api_key = item.get("api_key")
    enabled = bool(item.get("enabled", True))

    if not isinstance(provider, str) or not isinstance(api_key, str):
        return None

    if not _is_supported_enabled_provider(provider, api_key, enabled):
        return None

    return CustomProviderCredential(
        provider=provider,
        api_key=api_key,
        enabled=enabled,
        max_results=_coerce_max_results(item.get("max_results")),
        allow_secondary=bool(item.get("allow_secondary", False)),
    )


def _is_supported_enabled_provider(provider: str, api_key: str, enabled: bool) -> bool:
    return enabled and provider in CUSTOM_PROVIDER_NAMES and bool(api_key)


def _coerce_max_results(value: Any) -> int:
    if value is None:
        return _DEFAULT_CUSTOM_MAX_RESULTS
    if not isinstance(value, int):
        return _DEFAULT_CUSTOM_MAX_RESULTS

    return max(1, min(value, 20))
