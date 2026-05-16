from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True, slots=True)
class ExportOptions:
    timeout_seconds: float = 60.0
    assets_dir: Optional[Path] = None
    title: Optional[str] = None
    css: Optional[str] = None
    reference_docx: Optional[Path] = None


@dataclass(frozen=True, slots=True)
class ExportRequest:
    session_id: str
    markdown: str
    target_format: str
    output_path: Path
    file_name: Optional[str]
    options: ExportOptions


@dataclass(frozen=True, slots=True)
class GeneratedDocumentFile:
    file_path: Path
    file_name: str
    content_type: str
    target_format: str
    size_bytes: int
