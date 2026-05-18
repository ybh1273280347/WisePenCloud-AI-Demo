from __future__ import annotations

import asyncio
from typing import List, Optional, Sequence

from chat.application.web_search.errors import (
    CustomSearchProviderUnavailableError,
    SearchProviderError,
    SearchRateLimitError,
    SearchTimeoutError,
)
from chat.application.web_search.internal.cache import SearchCache, make_search_cache_key
from chat.application.web_search.internal.models.helpers import has_response_content
from chat.application.web_search.internal.planning.models import VariantSearchResponse
from chat.application.web_search.internal.searcher.brave_searcher import BraveSearcher
from chat.application.web_search.internal.searcher.exa_searcher import ExaSearcher
from chat.application.web_search.internal.searcher.perplexity_searcher import (
    PerplexitySearcher,
)
from chat.application.web_search.internal.searcher.serpapi_searcher import (
    SerpApiSearcher,
)
from chat.application.web_search.internal.searcher.serper_searcher import (
    SerperAuthError,
    SerperRateLimitError,
    SerperSearchError,
    SerperSearcher,
)
from chat.application.web_search.internal.searcher.tavily_searcher import TavilySearcher
from chat.application.web_search.internal.provider_policy import (
    CustomProviderCredential,
    ProviderCall,
    effective_with_images,
    hash_user_id,
    provider_params_hash,
)
from chat.application.web_search.search_provider_config.constants import (
    ERROR_EMPTY_RESULT,
    ERROR_INVALID_KEY,
    ERROR_NOT_CONFIGURED,
    ERROR_PROVIDER_ERROR,
    ERROR_RATE_LIMITED,
    ERROR_TIMEOUT,
    PUBLIC_ERROR_EMPTY_RESULT,
    PUBLIC_ERROR_KEY_INVALID,
    PUBLIC_ERROR_NOT_CONFIGURED,
    PUBLIC_ERROR_PROVIDER_ERROR,
    PUBLIC_ERROR_RATE_LIMITED,
    PUBLIC_ERROR_TIMEOUT,
    STATUS_INVALID,
    STATUS_PROVIDER_ERROR,
    STATUS_RATE_LIMITED,
)
from chat.application.web_search.utils.notes import add_note
from common.logger import log_event, log_fail

_CUSTOM_PARALLEL_LIMIT = 2
_CUSTOM_CACHE_PURPOSE = "recall"


async def run_custom_provider_calls(
    *,
    provider_calls: Sequence[ProviderCall],
    credentials: Sequence[CustomProviderCredential],
    cache: SearchCache,
    user_id: Optional[str],
    with_images: bool = False,
    notes: Optional[List[str]] = None,
    strict: bool = False,
) -> List[VariantSearchResponse]:
    if not provider_calls:
        return []

    if not user_id:
        if strict:
            raise CustomSearchProviderUnavailableError(
                provider="custom",
                public_code=PUBLIC_ERROR_NOT_CONFIGURED,
                status=STATUS_PROVIDER_ERROR,
                last_error_code="missing_user_id",
                message="Custom provider search requires authenticated user.",
            )
        add_note(
            notes,
            "Custom provider requires authenticated user_id; custom recall was skipped.",
        )
        log_fail(
            "Custom provider variant 搜索",
            "missing_user_id",
            calls=len(provider_calls),
        )
        return []

    credentials_by_provider = {
        credential.provider: credential
        for credential in credentials
        if credential.enabled
    }

    semaphore = asyncio.Semaphore(_CUSTOM_PARALLEL_LIMIT)

    async def run_one(call: ProviderCall) -> Optional[VariantSearchResponse]:
        async with semaphore:
            credential = credentials_by_provider.get(call.provider)
            if credential is None:
                if strict:
                    raise CustomSearchProviderUnavailableError(
                        provider=call.provider,
                        public_code=PUBLIC_ERROR_NOT_CONFIGURED,
                        status=STATUS_PROVIDER_ERROR,
                        last_error_code=ERROR_NOT_CONFIGURED,
                        message="Custom provider credential is missing.",
                    )
                return None

            return await _run_one_custom_provider_call(
                call=call,
                credential=credential,
                cache=cache,
                user_id=user_id,
                with_images=with_images,
                notes=notes,
                strict=strict,
            )

    raw_results = await asyncio.gather(
        *(run_one(call) for call in provider_calls),
        return_exceptions=True,
    )

    if strict:
        for item in raw_results:
            if isinstance(item, CustomSearchProviderUnavailableError):
                raise item
            if isinstance(item, Exception):
                raise _provider_error(
                    provider="custom",
                    message="Custom provider search failed.",
                )

    results = [item for item in raw_results if isinstance(item, VariantSearchResponse)]

    log_event(
        "Custom provider variant 搜索完成",
        calls=len(provider_calls),
        results=len(results),
    )

    return results


