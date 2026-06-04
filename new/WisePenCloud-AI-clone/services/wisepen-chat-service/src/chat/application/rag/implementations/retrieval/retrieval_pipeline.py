from typing import List

from common.logger import log_event
from chat.application.algorithms.ranking.mmr import (
    MmrCandidate,
    select_by_mmr,
)
from chat.application.rag.domain.answerability import (
    EvidenceSufficiencyEvaluator,
)
from chat.application.rag.domain.candidate_fusion import RagCandidateFusion
from chat.application.rag.domain.parent_aggregation import RagParentAggregator
from chat.application.rag.domain.retrieval_execution import (
    RagRetrievalDiagnosticItem,
    RagRetrievalPipelineResult,
)
from chat.application.rag.domain.retrieval_hits import ChannelRetrievalResult
from chat.application.rag.domain.retrieval_planning import RagRetrievalQuery
from .evidence_assembler import RagEvidenceAssembler
from .reranker import ZeroEntropyReranker
from .retrieval_orchetrator import RagRetrievalOrchestrator


class RagRetrievalPipeline:
    """RAG 检索流水线。

    - 任何中间环节失败都视为失败，返回空结果
    """

    def __init__(
            self,
            retrieval_orchestrator: RagRetrievalOrchestrator,
            candidate_fusion: RagCandidateFusion,
            evidence_assembler: RagEvidenceAssembler,
            reranker: ZeroEntropyReranker,
            sufficiency_evaluator: EvidenceSufficiencyEvaluator,
            parent_aggregator: RagParentAggregator,
    ) -> None:
        """初始化对象依赖。"""
        self._retrieval_orchestrator = retrieval_orchestrator
        self._candidate_fusion = candidate_fusion
        self._evidence_assembler = evidence_assembler
        self._reranker = reranker
        self._sufficiency_evaluator = sufficiency_evaluator
        self._parent_aggregator = parent_aggregator

    async def retrieve(self, query: RagRetrievalQuery) -> RagRetrievalPipelineResult:
        """RAG 混合检索、重排与多样性网格组装流。"""

        # 异步多通道召回
        channel_results = await self._retrieval_orchestrator.retrieve_channels(query)
        diagnostics: List[RagRetrievalDiagnosticItem] = []
        log_event(
            "rag retrieval pipeline stage",
            user_id=query.user_id,
            stage="channel_retrieval",
            channel_count=len(channel_results),
            candidate_count=sum(len(result.candidates) for result in channel_results),
        )

        # RRF 算法候选融合
        fused_candidates = self._candidate_fusion.fuse(
            mode=query.mode,
            channel_results=channel_results,
            top_k=query.fusion_top_k,
        )
        log_event(
            "rag retrieval pipeline stage",
            user_id=query.user_id,
            stage="candidate_fusion",
            candidate_count=len(fused_candidates),
        )

        if not fused_candidates:
            return self._build_empty_result(query, channel_results, diagnostics)

        diagnostics.extend(_build_fusion_diagnostics(fused_candidates))

        # 异步补全上下文水合
        parent_candidates = self._parent_aggregator.aggregate(
            fused_candidates=fused_candidates,
            top_k=query.fusion_top_k,
        )
        log_event(
            "rag retrieval pipeline stage",
            user_id=query.user_id,
            stage="parent_aggregation",
            candidate_count=len(parent_candidates),
        )

        if not parent_candidates:
            return self._build_empty_result(query, channel_results, diagnostics)

        hydrated_candidates = await self._evidence_assembler.hydrate_candidates(
            parent_candidates=parent_candidates,
            neighbor_before=query.neighbor_before,
            neighbor_after=query.neighbor_after,
        )
        log_event(
            "rag retrieval pipeline stage",
            user_id=query.user_id,
            stage="evidence_hydration",
            candidate_count=len(hydrated_candidates),
        )

        # 提取重排输入并执行精排
        rerankable_documents = self._evidence_assembler.build_rerankable_documents(
            hydrated_candidates
        )
        reranked_documents = await self._reranker.rerank(
            query=query.query,
            documents=rerankable_documents,
            top_n=query.rerank_top_n,
        )
        log_event(
            "rag retrieval pipeline stage",
            user_id=query.user_id,
            stage="rerank",
            input_count=len(rerankable_documents),
            candidate_count=len(reranked_documents),
        )

        if not reranked_documents:
            return self._build_empty_result(query, channel_results, diagnostics)

        hydrated_map = {c.candidate_id: c for c in hydrated_candidates}
        diagnostics.extend(_build_rerank_diagnostics(reranked_documents, hydrated_map))

        # 组装 MMR 多样性算子输入
        mmr_candidates = [
            MmrCandidate(
                id=doc.id,
                relevance_score=doc.score,
                similarity_text=hydrated_map[doc.id].mmr_text,
                group_key=hydrated_map[doc.id].group_key,
            )
            for doc in reranked_documents
        ]

        # MMR 语义去重
        selected_items = select_by_mmr(
            mmr_candidates,
            top_k=query.final_top_k,
            lambda_mult=query.mmr_lambda,
        )
        log_event(
            "rag retrieval pipeline stage",
            user_id=query.user_id,
            stage="mmr_selection",
            input_count=len(mmr_candidates),
            candidate_count=len(selected_items),
        )

        if not selected_items:
            return self._build_empty_result(query, channel_results, diagnostics)

        diagnostics.extend(_build_mmr_diagnostics(selected_items, hydrated_map))

        # 回连水合实体，包装输出最终 Evidence 证据链
        evidences = self._evidence_assembler.build_evidences(
            hydrated_candidates=hydrated_candidates,
            selected_items=selected_items,
        )

        sufficiency = self._sufficiency_evaluator.evaluate(
            query=query,
            evidences=evidences,
        )
        log_event(
            "rag retrieval pipeline completed",
            user_id=query.user_id,
            evidence_count=len(evidences),
            sufficient=sufficiency.sufficient,
            insufficient_reason=(
                sufficiency.reason.value if sufficiency.reason is not None else None
            ),
        )

        return RagRetrievalPipelineResult(
            query=query,
            channel_results=channel_results,
            evidences=evidences,
            sufficiency=sufficiency,
            diagnostics=diagnostics,
        )

    def _build_empty_result(
            self,
            query: RagRetrievalQuery,
            channel_results: List[ChannelRetrievalResult],
            diagnostics: List[RagRetrievalDiagnosticItem],
    ) -> RagRetrievalPipelineResult:
        """空结果构造"""
        return RagRetrievalPipelineResult(
            query=query,
            channel_results=channel_results,
            evidences=[],
            sufficiency=self._sufficiency_evaluator.evaluate(
                query=query,
                evidences=[],
            ),
            diagnostics=diagnostics,
        )


