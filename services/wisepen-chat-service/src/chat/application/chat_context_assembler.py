from typing import Any, Dict, List, Optional, Set, Tuple

from chat.domain.entities import ChatMessage, ChatSession, Role
from chat.domain.entities.skill import SkillMeta
from chat.domain.repositories import (
    HotContextRepository,
    MessageRepository,
    SessionRepository,
)
from common.logger import log_error, log_fail

_CTX_TOKEN_LIMIT = 128000
_CTX_HIGH_WATERMARK_RATIO = 0.8
_CTX_LOW_WATERMARK_RATIO = 0.5
_CTX_FALLBACK_HISTORY_LIMIT = 20


class ChatContextAssembler:
    """负责短期上下文的全生命周期管理：Redis 热缓存读取与降级回填、上下文裁剪、Prompt 组装"""

    def __init__(
        self,
        message_repo: MessageRepository,
        session_repo: SessionRepository,
        hot_context_repo: HotContextRepository,
    ):
        self.message_repo = message_repo
        self.session_repo = session_repo
        self.hot_context_repo = hot_context_repo

    async def get_or_repopulate_hot_context(self, session_id: str) -> List[ChatMessage]:
        """
        从 Redis 拉取短期上下文
        若返回空列表（缓存过期或异常），则从 MongoDB 回填最近 N 条记录，重建热缓存
        若会话有历史摘要，只拉取摘要时间戳之后的未压缩明细，避免已压缩历史重复注入
        """
        try:
            recent_messages = await self.hot_context_repo.get_recent_context(session_id)
        except Exception as e:
            log_fail("Redis 上下文读取", e, session=session_id)
            recent_messages = []

        if not recent_messages:
            try:
                session: Optional[ChatSession] = await self.session_repo.get_by_id(
                    session_id
                )
                if session and session.summary_updated_at:
                    # 有压缩记录：只拉取压缩时间戳之后的明细，跳过已摘要的历史
                    history = await self.message_repo.get_after_time(
                        session_id=session_id,
                        after=session.summary_updated_at,
                        limit=_CTX_FALLBACK_HISTORY_LIMIT,
                    )
                else:
                    # 无压缩记录：正常拉取最近 N 条
                    history = await self.message_repo.get_by_session(
                        session_id, limit=_CTX_FALLBACK_HISTORY_LIMIT
                    )
                if history:
                    await self.hot_context_repo.load_messages(session_id, history)
                    return history
            except Exception as e:
                log_error("Redis 上下文回填", e, session=session_id)

        return recent_messages

    async def get_session_summary(self, session_id: str) -> Optional[str]:
        """从 MongoDB 读取当前会话的摘要（如有）"""
        try:
            session: Optional[ChatSession] = await self.session_repo.get_by_id(
                session_id
            )
            return session.current_summary if session else None
        except Exception:
            return None

    async def build_context_window(
        self,
        messages: List[ChatMessage],
    ) -> Tuple[List[ChatMessage], List[ChatMessage], bool]:
        """
        从后往前累加Token，构建不超过高水位预算的动态滑动窗口。若超过高水位，则触发摘要。
        """
        high_budget = int(_CTX_TOKEN_LIMIT * _CTX_HIGH_WATERMARK_RATIO)
        low_budget = int(_CTX_TOKEN_LIMIT * _CTX_LOW_WATERMARK_RATIO)

        total_token = 0

        messages_compress_candidates: List[ChatMessage] = []
        messages_keep: List[ChatMessage] = []

        for msg in reversed(messages):
            total_token += msg.token_count
            if total_token <= low_budget:
                messages_keep.insert(0, msg)  # 保留在 messages_keep
            else:
                messages_compress_candidates.insert(
                    0, msg
                )  # 超出低水位，进入 messages_compress_candidates

        # 当整体 Token 超过高水位时，触发需要压缩的标志
        needs_compression = (
            total_token >= high_budget
        )  # 由于高水位线（如 80%）预留了安全 Buffer，即便把它们全发给模型，也不会触发 Token 溢出报错

        return messages_keep, messages_compress_candidates, needs_compression

    def assemble_prompt(
        self,
        session_id: str,
        user_query: str,
        windowed_messages: List[ChatMessage],
        relevant_facts: List[str],
        session_summary: Optional[str],
        states: Optional[List[Dict[str, Any]]] = None,
        candidate_skills: Optional[List[SkillMeta]] = None,
        locale: str = "zh-CN",
    ) -> List[ChatMessage]:
        """组装最终发往 LLM 的消息列表。"""
        system_prompt = """
        # Role
        You are the official AI Assistant for the WisePen system. You are helpful, professional, and precise. 
        
        # Core Task
        Answer the user's queries accurately and comprehensively, relying strictly on the provided retrieved context.
        
        # Constraints & Guidelines
        1. Language Consistency: Prefer the language of the user's current message. If the user explicitly requests a response language in this turn, that request has the highest priority. If the user's language is ambiguous, use the user's preferred locale below.
        2. Contextual Grounding: Base your answers ONLY on the `<retrieved_context>`. Do not introduce outside information or hallucinate facts. 
        3. Handling Unknowns: If the provided context does not contain the information needed to answer the question, clearly and politely state that you do not have enough information, rather than guessing.
        4. Tone: Maintain a professional, encouraging, and clear tone suitable for users of an advanced educational and productivity tool.
        5. Formatting: Use Markdown (e.g., bullet points, bold text, code blocks) to structure your response for maximum readability.
        """  # 全局指令

        system_prompt += (
            f"\n[User Language Preference]\n"
            f"User preferred locale: {locale}.\n"
            "Prefer the language of the user's current message. "
            "If the user's language is ambiguous, use the preferred locale.\n"
        )

        # 如果有从 Mem0 召回的相关事实，作为补充信息拼接到 System Prompt 中
        if relevant_facts:
            facts_text = "\n".join([f"- {fact}" for fact in relevant_facts])
            system_prompt += f"\n[Relevant User Memories]:\n{facts_text}\n"

        messages: List[ChatMessage] = [
            ChatMessage(session_id=session_id, role=Role.SYSTEM, content=system_prompt)
        ]

        # 如果有摘要，将其注入为第二条 system 消息，位于明细上下文之前
        if session_summary:
            messages.append(
                ChatMessage(
                    session_id=session_id,
                    role=Role.SYSTEM,
                    content=f"[Conversation Summary so far]:\n{session_summary}",
                )
            )

        # Skill 候选清单：受控披露，只在 matcher 命中时注入；明确限制"仅在直接相关时加载"
        # 用 skill_id 作机器标识，description 给 LLM 做相关性判断
        if candidate_skills:
            skill_lines = [
                f'- id="{s.skill_id}": {s.description}' for s in candidate_skills
            ]
            skill_block = (
                "[Available Skills]\n"
                "The following skills MAY be relevant to the user's current request. "
                "Each skill contains detailed domain instructions (SKILL.md) and possibly supporting assets.\n"
                "Strict rules:\n"
                "1. Load a skill ONLY when it is DIRECTLY required to fulfill the current request. Do not load speculatively.\n"
                "2. To load, call the tool `load_skill` with `skill_id` exactly as listed below.\n"
                "3. After loading, follow the SKILL.md instructions precisely. "
                "Use `load_skill_asset` to open a specific reference/template only if SKILL.md explicitly says to.\n"
                "4. If none of the skills apply, simply ignore this list and answer normally.\n\n"
                "Skills:\n" + "\n".join(skill_lines)
            )
            messages.append(
                ChatMessage(
                    session_id=session_id,
                    role=Role.SYSTEM,
                    content=skill_block,
                )
            )

        # 经过滑动窗口裁剪后的近期对话明细
        # 修复可能存在的孤立 TOOL 消息（前置 ASSISTANT(tool_calls) 被裁剪或丢失的情况）
        safe_messages = _ensure_tool_message_pairing(windowed_messages)
        messages.extend(safe_messages)

        # 前端上下文注入：作为独立 SYSTEM 消息插入在历史消息和用户问题之间
        active_states = [
            s for s in (states or []) if not s.get("disabled", False) and s.get("value")
        ]
        if active_states:
            ctx_lines = [
                f'<context key="{s["key"]}">\n{s["value"]}\n</context>'
                for s in active_states
            ]
            messages.append(
                ChatMessage(
                    session_id=session_id,
                    role=Role.SYSTEM,
                    content="[Frontend Context]\n" + "\n".join(ctx_lines),
                )
            )

        # 用户最新输入的问题
        messages.append(
            ChatMessage(session_id=session_id, role=Role.USER, content=user_query)
        )
        return messages


def _ensure_tool_message_pairing(messages: List[ChatMessage]) -> List[ChatMessage]:
    """
    确保消息序列中每个 role=tool 的消息都有对应的 role=assistant 且含 tool_calls 的前置消息。
    如果发现孤立的 TOOL 消息（前置 ASSISTANT 的 tool_calls 被裁剪/丢失），则一并移除。
    这是一道防线，防止历史脏数据触发 OpenAI 协议校验错误。
    """
    valid_tool_call_ids: Set[str] = set()
    result: List[ChatMessage] = []
    for msg in messages:
        if msg.role == Role.ASSISTANT and msg.tool_calls:
            for tc in msg.tool_calls:
                tc_id = tc.get("id") if isinstance(tc, dict) else None
                if tc_id:
                    valid_tool_call_ids.add(tc_id)
            result.append(msg)
        elif msg.role == Role.TOOL:
            if msg.tool_call_id and msg.tool_call_id in valid_tool_call_ids:
                result.append(msg)
        else:
            result.append(msg)
    return result
