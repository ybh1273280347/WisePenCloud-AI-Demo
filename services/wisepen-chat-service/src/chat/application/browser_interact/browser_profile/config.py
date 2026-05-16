import json
import sys
from pathlib import Path
from typing import Mapping, Optional, Tuple

from common.logger import log_event, log_fail

from .paths import BROWSER_CHANNELS, default_config_file, resolve_env, resolve_home

_CONFIG_PROFILE_KEY = "automation_user_data_dir"
_CONFIG_CHANNEL_KEY = "browser_channel"


class AutomationProfileConfig:
    def __init__(
        self,
        config_file: Optional[Path] = None,
        *,
        platform: str = sys.platform,
        home: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._config_file = config_file
        self._platform = platform
        self._home = resolve_home(home)
        self._env = resolve_env(env)

    @property
    def config_file(self) -> Path:
        if self._config_file is not None:
            return self._config_file

        return default_config_file(
            platform=self._platform,
            home=self._home,
            env=self._env,
        )

    def load(self) -> Tuple[Optional[Path], Optional[str]]:
        config_file = self.config_file

        if not config_file.exists():
            return (None, None)

        try:
            data = json.loads(config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            log_fail("读取自动化浏览器配置", error)
            return (None, None)

        if not isinstance(data, dict):
            return (None, None)

        profile_dir = data.get(_CONFIG_PROFILE_KEY)
        channel = data.get(_CONFIG_CHANNEL_KEY)

        if not isinstance(profile_dir, str) or not profile_dir:
            return (None, None)

        if not isinstance(channel, str) or not channel:
            channel = None
        else:
            channel = channel.strip().lower() or None

        if channel is not None and channel not in BROWSER_CHANNELS:
            log_event(
                "已保存的 browser_channel 无效，忽略该 channel",
                browser_channel=channel,
            )
            channel = None

        return (Path(profile_dir), channel)

    def save(
        self, automation_user_data_dir: Path, browser_channel: str
    ) -> Optional[str]:
        payload = json.dumps(
            {
                _CONFIG_PROFILE_KEY: str(automation_user_data_dir),
                _CONFIG_CHANNEL_KEY: browser_channel,
            },
            ensure_ascii=False,
            indent=2,
        )

        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            self.config_file.write_text(payload, encoding="utf-8")
        except OSError as error:
            log_fail("保存自动化浏览器配置", error)
            return f"保存自动化浏览器配置失败: {error}"

        return None
