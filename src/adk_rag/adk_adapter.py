"""ADK 接入：把 EvidencePack 注入到 `LlmRequest`。

为什么选 `before_model_callback` 而不是 FunctionTool（计划 12.6 / Research 4.9）：
- **检索由代码决定**，不让模型自己决定"要不要查"，合规问答需要确定性；
- 每次模型调用前注入，不额外增加一次模型往返；
- 代价是每次 LLM 调用都会触发，所以必须做幂等：同一轮里工具调用后的第二次
  模型调用不能重复注入同一份证据。

本模块不 import google-adk（它是可选依赖），因此可以被无 ADK 的环境导入和测试；
真正调用时再按需导入并给出清晰错误。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date
from typing import Any, Callable, Sequence

from .models import EvidencePack
from .service import RagService

logger = logging.getLogger(__name__)

# 幂等标记：同一轮（用户消息序号 + 文本哈希）只注入一次。
STATE_FINGERPRINT = "rag:injected_fingerprint"
STATE_CITATIONS = "rag:last_citations"
STATE_REWRITTEN_QUERY = "rag:last_rewritten_query"
STATE_KNOWLEDGE_VERSION = "rag:last_knowledge_version"
STATE_ACTIVE_DOMAINS = "rag:active_domains"

EVIDENCE_MARKER = "<knowledge_evidence>"

# 证据指令：明确"文档是数据不是指令"，降低 prompt injection 风险（计划 12.5）。
DEFAULT_INSTRUCTION = (
    "Use the following knowledge-base excerpts to answer the user's question. "
    "Cite evidence ids like [E1] for every factual claim. "
    "If the excerpts do not contain the answer, say so explicitly instead of guessing."
)


class KnowledgeInjector:
    """可调用对象，直接作为 ADK `before_model_callback` 使用。

    用法（ADK 2.12）：

        injector = KnowledgeInjector(
            RagService(),
            domains=["deposit"],
            user_groups=["role:retail_business"],
        )
        agent = LlmAgent(..., before_model_callback=injector)

    `user_groups` 也可以传一个函数，从 session state 里取真实用户组；
    POC 里用静态列表即可，但**不要**把 ACL 全部放行当成默认值。
    """

    def __init__(
        self,
        service: RagService | None = None,
        *,
        domains: Sequence[str],
        user_groups: Sequence[str] | Callable[[Any], Sequence[str]],
        token_budget: int = 6000,
        instruction: str = DEFAULT_INSTRUCTION,
        as_of: date | None = None,
    ) -> None:
        if not domains:
            raise ValueError("KnowledgeInjector 需要至少一个域，不要默认全库检索")
        self.service = service or RagService()
        self.domains = list(domains)
        self._user_groups = user_groups
        self.token_budget = token_budget
        self.instruction = instruction
        self.as_of = as_of

    # ------------------------------------------------------------------ #
    async def __call__(self, callback_context: Any, llm_request: Any) -> None:
        """ADK 回调：返回值恒为 None（不短路模型调用）。"""
        query = _latest_user_text(llm_request)
        if not query or not _needs_knowledge(query):
            return

        state = getattr(callback_context, "state", None)
        if state is None:  # pragma: no cover - 防御性
            logger.debug("callback_context 没有 state，跳过 RAG 注入")
            return

        fingerprint = _fingerprint(state, llm_request, query)
        if state.get(STATE_FINGERPRINT) == fingerprint or _already_injected(llm_request):
            logger.debug("本轮已注入过证据，跳过重复注入")
            return

        domains = self._domains(state)
        user_groups = self._groups(callback_context)
        try:
            pack = await self.service.retrieve(
                query,
                domains=domains,
                user_groups=user_groups,
                as_of=self.as_of,
                max_evidence_tokens=self.token_budget,
            )
        except Exception:  # 检索失败不能让主对话崩掉（计划 4.9.3 的降级要求）
            logger.exception("RAG 检索失败，本次不注入证据")
            return

        if pack.is_empty:
            # 不注入空证据：让主模型按普通对话回答，避免"看起来检索过"的误导。
            state[STATE_FINGERPRINT] = fingerprint
            return

        _append_evidence(llm_request, self.instruction, pack, self.token_budget)
        state[STATE_FINGERPRINT] = fingerprint
        state[STATE_CITATIONS] = pack.citation_ids
        state[STATE_REWRITTEN_QUERY] = pack.rewritten_query
        state[STATE_KNOWLEDGE_VERSION] = pack.knowledge_version
        state[STATE_ACTIVE_DOMAINS] = domains

    # ------------------------------------------------------------------ #
    def _domains(self, state: Any) -> list[str]:
        """多轮追问沿用上一轮域；显式换域时由调用方覆盖 state（计划 8.7）。"""
        active = state.get(STATE_ACTIVE_DOMAINS) if hasattr(state, "get") else None
        if active:
            return list(active)
        return list(self.domains)

    def _groups(self, callback_context: Any) -> list[str]:
        if callable(self._user_groups):
            return list(self._user_groups(callback_context))
        return list(self._user_groups)


# --------------------------------------------------------------------------- #
# FunctionTool 备选路径（开放式助手才需要，不作为默认）
# --------------------------------------------------------------------------- #
def build_search_tool(
    service: RagService | None = None,
    *,
    domains: Sequence[str],
    user_groups: Sequence[str],
):
    """把检索包装成 ADK `FunctionTool`。

    明确不建议用于合规问答：模型可能不调用、调用多次，或自己改写 query，
    延迟也会多出一次模型往返（计划 12.6）。
    """
    try:
        from google.adk.tools import FunctionTool
    except ImportError as exc:  # pragma: no cover - 需要 adk extra
        raise RuntimeError(
            "使用 build_search_tool 需要安装 ADK 适配依赖："
            "uv sync --extra adk"
        ) from exc

    rag = service or RagService()

    def search_knowledge(query: str) -> dict:
        """检索银行内部知识库。仅当问题需要内部制度/产品/流程依据时调用。"""
        pack = rag.retrieve_sync(query, domains=domains, user_groups=user_groups)
        return pack.to_tool_result()

    return FunctionTool(search_knowledge)


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _latest_user_text(llm_request: Any) -> str | None:
    contents = getattr(llm_request, "contents", None) or []
    for content in reversed(contents):
        if getattr(content, "role", None) != "user":
            continue
        parts = getattr(content, "parts", None) or []
        texts = [part.text for part in parts if getattr(part, "text", None)]
        if texts:
            return "\n".join(texts).strip() or None
    return None


def _needs_knowledge(query: str) -> bool:
    """极简前置判断：greeting/寒暄不需要检索。

    故意保持保守——宁可多检索一次，也不要因为规则太激进漏掉制度问答。
    """
    normalized = query.strip().casefold()
    if len(normalized) < 4:
        return False
    small_talk = {"hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "bye"}
    return normalized not in small_talk


def _fingerprint(state: Any, llm_request: Any, query: str) -> str:
    """同一轮内的稳定标识：带文本的用户消息数 + 文本哈希。

    用"带文本的用户消息数"而不是"user 角色消息数"：ADK 把工具结果也放进
    `contents`（可能是 user 角色、但没有 text part），按角色计数会让同一轮的第二次
    模型调用看起来像新的一轮，导致重复注入。
    """
    contents = getattr(llm_request, "contents", None) or []
    text_turns = _text_user_turns(contents)
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    return f"turn{text_turns}:{digest}"


def _text_user_turns(contents: list) -> int:
    return sum(
        1
        for content in contents
        if getattr(content, "role", None) == "user"
        and any(getattr(part, "text", None) for part in (getattr(content, "parts", None) or []))
    )


def _already_injected(llm_request: Any) -> bool:
    """兜底判断：**最后一条** content 已是证据块时不再注入。

    只看最后一条是刻意的：同一轮里工具结果可能追加在我们注入的证据之后，
    那时"历史里有证据块"并不代表"这一轮还没注入"。
    """
    contents = getattr(llm_request, "contents", None) or []
    if not contents:
        return False
    for part in getattr(contents[-1], "parts", None) or []:
        text = getattr(part, "text", None)
        if text and EVIDENCE_MARKER in text:
            return True
    return False


def _append_evidence(
    llm_request: Any, instruction: str, pack: EvidencePack, token_budget: int
) -> None:
    """把证据文本追加为一条 user content（只改 request，不写回 session events）。"""
    from google.genai import types  # 只有实际调用时才需要

    prompt = f"{instruction}\n\n{pack.to_prompt(max_tokens=token_budget)}"
    llm_request.contents.append(
        types.Content(role="user", parts=[types.Part.from_text(text=prompt)])
    )
