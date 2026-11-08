"""融合与父级聚合。

1. **RRF（Reciprocal Rank Fusion）**：两条通道的原始分数尺度不同——
   `ts_rank_cd` 是 cover density 分数，exact 通道是"命中词数"。
   直接相加会让长尾分数压过另一条通道，所以只使用**排名**：
       score(d) = Σ_channel w_channel / (k + rank_channel(d))
   k=60 是 RRF 的常用默认值，exact 权重可用 `exact_boost` 单独调高。

2. **父级聚合**：child chunk 用来检索，但证据要按父级 section 返回，
   否则列举题会只答一半。分组键是 `parent_id`（最近的 H2 祖先），
   组分数默认取组内最强子块（`max`），可选 `sum`。
   跨域查询按"每域配额"截断，避免某个域淹没其他域（计划 8.7）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Candidate


@dataclass
class ParentGroup:
    """一个父级 section 的聚合结果。"""

    parent_id: str
    domain_id: str
    score: float
    candidates: list[Candidate] = field(default_factory=list)

    @property
    def best(self) -> Candidate:
        return max(self.candidates, key=lambda candidate: candidate.score)

    @property
    def section_ids(self) -> list[str]:
        return sorted({candidate.section_id for candidate in self.candidates})

    @property
    def chunk_ids(self) -> list[str]:
        return [candidate.chunk_id for candidate in self.candidates]

    @property
    def channels(self) -> set[str]:
        channels: set[str] = set()
        for candidate in self.candidates:
            channels.update(candidate.channel.split("+"))
        return channels


def fuse_rrf(
    fts_candidates: list[Candidate],
    exact_candidates: list[Candidate],
    *,
    k: int = 60,
    fts_weight: float = 1.0,
    exact_weight: float = 1.0,
) -> list[Candidate]:
    """按排名融合两路候选；返回按融合分数降序的候选（score 已替换为 RRF 分数）。"""
    ranks: dict[str, dict[str, float]] = {}
    by_id: dict[str, Candidate] = {}

    for channel, candidates, weight in (
        ("fts", fts_candidates, fts_weight),
        ("exact", exact_candidates, exact_weight),
    ):
        for rank, candidate in enumerate(candidates, start=1):
            if weight <= 0:
                continue
            entry = ranks.setdefault(candidate.chunk_id, {})
            # 同一 chunk 在同一通道只可能出现一次；防御性保留更靠前的排名。
            entry[channel] = max(entry.get(channel, 0.0), weight / (k + rank))
            by_id.setdefault(candidate.chunk_id, candidate)

    fused: list[Candidate] = []
    for chunk_id, contributions in ranks.items():
        candidate = by_id[chunk_id].model_copy(deep=True)
        candidate.score = round(sum(contributions.values()), 8)
        channels = [name for name in ("fts", "exact") if name in contributions]
        candidate.channel = "+".join(channels)
        fused.append(candidate)

    fused.sort(key=lambda candidate: (-candidate.score, candidate.chunk_id))
    return fused


def aggregate_parents(
    candidates: list[Candidate],
    *,
    per_domain_parents: int,
    final_parents: int,
    parent_score: str = "max",
) -> list[ParentGroup]:
    """按 (domain, parent) 聚合，再按每域配额与总数截断。"""
    groups: dict[tuple[str, str], ParentGroup] = {}
    for candidate in candidates:
        parent_id = candidate.parent_id or candidate.section_id
        key = (candidate.domain_id, parent_id)
        group = groups.get(key)
        if group is None:
            group = ParentGroup(parent_id=parent_id, domain_id=candidate.domain_id, score=0.0)
            groups[key] = group
        group.candidates.append(candidate)
        if parent_score == "sum":
            group.score += candidate.score
        else:
            group.score = max(group.score, candidate.score)

    # 每域先各自排序取配额，保证跨域查询不会被单域淹没。
    by_domain: dict[str, list[ParentGroup]] = {}
    for group in groups.values():
        by_domain.setdefault(group.domain_id, []).append(group)

    selected: list[ParentGroup] = []
    for domain_groups in by_domain.values():
        domain_groups.sort(key=lambda group: (-group.score, group.parent_id))
        selected.extend(domain_groups[:per_domain_parents])

    selected.sort(key=lambda group: (-group.score, group.parent_id))
    return selected[:final_parents]
