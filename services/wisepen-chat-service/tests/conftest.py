import sys
import types


def _install_module_stub(name: str, **attrs) -> None:
    if name in sys.modules:
        return

    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module


class _ContentProcessor:
    pass


class _FetchCoordinator:
    pass


class _FetchResultItem:
    pass


class _LocalScriptFetcher:
    pass


class _SteelFetcher:
    pass


class _SteelFetcherConfig:
    pass


_install_module_stub(
    "chat.application.web_fetch.content_processor",
    ContentProcessor=_ContentProcessor,
)
_install_module_stub(
    "chat.application.web_fetch.fetch_coordinator",
    FetchCoordinator=_FetchCoordinator,
    FetchResultItem=_FetchResultItem,
)
_install_module_stub(
    "chat.application.web_fetch.fetcher.local_fetcher",
    LocalScriptFetcher=_LocalScriptFetcher,
)
_install_module_stub(
    "chat.application.web_fetch.fetcher.steel_fetcher",
    SteelFetcher=_SteelFetcher,
    SteelFetcherConfig=_SteelFetcherConfig,
)
