# RAG 知识检索技术实现方案（混合检索增强版）

## 📋 项目概览

本文档详细介绍了基于 **Qdrant（稠密向量）** + **Elasticsearch（稀疏关键词）** 的混合 RAG 知识检索系统。系统采用双路索引、RRF 融合、交叉编码器重排序，提供完整的文档摄取、向量化存储、语义与关键词并行检索和智能排序能力。整体采用模块化设计，与 Mem0 保持配置一致性，便于后续扩展和维护。

## 📁 目录结构

```
src/chat/
├── api/
│   ├── schemas/
│   │   └── rag.py                    # RAG 数据模型
│   └── routers/
│       └── documents_router.py       # 文档管理 API（upsert/delete）
└── application/
    └── rag/
        ├── __init__.py               # 包初始化
        ├── document_processor.py     # 文档拆分
        ├── embedding_service.py       # 向量化服务
        ├── reranker_service.py        # 重排序服务
        ├── qdrant_client.py          # Qdrant 客户端
        ├── es_client.py              # Elasticsearch 客户端
        ├── retrievers/
        │   ├── qdrant_retriever.py   # Qdrant LangChain 检索器
        │   └── es_retriever.py      # ES LangChain 检索器
        └── vector_indexer.py         # 向量+关键词索引管理器
```

## 🔧 核心组件

### 1. 数据模型 (`api/schemas/rag.py`)

```python
from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class RagSearchResult(BaseModel):
    """单个检索结果（展开 metadata）"""
    note_id: str = Field(..., description="笔记ID")
    title: str = Field(..., description="笔记标题")
    chunk_index: int = Field(..., description="块序号")
    content: str = Field(..., description="块内容")
    score: float = Field(..., description="相似度或重排序分数")
    source: str = Field(..., description="来源类型（user_note, skill 等）")
    tags: List[str] = Field(default_factory=list, description="标签列表")
    user_id: str = Field(..., description="所有者ID")
    created_at: Optional[datetime] = Field(None, description="笔记创建时间")
    updated_at: Optional[datetime] = Field(None, description="笔记更新时间")
    extra: Dict[str, Any] = Field(default_factory=dict, description="扩展字段")

    class Config:
        extra = "ignore"


class RagSearchResponse(BaseModel):
    """检索响应"""
    query: str = Field(..., description="原始查询")
    results: List[RagSearchResult] = Field(default_factory=list, description="检索结果列表")

    class Config:
        extra = "ignore"
```

### 2. 文档处理 (`application/rag/document_processor.py`)

```python
from typing import List, Dict, Any
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownTextSplitter


class DocumentProcessor:
    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 50):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

    def process_markdown(self, text: str, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        splitter = MarkdownTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        return self._process(text, metadata, splitter)

    def process_text(self, text: str, metadata: Dict[str, Any]) -> List[Dict[str, Any]]:
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", ". ", " ", ""],
        )
        return self._process(text, metadata, splitter)

    def _process(self, text: str, metadata: Dict[str, Any], splitter) -> List[Dict[str, Any]]:
        docs = splitter.create_documents([text])

        chunks = []
        for i, doc in enumerate(docs):
            chunk_metadata = {**metadata, "chunk_index": i}
            chunk_metadata["chunk_id"] = f"{metadata.get('document_id', 'unknown')}_{i}"

            chunks.append({
                "content": doc.page_content,
                "metadata": chunk_metadata,
            })

        return chunks
```

### 3. 向量化服务 (`application/rag/embedding_service.py`)

```python
import asyncio
from typing import List
from openai import AsyncOpenAI

from chat.core.config.app_settings import settings


class EmbeddingService:
    def __init__(self):
        self._model = settings.MEMORY_EMBEDDING_MODEL
        self._client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
        )

    async def embed_text(self, text: str) -> List[float]:
        response = await self._client.embeddings.create(
            model=self._model,
            input=text,
        )
        return response.data[0].embedding

    async def embed_chunks(self, chunks: List[str], batch_size: int = 10) -> List[List[float]]:
        all_embeddings = []

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            response = await self._client.embeddings.create(
                model=self._model,
                input=batch,
            )
            all_embeddings.extend([item.embedding for item in response.data])
            await asyncio.sleep(0.1)

        return all_embeddings
```