def _build_fusion_diagnostics(fused_candidates) -> List[RagRetrievalDiagnosticItem]:
    items: List[RagRetrievalDiagnosticItem] = []
    for fused_candidate in fused_candidates[:10]:
        candidate = fused_candidate.candidate
        items.append(
            RagRetrievalDiagnosticItem(
                stage="fusion",
                rank=fused_candidate.rank,
                candidate_id=candidate.chunk_id,
                resource_id=candidate.resource_id,
                chunk_id=candidate.chunk_id,
                parent_chunk_id=candidate.parent_chunk_id,
                score=round(fused_candidate.rrf_score, 6),
                sources=fused_candidate.sources,
            )
        )
    return items


def _build_rerank_diagnostics(
    reranked_documents,
    hydrated_map,
) -> List[RagRetrievalDiagnosticItem]:
    items: List[RagRetrievalDiagnosticItem] = []
    for doc in reranked_documents[:10]:
        hydrated = hydrated_map[doc.id]
        parent_candidate = hydrated.parent_candidate
        best_child_hit = parent_candidate.best_child_hit
        items.append(
            RagRetrievalDiagnosticItem(
                stage="rerank",
                rank=doc.rank,
                candidate_id=doc.id,
                resource_id=parent_candidate.resource_id,
                chunk_id=best_child_hit.chunk_id,
                parent_chunk_id=parent_candidate.chunk_id,
                score=round(doc.score, 6),
                sources=[channel.value for channel in parent_candidate.matched_channels],
            )
        )
    return items


def _build_mmr_diagnostics(
    selected_items,
    hydrated_map,
) -> List[RagRetrievalDiagnosticItem]:
    items: List[RagRetrievalDiagnosticItem] = []
    for item in selected_items[:10]:
        hydrated = hydrated_map[item.id]
        parent_candidate = hydrated.parent_candidate
        best_child_hit = parent_candidate.best_child_hit
        items.append(
            RagRetrievalDiagnosticItem(
                stage="mmr",
                rank=item.rank,
                candidate_id=item.id,
                resource_id=parent_candidate.resource_id,
                chunk_id=best_child_hit.chunk_id,
                parent_chunk_id=parent_candidate.chunk_id,
                score=round(item.mmr_score, 6),
                sources=[channel.value for channel in parent_candidate.matched_channels],
            )
        )
    return items
