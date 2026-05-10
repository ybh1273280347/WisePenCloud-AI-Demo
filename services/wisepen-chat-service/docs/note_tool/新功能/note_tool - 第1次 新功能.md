# 笔记工具（NoteTool）技术实现方案

## 📋 项目概览

本文档详细介绍了基于 `BaseTool` 接口实现的笔记工具套件，提供完整的笔记管理能力，包括读取、编辑、列表查询、版本回滚和向量检索预留功能。该方案采用模块化设计，将数据库操作、文本处理和工具实现分离，便于后续扩展和维护。

## 📁 目录结构

```
tools/notes/
├── __init__.py             # 包初始化，导出工具类
├── _note_db.py             # 数据库操作辅助函数
├── _text_utils.py          # 文本处理辅助函数
├── read.py                 # 读取笔记工具
├── edit.py                 # 编辑笔记工具
├── list.py                 # 列出笔记工具
├── restore.py              # 版本回滚工具
└── search.py               # 向量检索
```

## 🔧 核心组件

### 1. 实体定义（`domain/entities/note.py`）

```python
from datetime import datetime
from typing import List
from beanie import Document
from pydantic import Field

class Note(Document):
    """笔记实体"""
    user_id: str              # 所有者 ID
    title: str                # 笔记标题
    content: str              # 笔记内容
    version: int = Field(default=1)  # 版本号（乐观锁）
    tags: List[str] = Field(default_factory=list)  # 标签
    created_at: datetime = Field(default_factory=datetime.utcnow)  # 创建时间
    updated_at: datetime = Field(default_factory=datetime.utcnow)  # 更新时间

    class Settings:
        name = "notes"
        indexes = [
            ("user_id", "title"),  # 复合索引
            "user_id"              # 单字段索引
        ]

class NoteVersion(Document):
    """笔记历史版本"""
    note_id: str              # 关联的笔记 ID
    content: str              # 历史版本内容
    version: int              # 版本号
    created_at: datetime = Field(default_factory=datetime.utcnow)  # 创建时间

    class Settings:
        name = "note_versions"
        indexes = [
            ("note_id", "version")  # 复合唯一索引
        ]
```

### 2. 辅助函数：数据库操作 (`_note_db.py`)

```python
from datetime import datetime
from typing import Optional, Tuple, List
from domain.entities.note import Note, NoteVersion

async def get_note_by_user(note_id: str, user_id: str) -> Optional[Note]:
    """获取笔记并校验所有权"""
    return await Note.find_one(Note.id == note_id, Note.user_id == user_id)

async def save_note_version(note_id: str, content: str, version: int):
    """保存历史版本"""
    await NoteVersion(note_id=note_id, content=content, version=version).insert()

async def delete_note_version(note_id: str, version: int):
    """删除指定版本"""
    await NoteVersion.find_one(
        NoteVersion.note_id == note_id, 
        NoteVersion.version == version
    ).delete()

async def update_note_content(note_id: str, new_content: str, expected_version: int) -> bool:
    """乐观锁更新，成功返回 True"""
    result = await Note.find_one(
        Note.id == note_id,
        Note.version == expected_version
    ).update({"$set": {
        "content": new_content,
        "version": expected_version + 1,
        "updated_at": datetime.utcnow()
    }})
    return result.modified_count == 1

async def list_user_notes(user_id: str, offset: int, limit: int) -> Tuple[List[Note], int]:
    """分页获取用户笔记列表（按更新时间倒序）"""
    query = Note.find(Note.user_id == user_id).sort(-Note.updated_at)
    total = await query.count()
    items = await query.skip(offset).limit(limit).to_list()
    return items, total
```

### 3. 辅助函数：文本处理 (`_text_utils.py`)

```python
def find_nth_occurrence(text: str, sub: str, n: int) -> int:
    """返回 sub 第 n 次出现的起始索引，若不足则返回 -1"""
    if n <= 0:
        return -1
    start = 0
    for _ in range(n):
        pos = text.find(sub, start)
        if pos == -1:
            return -1
        start = pos + len(sub)
    return pos

def replace_nth_occurrence(text: str, old: str, new: str, n: int) -> str:
    """替换第 n 次出现的 old 为 new；若不足 n 次则返回原文本"""
    pos = find_nth_occurrence(text, old, n)
    if pos == -1:
        return text
    return text[:pos] + new + text[pos + len(old):]
```

### 4. 工具实现

#### 4.1 `read.py` – 读取笔记