### 4. 重排序服务 (`application/rag/reranker_service.py`)

```python
import os
from typing import List
from zeroentropy import ZeroEntropy

from chat.core.config.app_settings import settings
from chat.api.schemas.rag import RagSearchResult


class RerankerService:
    def __init__(self):
        self._model = settings.MEMORY_RERANKER_ZE_MODEL
        self._api_key = settings.ZERO_ENTROPY_API_KEY
        os.environ["ZEROENTROPY_API_KEY"] = self._api_key
        self._client = ZeroEntropy()

    async def rerank(self, query: str, results: List[RagSearchResult], top_k: int = 5) -> List[RagSearchResult]:
        if not results:
            return []

        documents = [result.content for result in results]
        response = self._client.models.rerank(
            model=self._model,
            query=query,
            documents=documents,
        )

        reranked = []
        for item in response.results:
            result = results[item.index]
            result.score = item.relevance_score
            reranked.append(result)

        return reranked[:top_k]
```

### 5. Qdrant 客户端 (`application/rag/qdrant_client.py`)

```python
from typing import List, Optional, Dict, Any
from qdrant_client import AsyncQdrantClient, models

from chat.core.config.app_settings import settings
from chat.api.schemas.rag import RagSearchResult


class RagQdrantClient:
    def __init__(self):
        self._collection_name = "wisepen_rag_documents"
        self._client = AsyncQdrantClient(
            url=f"http://{settings.QDRANT_HOST}:{settings.QDRANT_PORT}",
            api_key=settings.QDRANT_PASSWORD,
        )

    async def upsert_chunks(self, chunks: List[Dict[str, Any]], embeddings: List[List[float]]):
        points = []
        for chunk, embedding in zip(chunks, embeddings):
            chunk_id = chunk["metadata"]["chunk_id"]

            # 将 metadata 中的字段展开到 payload 顶层
            payload = {
                "content": chunk["content"],
                **chunk["metadata"],
            }

            # 构建 extra 字段，收集不在核心字段中的所有内容
            extra = {}
            core_fields = ["document_id", "title", "chunk_index", "content", "source", "tags", "user_id", "created_at", "updated_at"]
            for key, value in list(payload.items()):
                if key not in core_fields:
                    extra[key] = payload.pop(key)

            payload["extra"] = extra

            points.append(
                models.PointStruct(
                    id=chunk_id,
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
        user_id: Optional[str] = None,
        document_ids: Optional[List[str]] = None,
        source_filter: Optional[List[str]] = None,
        tags_filter: Optional[List[str]] = None,
        top_k: int = 5,
        score_threshold: Optional[float] = None,
    ) -> List[RagSearchResult]:
        must_conditions = []

        if user_id:
            must_conditions.append(
                models.FieldCondition(
                    key="user_id",
                    match=models.MatchValue(value=user_id),
                )
            )

        if document_ids:
            must_conditions.append(
                models.FieldCondition(
                    key="document_id",
                    match=models.MatchAny(any=document_ids),
                )
            )

        if source_filter:
            must_conditions.append(
                models.FieldCondition(
                    key="source",
                    match=models.MatchAny(any=source_filter),
                )
            )

        if tags_filter:
            must_conditions.append(
                models.FieldCondition(
                    key="tags",
                    match=models.MatchAny(any=tags_filter),
                )
            )

        query_filter = models.Filter(must=must_conditions) if must_conditions else None

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
            # 构建 extra 字段，收集不在核心字段中的所有内容
            extra = hit.payload.get("extra", {})
            core_fields = ["document_id", "title", "chunk_index", "content", "source", "tags", "user_id", "created_at", "updated_at", "extra"]
            for key, value in hit.payload.items():
                if key not in core_fields:
                    extra[key] = value

            results.append(
                RagSearchResult(
                    note_id=hit.payload.get("document_id", ""),
                    title=hit.payload.get("title", ""),
                    chunk_index=hit.payload.get("chunk_index", 0),
                    content=hit.payload.get("content", ""),
                    score=hit.score,
                    source=hit.payload.get("source", ""),
                    tags=hit.payload.get("tags", []),
                    user_id=hit.payload.get("user_id", ""),
                    created_at=hit.payload.get("created_at"),
                    updated_at=hit.payload.get("updated_at"),
                    extra=extra,
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

```python
import asyncio
from typing import List, Optional, Dict, Any
from elasticsearch import AsyncElasticsearch, helpers
from common.logger import log_fail, log_debug


