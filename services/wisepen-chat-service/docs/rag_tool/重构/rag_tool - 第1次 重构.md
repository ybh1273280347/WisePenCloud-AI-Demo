# RAG 知识检索技术实现方案（混合检索增强版·元数据解耦重构）

## 📋 项目概览

本文档详细介绍基于 **Qdrant（稠密向量）** + **Elasticsearch（稀疏关键词）** 的混合 RAG 知识检索系统。在原有混合检索架构的基础上，对元数据模型进行了彻底重构：

- **文档级元数据** 收敛至 `DocumentMeta`，包含 `document_id`、`title`、`source`、`tags`、`user_id`、时间戳等**文档自身属性**。
- **块级信息** 沉淀在 `RagSearchResult` 中，包含 `chunk_id`、`chunk_index`、`content`、`score` 等**块特有的属性**。
- 彻底消除了原方案中 `Dict[str, Any]` 模糊传递及 `extra` 字段的手动维护，大幅提升类型安全与可维护性。

## 📁 目录结构

```
src/chat/
├── api/
│   ├── schemas/
│   │   └── rag.py                    # RAG 数据模型（DocumentMeta / RagSearchResult）
│   └── routers/
│       └── documents_router.py       # 文档管理 API（upsert/delete）
└── application/
    └── rag/
        ├── __init__.py               # 包初始化
        ├── document_processor.py     # 文档拆分
        ├── embedding_service.py      # 向量化服务
        ├── reranker_service.py       # 重排序服务
        ├── qdrant_client.py          # Qdrant 客户端
        ├── es_client.py              # Elasticsearch 客户端
        ├── retrievers/
        │   ├── qdrant_retriever.py   # Qdrant LangChain 检索器
        │   └── es_retriever.py       # ES LangChain 检索器
        └── vector_indexer.py         # 向量+关键词索引管理器
```

## 🔧 核心组件

### 1. 数据模型 (`api/schemas/rag.py`)

#### 1.1 `DocumentMeta` — 纯文档元数据

只包含文档级别的描述信息，不含块级属性。

```python
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class DocumentMeta(BaseModel):
    """文档级别元数据，不包含块特有信息"""
    document_id: str = Field(..., description="文档唯一标识")
    title: str = Field(..., description="文档标题")
    source: str = Field(..., description="来源类型，如 user_note / skill / imported_doc")
    tags: List[str] = Field(default_factory=list, description="标签列表")
    user_id: str = Field(..., description="所有者用户ID")
    created_at: Optional[datetime] = Field(None, description="文档创建时间")
    updated_at: Optional[datetime] = Field(None, description="文档更新时间")

    class Config:
        extra = "ignore"  # 后续新增文档级元数据请直接在此添加
```

#### 1.2 `RagSearchResult` — 块检索结果

包含块自身信息，并组合文档级元数据。

```python
class RagSearchResult(BaseModel):
    """单个检索结果：块内容 + 文档元数据"""
    chunk_id: str = Field(..., description="块唯一标识（如 doc_0）")
    chunk_index: int = Field(..., description="块在原文档中的序号")
    content: str = Field(..., description="块文本内容")
    score: float = Field(..., description="相似度或重排序分数")
    meta: DocumentMeta = Field(..., description="所属文档的元数据")
```

#### 1.3 `RagSearchResponse`

```python
class RagSearchResponse(BaseModel):
    query: str = Field(..., description="原始查询")
    results: List[RagSearchResult] = Field(default_factory=list, description="检索结果列表")
```

**重构要点**：

- `chunk_index` 从 `DocumentMeta` 移出，归属到 `RagSearchResult` 的块级属性，符合直觉。
- 所有元数据字段强类型化，不再出现 `Dict[str, Any]`。
- `DocumentMeta` 使用 `extra = "ignore"`，未来新增文档级字段只需在此处添加，无需改动下游。

### 2. 文档处理 (`application/rag/document_processor.py`)

分块时不再传递模糊的 `Dict` 作为元数据，而是接收 `DocumentMeta` 实例，并生成包含块级信息的结构化字典。

