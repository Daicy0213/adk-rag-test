"""评估指标与逐题打分测试。

评估代码本身出错比检索出错更隐蔽，所以这里的断言都用可手算的例子。
"""

from __future__ import annotations

from datetime import date

from adk_rag.eval.metrics import mean, ndcg_at_k, percentile, recall_at_k
from adk_rag.eval.runner import score_case
from adk_rag.eval.golden import GoldenCase
from adk_rag.models import Evidence, EvidencePack


def test_recall_at_k_basic():
    assert recall_at_k(["a", "b", "c"], ["b"], k=10) == 1.0
    assert recall_at_k(["a", "b", "c"], ["b", "z"], k=10) == 0.5
    assert recall_at_k(["a"], ["z"], k=10) == 0.0
    assert recall_at_k(["a"], [], k=10) is None


def test_recall_respects_cutoff():
    assert recall_at_k(["x", "y", "gold"], ["gold"], k=2) == 0.0
    assert recall_at_k(["x", "gold"], ["gold"], k=2) == 1.0


def test_ndcg_perfect_ranking_is_one():
    assert ndcg_at_k(["gold1", "gold2"], ["gold1", "gold2"], k=10) == 1.0


def test_ndcg_discounts_lower_positions():
    first = ndcg_at_k(["gold1"], ["gold1"], k=10)
    second = ndcg_at_k(["other", "gold1"], ["gold1"], k=10)
    assert first == 1.0
    assert 0 < second < first


def test_ndcg_ignores_duplicate_hits():
    once = ndcg_at_k(["gold1"], ["gold1"], k=10)
    twice = ndcg_at_k(["gold1", "gold1"], ["gold1"], k=10)
    assert once == twice == 1.0


def test_ndcg_with_no_gold_returns_none():
    assert ndcg_at_k(["a"], [], k=10) is None


def test_percentile():
    values = [10.0, 20.0, 30.0, 40.0]
    assert percentile(values, 50) == 25.0
    assert percentile(values, 100) == 40.0
    assert percentile(values, 0) == 10.0
    assert percentile([], 95) is None
    assert percentile([7.0], 95) == 7.0


def test_mean_skips_none():
    assert mean([1.0, None, 3.0]) == 2.0
    assert mean([None]) is None


def make_evidence(index: int, section_ids: list[str], parent: str, text: str) -> Evidence:
    return Evidence(
        evidence_id=f"E{index}",
        chunk_id=f"chunk-{index}",
        parent_id=parent,
        domain_id="deposit",
        doc_id="doc",
        title="Doc",
        version="1.0",
        heading_path="Doc > Section",
        text=text,
        score=0.01,
        section_ids=section_ids,
    )


def test_score_case_fact_hit():
    pack = EvidencePack(
        query="q",
        rewritten_query="q",
        evidence=[make_evidence(1, ["sec-1", "sec-2"], "sec-1", "The minimum is USD 100,000.")],
        citation_ids=["E1"],
        knowledge_version="deposit@batch-1",
        token_count=20,
        confidence="high",
    )
    case = GoldenCase(
        id="t1",
        type="fact",
        query="q",
        gold_section_ids=["sec-2"],
        must_include=["USD 100,000"],
    )

    result = score_case(case, pack, latency_ms=12.5, trace={}, section_parent_map={"sec-2": "sec-1"})

    assert result.recall_at_10 == 1.0
    assert result.ndcg_at_10 == 1.0  # 父级就是第一份证据
    assert 0 < result.ndcg_at_10_sections < 1  # 展开到 section 视角被稀释
    assert result.must_include_coverage == 1.0
    assert result.forbidden_hit == []
    assert result.empty is False


def test_score_case_exact_and_forbidden():
    pack = EvidencePack(
        query="q",
        rewritten_query="q",
        evidence=[make_evidence(1, ["sec-1"], "sec-1", "Circular [2022] No. 5 applies.")],
        confidence="high",
    )
    case = GoldenCase(
        id="t2",
        type="exact",
        query="q",
        gold_section_ids=["sec-9"],
        must_include=["Circular [2022] No. 5"],
        must_not_include=["Circular [2019] No. 1"],
    )
    result = score_case(case, pack, latency_ms=5.0, trace={})

    assert result.exact_hit is True
    assert result.recall_at_10 == 0.0
    assert result.forbidden_hit == []


def test_score_case_empty_pack():
    pack = EvidencePack(query="q", rewritten_query="q", gaps=["no_match"], confidence="none")
    case = GoldenCase(id="t3", type="unanswerable", query="q")
    result = score_case(case, pack, latency_ms=1.0, trace={})

    assert result.empty is True
    assert result.recall_at_10 is None
    assert result.must_include_coverage is None


def test_case_result_serializes_dates():
    case = GoldenCase(id="t4", type="fact", query="q", as_of="2026-11-08")
    assert case.as_of == date(2026, 11, 8)
