import re
import tempfile
from pathlib import Path

_SAFE_CHAR_PATTERN = re.compile(r"[^\w.\- \u4e00-\u9fff]")
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }
)
_MAX_SEGMENT_LENGTH = 120


def document_export_output_path() -> Path:
    """获取文档导出临时文件根目录，按 user/session 隔离。"""
    return Path(tempfile.gettempdir()) / "wisepen-chat-upload-files"


def sanitize_path_segment(value: str, *, fallback: str = "document") -> str:
    """清洗路径片段，移除路径危险字符，降级为 fallback 值（若清洗后为空）。"""
    if not value:
        return fallback

    # 去掉首尾危险字符，并阻断路径分隔符。
    cleaned = value.strip().strip(". ").replace("/", "_").replace("\\", "_")
    if cleaned in (".", "..", ""):
        return fallback

    # 处理 Windows 盘符形式，例如 C:\xxx 或 D:/xxx。
    if ":" in cleaned:
        prefix = cleaned.split(":")[0]
        if len(prefix) == 1 and prefix.isalpha():
            cleaned = cleaned.split(":", 1)[1].lstrip("\\/")

    # 清理文件系统不友好的字符。
    cleaned = _SAFE_CHAR_PATTERN.sub("_", cleaned).strip(". ")
    if not cleaned:
        return fallback

    # 避免 CON.txt / NUL.pdf 这类 Windows 保留名。
    base_name = cleaned.rsplit(".", 1)[0] if "." in cleaned else cleaned
    if base_name.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned += "_"

    return cleaned[:_MAX_SEGMENT_LENGTH]


def is_path_within_root(path: Path, root: Path) -> bool:
    """检查解析后的路径是否严格位于根目录内，防止路径穿越。"""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def display_file_name(*, storage_file_name: str) -> str:
    """从存储文件名（含 uuid 前缀）提取展示给用户的下载文件名。"""
    prefix, sep, rest = storage_file_name.partition("-")
    if sep and len(prefix) == 32 and all(c in "0123456789abcdef" for c in prefix):
        return rest or storage_file_name
    return storage_file_name


def storage_stem_for_download_ref(*, safe_stem: str, suffix: str) -> str:
    """生成适合作为下载引用的存储 stem，预留 uuid 前缀空间，截断过长名称。"""
    max_stem_length = 120 - 33 - len(suffix)
    if max_stem_length <= 0:
        return "document"
    return safe_stem[:max_stem_length] or "document"