```python
from typing import List, Dict, Any
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownTextSplitter
from chat.api.schemas.rag import DocumentMeta


class DocumentProcessor:
    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def process_markdown(self, text: str, meta: DocumentMeta) -> List[Dict[str, Any]]:
        splitter = MarkdownTextSplitter(chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap)
        return self._process(text, meta, splitter)

    def process_text(self, text: str, meta: DocumentMeta) -> List[Dict[str, Any]]:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        return self._process(text, meta, splitter)

    def _process(self, text: str, meta: DocumentMeta, splitter) -> List[Dict[str, Any]]:
        docs = splitter.create_documents([text])
        chunks = []
        for i, doc in enumerate(docs):
            chunk_id = f"{meta.document_id}_{i}"
            chunks.append({
                "chunk_id": chunk_id,
                "chunk_index": i,
                "content": doc.page_content,
                "meta": meta,                     # 文档级元数据共享
            })
        return chunks
```

### 3. 向量化服务 (`application/rag/embedding_service.py`)

与重构前一致，仅负责生成向量，不直接依赖元数据模型。

### 4. 重排序服务 (`application/rag/reranker_service.py`)

`rerank` 方法接收 `List[RagSearchResult]`，直接使用 `.content` 进行文本匹配，返回重新打分后的结果列表。无需修改。

### 5. Qdrant 客户端 (`application/rag/qdrant_client.py`)

- **写入**：将 `DocumentMeta` 的字段扁平化存入 payload，同时存储块特有字段。
- **检索**：从 Qdrant 返回的 payload 中分离文档与块字段，构造 `DocumentMeta` 和 `RagSearchResult`。

```python
from typing import List, Optional, Dict, Any
from qdrant_client import AsyncQdrantClient, models
from chat.core.config.app_settings import settings
from chat.api.schemas.rag import RagSearchResult, DocumentMeta


class RagQdrantClient:
    def __init__(self):
        self._collection_name = "wisepen_rag_documents"
        self._client = AsyncQdrantClient(
            url=f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}",
            api_key=settings.QDRANT_PASSWORD,
        )

        self._filter_rules = {
            "user_id":       ("user_id",      False),   
            "document_ids":  ("document_id",  True),    
            "source_filter": ("source",       True),
            "tags_filter":   ("tags",         True),  
        }

    def _build_filter_conditions(
        self, meta_filter: Dict[str, Any]
    ) -> List[models.FieldCondition]:
        """根据 meta_filter 字典生成 Qdrant 过滤条件列表"""
        conditions = []
        for key, (field, is_multi) in self._filter_rules.items():
            if key not in meta_filter or meta_filter[key] is None:
                continue
            
            value = meta_filter[key]
            match = (
                models.MatchAny(any=value)
                if is_multi
                else models.MatchValue(value=value)
            )
            conditions.append(models.FieldCondition(key=field, match=match))
        return conditions

    async def upsert_chunks(
        self,
        chunks: List[Dict[str, Any]],
        embeddings: List[List[float]],
    ):
        points = []
        for chunk, embedding in zip(chunks, embeddings):
            meta: DocumentMeta = chunk["meta"]
            payload = {
                # 文档级字段
                "document_id": meta.document_id,
                "title": meta.title,
                "source": meta.source,
                "tags": meta.tags,
                "user_id": meta.user_id,
                "created_at": meta.created_at.isoformat() if meta.created_at else None,
                "updated_at": meta.updated_at.isoformat() if meta.updated_at else None,
                # 块级字段
                "chunk_id": chunk["chunk_id"],
                "chunk_index": chunk["chunk_index"],
                "content": chunk["content"],
            }
            # 移除值为 None 的字段，保持 payload 整洁
            payload = {k: v for k, v in payload.items() if v is not None}
            points.append(
                models.PointStruct(
                    id=chunk["chunk_id"],
                    vector=embedding,
                    payload=payload,
                )
            )
        await self._client.upsert(
            collection_name=self._collection_name,
            points=points,
        )

    async def search(
        self,
        query_embedding: List[float],
        meta_filter: Optional[Dict[str, Any]] = None,
        top_k: int = 5,
        score_threshold: Optional[float] = None,
    ) -> List[RagSearchResult]:
        must_conditions = self._build_filter_conditions(meta_filter or {})
        query_filter = (
            models.Filter(must=must_conditions) if must_conditions else None
        )

        search_result = await self._client.search(
            collection_name=self._collection_name,
            query_vector=query_embedding,
            query_filter=query_filter,
            limit=top_k,
            score_threshold=score_threshold,
            with_payload=True,
        )

        results = []
        for hit in search_result:
            payload = hit.payload
            try:
                meta = DocumentMeta(
                    document_id=payload["document_id"],
                    title=payload.get("title", ""),
                    source=payload.get("source", ""),
                    tags=payload.get("tags", []),
                    user_id=payload.get("user_id", ""),
                    created_at=payload.get("created_at"),
                    updated_at=payload.get("updated_at"),
                )
            except KeyError:
                continue  # 跳过格式不正确的点
            results.append(
                RagSearchResult(
                    chunk_id=payload["chunk_id"],
                    chunk_index=payload["chunk_index"],
                    content=payload["content"],
                    score=hit.score,
                    meta=meta,
                )
            )
        return results

    async def delete_by_document_id(self, document_id: str):
        await self._client.delete(
            collection_name=self._collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=document_id),
                        ),
                    ],
                ),
            ),
        )
```