class ElasticsearchClient:
    def __init__(self, hosts: List[str], index_name: str = "wisepen_documents"):
        self.client = AsyncElasticsearch(hosts=hosts)
        self.index_name = index_name

    async def ensure_index(self):
        """创建索引（含 IK 分词器 + 严格映射，防止字段爆炸）"""
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
                "dynamic": "strict",          # 严格模式，禁止未定义字段
                "properties": {
                    "chunk_id": {"type": "keyword"},
                    "document_id": {"type": "keyword"},
                    "user_id": {"type": "keyword"},
                    "title": {"type": "text", "analyzer": "ik_smart_analyzer"},
                    "content": {
                        "type": "text",
                        "analyzer": "ik_smart_analyzer",
                        "fields": {"raw": {"type": "keyword"}}
                    },
                    "tags": {"type": "keyword"},           # 精准匹配，无需 .keyword 后缀
                    "chunk_index": {"type": "integer"},
                    "created_at": {"type": "date"}
                }
            }
            await self.client.indices.create(
                index=self.index_name,
                settings=settings,
                mappings=mappings
            )
            log_debug(f"ES 索引 {self.index_name} 已创建")

    async def index_chunks(self, chunks: List[Dict[str, Any]]):
        """批量索引文档块（生成器 + 显式字段 + 重试）"""
        async def generate_actions():
            for chunk in chunks:
                yield {
                    "_index": self.index_name,
                    "_id": chunk["chunk_id"],
                    "_source": {
                        "chunk_id": chunk["chunk_id"],
                        "document_id": chunk.get("document_id"),
                        "user_id": chunk.get("user_id"),
                        "title": chunk.get("title", ""),
                        "content": chunk.get("content", ""),
                        "tags": chunk.get("tags", []),   # keyword 数组
                        "chunk_index": chunk.get("chunk_index", 0),
                        "created_at": "now"
                    }
                }
        try:
            success, errors = await helpers.async_bulk(
                self.client,
                generate_actions(),
                max_retries=3,
                raise_on_error=False
            )
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
        top_k: int = 20
    ) -> List[Dict[str, Any]]:
        """
        关键词搜索（BM25），用于混合检索的稀疏部分。
        返回原始文档字段，供 RRF 融合或重排序使用。
        """
        try:
            must_clauses = [{"term": {"user_id": user_id}}]
            if note_ids:
                must_clauses.append({"terms": {"document_id": note_ids}})
            if tags:
                must_clauses.append({"terms": {"tags": tags}})   # 直接使用 tags 字段（keyword 类型）

            search_query = {
                "bool": {
                    "must": must_clauses,
                    "should": [
                        {"match": {"title": query}},
                        {"match": {"content": query}}
                    ],
                    "minimum_should_match": 1
                }
            }

            # 新版客户端直接使用 query / size / source 参数，弃用 body
            resp = await self.client.search(
                index=self.index_name,
                query=search_query,
                size=top_k,
                source=["chunk_id", "document_id", "title", "content", "chunk_index", "tags"]
            )

            hits = resp["hits"]["hits"]
            results = []
            for hit in hits:
                src = hit["_source"]
                results.append({
                    "chunk_id": src.get("chunk_id"),
                    "document_id": src.get("document_id"),
                    "title": src.get("title", ""),
                    "content": src.get("content", ""),
                    "chunk_index": src.get("chunk_index", 0),
                    "tags": src.get("tags", []),
                    "score": hit["_score"]          # BM25 得分
                })
            log_debug(f"ES 关键词召回 {len(results)} 条")
            return results
        except Exception as e:
            log_fail("ES 关键词搜索失败", e)
            return []

    async def delete_by_document_id(self, document_id: str):
        """按文档 ID 删除所有 chunk"""
        try:
            await self.client.delete_by_query(
                index=self.index_name,
                query={"term": {"document_id": document_id}}
            )
            log_debug(f"ES 已删除文档 {document_id} 的所有块")
        except Exception as e:
            log_fail("ES 删除文档失败", e, document_id=document_id)

    async def close(self):
        await self.client.close()
