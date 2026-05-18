from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from chat.application.web_search.errors import CustomSearchProviderUnavailableError
from chat.application.web_search.internal.runner.custom_provider_runner import (
    run_custom_provider_verification,
)
from chat.application.web_search.search_provider_config.constants import (
    ERROR_PROVIDER_ERROR,
    STATUS_PROVIDER_ERROR,
    STATUS_VALID,
)


@dataclass(frozen=True, slots=True)
class SearchProviderVerificationResult:
    status: str
    last_error_code: Optional[str]


class SearchProviderConfigValidator:
    async def verify(
        self,
        *,
        provider: str,
        api_key: str,
    ) -> SearchProviderVerificationResult:
        try:
            await run_custom_provider_verification(provider=provider, api_key=api_key)
        except CustomSearchProviderUnavailableError as e:
            return SearchProviderVerificationResult(
                status=e.status,
                last_error_code=e.last_error_code,
            )
        except Exception:
            return SearchProviderVerificationResult(
                status=STATUS_PROVIDER_ERROR,
                last_error_code=ERROR_PROVIDER_ERROR,
            )

        return SearchProviderVerificationResult(
            status=STATUS_VALID,
            last_error_code=None,
        )