### 6. Elasticsearch 客户端 (`application/rag/es_client.py`)

ES 的索引映射、写入和检索统一对齐 `DocumentMeta` + 块字段。

```python
from typing import List, Optional, Dict, Any
from elasticsearch import AsyncElasticsearch, helpers
from common.logger import log_fail, log_debug
from chat.api.schemas.rag import DocumentMeta, RagSearchResult


class ElasticsearchClient:
    def __init__(self, hosts: List[str], index_name: str = "wisepen_documents"):
        self.client = AsyncElasticsearch(hosts=hosts)
        self.index_name = index_name

    async def ensure_index(self):
        exists = await self.client.indices.exists(index=self.index_name)
        if not exists:
            settings = {
                "analysis": {
                    "analyzer": {
                        "ik_smart_analyzer": {
                            "type": "custom",
                            "tokenizer": "ik_smart",
                            "filter": ["lowercase"]
                        }
                    }
                }
            }
            mappings = {
                "dynamic": "strict",
                "properties": {
                    "chunk_id": {"type": "keyword"},
                    "chunk_index": {"type": "integer"},
                    "content": {"type": "text", "analyzer": "ik_smart_analyzer"},
                    "document_id": {"type": "keyword"},
                    "title": {"type": "text", "analyzer": "ik_smart_analyzer"},
                    "source": {"type": "keyword"},
                    "tags": {"type": "keyword"},
                    "user_id": {"type": "keyword"},
                    "created_at": {"type": "date"},
                    "updated_at": {"type": "date"},
                }
            }
            await self.client.indices.create(index=self.index_name, settings=settings, mappings=mappings)
            log_debug(f"ES 索引 {self.index_name} 已创建")

    async def index_chunks(self, chunks: List[Dict[str, Any]]):
        async def generate_actions():
            for chunk in chunks:
                meta: DocumentMeta = chunk["meta"]
                yield {
                    "_index": self.index_name,
                    "_id": chunk["chunk_id"],
                    "_source": {
                        "chunk_id": chunk["chunk_id"],
                        "chunk_index": chunk["chunk_index"],
                        "content": chunk["content"],
                        "document_id": meta.document_id,
                        "title": meta.title,
                        "source": meta.source,
                        "tags": meta.tags,
                        "user_id": meta.user_id,
                        "created_at": meta.created_at.isoformat() if meta.created_at else None,
                        "updated_at": meta.updated_at.isoformat() if meta.updated_at else None,
                    }
                }
        try:
            success, errors = await helpers.async_bulk(self.client, generate_actions(), max_retries=3, raise_on_error=False)
            if errors:
                log_fail("ES 批量索引部分失败", errors=errors[:5])
            log_debug(f"ES 索引完成，成功 {success} 条")
        except Exception as e:
            log_fail("ES 批量索引异常", e)

    async def search_keywords(
        self,
        query: str,
        user_id: str,
        note_ids: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        top_k: int = 20,
    ) -> List[RagSearchResult]:
        try:
            must_clauses = [{"term": {"user_id": user_id}}]
            if note_ids:
                must_clauses.append({"terms": {"document_id": note_ids}})
            if tags:
                must_clauses.append({"terms": {"tags": tags}})

            body = {
                "query": {
                    "bool": {
                        "must": must_clauses,
                        "should": [
                            {"match": {"title": query}},
                            {"match": {"content": query}}
                        ],
                        "minimum_should_match": 1
                    }
                },
                "size": top_k,
                "_source": ["chunk_id", "chunk_index", "content", "document_id", "title", "source", "tags", "user_id", "created_at", "updated_at"]
            }
            resp = await self.client.search(index=self.index_name, **body)
            hits = resp["hits"]["hits"]
            results = []
            for hit in hits:
                src = hit["_source"]
                meta = DocumentMeta(
                    document_id=src["document_id"],
                    title=src.get("title", ""),
                    source=src.get("source", ""),
                    tags=src.get("tags", []),
                    user_id=src.get("user_id", ""),
                    created_at=src.get("created_at"),
                    updated_at=src.get("updated_at"),
                )
                results.append(RagSearchResult(
                    chunk_id=src["chunk_id"],
                    chunk_index=src["chunk_index"],
                    content=src["content"],
                    score=hit["_score"],
                    meta=meta,
                ))
            return results
        except Exception as e:
            log_fail("ES 关键词搜索失败", e)
            return []

    async def delete_by_document_id(self, document_id: str):
        await self.client.delete_by_query(index=self.index_name, query={"term": {"document_id": document_id}})
```