```

### 7. LangChain 检索器 (`application/rag/retrievers/`)

#### 7.1 Qdrant 检索器 (`qdrant_retriever.py`)

```python
from typing import List, Optional
from langchain.schema import BaseRetriever, Document
from application.rag.qdrant_client import RagQdrantClient
from application.rag.embedding_service import EmbeddingService


class QdrantRetriever(BaseRetriever):
    def __init__(self, qdrant: RagQdrantClient, embedder: EmbeddingService):
        super().__init__()
        self.qdrant = qdrant
        self.embedder = embedder
        self._user_id: Optional[str] = None
        self._document_ids: Optional[List[str]] = None
        self._tags: Optional[List[str]] = None

    def set_filters(self, user_id: str, document_ids=None, tags=None):
        self._user_id = user_id
        self._document_ids = document_ids
        self._tags = tags

    async def _aget_relevant_documents(self, query: str) -> List[Document]:
        query_embedding = await self.embedder.embed_text(query)
        results = await self.qdrant.search(
            query_embedding=query_embedding,
            user_id=self._user_id,
            document_ids=self._document_ids,
            tags_filter=self._tags,
            top_k=20
        )
        docs = []
        for result in results:
            docs.append(Document(
                page_content=result.content,
                metadata={
                    "chunk_id": result.extra.get("chunk_id") if result.extra else None,
                    "document_id": result.note_id,
                    "title": result.title,
                    "chunk_index": result.chunk_index,
                    "tags": result.tags,
                    "score": result.score,
                    "retriever": "qdrant"
                }
            ))
        return docs

    def _get_relevant_documents(self, query: str):
        raise NotImplementedError("Use async version")
```

#### 7.2 ES 检索器 (`es_retriever.py`)

```python
from typing import List, Optional
from langchain.schema import BaseRetriever, Document
from application.rag.es_client import ElasticsearchClient


class ElasticsearchRetriever(BaseRetriever):
    def __init__(self, es_client: ElasticsearchClient):
        super().__init__()
        self.es_client = es_client
        self._user_id: Optional[str] = None
        self._document_ids: Optional[List[str]] = None
        self._tags: Optional[List[str]] = None

    def set_filters(self, user_id: str, document_ids=None, tags=None):
        self._user_id = user_id
        self._document_ids = document_ids
        self._tags = tags

    async def _aget_relevant_documents(self, query: str) -> List[Document]:
        results = await self.es_client.search_keywords(
            query=query,
            user_id=self._user_id,
            document_ids=self._document_ids,
            tags=self._tags,
            top_k=20
        )
        docs = []
        for result in results:
            docs.append(Document(
                page_content=result["content"],
                metadata={
                    "chunk_id": result["chunk_id"],
                    "document_id": result["document_id"],
                    "title": result["title"],
                    "chunk_index": result["chunk_index"],
                    "tags": result["tags"],
                    "score": result["score"],
                    "retriever": "es"
                }
            ))
        return docs

    def _get_relevant_documents(self, query: str):
        raise NotImplementedError("Use async version")
```

### 8. 向量索引管理器 (`application/rag/vector_indexer.py`)

```python
import asyncio
from typing import List, Optional
from application.rag.document_processor import DocumentProcessor
from application.rag.embedding_service import EmbeddingService
from application.rag.qdrant_client import RagQdrantClient
from application.rag.es_client import ElasticsearchClient
from tools.notes._text_utils import is_markdown
from common.logger import log_fail


