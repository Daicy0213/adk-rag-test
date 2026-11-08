"""FTS 通道：`websearch_to_tsquery` + `tsv @@ query` + `ts_rank_cd`。

AND 语义的取舍：
`websearch_to_tsquery` 把多个词连成 `&`（AND），对"一句话问句"太严格——
问句里常有一两个词在目标条款里根本不出现（如 "foreign customer"）。
所以这里先用 AND 召回；候选不足时再降级成 OR 召回一次（计划 8.5「去掉部分词」），
两条结果按"AND 在前、OR 补充在后"合并，保持通道内排名语义。
"""

from __future__ import annotations

from datetime import date

import psycopg

from ..models import Candidate
from . import queries


def tsquery_numnode(conn: psycopg.Connection, query_text: str) -> int:
    """查询解析后剩余的词条数；0 表示全是停用词/空查询，不该硬查。"""
    row = conn.execute(queries.TSQUERY_NUMNODE, {"query": query_text}).fetchone()
    return int(row["numnode"])


def search_fts(
    conn: psycopg.Connection,
    *,
    query_text: str,
    domains: list[str],
    user_groups: list[str],
    as_of: date,
    limit: int,
    normalization: int = 32,
) -> list[Candidate]:
    """返回按 `ts_rank_cd` 排序的候选 chunk。"""
    if not query_text.strip() or not domains or not user_groups:
        return []
    rows = conn.execute(
        queries.FTS_SEARCH,
        {
            "query": query_text,
            "domains": domains,
            "user_groups": user_groups,
            "as_of": as_of,
            "normalization": normalization,
            "limit": limit,
        },
    ).fetchall()
    return [Candidate(**row, channel="fts") for row in rows]


def search_fts_with_fallback(
    conn: psycopg.Connection,
    *,
    query_text: str,
    or_query: str,
    min_candidates: int,
    domains: list[str],
    user_groups: list[str],
    as_of: date,
    limit: int,
    normalization: int = 32,
) -> tuple[list[Candidate], dict]:
    """先 AND 召回，候选不足再 OR 召回补充；返回（候选, 追踪信息）。"""
    trace: dict = {"mode": "and", "and_candidates": 0, "or_candidates": 0}
    candidates = search_fts(
        conn,
        query_text=query_text,
        domains=domains,
        user_groups=user_groups,
        as_of=as_of,
        limit=limit,
        normalization=normalization,
    )
    trace["and_candidates"] = len(candidates)

    if len(candidates) >= min_candidates or not or_query:
        return candidates, trace

    extra = search_fts(
        conn,
        query_text=or_query,
        domains=domains,
        user_groups=user_groups,
        as_of=as_of,
        limit=limit,
        normalization=normalization,
    )
    trace["or_candidates"] = len(extra)
    if not extra:
        return candidates, trace

    trace["mode"] = "and+or"
    seen = {candidate.chunk_id for candidate in candidates}
    combined = list(candidates)
    for candidate in extra:
        if candidate.chunk_id in seen:
            continue
        seen.add(candidate.chunk_id)
        combined.append(candidate)
    return combined[:limit], trace
