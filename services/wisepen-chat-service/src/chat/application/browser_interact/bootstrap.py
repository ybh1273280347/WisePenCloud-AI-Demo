from pathlib import Path
from typing import Optional

from dependency_injector import providers

from common.logger import log_event
from .browser_profile.resolver import BrowserAutomationProfileResolver
from .browser_profile.models import ResolveSuccess
from .browser_profile.presenter import describe_resolve_result


def setup_browser_automation_profile(container) -> Optional[str]:
    from chat.application.tools import BrowseInteractTool
    resolver = BrowserAutomationProfileResolver()
    result = resolver.resolve()

    data_dir_str = describe_resolve_result(result)

    if isinstance(result, ResolveSuccess):
        browser_data_dir = result.automation_user_data_dir
        source = result.source.value
        log_event("浏览器自动化配置", data_dir=str(browser_data_dir), source=source)

        container.browse_interact_tool.override(providers.Singleton(
            BrowseInteractTool,
            automation_user_data_dir=str(browser_data_dir),
        ))
        return str(browser_data_dir)
    else:
        log_event("浏览器自动化配置", detail="未找到可用浏览器数据目录，将使用临时会话")
        return None