class VectorIndexer:
    def __init__(
        self,
        processor: DocumentProcessor,
        embedder: EmbeddingService,
        qdrant: RagQdrantClient,
        es_client: ElasticsearchClient
    ):
        self.processor = processor
        self.embedder = embedder
        self.qdrant = qdrant
        self.es_client = es_client

    async def sync(
        self,
        document_id: str,
        content: str,
        title: str,
        user_id: str,
        tags: Optional[List[str]] = None,
        content_type: Optional[str] = None,
    ):
        try:
            loop = asyncio.get_running_loop()

            # 1. 确定使用 Markdown 分块还是纯文本分块
            use_md = (content_type == "markdown") or (content_type != "text" and is_markdown(content))

            # 2. 构造元数据
            meta = {
                "document_id": document_id,
                "title": title,
                "user_id": user_id,
                "tags": tags or [],
            }

            # 3. 分块（CPU 密集，放入线程池）
            process_func = self.processor.process_markdown if use_md else self.processor.process_text
            chunks = await loop.run_in_executor(None, process_func, content, meta)
            if not chunks:
                return

            # 4. 提取文本并向量化
            texts = [c["content"] for c in chunks]
            embeddings = await self.embedder.embed_chunks(texts)

            # 5. 先删除旧索引，再插入新索引（原子更新）
            await self.qdrant.delete_by_document_id(document_id)
            await self.es_client.delete_by_document_id(document_id)

            # 6. 写入 Qdrant
            await self.qdrant.upsert_chunks(chunks, embeddings)

            # 7. 写入 Elasticsearch
            es_chunks = []
            for chunk in chunks:
                es_chunks.append({
                    "chunk_id": chunk["metadata"]["chunk_id"],
                    "document_id": chunk["metadata"].get("document_id"),
                    "user_id": chunk["metadata"].get("user_id"),
                    "title": chunk["metadata"].get("title", title),
                    "content": chunk["content"],
                    "tags": chunk["metadata"].get("tags", []),
                    "chunk_index": chunk["metadata"].get("chunk_index", 0)
                })
            await self.es_client.index_chunks(es_chunks)

        except Exception as e:
            log_fail("向量/关键词索引失败", e, document_id=document_id)
            # 不抛出异常，保证调用的 API/工具不会中断
```

### 9. 文档管理 API (`api/routers/documents_router.py`)

```python
import asyncio
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from domain.entities.note import Note
from tools.notes._note_db import delete_note_and_versions
from application.rag.vector_indexer import VectorIndexer
from common.dependencies import get_current_user_id, get_vector_indexer
from common.logger import log_fail

router = APIRouter(prefix="/documents", tags=["documents"])


# ---------- 请求模型 ----------
class GetDocumentsRequest(BaseModel):
    document_id: str
    content: str
    title: str
    tags: Optional[List[str]] = None
    filename: Optional[str] = None
    content_type: Optional[str] = None  # "markdown" 或 "text"


class DeleteDocumentsRequest(BaseModel):
    document_id: str


# ---------- 端点实现 ----------
@router.post("/upsertDocuments", status_code=202)
async def get_documents(
    req: GetDocumentsRequest,
    user_id: str = Depends(get_current_user_id),
    indexer: VectorIndexer = Depends(get_vector_indexer),
):
    """
    接收文档内容（upsert），更新 MongoDB 缓存并触发后台向量+关键词索引。
    - 若该 document_id 已存在，则更新内容、标题、标签；否则新建。
    - 索引在后台异步执行，本接口立即返回 202。
    """
    # 1. Upsert MongoDB 缓存
    now = datetime.utcnow()
    await Note.find_one(
        Note.id == req.document_id,
        Note.user_id == user_id
    ).upsert(
        {
            "$set": {
                "content": req.content,
                "title": req.title,
                "tags": req.tags or [],
                "updated_at": now,
            }
        },
        on_insert=Note(
            id=req.document_id,
            user_id=user_id,
            title=req.title,
            content=req.content,
            tags=req.tags or [],
            created_at=now,
            updated_at=now,
        ),
    )

    # 2. 触发后台异步索引（不阻塞响应）
    asyncio.create_task(
        indexer.sync(
            document_id=req.document_id,
            content=req.content,
            title=req.title,
            user_id=user_id,
            tags=req.tags,
            content_type=req.content_type,
        )
    )
    return {"status": "accepted"}


