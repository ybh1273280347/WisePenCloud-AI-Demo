from .checker import LOCK_FILE_PREFIXES, check_profile_dir, check_writable
from .config import AutomationProfileConfig
from .models import (
    ProfileDirCheck,
    ResolveFailure,
    ResolveFailureReason,
    ResolveResult,
    ResolveSource,
    ResolveSuccess,
)
from .presenter import describe_resolve_result, summarize_check
from .resolver import BrowserAutomationProfileResolver

__all__ = [
    "AutomationProfileConfig",
    "BrowserAutomationProfileResolver",
    "LOCK_FILE_PREFIXES",
    "ProfileDirCheck",
    "ResolveFailure",
    "ResolveFailureReason",
    "ResolveResult",
    "ResolveSource",
    "ResolveSuccess",
    "check_profile_dir",
    "check_writable",
    "describe_resolve_result",
    "summarize_check",
]