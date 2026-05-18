import sys
import types
from dataclasses import dataclass
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT.parent / "wisepen-common" / "src"))


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


@dataclass(frozen=True, slots=True)
class _FetchResultItem:
    url: str
    success: bool
    content: str = ""
    document: object = None
    error: str = ""


class _LocalScriptFetcher:
    pass


class _SteelFetcher:
    pass


class _SteelFetcherConfig:
    pass


_install_module_stub(
    "chat.application.tools.services.web_fetch.content_processor",
    ContentProcessor=_ContentProcessor,
)
_install_module_stub(
    "chat.application.tools.services.web_fetch.fetch_coordinator",
    FetchCoordinator=_FetchCoordinator,
    FetchResultItem=_FetchResultItem,
)
_install_module_stub(
    "chat.application.tools.services.web_fetch.fetcher.local_fetcher",
    LocalScriptFetcher=_LocalScriptFetcher,
)
_install_module_stub(
    "chat.application.tools.services.web_fetch.fetcher.steel_fetcher",
    SteelFetcher=_SteelFetcher,
    SteelFetcherConfig=_SteelFetcherConfig,
)
