"""RagService：检索门面。

为什么检索核心是同步的、这里才提供 async：
- psycopg 同步连接最简单，评估脚本和 pytest 不需要事件循环；
- ADK 的回调是 async，用 `asyncio.to_thread` 把同步检索丢到线程池即可，
  不会阻塞事件循环，也不需要为了 RAG 引入第二套 async 数据库访问。

`SparseRetriever` 是保留给 B/C 方案的接口形状：换索引实现时只替换 retriever，
`EvidencePack` 契约不变。
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Protocol, Sequence, runtime_checkable

from .config import Settings, load_settings
from .models import EvidencePack
from .retrieve.retriever import PgRetriever


@runtime_checkable
class SparseRetriever(Protocol):
    """检索器契约（计划 12.6）。"""

    async def retrieve(
        self,
        query: str,
        *,
        domains: list[str],
        user_groups: list[str],
        as_of: date,
        top_k: int = 80,
    ) -> EvidencePack: ...


class RagService:
    """检索门面：同步路径给评估/测试，异步路径给 ADK。"""

    def __init__(self, settings: Settings | None = None, retriever: PgRetriever | None = None) -> None:
        self.settings = settings or load_settings()
        self.retriever = retriever or PgRetriever(self.settings)

    def retrieve_sync(
        self,
        query: str,
        *,
        domains: Sequence[str],
        user_groups: Sequence[str],
        as_of: date | None = None,
        max_evidence_tokens: int | None = None,
    ) -> EvidencePack:
        return self.retriever.retrieve(
            query,
            domains=domains,
            user_groups=user_groups,
            as_of=as_of,
            max_evidence_tokens=max_evidence_tokens,
        )

    async def retrieve(
        self,
        query: str,
        *,
        domains: Sequence[str],
        user_groups: Sequence[str],
        as_of: date | None = None,
        max_evidence_tokens: int | None = None,
    ) -> EvidencePack:
        """异步检索：ADK 回调里直接 await，不阻塞事件循环。"""
        return await asyncio.to_thread(
            self.retrieve_sync,
            query,
            domains=domains,
            user_groups=user_groups,
            as_of=as_of,
            max_evidence_tokens=max_evidence_tokens,
        )

    @property
    def last_trace(self) -> dict:
        return self.retriever.last_trace
