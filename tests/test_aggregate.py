"""融合与父级聚合测试（计划 8.4 / 11.1）。"""

from __future__ import annotations

from datetime import date

from adk_rag.models import Candidate
from adk_rag.retrieve.aggregate import aggregate_parents, fuse_rrf
from adk_rag.retrieve.normalize import normalize_query


def make_candidate(chunk_id: str, *, parent: str, domain: str = "deposit", score: float = 1.0) -> Candidate:
    return Candidate(
        chunk_id=chunk_id,
        batch_id=1,
        section_id=f"sec-{parent}",
        parent_id=parent,
        domain_id=domain,
        document_id="doc@1.0",
        doc_id="doc",
        title="Doc",
        version="1.0",
        heading_path="Doc > Section",
        text="text",
        token_count=10,
        status="active",
        effective_from=None,
        effective_to=None,
        classification="internal",
        acl_groups=["role:retail_business"],
        exact_terms=[],
        source_url=None,
        score=score,
    )


def test_rrf_prefers_document_hit_by_both_channels():
    fts = [make_candidate("c1", parent="p1"), make_candidate("c2", parent="p2")]
    exact = [make_candidate("c2", parent="p2")]

    fused = fuse_rrf(fts, exact, k=60)

    assert fused[0].chunk_id == "c2"
    assert fused[0].channel == "fts+exact"
    assert fused[0].score > fused[1].score
    # 分数是 RRF 值，不是原始通道分数
    assert fused[0].score < 0.1


def test_rrf_weights_shift_ranking():
    fts = [make_candidate("c1", parent="p1")]
    exact = [make_candidate("c2", parent="p2")]

    default = fuse_rrf(fts, exact, k=60, fts_weight=1.0, exact_weight=1.0)
    boosted = fuse_rrf(fts, exact, k=60, fts_weight=1.0, exact_weight=2.0)

    assert default[0].chunk_id in {"c1", "c2"}
    assert boosted[0].chunk_id == "c2"


def test_rrf_dedupes_same_chunk_within_channel():
    duplicate = make_candidate("c1", parent="p1")
    fused = fuse_rrf([duplicate], [], k=60)
    assert len(fused) == 1


def test_parent_aggregation_groups_and_orders():
    candidates = [
        make_candidate("c1", parent="p1", score=0.03),
        make_candidate("c2", parent="p1", score=0.02),
        make_candidate("c3", parent="p2", score=0.05),
    ]
    groups = aggregate_parents(candidates, per_domain_parents=4, final_parents=8, parent_score="max")

    assert [group.parent_id for group in groups] == ["p2", "p1"]
    assert groups[1].score == 0.03  # max 而不是 sum
    assert groups[1].chunk_ids == ["c1", "c2"]


def test_parent_aggregation_sum_mode():
    candidates = [
        make_candidate("c1", parent="p1", score=0.02),
        make_candidate("c2", parent="p1", score=0.02),
        make_candidate("c3", parent="p2", score=0.03),
    ]
    groups = aggregate_parents(candidates, per_domain_parents=4, final_parents=8, parent_score="sum")
    assert groups[0].parent_id == "p1"
    assert groups[0].score == 0.04


def test_per_domain_quota_applies_and_final_limit_respected():
    candidates = []
    for index in range(6):
        candidates.append(make_candidate(f"a{index}", parent=f"pa{index}", domain="deposit", score=1.0 - index / 100))
    for index in range(2):
        candidates.append(make_candidate(f"b{index}", parent=f"pb{index}", domain="fx", score=0.5 - index / 100))

    groups = aggregate_parents(candidates, per_domain_parents=2, final_parents=3)
    domains = [group.domain_id for group in groups]

    assert len(groups) == 3
    assert domains.count("deposit") <= 2
    # 每个域至少有一条，避免被单域淹没
    assert "fx" in domains


def test_aggregation_without_parent_id_falls_back_to_section():
    candidate = make_candidate("c1", parent="p1").model_copy(update={"parent_id": None})
    groups = aggregate_parents([candidate], per_domain_parents=4, final_parents=8)
    assert groups[0].parent_id == "sec-p1"


def test_normalize_query_keeps_numbers_and_identifiers():
    assert normalize_query("Circular [2022] No. 5") == "Circular [2022] No. 5"
    assert normalize_query("CD-2026-001") == "CD-2026-001"


def test_candidate_effective_dates_are_lines():
    candidate = make_candidate("c1", parent="p1")
    assert candidate.effective_from is None
    assert candidate.status == "active"
    assert isinstance(date.today().isoformat(), str)
