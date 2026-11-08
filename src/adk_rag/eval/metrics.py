"""评估指标：Recall@k、nDCG@k、百分位。

都很小，单独成模块是为了能被单测直接覆盖——**评估代码本身出错比检索出错更隐蔽**。
"""

from __future__ import annotations

import math


def recall_at_k(retrieved: list[str], gold: list[str], k: int = 10) -> float | None:
    """|gold ∩ top-k| / |gold|；gold 为空时返回 None（不参与平均）。"""
    if not gold:
        return None
    top = set(retrieved[:k])
    hits = sum(1 for item in set(gold) if item in top)
    return hits / len(set(gold))


def ndcg_at_k(retrieved: list[str], gold: list[str], k: int = 10) -> float | None:
    """二值相关性 nDCG@k。

    同一 gold 命中多次只计一次（检索结果里父/子 section 可能重复出现），
    否则长证据包会被"重复命中"刷分。
    """
    if not gold:
        return None
    gold_set = set(gold)
    seen: set[str] = set()
    dcg = 0.0
    for rank, section_id in enumerate(retrieved[:k], start=1):
        if section_id in gold_set and section_id not in seen:
            seen.add(section_id)
            dcg += 1.0 / math.log2(rank + 1)

    ideal_hits = min(len(gold_set), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def percentile(values: list[float], q: float) -> float | None:
    """线性插值百分位（q 取 0–100）；空列表返回 None。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (q / 100)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def mean(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None