async def _run_one_custom_provider_call(
    *,
    call: ProviderCall,
    credential: CustomProviderCredential,
    cache: SearchCache,
    user_id: Optional[str],
    with_images: bool,
    notes: Optional[List[str]],
    strict: bool,
) -> Optional[VariantSearchResponse]:
    provider_with_images = effective_with_images(
        requested=with_images,
        provider=credential.provider,
        custom=True,
    )
    params_hash = provider_params_hash(
        {
            "provider": credential.provider,
            "max_results": call.max_results,
            "language": call.variant.language,
            "with_images": provider_with_images,
        }
    )
    cache_key = make_search_cache_key(
        source=f"custom:{credential.provider}",
        query=call.variant.text,
        max_results=call.max_results,
        with_images=provider_with_images,
        language=call.variant.language,
        engines=(credential.provider,),
        purpose=_CUSTOM_CACHE_PURPOSE,
        provider_mode="custom",
        user_id_hash=hash_user_id(user_id),
        provider_params_hash=params_hash,
    )

    cached = await cache.get(cache_key)
    if cached is not None:
        return VariantSearchResponse(
            variant=call.variant,
            response=cached.response,
            cache_hit=True,
        )

    try:
        response = await _search_custom_provider(
            call=call,
            credential=credential,
            with_images=provider_with_images,
        )
    except (SerperAuthError, SearchProviderError) as e:
        if strict:
            raise classify_custom_provider_failure(credential.provider, e) from e
        add_note(
            notes,
            f"Custom {credential.provider} search failed: key invalid or provider rejected the request.",
        )
        _log_custom_provider_failure(
            call, credential, e, error_type="auth_or_provider_error"
        )
        return None
    except (SerperRateLimitError, SearchRateLimitError) as e:
        if strict:
            raise classify_custom_provider_failure(credential.provider, e) from e
        add_note(
            notes,
            f"Custom {credential.provider} quota or rate limit was reached.",
        )
        _log_custom_provider_failure(call, credential, e, error_type="rate_limit")
        return None
    except (SerperSearchError, SearchTimeoutError) as e:
        if strict:
            raise classify_custom_provider_failure(credential.provider, e) from e
        add_note(
            notes,
            f"Custom {credential.provider} search timed out or failed; default results were kept.",
        )
        _log_custom_provider_failure(call, credential, e, error_type="provider_failure")
        return None

    if not has_response_content(response):
        if strict:
            raise _empty_result(credential.provider)
        return None

    await cache.set(cache_key, response)

    return VariantSearchResponse(
        variant=call.variant,
        response=response,
        cache_hit=False,
    )


async def run_custom_provider_verification(
    *,
    provider: str,
    api_key: str,
) -> None:
    from chat.application.web_search.internal.planning.models import QueryVariant

    credential = CustomProviderCredential(
        provider=provider,
        api_key=api_key,
        enabled=True,
        max_results=1,
        allow_secondary=False,
    )
    call = ProviderCall(
        provider=provider,
        variant=QueryVariant(
            id="verify",
            text="OpenAI",
            role="primary",
            language="en",
            engines=None,
            serial=False,
            max_results=1,
            weight=1.0,
        ),
        max_results=1,
    )

    try:
        response = await _search_custom_provider(
            call=call,
            credential=credential,
            with_images=False,
        )
    except Exception as e:
        raise classify_custom_provider_failure(provider, e) from e

    if not has_response_content(response):
        raise _empty_result(provider)