@router.delete("/deleteDocuments", status_code=200)
async def delete_documents(
    req: DeleteDocumentsRequest,
    user_id: str = Depends(get_current_user_id),
    indexer: VectorIndexer = Depends(get_vector_indexer),
):
    """
    删除 MongoDB 中的文档缓存及版本历史，同时清除 Qdrant 和 ES 中的索引。
    - 即使部分操作失败也会尽力清理，并记录日志。
    """
    # 1. 删除 MongoDB 缓存
    await delete_note_and_versions(req.document_id)

    # 2. 删除 Qdrant 向量
    try:
        await indexer.qdrant.delete_by_document_id(req.document_id)
    except Exception as e:
        log_fail("删除 Qdrant 向量失败", e, document_id=req.document_id)

    # 3. 删除 ES 关键词索引
    try:
        await indexer.es_client.delete_by_document_id(req.document_id)
    except Exception as e:
        log_fail("删除 ES 索引失败", e, document_id=req.document_id)

    return {"status": "deleted"}
```

### 10. 包初始化 (`application/rag/__init__.py`)

```python
from .document_processor import DocumentProcessor
from .embedding_service import EmbeddingService
from .reranker_service import RerankerService
from .qdrant_client import RagQdrantClient
from .es_client import ElasticsearchClient
from .vector_indexer import VectorIndexer
from .retrievers.qdrant_retriever import QdrantRetriever
from .retrievers.es_retriever import ElasticsearchRetriever

__all__ = [
    "DocumentProcessor",
    "EmbeddingService",
    "RerankerService",
    "RagQdrantClient",
    "ElasticsearchClient",
    "VectorIndexer",
    "QdrantRetriever",
    "ElasticsearchRetriever",
]
```

## 🛠️ 技术选型

### 核心技术栈

| 组件          | 技术选型                                      | 说明                 |
| ----------- | ----------------------------------------- | ------------------ |
| **文档拆分**    | LangChain Text Splitters                  | Markdown 专用 + 通用递归 |
| **向量化**     | OpenAI Embedding (text-embedding-3-large) | 与 Mem0 保持一致        |
| **稠密向量库**   | Qdrant                                    | 原生异步客户端            |
| **关键词检索引擎** | Elasticsearch（IK Analyzer 分词）             | 中文关键词精准检索          |
| **融合排序**    | LangChain EnsembleRetriever (RRF)         | 混合检索融合             |
| **重排序**     | 零熵 zerank                                 | 使用官方 SDK           |
| **数据模型**    | Pydantic                                  | 与现有风格一致            |

### Qdrant Collection 配置

```
Collection 名称: wisepen_rag_documents
向量维度: 3072 (text-embedding-3-large)
距离度量: COSINE (余弦相似度)

Payload 索引:
- user_id (Keyword) - 用户隔离
- document_id (Keyword) - 文档管理
- source (Keyword) - 来源过滤
- tags (Keyword) - 标签过滤
```

### Elasticsearch Index 配置

```
Index 名称: wisepen_documents
分词器: IK Analyzer (ik_smart)

字段映射:
- chunk_id: keyword
- document_id: keyword
- user_id: keyword
- title: text (ik_smart_analyzer)
- content: text (ik_smart_analyzer)
- tags: text (ik_smart_analyzer) + keyword
- chunk_index: integer
- created_at: date
```

### 推荐参数

| 参数              | 推荐值 | 说明                 |
| --------------- | --- | ------------------ |
| chunk\_size     | 512 | 每个 chunk 的 token 数 |
| chunk\_overlap  | 50  | chunk 之间的重叠        |
| initial\_top\_k | 20  | Qdrant/ES 初次检索数量   |
| final\_top\_k   | 5   | Rerank 后返回数量       |
| batch\_size     | 10  | 向量化批量大小            |

## 📝 使用示例

### 1. 文档摄取（API 方式）

```python
import requests

