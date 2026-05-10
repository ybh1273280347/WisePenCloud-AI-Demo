from .models import (
    ProfileDirCheck,
    ResolveFailure,
    ResolveFailureReason,
    ResolveResult,
    ResolveSource,
    ResolveSuccess,
)

def describe_resolve_result(result: ResolveResult) -> str:
    if isinstance(result, ResolveSuccess):
        return describe_success(result)
    return describe_failure(result)


def describe_success(result: ResolveSuccess) -> str:
    source_labels = {
        ResolveSource.CLI: "CLI 指定",
        ResolveSource.PERSISTED: "已保存配置",
        ResolveSource.DEFAULT_PROFILE: "默认工具",
    }

    label = source_labels.get(result.source, "未知来源")
    message = (
        f"使用{label}的自动化浏览器 profile: {result.automation_user_data_dir} "
        f"(channel={result.browser_channel})"
    )

    if result.warning:
        return f"{message}\n警告: {result.warning}"

    return message


def describe_failure(result: ResolveFailure) -> str:
    if result.reason == ResolveFailureReason.PROFILE_LOCKED:
        check = result.check
        path_str = str(check.path) if check else "未知路径"
        return (
            f"本工具的浏览器 profile 当前被已有浏览器进程占用: {path_str}。"
            "请关闭该工具启动的浏览器窗口，或复用当前 browser_session_id。"
            "（如浏览器已关闭，可能是异常退出残留的锁文件）"
        )

    if result.reason == ResolveFailureReason.INVALID_CLI_PROFILE:
        check = result.check
        if check is None:
            return "CLI 指定的自动化浏览器 profile 无效。"

        return (
            f"CLI 指定的自动化浏览器 profile 无效: "
            f"{check.path} ({summarize_check(check)})"
        )

    if result.reason == ResolveFailureReason.INVALID_BROWSER_CHANNEL:
        return result.message or "指定的浏览器 channel 无效。"

    if result.reason == ResolveFailureReason.PROFILE_UNAVAILABLE:
        check = result.check
        if check is None:
            return "自动化浏览器 profile 不可用。"

        return (
            f"自动化浏览器 profile 不可用: "
            f"{check.path} ({summarize_check(check)})"
        )

    if result.reason == ResolveFailureReason.UNSUPPORTED_PLATFORM:
        return result.message or "当前平台不支持自动化浏览器 profile 管理。"

    return result.message or "无法解析自动化浏览器 profile。"


def summarize_check(check: ProfileDirCheck) -> str:
    if not check.exists:
        return "不存在"
    if not check.is_dir:
        return "不是目录"
    if not check.readable:
        return "不可读"
    if check.locked:
        return "被浏览器锁定"
    if check.writable is False:
        return "不可写"
    return check.detail or "不可用"