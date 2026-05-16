from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from chat.application.web_search.planning import QueryVariant, VariantSearchResponse

CUSTOM_PROVIDER_NAMES = frozenset(
    {"serper", "tavily", "brave", "serpapi", "exa", "perplexity"}
)

_MIN_USEFUL_RESULTS = 3

_DEFAULT_CUSTOM_MAX_RESULTS = 10


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supports_web: bool = True
    supports_images: bool = False


_PROVIDER_CAPABILITIES = {
    "searxng": ProviderCapabilities(supports_web=True, supports_images=True),
    "serper": ProviderCapabilities(supports_web=True, supports_images=True),
    "custom:serper": ProviderCapabilities(supports_web=True, supports_images=True),
    "custom:serpapi": ProviderCapabilities(supports_web=True, supports_images=True),
    "custom:brave": ProviderCapabilities(supports_web=True, supports_images=True),
    "custom:tavily": ProviderCapabilities(supports_web=True, supports_images=False),
    "custom:exa": ProviderCapabilities(supports_web=True, supports_images=False),
    "custom:perplexity": ProviderCapabilities(supports_web=True, supports_images=False),
}


@dataclass(frozen=True, slots=True)
class CustomProviderCredential:
    provider: str
    api_key: str
    enabled: bool = True
    max_results: int = _DEFAULT_CUSTOM_MAX_RESULTS
    allow_secondary: bool = False


@dataclass(frozen=True, slots=True)
class ProviderCall:
    provider: str
    variant: QueryVariant
    max_results: int


def provider_capabilities(
    provider: str, *, custom: bool = False
) -> ProviderCapabilities:
    normalized = provider.strip().lower()
    key = (
        f"custom:{normalized}"
        if custom and not normalized.startswith("custom:")
        else normalized
    )
    return _PROVIDER_CAPABILITIES.get(key, ProviderCapabilities())


def provider_supports_images(provider: str, *, custom: bool = False) -> bool:
    return provider_capabilities(provider, custom=custom).supports_images


def provider_supports_web(provider: str, *, custom: bool = False) -> bool:
    return provider_capabilities(provider, custom=custom).supports_web


def effective_with_images(
    *,
    requested: bool,
    provider: str,
    custom: bool = False,
) -> bool:
    return bool(requested and provider_supports_images(provider, custom=custom))


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

    provider = str(item.get("provider") or "").strip()
    api_key = str(item.get("api_key") or "").strip()
    enabled = bool(item.get("enabled", True))

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
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_CUSTOM_MAX_RESULTS

    return max(1, min(parsed, 20))


def select_default_provider_calls(
    *,
    mode: str,
    variants: Tuple[QueryVariant, ...],
    searxng_responses: Sequence[VariantSearchResponse],
    serper_enabled: bool,
) -> Tuple[ProviderCall, ...]:
    if not serper_enabled:
        return ()

    primary = find_variant(variants, "primary")
    if primary is None:
        return ()

    useful = total_useful_results(searxng_responses)

    if mode == "fast":
        if useful == 0:
            return (ProviderCall("serper", primary, primary.max_results),)
        return ()

    if mode == "normal":
        if useful < _MIN_USEFUL_RESULTS:
            return (ProviderCall("serper", primary, primary.max_results),)
        return ()

    if mode == "deep":
        return (ProviderCall("serper", primary, primary.max_results),)

    return ()


def select_custom_provider_calls(
    *,
    mode: str,
    variants: Tuple[QueryVariant, ...],
    credentials: Sequence[CustomProviderCredential],
    force: bool = False,
) -> Tuple[ProviderCall, ...]:
    if mode == "fast" and not force:
        return ()

    primary = find_variant(variants, "primary")
    if primary is None:
        return ()

    secondary = find_variant(variants, "secondary")
    calls: List[ProviderCall] = []

    for credential in credentials:
        calls.append(
            ProviderCall(
                provider=credential.provider,
                variant=primary,
                max_results=credential.max_results,
            )
        )

        if mode == "deep" and credential.allow_secondary and secondary is not None:
            calls.append(
                ProviderCall(
                    provider=credential.provider,
                    variant=secondary,
                    max_results=credential.max_results,
                )
            )

    return tuple(calls)


def find_variant(
    variants: Tuple[QueryVariant, ...],
    role: str,
) -> Optional[QueryVariant]:
    for variant in variants:
        if variant.role == role:
            return variant
    return None


def total_useful_results(
    responses: Sequence[VariantSearchResponse],
) -> int:
    total = 0
    for item in responses:
        for result in item.response.results:
            if result.title.strip() and result.url.strip():
                total += 1
    return total


def hash_user_id(user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None

    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:16]


def provider_params_hash(params: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        params,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]
