import sys
from pathlib import Path
from typing import Mapping, Optional, Tuple

from common.logger import log_event

from .checker import (
    LOCK_FILE_PREFIXES,
    check_profile_dir,
    ensure_directory,
)
from .config import AutomationProfileConfig
from .presenter import summarize_check
from .models import (
    ResolveFailure,
    ResolveFailureReason,
    ResolveResult,
    ResolveSource,
    ResolveSuccess,
)
from .paths import (
    default_automation_profile_dir,
    find_system_browser_dir,
    mask_home,
    normalize_channel,
    resolve_env,
    resolve_home,
)

class BrowserAutomationProfileResolver:
    def __init__(
        self,
        *,
        config: Optional[AutomationProfileConfig] = None,
        platform: str = sys.platform,
        home: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
        lock_prefixes: Tuple[str, ...] = LOCK_FILE_PREFIXES,
        probe_writable: bool = False,
    ) -> None:
        self.platform = platform
        self.home = resolve_home(home)
        self.env = resolve_env(env)
        self.lock_prefixes = lock_prefixes
        self.probe_writable = probe_writable
        self.config = config or AutomationProfileConfig(
            platform=self.platform,
            home=self.home,
            env=self.env,
        )

    def resolve(
        self,
        cli_automation_user_data_dir: Optional[str | Path] = None,
        *,
        persist_cli_profile: bool = False,
        browser_channel: Optional[str] = None,
    ) -> ResolveResult:
        if self.platform not in SUPPORTED_PLATFORMS:
            return ResolveFailure(
                reason=ResolveFailureReason.UNSUPPORTED_PLATFORM,
                message=f"unsupported platform: {self.platform}",
            )

        channel = normalize_channel(browser_channel)
        if channel is None:
            return ResolveFailure(
                reason=ResolveFailureReason.INVALID_BROWSER_CHANNEL,
                message=f"invalid browser channel: {browser_channel}",
            )

        if cli_automation_user_data_dir is not None:
            return self._resolve_cli_dir(
                cli_automation_user_data_dir,
                channel,
                persist_cli_profile,
            )

        persisted_result = self._resolve_persisted_dir(
            requested_channel=browser_channel,
            fallback_channel=channel,
        )
        if persisted_result is not None:
            return persisted_result

        return self._resolve_default_profile(channel)

    def _resolve_cli_dir(
        self,
        cli_dir: str | Path,
        channel: str,
        persist: bool,
    ) -> ResolveResult:
        check = check_profile_dir(
            cli_dir,
            lock_prefixes=self.lock_prefixes,
            probe_writable=self.probe_writable,
        )

        if not check.exists:
            mkdir_error_check = ensure_directory(cli_dir)
            if mkdir_error_check is not None:
                return ResolveFailure(
                    reason=ResolveFailureReason.INVALID_CLI_PROFILE,
                    check=mkdir_error_check,
                )

            check = check_profile_dir(
                cli_dir,
                lock_prefixes=self.lock_prefixes,
                probe_writable=self.probe_writable,
            )

        if check.locked:
            return ResolveFailure(
                reason=ResolveFailureReason.PROFILE_LOCKED,
                check=check,
            )

        if not check.usable:
            return ResolveFailure(
                reason=ResolveFailureReason.INVALID_CLI_PROFILE,
                check=check,
            )

        warning = self._system_profile_warning(check.path, channel)

        if persist:
            save_warning = self.config.save(check.path, channel)
            if save_warning:
                warning = f"{warning}\n{save_warning}" if warning else save_warning

        return ResolveSuccess(
            automation_user_data_dir=check.path,
            browser_channel=channel,
            source=ResolveSource.CLI,
            check=check,
            warning=warning,
        )

    def _resolve_persisted_dir(
        self,
        *,
        requested_channel: Optional[str],
        fallback_channel: str,
    ) -> Optional[ResolveResult]:
        persisted_dir, persisted_channel = self.config.load()

        if persisted_dir is None:
            return None

        if (
            requested_channel is not None
            and persisted_channel is not None
            and persisted_channel != fallback_channel
        ):
            log_event(
                "已保存的自动化浏览器 channel 与请求不一致，回退到默认 profile",
                persisted_channel=persisted_channel,
                requested_channel=fallback_channel,
            )
            return None

        channel = (
            fallback_channel
            if requested_channel is not None
            else persisted_channel or fallback_channel
        )

        check = check_profile_dir(
            persisted_dir,
            lock_prefixes=self.lock_prefixes,
            probe_writable=self.probe_writable,
        )

        if check.locked:
            return ResolveFailure(
                reason=ResolveFailureReason.PROFILE_LOCKED,
                check=check,
            )

        if check.usable:
            return ResolveSuccess(
                automation_user_data_dir=check.path,
                browser_channel=channel,
                source=ResolveSource.PERSISTED,
                check=check,
            )

        log_event(
            "已保存的自动化浏览器配置不可用，回退到默认 profile",
            path=mask_home(check.path, self.home),
            state=check.detail or summarize_check(check),
        )

        return None

    def _resolve_default_profile(self, channel: str) -> ResolveResult:
        profile_dir = default_automation_profile_dir(
            browser_channel=channel,
            platform=self.platform,
            home=self.home,
            env=self.env,
        )

        check = check_profile_dir(
            profile_dir,
            lock_prefixes=self.lock_prefixes,
            probe_writable=self.probe_writable,
        )

        if not check.exists:
            mkdir_error_check = ensure_directory(profile_dir)
            if mkdir_error_check is not None:
                return ResolveFailure(
                    reason=ResolveFailureReason.PROFILE_UNAVAILABLE,
                    check=mkdir_error_check,
                )

            check = check_profile_dir(
                profile_dir,
                lock_prefixes=self.lock_prefixes,
                probe_writable=self.probe_writable,
            )

        if check.locked:
            return ResolveFailure(
                reason=ResolveFailureReason.PROFILE_LOCKED,
                check=check,
            )

        if not check.usable:
            return ResolveFailure(
                reason=ResolveFailureReason.PROFILE_UNAVAILABLE,
                check=check,
            )

        return ResolveSuccess(
            automation_user_data_dir=check.path,
            browser_channel=channel,
            source=ResolveSource.DEFAULT_PROFILE,
            check=check,
        )

    def _system_profile_warning(self, profile_dir: Path, channel: str) -> Optional[str]:
        system_dir = find_system_browser_dir(
            browser_channel=channel,
            platform=self.platform,
            home=self.home,
            env=self.env,
        )

        if system_dir is None:
            return None

        if profile_dir == system_dir.resolve(strict=False):
            return (
                "你指定的是系统浏览器主 User Data 目录。"
                "该目录可能与日常浏览器冲突，也可能不被 Playwright 稳定支持。"
                "建议使用本工具专用 automation profile。"
            )

        return None