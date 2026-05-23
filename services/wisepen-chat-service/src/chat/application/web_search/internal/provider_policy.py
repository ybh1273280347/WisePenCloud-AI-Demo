from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from chat.application.web_search.internal.planning.models import (
    QueryVariant,
    VariantSearchResponse,
)
from chat.application.web_search.provider_policy import CustomProviderCredential
from chat.application.web_search.utils.domains import extract_domain

_DEEP_FOURGET_MIN_USEFUL_RESULTS = 5
_DEEP_FOURGET_MIN_UNIQUE_DOMAINS = 3

@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supports_web: bool = True
    supports_images: bool = False


_PROVIDER_CAPABILITIES = {
    "fourget": ProviderCapabilities(supports_web=True, supports_images=False),
    "searxng": ProviderCapabilities(supports_web=True, supports_images=True),
    "serper": ProviderCapabilities(supports_web=True, supports_images=True),
    "custom:serper": ProviderCapabilities(supports_web=True, supports_images=True),
    "custom:serpapi": ProviderCapabilities(supports_web=True, supports_images=True),
    "custom:brave": ProviderCapabilities(supports_web=True, supports_images=True),
    "custom:tavily": ProviderCapabilities(supports_web=True, supports_images=False),
    "custom:exa": ProviderCapabilities(supports_web=True, supports_images=False),
    "custom:perplexity": ProviderCapabilities(supports_web=True, supports_images=False),
    "custom:anysearch": ProviderCapabilities(supports_web=True, supports_images=False),
}


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


def select_default_provider_calls(
    *,
    mode: str,
    variants: Tuple[QueryVariant, ...],
    primary_responses: Sequence[VariantSearchResponse],
    serper_enabled: bool,
    primary_provider: str = "fourget",
) -> Tuple[ProviderCall, ...]:
    if not serper_enabled:
        return ()

    primary = find_variant(variants, "primary")
    if primary is None:
        return ()

    useful = total_useful_results(primary_responses)

    if primary_provider == "fourget":
        if mode in {"fast", "normal", "deep"} and useful == 0:
            return (ProviderCall("serper", primary, primary.max_results),)
        if mode == "deep" and _needs_deep_serper_supplement(primary_responses):
            return (ProviderCall("serper", primary, primary.max_results),)
        return ()

    if mode == "fast":
        if useful == 0:
            return (ProviderCall("serper", primary, primary.max_results),)
        return ()

    if mode == "normal":
        if useful < 3:
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


def _needs_deep_serper_supplement(
    responses: Sequence[VariantSearchResponse],
) -> bool:
    useful = total_useful_results(responses)
    if useful < _DEEP_FOURGET_MIN_USEFUL_RESULTS:
        return True
    return unique_result_domains(responses) < _DEEP_FOURGET_MIN_UNIQUE_DOMAINS


def unique_result_domains(
    responses: Sequence[VariantSearchResponse],
) -> int:
    domains: set[str] = set()
    for item in responses:
        for result in item.response.results:
            domain = extract_domain(result.url)
            if domain:
                domains.add(domain)
    return len(domains)


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
