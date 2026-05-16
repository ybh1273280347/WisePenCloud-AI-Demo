from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileHandoffResult:
    file_ref: str
    local_path: Path
    filename: str
    size_bytes: int
