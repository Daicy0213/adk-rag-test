"""exact 通道：`exact_terms && ARRAY[...]`（GIN）+ 命中词数排序。

存在意义（计划 6.2）：文号/产品代码/金额/日期一旦进入 tsvector 就会被
tokenize/stemming 改写，FTS 无法保证"精确命中"。这条通道不做分词，
查询侧与 ingest 侧共用同一套抽取规则（ingest/exact_terms.py）。
"""

from __future__ import annotations

from datetime import date

import psycopg

from ..models import Candidate
from . import queries


def search_exact(
    conn: psycopg.Connection,
    *,
    terms: list[str],
    domains: list[str],
    user_groups: list[str],
    as_of: date,
    limit: int,
) -> list[Candidate]:
    """按精确词数组重叠召回；没有精确词时直接返回空。"""
    if not terms or not domains or not user_groups:
        return []
    rows = conn.execute(
        queries.EXACT_SEARCH,
        {
            "terms": terms,
            "domains": domains,
            "user_groups": user_groups,
            "as_of": as_of,
            "limit": limit,
        },
    ).fetchall()
    return [Candidate(**row, channel="exact") for row in rows]
