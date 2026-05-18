from chat.application.web_search.search_provider_config.constants import (
    ERROR_NOT_CONFIGURED,
    MODE_CUSTOM,
    MODE_DEFAULT,
    MODES,
    PROVIDERS,
    PUBLIC_ERROR_NOT_CONFIGURED,
    STATUS_PROVIDER_ERROR,
)

__all__ = [
    "ERROR_NOT_CONFIGURED",
    "MODE_CUSTOM",
    "MODE_DEFAULT",
    "MODES",
    "PROVIDERS",
    "PUBLIC_ERROR_NOT_CONFIGURED",
    "RuntimeSearchProviderContext",
    "SearchProviderConfigService",
    "STATUS_PROVIDER_ERROR",
]


def __getattr__(name: str):
    if name in {"RuntimeSearchProviderContext", "SearchProviderConfigService"}:
        from chat.application.web_search.search_provider_config.service import (
            RuntimeSearchProviderContext,
            SearchProviderConfigService,
        )

        exports = {
            "RuntimeSearchProviderContext": RuntimeSearchProviderContext,
            "SearchProviderConfigService": SearchProviderConfigService,
        }
        globals().update(exports)
        return exports[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