def classify_custom_provider_failure(
    provider: str,
    e: Exception,
) -> CustomSearchProviderUnavailableError:
    message = str(e).lower()

    if isinstance(e, SerperAuthError) or (
        isinstance(e, SearchProviderError) and "authentication failed" in message
    ):
        return CustomSearchProviderUnavailableError(
            provider=provider,
            public_code=PUBLIC_ERROR_KEY_INVALID,
            status=STATUS_INVALID,
            last_error_code=ERROR_INVALID_KEY,
            message="Custom provider API key is invalid.",
        )

    if isinstance(e, (SerperRateLimitError, SearchRateLimitError)):
        return CustomSearchProviderUnavailableError(
            provider=provider,
            public_code=PUBLIC_ERROR_RATE_LIMITED,
            status=STATUS_RATE_LIMITED,
            last_error_code=ERROR_RATE_LIMITED,
            message="Custom provider quota or rate limit was reached.",
        )

    if isinstance(e, SearchTimeoutError) or (
        isinstance(e, SerperSearchError) and "timed out" in message
    ):
        return CustomSearchProviderUnavailableError(
            provider=provider,
            public_code=PUBLIC_ERROR_TIMEOUT,
            status=STATUS_PROVIDER_ERROR,
            last_error_code=ERROR_TIMEOUT,
            message="Custom provider search timed out.",
        )

    return _provider_error(
        provider=provider,
        message="Custom provider search failed.",
    )


def _provider_error(
    *,
    provider: str,
    message: str,
) -> CustomSearchProviderUnavailableError:
    return CustomSearchProviderUnavailableError(
        provider=provider,
        public_code=PUBLIC_ERROR_PROVIDER_ERROR,
        status=STATUS_PROVIDER_ERROR,
        last_error_code=ERROR_PROVIDER_ERROR,
        message=message,
    )


def _empty_result(provider: str) -> CustomSearchProviderUnavailableError:
    return CustomSearchProviderUnavailableError(
        provider=provider,
        public_code=PUBLIC_ERROR_EMPTY_RESULT,
        status=STATUS_PROVIDER_ERROR,
        last_error_code=ERROR_EMPTY_RESULT,
        message="Custom provider returned no usable search results.",
    )


async def _search_custom_provider(
    *,
    call: ProviderCall,
    credential: CustomProviderCredential,
    with_images: bool,
):
    source = f"custom:{credential.provider}"
    searcher = _build_custom_searcher(credential)

    try:
        if credential.provider == "serper":
            return await searcher.search(
                call.variant.text,
                max_results=call.max_results,
                with_images=with_images,
                language=call.variant.language,
                source=source,
            )

        if credential.provider == "tavily":
            return await searcher.search(
                call.variant.text,
                max_results=call.max_results,
                with_images=False,
                source=source,
            )

        if credential.provider == "brave":
            return await searcher.search(
                call.variant.text,
                max_results=call.max_results,
                with_images=with_images,
                language=call.variant.language,
                source=source,
            )

        if credential.provider == "serpapi":
            return await searcher.search(
                call.variant.text,
                max_results=call.max_results,
                with_images=with_images,
                language=call.variant.language,
                source=source,
            )

        if credential.provider == "exa":
            return await searcher.search(
                call.variant.text,
                max_results=call.max_results,
                with_images=False,
                source=source,
            )

        if credential.provider == "perplexity":
            return await searcher.search(
                call.variant.text,
                max_results=call.max_results,
                with_images=False,
                source=source,
            )
    finally:
        await searcher.close()

    raise SearchProviderError(credential.provider, "unsupported custom provider")


def _build_custom_searcher(credential: CustomProviderCredential):
    from chat.core.config.app_settings import settings

    if credential.provider == "serper":
        return SerperSearcher(
            api_key=credential.api_key,
            base_url=settings.SERPER_BASE_URL,
        )
    if credential.provider == "tavily":
        return TavilySearcher(
            api_key=credential.api_key,
            base_url=settings.TAVILY_BASE_URL,
        )
    if credential.provider == "brave":
        return BraveSearcher(
            api_key=credential.api_key,
            base_url=settings.BRAVE_SEARCH_BASE_URL,
        )
    if credential.provider == "serpapi":
        return SerpApiSearcher(
            api_key=credential.api_key,
            base_url=settings.SERPAPI_BASE_URL,
        )
    if credential.provider == "exa":
        return ExaSearcher(
            api_key=credential.api_key,
            base_url=settings.EXA_BASE_URL,
        )
    if credential.provider == "perplexity":
        return PerplexitySearcher(
            api_key=credential.api_key,
            base_url=settings.PERPLEXITY_BASE_URL,
        )
    raise SearchProviderError(credential.provider, "unsupported custom provider")


def _log_custom_provider_failure(
    call: ProviderCall,
    credential: CustomProviderCredential,
    e: Exception,
    *,
    error_type: str,
) -> None:
    log_fail(
        "Custom provider variant 搜索",
        repr(e),
        provider=credential.provider,
        query_role=call.variant.role,
        language=call.variant.language,
        max_results=call.max_results,
        error_type=error_type,
    )
