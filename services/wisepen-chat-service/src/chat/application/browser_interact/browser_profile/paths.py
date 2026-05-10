import os
import sys
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from chat.application.browser_interact.browser_profile.catalog import SYSTEM_BROWSER_CATALOG

APP_NAME = "WisePenCloud"
CONFIG_FILE_NAME = "config.json"
BROWSER_CHANNELS: Tuple[str, ...] = ("chrome", "msedge", "chromium")
DEFAULT_BROWSER_CHANNEL = "chrome"


def resolve_home(home: Optional[Path]) -> Path:
    return Path.home() if home is None else Path(home)


def resolve_env(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    return os.environ if env is None else env


def normalize_channel(browser_channel: Optional[str]) -> Optional[str]:
    if browser_channel is None:
        return DEFAULT_BROWSER_CHANNEL

    normalized = browser_channel.strip().lower()
    if not normalized:
        return DEFAULT_BROWSER_CHANNEL

    if normalized not in BROWSER_CHANNELS:
        return None

    return normalized


def default_automation_profile_dir(
    *,
    browser_channel: str = DEFAULT_BROWSER_CHANNEL,
    platform: str = sys.platform,
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Path:
    resolved_home = resolve_home(home)
    resolved_env = resolve_env(env)

    if platform == "win32":
        base = resolved_env.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_NAME / "browser-profiles" / browser_channel
        return resolved_home / APP_NAME / "browser-profiles" / browser_channel

    if platform == "darwin":
        return (
            resolved_home
            / "Library"
            / "Application Support"
            / APP_NAME
            / "browser-profiles"
            / browser_channel
        )

    if platform == "linux":
        xdg_data = resolved_env.get("XDG_DATA_HOME")
        data_base = Path(xdg_data) if xdg_data else resolved_home / ".local" / "share"
        return data_base / APP_NAME / "browser-profiles" / browser_channel

    return resolved_home / APP_NAME / "browser-profiles" / browser_channel


def default_config_file(
    *,
    platform: str = sys.platform,
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Path:
    resolved_home = resolve_home(home)
    resolved_env = resolve_env(env)

    if platform == "win32":
        app_data = resolved_env.get("APPDATA") or resolved_env.get("LOCALAPPDATA")
        if app_data:
            return Path(app_data) / APP_NAME / CONFIG_FILE_NAME
        return resolved_home / APP_NAME / CONFIG_FILE_NAME

    if platform == "darwin":
        return (
            resolved_home
            / "Library"
            / "Application Support"
            / APP_NAME
            / CONFIG_FILE_NAME
        )

    if platform == "linux":
        xdg = resolved_env.get("XDG_CONFIG_HOME")
        config_base = Path(xdg) if xdg else resolved_home / ".config"
        return config_base / APP_NAME / CONFIG_FILE_NAME

    return resolved_home / APP_NAME / CONFIG_FILE_NAME


def find_system_browser_dir(
    *,
    browser_channel: str,
    platform: str = sys.platform,
    home: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
    catalog: Sequence[SystemBrowserDefinition] = SYSTEM_BROWSER_CATALOG,
) -> Optional[Path]:
    resolved_home = resolve_home(home)
    resolved_env = resolve_env(env)

    for definition in catalog:
        if definition.channel != browser_channel:
            continue

        templates = definition.paths_by_platform.get(platform)
        if templates is None:
            continue

        for template in templates:
            base = resolve_template_base(template, resolved_home, resolved_env)
            if base is None:
                continue
            return base.joinpath(*template.parts)

    return None


def mask_home(path: Path, home: Path) -> str:
    try:
        relative = path.resolve(strict=False).relative_to(home.resolve(strict=False))
        return "~/" + str(relative)
    except ValueError:
        return str(path)


def resolve_template_base(
    template: PathTemplate,
    home: Path,
    env: Mapping[str, str],
) -> Optional[Path]:
    if template.base == PathBase.HOME:
        return home

    if template.base == PathBase.ENV:
        env_var = template.env_var
        if not env_var:
            return None

        value = env.get(env_var)
        if not value:
            return None

        return Path(value)

    return None