```python
from tools.notes._note_db import get_note_by_user

class ReadNoteTool(BaseTool):
    name = "read_note"
    description = "读取指定笔记的内容，支持分页查看"
    parameters_schema = {
        "type": "object",
        "properties": {
            "note_id": {
                "type": "string",
                "description": "笔记 ID"
            },
            "offset": {
                "type": "integer",
                "description": "起始偏移量",
                "default": 0
            },
            "limit": {
                "type": "integer",
                "description": "返回字符数限制",
                "default": 5000,
                "maximum": 20000
            }
        },
        "required": ["note_id"]
    }

    async def execute(self, context, **kwargs):
        user_id = context["user_id"]
        note = await get_note_by_user(kwargs["note_id"], user_id)
        if not note:
            return "[Error] 笔记不存在或无权限"

        offset = kwargs.get("offset", 0)
        limit = min(kwargs.get("limit", 5000), 20000)
        content = note.content
        total = len(content)

        if offset >= total:
            return "[Info] 已到文件末尾"

        segment = content[offset:offset+limit]
        has_more = offset+limit < total
        
        # 构建标签展示
        tags_str = f" 标签: [{', '.join(note.tags)}]" if note.tags else ""
        result = f"📄 **{note.title}** (版本 {note.version}{tags_str})\n{segment}"
        
        if has_more:
            result += f"\n\n[共 {total} 字符，使用 offset={offset+limit} 继续读取]"
        return result
```

#### 4.2 `edit.py` – 编辑笔记（核心）

```python
from tools.notes._note_db import get_note_by_user, save_note_version, delete_note_version, update_note_content
from tools.notes._text_utils import replace_nth_occurrence

class EditNoteTool(BaseTool):
    name = "edit_note"
    description = "编辑笔记内容，支持追加、替换指定内容或全部替换"
    parameters_schema = {
        "type": "object",
        "properties": {
            "note_id": {
                "type": "string",
                "description": "笔记 ID"
            },
            "expected_version": {
                "type": "integer",
                "description": "期望的当前版本号（用于乐观锁）"
            },
            "old_string": {
                "type": "string",
                "description": "要替换的旧字符串"
            },
            "new_string": {
                "type": "string",
                "description": "替换后的新字符串",
                "default": ""
            },
            "append_content": {
                "type": "string",
                "description": "追加到末尾的内容"
            },
            "mode": {
                "type": "string",
                "description": "替换模式：all（全部）或 nth（第 n 次）",
                "default": "nth"
            },
            "occurrence": {
                "type": "integer",
                "description": "第几次出现（mode=nth 时使用）",
                "default": 1
            }
        },
        "required": ["note_id", "expected_version"]
    }

    async def execute(self, context, **kwargs):
        user_id = context["user_id"]
        note_id = kwargs["note_id"]
        expected_version = kwargs["expected_version"]
        old = kwargs.get("old_string")
        new = kwargs.get("new_string", "")
        append = kwargs.get("append_content")
        mode = kwargs.get("mode", "nth")
        occurrence = kwargs.get("occurrence", 1)

        note = await get_note_by_user(note_id, user_id)
        if not note:
            return "[Error] 笔记不存在或无权限"
        if note.version != expected_version:
            return f"[Error] 版本冲突，当前版本 {note.version}"

        # 构造新内容
        if append is not None:
            new_content = note.content.rstrip('\n') + '\n' + append
        elif old is not None:
            if mode == "all":
                if old not in note.content:
                    return "[Error] 未找到要替换的文本"
                new_content = note.content.replace(old, new)
            else:  # nth
                new_content = replace_nth_occurrence(note.content, old, new, occurrence)
                if new_content == note.content:
                    return f"[Error] 未找到第 {occurrence} 次出现"
        else:
            return "[Error] 必须提供 old_string 或 append_content"

        # 保存版本 & 乐观锁更新
        await save_note_version(note.id, note.content, note.version)
        success = await update_note_content(note.id, new_content, expected_version)
        if not success:
            await delete_note_version(note.id, note.version)
            return "[Error] 并发修改，请重试"

        return f"✅ 笔记已更新，新版本号 {expected_version + 1}"
```

#### 4.3 `list.py` – 列出笔记

```python
from tools.notes._note_db import list_user_notes

class ListNotesTool(BaseTool):
    name = "list_notes"
    description = "列出用户的所有笔记，支持分页"
    parameters_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "每页数量",
                "default": 20,
                "maximum": 100
            },
            "offset": {
                "type": "integer",
                "description": "偏移量",
                "default": 0
            }
        }
    }

    async def execute(self, context, **kwargs):
        user_id = context["user_id"]
        limit = min(kwargs.get("limit", 20), 100)
        offset = kwargs.get("offset", 0)

        notes, total = await list_user_notes(user_id, offset, limit)
        if not notes:
            return "暂无笔记"

        lines = [f"📚 共 {total} 条笔记，显示 {offset+1}-{offset+len(notes)}："]
        for n in notes:
            tags_str = f" [{', '.join(n.tags)}]" if n.tags else ""
            lines.append(f"- **{n.title}** (v{n.version}, {n.updated_at.strftime('%Y-%m-%d')}){tags_str}")
        return "\n".join(lines)
```

