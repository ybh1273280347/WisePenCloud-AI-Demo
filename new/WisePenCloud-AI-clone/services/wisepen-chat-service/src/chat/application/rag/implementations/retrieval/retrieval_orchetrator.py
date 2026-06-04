import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable
from typing import List

from common.logger import log_event, log_fail
from chat.application.rag.domain.enums import RetrievalChannel
from chat.application.rag.domain.retrieval_hits import ChannelRetrievalResult
from chat.application.rag.domain.retrieval_planning import (
    RagIndexScope,
    RagRetrievalQuery,
)
from .elasticsearch_retriever import (
    ElasticsearchKeywordRetriever,
)
from .manifest_resolver import RagManifestResolver
from .qdrant_retriever import QdrantChunkRetriever


@dataclass(frozen=True, slots=True)
class _RetrievalTaskSpec:
    """单个检索通道任务描述。"""

    name: str
    query: str
    task: Awaitable[ChannelRetrievalResult]


class RagRetrievalOrchestrator:
    """RAG 多通道检索编排服务。

    - 读取当前用户已发布 Manifest。
    - 基于 Manifest 构造线上索引范围。
    - 并发调用 dense / bm25 / keyword 三个召回通道。
    - 不负责候选融合。
    - 不负责 evidence 组装。
    - 不负责 rerank。
    """

    def __init__(
            self,
            manifest_resolver: RagManifestResolver,
            qdrant_retriever: QdrantChunkRetriever,
            elasticsearch_retriever: ElasticsearchKeywordRetriever,
    ) -> None:
        """初始化对象依赖。"""
        self._manifest_resolver = manifest_resolver
        self._qdrant_retriever = qdrant_retriever
        self._elasticsearch_retriever = elasticsearch_retriever

    async def retrieve_channels(
            self,
            query: RagRetrievalQuery,
    ) -> List[ChannelRetrievalResult]:
        """执行多通道候选召回。"""

        manifests = await self._manifest_resolver.resolve_user_manifests(
            user_id=query.user_id,
            resource_kinds=query.resource_kinds,
        )

        # 检索过滤范围
        scopes = [
            RagIndexScope(
                user_id=manifest.user_id,
                resource_kind=manifest.resource_kind,
                resource_id=manifest.resource_id,
                index_version=manifest.current_index_version,
            )
            for manifest in manifests
        ]

        # 没有已发布 Manifest 时，当前用户没有可检索索引，直接安全阻断
        if not scopes:
            log_event(
                "rag retrieval channels skipped",
                user_id=query.user_id,
                reason="no_published_manifest",
            )
            return []

        semantic_queries: List[str] = query.semantic_queries if query.semantic_queries else [query.query,]
        keyword_queries: List[str] = query.keyword_queries if query.keyword_queries else [query.query,]

        task_specs: List[_RetrievalTaskSpec] = []

        # 灌装 Dense 稠密向量检索任务
        for semantic_query in semantic_queries:
            task_specs.append(
                _RetrievalTaskSpec(
                    name=RetrievalChannel.DENSE_SEMANTIC.value,
                    query=semantic_query,
                    task=self._qdrant_retriever.retrieve_dense(
                        query=semantic_query,
                        scopes=scopes,
                        top_k=query.top_k,
                    ),
                )
            )

        # 灌装 BM25 与 ES 关键词检索任务
        for keyword_query in keyword_queries:
            task_specs.append(
                _RetrievalTaskSpec(
                    name=RetrievalChannel.SPARSE_LEXICAL.value,
                    query=keyword_query,
                    task=self._qdrant_retriever.retrieve_bm25(
                        query=keyword_query,
                        scopes=scopes,
                        top_k=query.top_k,
                    ),
                )
            )
            task_specs.append(
                _RetrievalTaskSpec(
                    name=RetrievalChannel.KEYWORD_EXACT.value,
                    query=keyword_query,
                    task=self._elasticsearch_retriever.retrieve_keyword(
                        query=keyword_query,
                        scopes=scopes,
                        top_k=query.top_k,
                    ),
                )
            )

        started_at = time.monotonic()
        raw_results = await asyncio.gather(
            *(spec.task for spec in task_specs),
            return_exceptions=True,
        )

        channel_results: List[ChannelRetrievalResult] = []
        for spec, result in zip(task_specs, raw_results):
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            if isinstance(result, Exception):
                log_fail(
                    "rag retrieval channel failed",
                    result,
                    user_id=query.user_id,
                    channel=spec.name,
                    query=spec.query,
                    scope_count=len(scopes),
                    elapsed_ms=elapsed_ms,
                )
                continue

            log_event(
                "rag retrieval channel succeeded",
                user_id=query.user_id,
                channel=result.channel.value,
                query=spec.query,
                candidate_count=len(result.candidates),
                scope_count=len(scopes),
                elapsed_ms=elapsed_ms,
            )
            channel_results.append(result)

        log_event(
            "rag retrieval channels completed",
            user_id=query.user_id,
            channel_count=len(task_specs),
            success_count=len(channel_results),
            failure_count=len(task_specs) - len(channel_results),
            candidate_count=sum(len(result.candidates) for result in channel_results),
            elapsed_ms=int((time.monotonic() - started_at) * 1000),
        )

        return channel_results
