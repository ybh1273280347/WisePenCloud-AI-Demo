import re


_EXCESSIVE_BLANK_LINES_RE = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """统一文本换行、行尾空白和连续空行。"""

    lines = [
        line.rstrip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    result = "\n".join(lines).strip()
    return _EXCESSIVE_BLANK_LINES_RE.sub("\n\n", result)