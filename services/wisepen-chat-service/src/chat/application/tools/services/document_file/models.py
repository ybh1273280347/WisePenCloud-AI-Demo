from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True, slots=True)
class ResolvedDocumentSource:
    path: Path
    user_id: str
    session_id: str
    source_file_name: str
    size_bytes: int
    content_type: Optional[str] = None