#### 4.4 `restore.py` – 回滚版本

```python
from tools.notes._note_db import get_note_by_user, save_note_version, delete_note_version, update_note_content
from domain.entities.note import NoteVersion

class RestoreNoteTool(BaseTool):
    name = "restore_note"
    description = "回滚笔记到指定版本，不提供 target_version 则列出版本历史"
    parameters_schema = {
        "type": "object",
        "properties": {
            "note_id": {
                "type": "string",
                "description": "笔记 ID"
            },
            "target_version": {
                "type": "integer",
                "description": "目标版本号"
            },
            "expected_version": {
                "type": "integer",
                "description": "期望的当前版本号（用于乐观锁）"
            }
        },
        "required": ["note_id"]
    }

    async def execute(self, context, **kwargs):
        user_id = context["user_id"]
        note_id = kwargs["note_id"]
        target_version = kwargs.get("target_version")
        expected_version = kwargs.get("expected_version")

        note = await get_note_by_user(note_id, user_id)
        if not note:
            return "[Error] 笔记不存在或无权限"

        if not target_version:   # 列出版本
            versions = await NoteVersion.find(
                NoteVersion.note_id == note_id
            ).sort(-NoteVersion.version).limit(20).to_list()
            if not versions:
                return "暂无历史版本"
            lines = [f"版本 {v.version} - {v.created_at.isoformat()}" for v in versions]
            return "历史版本：\n" + "\n".join(lines)

        # 执行回滚
        ver = await NoteVersion.find_one(
            NoteVersion.note_id == note_id, 
            NoteVersion.version == target_version
        )
        if not ver:
            return f"[Error] 版本 {target_version} 不存在"

        if expected_version and note.version != expected_version:
            return f"[Error] 版本冲突，当前 {note.version}"

        await save_note_version(note.id, note.content, note.version)
        success = await update_note_content(note.id, ver.content, note.version)
        if not success:
            await delete_note_version(note.id, note.version)
            return "[Error] 并发修改，回滚失败"

        return f"✅ 已恢复到版本 {target_version}，新版本号 {note.version + 1}"
```

#### 4.5 `search.py` – 混合检索（语义 + 关键词）

```python
from langchain.retrievers import EnsembleRetriever
from application.rag.retrievers.qdrant_retriever import QdrantRetriever
from application.rag.retrievers.es_retriever import ElasticsearchRetriever
from application.rag.reranker_service import RerankerService
from chat.api.schemas.rag import RagSearchResult


class SearchNotesTool(BaseTool):
    name = "search_notes"
    description = "混合搜索用户笔记（语义搜索 + 关键词搜索，自动融合"
    parameters_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "搜索查询语句"
            },
            "tags": {
                "type": "array",
                "items": {
                    "type": "string"
                },
                "description": "标签过滤，可选"
            },
            "note_ids": {
                "type": "array",
                "items": {
                    "type": "string"
                },
                "description": "笔记ID过滤，可选"
            },
            "limit": {
                "type": "integer",
                "description": "返回结果数量",
                "default": 5,
                "maximum": 20
            }
        },
        "required": ["query"]
    }

    def __init__(
        self,
        qdrant_retriever: QdrantRetriever,
        es_retriever: ElasticsearchRetriever,
        reranker: RerankerService
    ):
        self.qdrant_retriever = qdrant_retriever
        self.es_retriever = es_retriever
        self.reranker = reranker
        self.ensemble = EnsembleRetriever(
            retrievers=[self.qdrant_retriever, self.es_retriever],
            weights=None  # RRF 模式
        )

    async def execute(self, context, **kwargs):
        user_id = context["user_id"]
        query = kwargs["query"]
        tags = kwargs.get("tags")
        note_ids = kwargs.get("note_ids")
        final_top_k = kwargs.get("limit", 5)

        # 设置过滤器
        self.qdrant_retriever.set_filters(user_id, note_ids, tags)
        self.es_retriever.set_filters(user_id, note_ids, tags)

        # 混合召回 + RRF 融合
        fused_docs = await self.ensemble.aget_relevant_documents(query)
        if not fused_docs:
            return "未找到相关笔记"

        # 转换为 RagSearchResult
        candidates = []
        for doc in fused_docs:
            candidates.append(RagSearchResult(
                note_id=doc.metadata.get("document_id", ""),
                title=doc.metadata.get("title", ""),
                chunk_index=doc.metadata.get("chunk_index", 0),
                content=doc.page_content,
                score=doc.metadata.get("score", 0.0),
                source="user_note",
                tags=doc.metadata.get("tags", []),
                user_id=user_id,
            ))

        # 精排
        reranked = await self.reranker.rerank(query, candidates, final_top_k)
        results = []
        for r in reranked:
            results.append(
                f"📄 {r.title} (块 {r.chunk_index}) [得分: {r.score:.2f}]\n{r.content[:300]}..."
            )
        return "\n\n---\n\n".join(results)
```

