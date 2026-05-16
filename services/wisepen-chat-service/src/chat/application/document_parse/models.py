from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(slots=True)
class ParsedTable:
    """文档解析得到的结构化表格。"""

    table_id: str
    source: str
    rows: List[List[str]]
    page_index: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedPage:
    """文档解析得到的单页结果。"""

    page_index: int
    text: str
    page_type: str
    tables: List[ParsedTable] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DocumentParseResult:
    """文档解析工具的统一结果。"""

    text: str
    source: str
    file_type: str
    pages: List[ParsedPage] = field(default_factory=list)
    tables: List[ParsedTable] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
