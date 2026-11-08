"""查询归一化与 FTS 兜底词构造。

为什么**不**把查询小写：
`websearch_to_tsquery` 用大写 `OR` 表示"或"、用引号表示短语、用 `-` 表示排除。
如果统一 casefold，`early OR redemption` 会退化成普通词，语义直接丢失。
所以这里只做 NFKC + 空白归一，大小写留给 `websearch_to_tsquery` 自己处理
（它内部的 english 配置本来就会把词条小写化）。

exact_terms 抽取则统一 casefold（见 ingest/exact_terms.py），两条通道各自成立。
"""

from __future__ import annotations

import re
import unicodedata

_WS_RE = re.compile(r"\s+")
# 只取"像词"的片段构造 OR 兜底，避免把引号/括号/操作符拼进 tsquery。
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._'-]*")

# 计划 8.3：不要去掉这些否定/限定词。
NEGATION_WORDS = ("not", "no", "except", "unless", "only", "without")


def normalize_query(query: str) -> str:
    """NFKC（全角→半角）+ 压缩空白 + 去首尾空白；保留大小写与标点。"""
    if not query:
        return ""
    text = unicodedata.normalize("NFKC", query)
    return _WS_RE.sub(" ", text).strip()


def fts_terms(query: str, *, max_terms: int = 12) -> list[str]:
    """从归一化查询里取候选词，用于 AND 召回不足时的 OR 兜底。

    规则（计划 8.3「不要过度扩展」）：
    - 去重、保序、最多 12 个词；
    - 保留否定词（它们不是操作符，但删掉会改变检索意图）；
    - 不展开同义词，也不加词干（stemming 由 PG 的 english 配置做）。
    """
    seen: set[str] = set()
    terms: list[str] = []
    for match in _WORD_RE.finditer(query):
        term = match.group(0)
        # 纯符号/单字符没有区分度；"no" 这类词保留。
        if len(term) < 2 and term.lower() not in NEGATION_WORDS:
            continue
        key = term.casefold()
        if key in seen:
            continue
        seen.add(key)
        terms.append(term)
        if len(terms) >= max_terms:
            break
    return terms


def or_fallback_query(query: str, *, max_terms: int = 12) -> str:
    """构造 `a OR b OR c` 形式的兜底查询；没有可用词时返回空串。"""
    terms = fts_terms(query, max_terms=max_terms)
    return " OR ".join(terms)
