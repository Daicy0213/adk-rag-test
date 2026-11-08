"""检索编排：normalize → FTS + exact → RRF → 父级聚合 → 证据包。

`SparseRetriever` 是给上层（RagService / ADK 适配层 / 评估脚本）的稳定接口：
以后换 Tantivy/BM25 只替换这个类，`EvidencePack` 契约不变（计划 4.3）。

一次 retrieve 的全部 SQL 在**同一个事务**里执行：
- 保证 `domain.active_batch_id` 在本次查询内不会中途变化（批次切换不是原子的跨语句视图）；
- 便于统一设置 `statement_timeout`（计划 10.5 的 timeouts_ms.query）。
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import date
from typing import Sequence

from ..config import Settings, load_settings
from ..db import connection, transaction
from ..ingest.exact_terms import extract_exact_terms
from ..models import EvidencePack
from . import queries
from .aggregate import ParentGroup, aggregate_parents, fuse_rrf
from .exact import search_exact
from .fts import search_fts_with_fallback, tsquery_numnode
from .normalize import normalize_query, or_fallback_query
from .pack import build_pack

logger = logging.getLogger(__name__)


class PgRetriever:
    """基于 PostgreSQL FTS + exact_terms 的稀疏检索器。"""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        # 最近一次检索的追踪信息（结构化日志用；不含原文）。
        self.last_trace: dict = {}

    def retrieve(
        self,
        query: str,
        *,
        domains: Sequence[str],
        user_groups: Sequence[str],
        as_of: date | None = None,
        max_evidence_tokens: int | None = None,
    ) -> EvidencePack:
        """检索并返回证据包。空结果也是正常返回，不抛异常。"""
        config = self.settings.retrieval
        started = time.perf_counter()
        timings: dict[str, float] = {}
        domains_list = [d for d in domains if d]
        groups_list = list(dict.fromkeys(user_groups))
        as_of = as_of or date.today()
        budget = max_evidence_tokens or config.max_evidence_tokens

        normalized = normalize_query(query)
        terms = extract_exact_terms(normalized)

        trace: dict = {
            "query_hash": _hash(query),
            "domains": domains_list,
            "exact_terms": terms,
            "fts_candidates": 0,
            "exact_candidates": 0,
            "selected_parents": 0,
            "knowledge_version": "",
            "gaps": [],
        }

        if not domains_list:
            return self._empty_pack(query, normalized, ["no_domains"], trace)
        if not groups_list:
            # 空 ACL 不匹配任何文档（计划 6.4）：这是配置错误，不是"没有答案"。
            return self._empty_pack(query, normalized, ["no_acl_groups"], trace)
        if not normalized:
            return self._empty_pack(query, normalized, ["empty_query"], trace)

        with connection(self.settings) as conn:
            with transaction(conn, statement_timeout_ms=config.timeouts_ms.query):
                step = time.perf_counter()
                numnode = tsquery_numnode(conn, normalized)
                timings["tsquery"] = _ms(step)
                gaps: list[str] = []

                if numnode == 0:
                    # 全是停用词/纯标点：不要"硬查"，直接说明原因（计划 8.5）。
                    gaps.append("fts_query_empty")
                    fts_candidates = []
                    fts_trace = {"mode": "skipped", "and_candidates": 0, "or_candidates": 0}
                else:
                    step = time.perf_counter()
                    fts_candidates, fts_trace = search_fts_with_fallback(
                        conn,
                        query_text=normalized,
                        or_query=or_fallback_query(normalized),
                        min_candidates=config.or_fallback_min_candidates,
                        domains=domains_list,
                        user_groups=groups_list,
                        as_of=as_of,
                        limit=config.fts_top_k,
                        normalization=config.ts_rank_normalization,
                    )
                    timings["fts"] = _ms(step)
                    if fts_trace.get("mode") == "and+or":
                        gaps.append("or_fallback_used")

                step = time.perf_counter()
                exact_candidates = (
                    search_exact(
                        conn,
                        terms=terms,
                        domains=domains_list,
                        user_groups=groups_list,
                        as_of=as_of,
                        limit=config.exact_top_k,
                    )
                    if terms
                    else []
                )
                timings["exact"] = _ms(step)
                if terms and not exact_candidates:
                    gaps.append("exact_terms_no_match")

                step = time.perf_counter()
                fused = fuse_rrf(
                    fts_candidates,
                    exact_candidates,
                    k=config.rrf_k,
                    fts_weight=1.0,
                    exact_weight=config.exact_boost,
                )
                groups = aggregate_parents(
                    fused,
                    per_domain_parents=config.per_domain_parents,
                    final_parents=config.final_parents,
                    parent_score=config.parent_score,
                )
                timings["aggregate"] = _ms(step)

                step = time.perf_counter()
                pack, pack_trace = build_pack(
                    conn,
                    query=query,
                    rewritten_query=normalized,
                    groups=groups,
                    max_tokens=budget,
                    knowledge_version=self._knowledge_version(conn, domains_list),
                    gaps=gaps,
                )
                timings["pack"] = _ms(step)

                if not fused:
                    gaps.append("no_candidates")
                if pack.is_empty and "no_match" not in pack.gaps:
                    pack.gaps = list(dict.fromkeys([*pack.gaps, "no_match"]))

        trace.update(
            {
                "fts_candidates": len(fts_candidates),
                "fts_mode": fts_trace.get("mode"),
                "exact_candidates": len(exact_candidates),
                "fused_candidates": len(fused),
                "selected_parents": len(groups),
                "knowledge_version": pack.knowledge_version,
                "gaps": pack.gaps,
                "empty": pack.is_empty,
                "evidence_tokens": pack.token_count,
                "deduped": pack_trace.get("deduped", 0),
                "truncated": pack_trace.get("truncated", False),
            }
        )
        timings["total"] = _ms(started)
        trace["latency_ms"] = {key: round(value, 2) for key, value in timings.items()}
        self.last_trace = trace
        logger.debug("retrieval trace: %s", trace)
        return pack

    def retrieve_debug(
        self,
        query: str,
        *,
        domains: Sequence[str],
        user_groups: Sequence[str],
        as_of: date | None = None,
    ) -> dict:
        """返回中间结果（候选/融合/聚合），用于调试与消融实验，不对外承诺稳定。"""
        config = self.settings.retrieval
        normalized = normalize_query(query)
        terms = extract_exact_terms(normalized)
        as_of = as_of or date.today()

        with connection(self.settings) as conn:
            with transaction(conn, statement_timeout_ms=config.timeouts_ms.query):
                fts_candidates, fts_trace = search_fts_with_fallback(
                    conn,
                    query_text=normalized,
                    or_query=or_fallback_query(normalized),
                    min_candidates=config.or_fallback_min_candidates,
                    domains=list(domains),
                    user_groups=list(user_groups),
                    as_of=as_of,
                    limit=config.fts_top_k,
                    normalization=config.ts_rank_normalization,
                )
                exact_candidates = search_exact(
                    conn,
                    terms=terms,
                    domains=list(domains),
                    user_groups=list(user_groups),
                    as_of=as_of,
                    limit=config.exact_top_k,
                )
                fused = fuse_rrf(
                    fts_candidates,
                    exact_candidates,
                    k=config.rrf_k,
                    fts_weight=1.0,
                    exact_weight=config.exact_boost,
                )
                groups = aggregate_parents(
                    fused,
                    per_domain_parents=config.per_domain_parents,
                    final_parents=config.final_parents,
                    parent_score=config.parent_score,
                )
        return {
            "normalized_query": normalized,
            "exact_terms": terms,
            "fts_trace": fts_trace,
            "fts": fts_candidates,
            "exact": exact_candidates,
            "fused": fused,
            "parents": groups,
        }

    # ------------------------------------------------------------------ #
    def _knowledge_version(self, conn, domains: list[str]) -> str:
        rows = conn.execute(queries.ACTIVE_VERSIONS, {"domains": domains}).fetchall()
        versions = {
            row["id"]: f"{row['id']}@batch-{row['active_batch_id']}"
            for row in rows
            if row["active_batch_id"] is not None
        }
        return ",".join(versions[domain] for domain in domains if domain in versions)

    def _empty_pack(
        self, query: str, normalized: str, gaps: list[str], trace: dict
    ) -> EvidencePack:
        trace.update({"gaps": gaps, "empty": True, "latency_ms": {}})
        self.last_trace = trace
        return EvidencePack(
            query=query,
            rewritten_query=normalized,
            gaps=gaps,
            confidence="none",
        )


def _hash(value: str) -> str:
    """查询哈希：日志里只留哈希，不落原文（计划 12.4）。"""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000


def parent_ids(pack: EvidencePack) -> list[str]:
    """证据覆盖的父级 section ID，评估脚本按它对齐 gold_section_ids。"""
    return [item.parent_id for item in pack.evidence]


__all__ = ["PgRetriever", "ParentGroup", "parent_ids"]
