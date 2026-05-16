from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class ResolvedDocumentFile:
    local_path: Path


class LocalDocumentFileResolver:
    """解析已存在的本地文档路径引用。"""

    def __init__(self, *, base_dir: Optional[Path] = None):
        self._base_dir = base_dir

    def resolve(self, file_ref: str) -> ResolvedDocumentFile:
        path = Path(file_ref)

        if not path.is_absolute():
            root = self._base_dir or Path.cwd()
            path = root / path

        path = path.resolve(strict=False)
        if not path.is_file():
            raise FileNotFoundError(f"Document file not found: {path}")

        return ResolvedDocumentFile(local_path=path)
