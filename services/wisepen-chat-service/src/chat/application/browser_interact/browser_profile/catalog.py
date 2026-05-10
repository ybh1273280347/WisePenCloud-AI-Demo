from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple

class PathBase(Enum):
    HOME = "home"
    ENV = "env"


@dataclass(frozen=True, slots=True)
class PathTemplate:
    base: PathBase
    parts: Tuple[str, ...] = ()
    env_var: Optional[str] = None


@dataclass(frozen=True)
class SystemBrowserDefinition:
    name: str
    channel: str
    paths_by_platform: Dict[str, Tuple[PathTemplate, ...]]


SYSTEM_BROWSER_CATALOG: Tuple[SystemBrowserDefinition, ...] = (
    SystemBrowserDefinition(
        name="Chrome",
        channel="chrome",
        paths_by_platform={
            "win32": (
                PathTemplate(
                    PathBase.ENV,
                    ("Google", "Chrome", "User Data"),
                    env_var="LOCALAPPDATA",
                ),
            ),
            "darwin": (
                PathTemplate(
                    PathBase.HOME,
                    ("Library", "Application Support", "Google", "Chrome"),
                ),
            ),
            "linux": (
                PathTemplate(
                    PathBase.HOME,
                    (".config", "google-chrome"),
                ),
            ),
        },
    ),
    SystemBrowserDefinition(
        name="Edge",
        channel="msedge",
        paths_by_platform={
            "win32": (
                PathTemplate(
                    PathBase.ENV,
                    ("Microsoft", "Edge", "User Data"),
                    env_var="LOCALAPPDATA",
                ),
            ),
            "darwin": (
                PathTemplate(
                    PathBase.HOME,
                    ("Library", "Application Support", "Microsoft Edge"),
                ),
            ),
            "linux": (
                PathTemplate(
                    PathBase.HOME,
                    (".config", "microsoft-edge"),
                ),
            ),
        },
    ),
    SystemBrowserDefinition(
        name="Chromium",
        channel="chromium",
        paths_by_platform={
            "win32": (
                PathTemplate(
                    PathBase.ENV,
                    ("Chromium", "User Data"),
                    env_var="LOCALAPPDATA",
                ),
            ),
            "darwin": (
                PathTemplate(
                    PathBase.HOME,
                    ("Library", "Application Support", "Chromium"),
                ),
            ),
            "linux": (
                PathTemplate(
                    PathBase.HOME,
                    (".config", "chromium"),
                ),
            ),
        },
    ),
)