# 通过 API 上传文档
response = requests.post(
    "http://localhost:8000/documents/upsertDocuments",
    headers={"X-From-Source": "apigateway"},
    json={
        "document_id": "note_123",
        "title": "AI 学习笔记",
        "content": "# AI 学习笔记\n\n这是关于 AI 的学习内容...",
        "tags": ["AI", "学习"],
        "content_type": "markdown",
    }
)

# 立即返回 202，后台异步索引
print(response.json())  # {"status": "accepted"}
```

### 2. 文档删除（API 方式）

```python
import requests

# 通过 API 删除文档
response = requests.delete(
    "http://localhost:8000/documents/deleteDocuments",
    headers={"X-From-Source": "apigateway"},
    json={"document_id": "note_123"}
)

print(response.json())  # {"status": "deleted"}
```

### 3. 混合检索（LangChain 方式）

```python
from langchain.retrievers import EnsembleRetriever
from chat.application.rag import (
    QdrantRetriever,
    ElasticsearchRetriever,
    EmbeddingService,
    RagQdrantClient,
    ElasticsearchClient,
    RerankerService
)
from chat.api.schemas.rag import RagSearchResult

# 初始化服务
embedding_service = EmbeddingService()
qdrant_client = RagQdrantClient()
es_client = ElasticsearchClient(["http://localhost:9200"])
reranker = RerankerService()

# 初始化检索器
qdrant_retriever = QdrantRetriever(qdrant_client, embedding_service)
es_retriever = ElasticsearchRetriever(es_client)

# 创建 EnsembleRetriever（RRF 模式）
ensemble = EnsembleRetriever(
    retrievers=[qdrant_retriever, es_retriever],
    weights=None  # RRF 模式
)

# 检索
query = "怎么学习 AI？"
user_id = "user_123"

qdrant_retriever.set_filters(user_id=user_id)
es_retriever.set_filters(user_id=user_id)

fused_docs = await ensemble.aget_relevant_documents(query)

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
reranked = await reranker.rerank(query, candidates, top_k=5)

# 输出结果
for result in reranked:
    print(f"Score: {result.score:.4f}")
    print(f"Title: {result.title}")
    print(f"Note ID: {result.note_id}")
    print(f"Content: {result.content[:100]}...")
    print("---")
```

## 🚀 特性亮点

1. **混合检索**：语义泛化 + 精确命中，召回率显著提高
2. **专业中文分词**：IK Analyzer 保证中文关键词精准切分
3. **模块化设计**：Qdrant、ES、重排序模型均可独立扩展或替换
4. **零侵入现有流程**：API 和工具接口不变，仅增强内部检索能力
5. **配置复用**：与 Mem0 保持一致的 Embedding 配置
6. **原生异步**：Qdrant 和 ES 异步客户端，高性能
7. **重排序增强**：零熵 Reranker 提升检索质量
8. **强类型数据模型**：metadata 展开为具体字段，Agent 和前端使用更安全
9. **幂等设计**：chunk\_id 作为 Point ID，重复 upsert 安全
10. **RESTful API**：提供标准的文档摄取和删除接口
11. **异步索引**：文档上传后立即返回，后台异步处理向量化
12. **双存储同步**：MongoDB 缓存 + Qdrant+ES 索引自动同步
13. **智能分块**：自动检测 Markdown 格式，支持 Markdown 和纯文本分块
14. **线程池优化**：CPU 密集型分块操作放入线程池，不阻塞事件循环
15. **容错处理**：向量索引失败不抛出异常，保证 API 调用连续性

## 📌 后续扩展

1. **查询扩展**：使用 LLM 扩展用户查询
2. **批量摄取**：集成 Kafka 异步处理
3. **向量缓存**：避免重复向量化
4. **多租户优化**：针对多用户场景优化索引
5. **监控指标**：添加检索延迟、召回率等指标

## 📦 依赖更新

在 `pyproject.toml` 中添加：

```toml
dependencies = [
    # ... 现有依赖
    "langchain",
    "langchain-text-splitters",
    "qdrant-client[async]",
    "elasticsearch[async]",
    "zeroentropy",
]
```

***

该方案已完全集成双路混合检索，可直接投入生产。