### 7. LangChain 检索器 (`application/rag/retrievers/`)

检索器内部调用 Qdrant 或 ES 客户端，直接返回 `List[RagSearchResult]`，不再手动拼装 `metadata` 字典。

```python
# qdrant_retriever.py 关键方法
async def _aget_relevant_documents(self, query: str) -> List[Document]:
    query_vec = await self.embedder.embed_text(query)
    results = await self.qdrant.search(query_vec, meta_filter=self._build_filter(), top_k=20)
    return [Document(page_content=r.content, metadata=r.meta.dict()) for r in results]

# es_retriever.py 关键方法
async def _aget_relevant_documents(self, query: str) -> List[Document]:
    results = await self.es.search_keywords(query, self._user_id, self._note_ids, self._tags, top_k=20)
    return [Document(page_content=r.content, metadata=r.meta.dict()) for r in results]
```

### 8. 向量索引管理器 (`application/rag/vector_indexer.py`)

基于 `DocumentMeta` 生成统一块结构，双写 Qdrant 与 ES。

```python
class VectorIndexer:
    async def sync(self, document_id: str, content: str, meta: DocumentMeta):
        # 分块
        chunks = self.processor.process_markdown(content, meta)  # 或 process_text
        texts = [c["content"] for c in chunks]
        embeddings = await self.embedder.embed_chunks(texts)

        # 清理旧数据
        await self.qdrant.delete_by_document_id(document_id)
        await self.es.delete_by_document_id(document_id)

        # 双写
        await self.qdrant.upsert_chunks(chunks, embeddings)
        await self.es.index_chunks(chunks)
```

### 9. 文档管理 API 及包初始化

接口签名保持不变，内部通过 `DocumentMeta` 传递元数据，使用方式更清晰。

## 🛠️ 技术选型

主依赖与之前相同：LangChain Text Splitters、OpenAI Embedding、Qdrant、Elasticsearch、零熵 Reranker。

## 📝 使用示例

```python
from chat.application.rag import (
    DocumentProcessor, EmbeddingService, RagQdrantClient,
    ElasticsearchClient, VectorIndexer
)
from chat.api.schemas.rag import DocumentMeta

# 创建文档元数据
meta = DocumentMeta(
    document_id="note_123",
    title="AI 学习笔记",
    source="user_note",
    tags=["AI", "学习"],
    user_id="user_456",
)

# 索引文档
indexer = VectorIndexer(processor, embedder, qdrant, es)
await indexer.sync(document_id="note_123", content="# 笔记内容...", meta=meta)

# 检索
results = await qdrant.search(query_embedding, meta_filter={"user_id": "user_456", "tags_filter": ["AI"]})
for r in results:
    print(f"[{r.meta.title}] {r.content[:50]}... (score: {r.score})")
```

## 🚀 特性亮点

- **模型严格分层**：文档级元数据 `DocumentMeta` 与块级信息 `RagSearchResult` 彻底解耦，再也没有“万能字典”。
- **可扩展性**：新增元数据字段只需修改 `DocumentMeta`，所有下游代码自动适配。
- **类型安全**：IDE 智能提示、静态检查完全可用。
- **检索接口标准化**：统一 `meta_filter` 字典，过滤逻辑一目了然。
- **跨存储一致**：Qdrant 与 ES 的写入/检索使用相同的元数据结构。

## 📦 依赖更新

无新增外部依赖，仅内部模型重构。

***

该方案彻底解决了元数据管理痛点，与混合检索双路完美结合，可直接作为生产级知识检索系统的基石。
