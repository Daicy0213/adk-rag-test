"""上下文装配：把父级聚合结果变成 4–8k tokens 的证据包。

为什么证据文本用"父 section 的拼装文本"而不是命中的 child chunk：
child 块只有 250–400 tokens，列举题/材料清单经常跨块；父级 section（H2 及其子节）
通常能覆盖完整条款。命中信息（chunk_id / score）仍然保留，方便审计"是哪一块命中的"。

预算控制（计划 1.3、8.2）：
- 总预算 `max_evidence_tokens`；
- 单条被截断时记录 gap，不静默丢内容；
- 按分数顺序装填，装不下就停，不为了凑数塞低分证据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import psycopg

from ..models import Evidence, EvidencePack
from ..tokens import estimate_tokens, truncate_to_tokens
from .aggregate import ParentGroup
from .queries import SECTION_CONTEXT

# 每条证据的固定开销（标题/章节/来源标签）估算，避免预算刚好卡满。
EVIDENCE_OVERHEAD_TOKENS = 40
# 剩余预算低于这个值就不再塞新证据。
MIN_EVIDENCE_TOKENS = 120


@dataclass
class ParentContext:
    parent_id: str
    domain_id: str
    heading_path: str
    text: str
    section_ids: list[str]
    batch_id: int


def build_pack(
    conn: psycopg.Connection,
    *,
    query: str,
    rewritten_query: str,
    groups: list[ParentGroup],
    max_tokens: int,
    knowledge_version: str,
    gaps: Iterable[str] = (),
) -> tuple[EvidencePack, dict]:
    """返回（证据包, 追踪信息）。"""
    trace: dict = {
        "parent_groups": len(groups),
        "deduped": 0,
        "truncated": False,
        "evidence_tokens": 0,
    }
    gap_list = list(dict.fromkeys(gaps))

    if not groups:
        return (
            EvidencePack(
                query=query,
                rewritten_query=rewritten_query,
                knowledge_version=knowledge_version,
                gaps=gap_list or ["no_match"],
                confidence="none",
            ),
            trace,
        )

    contexts = _load_contexts(conn, groups)
    evidence: list[Evidence] = []
    seen_texts: set[str] = set()
    used = 0

    for group in groups:
        context = contexts.get(group.parent_id)
        if context is None:
            # section 行缺失（理论上不该发生）：退回 child chunk 文本，保证不丢证据。
            best = group.best
            context = ParentContext(
                parent_id=group.parent_id,
                domain_id=group.domain_id,
                heading_path=best.heading_path,
                text=best.text,
                section_ids=[best.section_id],
                batch_id=best.batch_id,
            )
            gap_list.append("parent_section_missing")

        fingerprint = " ".join(context.text.split()).casefold()
        if not fingerprint or fingerprint in seen_texts:
            trace["deduped"] += 1
            continue
        seen_texts.add(fingerprint)

        best = group.best
        remaining = max_tokens - used - EVIDENCE_OVERHEAD_TOKENS
        if remaining < MIN_EVIDENCE_TOKENS:
            gap_list.append("evidence_budget_exhausted")
            break

        text = context.text
        if estimate_tokens(text) > remaining:
            text = truncate_to_tokens(text, remaining)
            trace["truncated"] = True
            gap_list.append("evidence_truncated")

        token_count = estimate_tokens(text)
        used += token_count + EVIDENCE_OVERHEAD_TOKENS
        evidence.append(
            Evidence(
                evidence_id=f"E{len(evidence) + 1}",
                chunk_id=best.chunk_id,
                parent_id=group.parent_id,
                domain_id=group.domain_id,
                doc_id=best.doc_id,
                title=best.title,
                version=best.version,
                heading_path=context.heading_path or best.heading_path,
                text=text,
                source_url=best.source_url,
                score=round(group.score, 6),
                section_ids=context.section_ids or [best.section_id],
            )
        )

    trace["evidence_tokens"] = used

    if not evidence:
        gap_list.append("no_match")
        return (
            EvidencePack(
                query=query,
                rewritten_query=rewritten_query,
                knowledge_version=knowledge_version,
                gaps=list(dict.fromkeys(gap_list)),
                confidence="none",
            ),
            trace,
        )

    pack = EvidencePack(
        query=query,
        rewritten_query=rewritten_query,
        evidence=evidence,
        citation_ids=[item.evidence_id for item in evidence],
        knowledge_version=knowledge_version,
        token_count=used,
        gaps=list(dict.fromkeys(gap_list)),
        confidence=_confidence(groups[: len(evidence)], gap_list),
    )
    return pack, trace


def _load_contexts(
    conn: psycopg.Connection, groups: list[ParentGroup]
) -> dict[str, ParentContext]:
    """按 (batch_id) 批量取父 section 与其子 section 的文本。"""
    by_batch: dict[int, set[str]] = {}
    for group in groups:
        batch_id = group.best.batch_id
        by_batch.setdefault(batch_id, set()).add(group.parent_id)

    rows_by_parent: dict[str, list[dict]] = {}
    for batch_id, parent_ids in by_batch.items():
        rows = conn.execute(
            SECTION_CONTEXT, {"batch_id": batch_id, "ids": sorted(parent_ids)}
        ).fetchall()
        for row in rows:
            owner = row["id"] if row["id"] in parent_ids else row["parent_id"]
            rows_by_parent.setdefault(owner, []).append(dict(row))

    contexts: dict[str, ParentContext] = {}
    for group in groups:
        rows = rows_by_parent.get(group.parent_id)
        if not rows:
            continue
        rows.sort(key=lambda row: row["ordinal"])
        parent_row = next((row for row in rows if row["id"] == group.parent_id), rows[0])
        text = "\n\n".join(row["text"].strip() for row in rows if row["text"].strip())
        contexts[group.parent_id] = ParentContext(
            parent_id=group.parent_id,
            domain_id=group.domain_id,
            heading_path=parent_row["heading_path"],
            text=text,
            section_ids=[row["id"] for row in rows],
            batch_id=parent_row["batch_id"],
        )
    return contexts


def _confidence(groups: list[ParentGroup], gaps: list[str]) -> str:
    """启发式置信度，只用于给调用方一个"要不要弃答"的信号。

    只看证据形态，不依赖 RRF 的具体数值（它会随 k/权重变化）：
    - high：exact 命中，或纯 AND 的 FTS 命中（词全部对上，通常就是目标条款）；
    - medium：靠 OR 兜底才召回的 FTS 结果（可能只是部分词相关）；
    - low：只有弱证据（例如只剩 exact 的边角命中）。
    """
    if not groups:
        return "none"
    top = groups[0]
    if "exact" in top.channels:
        return "high"
    if "fts" in top.channels:
        return "medium" if "or_fallback_used" in gaps else "high"
    return "low"
