from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True, slots=True)
class FileHandoffResult:
    file_ref: str
    local_path: Path
    filename: str
    size_bytes: int
    user_id: str
    session_id: str
    original_file_name: str
    content_type: Optional[str] = None
