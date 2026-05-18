from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, TypeAlias


@dataclass(frozen=True, slots=True)
class ProfileDirCheck:
    path: Path
    exists: bool = False
    is_dir: bool = False
    readable: bool = False
    writable: Optional[bool] = None
    locked: bool = False
    detail: Optional[str] = None

    @property
    def usable(self) -> bool:
        if not self.exists:
            return False
        if not self.is_dir:
            return False
        if not self.readable:
            return False
        if self.locked:
            return False
        if self.writable is False:
            return False
        return True


class ResolveSource(Enum):
    CLI = "cli"
    PERSISTED = "persisted"
    DEFAULT_PROFILE = "default_profile"


class ResolveFailureReason(Enum):
    INVALID_CLI_PROFILE = "invalid_cli_profile"
    INVALID_BROWSER_CHANNEL = "invalid_browser_channel"
    PROFILE_LOCKED = "profile_locked"
    PROFILE_UNAVAILABLE = "profile_unavailable"
    UNSUPPORTED_PLATFORM = "unsupported_platform"


@dataclass(frozen=True, slots=True)
class ResolveSuccess:
    automation_user_data_dir: Path
    browser_channel: str
    source: ResolveSource
    check: Optional[ProfileDirCheck] = None
    warning: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ResolveFailure:
    reason: ResolveFailureReason
    check: Optional[ProfileDirCheck] = None
    message: Optional[str] = None


ResolveResult: TypeAlias = ResolveSuccess | ResolveFailure
