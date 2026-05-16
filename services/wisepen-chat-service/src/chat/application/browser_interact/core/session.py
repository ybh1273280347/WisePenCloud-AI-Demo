import asyncio
import secrets
from typing import Optional, Tuple

from common.logger import log_error, log_fail
from playwright.async_api import BrowserContext, Page, async_playwright

from ..browser_profile.checker import check_profile_dir
from .protocol import ToolErrorCode

_SESSION_ID_BYTES = 12


class BrowserSessionError(RuntimeError):
    def __init__(
        self, message: str, diagnostic_code: str = "BROWSER_SESSION_ERROR"
    ) -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code


class BrowserSessionManager:
    def __init__(
        self,
        automation_user_data_dir=None,
        browser_channel=None,
        timeout: int = 30,
        disable_sandbox: bool = False,
        disable_dev_shm_usage: bool = False,
    ) -> None:
        self._automation_user_data_dir = automation_user_data_dir
        self._browser_channel = browser_channel
        self._timeout = timeout
        self._disable_sandbox = disable_sandbox
        self._disable_dev_shm_usage = disable_dev_shm_usage

        self._playwright = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None
        self._session_id: Optional[str] = None
        self._lock = asyncio.Lock()

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @property
    def page(self) -> Optional[Page]:
        return self._page

    @property
    def has_session(self) -> bool:
        return self._session_id is not None

    @property
    def is_session_alive(self) -> bool:
        return self._page is not None and not self._page.is_closed()

    async def cleanup(self) -> None:
        await self._close()

    async def _close(self) -> None:
        if self._page is not None:
            try:
                await self._page.close()
            except Exception:
                log_fail("关闭 page，可能存在资源泄漏", "")
            self._page = None

        if self._context is not None:
            try:
                await self._context.close()
            except Exception:
                log_fail("关闭 browser context，可能存在资源泄漏", "")
            self._context = None

        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:
                log_fail("停止 Playwright，可能存在资源泄漏", "")
            self._playwright = None

        self._session_id = None

    async def validate_session(
        self,
        browser_session_id: Optional[str],
    ) -> Optional[ToolErrorCode]:
        if browser_session_id is None:
            return ToolErrorCode.SESSION_REQUIRED

        if self._session_id is None:
            return ToolErrorCode.SESSION_NOT_FOUND

        if browser_session_id != self._session_id:
            return ToolErrorCode.SESSION_MISMATCH

        if not self.is_session_alive:
            return ToolErrorCode.SESSION_EXPIRED

        return None

    async def get_existing_page(
        self,
        browser_session_id: Optional[str],
    ) -> Tuple[Optional[Page], Optional[ToolErrorCode]]:
        browser_session_id = browser_session_id or None
        error_code = await self.validate_session(browser_session_id)
        if error_code is not None:
            return None, error_code

        return self._page, None

    async def get_or_create_page(
        self,
        browser_session_id: Optional[str],
    ) -> Tuple[Optional[Page], Optional[ToolErrorCode]]:
        browser_session_id = browser_session_id or None

        if browser_session_id is not None:
            error_code = await self.validate_session(browser_session_id)
            if error_code is not None:
                return None, error_code
            return self._page, None

        async with self._lock:
            if self._page is not None and not self._page.is_closed():
                return self._page, None

            try:
                precheck_error = self._pre_launch_check()
                if precheck_error is not None:
                    return None, precheck_error

                if self._playwright is None:
                    self._playwright = await async_playwright().start()

                launch_args = []
                if self._disable_sandbox:
                    launch_args.append("--no-sandbox")
                if self._disable_dev_shm_usage:
                    launch_args.append("--disable-dev-shm-usage")

                launch_kwargs = {
                    "headless": False,
                    "timeout": self._timeout * 1000,
                    "user_data_dir": str(self._automation_user_data_dir),
                }

                if self._browser_channel:
                    launch_kwargs["channel"] = self._browser_channel

                if launch_args:
                    launch_kwargs["args"] = launch_args

                self._context = (
                    await self._playwright.chromium.launch_persistent_context(
                        **launch_kwargs
                    )
                )

                self._page = (
                    self._context.pages[0]
                    if self._context.pages
                    else await self._context.new_page()
                )
                self._session_id = secrets.token_hex(_SESSION_ID_BYTES)

                return self._page, None

            except Exception as e:
                diagnostic_code = self._classify_launch_error(e)
                log_error(
                    "浏览器启动",
                    str(e),
                    diagnostic_code=diagnostic_code,
                    automation_user_data_dir=str(self._automation_user_data_dir),
                    browser_channel=self._browser_channel,
                )
                await self._close()
                raise BrowserSessionError(
                    f"Failed to create browser session: {e}",
                    diagnostic_code=diagnostic_code,
                ) from e

    def _pre_launch_check(self) -> Optional[ToolErrorCode]:
        if self._automation_user_data_dir:
            check = check_profile_dir(self._automation_user_data_dir)
            if check.locked:
                log_fail(
                    "浏览器启动预检",
                    "配置文件目录已被锁定",
                    path=str(check.path),
                    diagnostic_code="PROFILE_LOCKED",
                )
                return ToolErrorCode.PROFILE_LOCKED
            if not check.usable:
                log_fail(
                    "浏览器启动预检",
                    "配置文件目录不可用",
                    path=str(check.path),
                    exists=check.exists,
                    is_dir=check.is_dir,
                    readable=check.readable,
                    writable=check.writable,
                    detail=check.detail,
                    diagnostic_code="PROFILE_UNAVAILABLE",
                )
                return ToolErrorCode.PROFILE_UNAVAILABLE
        return None

    @staticmethod
    def _classify_launch_error(e: Exception) -> str:
        message = str(e).lower()
        if "executable doesn't exist" in message or "executable not found" in message:
            return "INVALID_BROWSER_CHANNEL"
        if "profile is locked" in message or "singletonlock" in message:
            return "PROFILE_LOCKED"
        if "permission denied" in message or "access is denied" in message:
            return "PROFILE_UNAVAILABLE"
        if (
            "no display" in message
            or "missing x server" in message
            or "xvfb" in message
        ):
            return "NO_DISPLAY_SERVER"
        if "target page, context or browser has been closed" in message:
            return "BROWSER_CRASHED_ON_START"
        if "timeout" in message:
            return "LAUNCH_TIMEOUT"
        return "BROWSER_LAUNCH_FAILED"