### 5. 包导出（`tools/notes/__init__.py`）

```python
from tools.notes.read import ReadNoteTool
from tools.notes.edit import EditNoteTool
from tools.notes.list import ListNotesTool
from tools.notes.restore import RestoreNoteTool
from tools.notes.search import SearchNotesTool

__all__ = [
    "ReadNoteTool",
    "EditNoteTool",
    "ListNotesTool",
    "RestoreNoteTool",
    "SearchNotesTool"
]
```

### 6. 注册到容器（`container.py` 片段）

```python
from tools.notes import ReadNoteTool, EditNoteTool, ListNotesTool, RestoreNoteTool, SearchNotesTool

# 初始化笔记工具
read_note_tool = ReadNoteTool()
edit_note_tool = EditNoteTool()
list_notes_tool = ListNotesTool()
restore_note_tool = RestoreNoteTool()
search_notes_tool = SearchNotesTool()

# 注册到工具提供者列表
tool_providers = providers.List(
    read_note_tool,
    edit_note_tool,
    list_notes_tool,
    restore_note_tool,
    search_notes_tool,
    # ... 其他工具
)
```

## 🔒 权限控制

建议在工具调用链上实现权限控制，不侵入工具本身：

1. **调用前校验**：在 `LLMRunner._invoke_tool` 前检查用户角色
2. **工具可见性**：根据用户角色动态过滤可用工具列表
3. **操作权限**：例如只允许教师角色调用写操作相关工具

## 🚀 特性亮点

1. **模块化设计**：数据库操作、文本处理、工具实现完全分离
2. **版本管理**：自动保存历史版本，支持回滚
3. **乐观锁**：防止并发修改冲突
4. **分页支持**：读取和列表操作均支持分页
5. **安全校验**：严格的权限和版本校验
6. **标签展示**：在列表和读取工具中展示笔记标签
7. **混合检索**：集成语义搜索（Qdrant）和关键词搜索（Elasticsearch），自动融合召回结果
8. **重排序增强**：使用 ZeroEntropy Reranker 对搜索结果进行精排，提升相关度
9. **中文分词优化**：Elasticsearch 使用 IK Analyzer 提供专业的中文分词

## 📝 使用示例

### 1. 列出所有笔记

```python
# 工具调用
result = await list_notes_tool.execute(
    context={"user_id": "user_123"},
    limit=10,
    offset=0
)

# 输出示例
"""
📚 共 5 条笔记，显示 1-5：
- **AI 学习笔记** (v3, 2026-04-25) [AI, 学习]
- **项目计划** (v1, 2026-04-24)
- **会议记录** (v2, 2026-04-23) [工作]
"""
```

### 2. 编辑笔记

```python
# 工具调用
result = await edit_note_tool.execute(
    context={"user_id": "user_123"},
    note_id="note_123",
    expected_version=3,
    append_content="\n## 新章节\n这是新增的内容"
)

# 输出示例
"""
✅ 笔记已更新，新版本号 4
"""
```

### 3. 版本回滚

```python
# 工具调用 - 列出版本
result = await restore_note_tool.execute(
    context={"user_id": "user_123"},
    note_id="note_123"
)

# 输出示例
"""
历史版本：
版本 4 - 2026-04-26T10:00:00Z
版本 3 - 2026-04-25T15:30:00Z
版本 2 - 2026-04-24T09:15:00Z
"""

# 工具调用 - 执行回滚
result = await restore_note_tool.execute(
    context={"user_id": "user_123"},
    note_id="note_123",
    target_version=2,
    expected_version=4
)

# 输出示例
"""
✅ 已恢复到版本 2，新版本号 5
"""
```

## 📌 后续扩展

1. **向量检索**：集成 `Qdrant` 和 embedding 模型，实现语义搜索
2. **标签管理**：添加标签增删改查功能
3. **分享功能**：支持笔记分享给其他用户
4. **导入导出**：支持 Markdown、PDF 等格式的导入导出
5. **协作编辑**：支持多用户协作编辑（需调整乐观锁机制）

## 🛠️ 技术栈

- **数据库**：MongoDB + Beanie ORM
- **异步**：Python asyncio

该实现方案已完全满足笔记管理的核心需求，且具有良好的可扩展性和可维护性。
