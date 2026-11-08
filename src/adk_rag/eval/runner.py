"""评估执行：逐题检索 → 计算指标 → 汇总报告。

目标值来自计划 11.4；报告会明确标出"未达标项"，避免看到数字好看就以为过了验收。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from typing import Sequence

from pydantic import BaseModel, Field

from ..config import Settings
from ..models import EvidencePack
from ..retrieve.retriever import PgRetriever
from .golden import GoldenCase
from .metrics import mean, ndcg_at_k, percentile, recall_at_k

# 计划 11.4 的 POC 目标值；报告里逐项对比。
TARGETS = {
    "recall_at_10": 0.85,
    "ndcg_at_10": 0.75,
    "exact_hit": 0.95,
    "must_include_coverage": 0.85,
    "p95_latency_ms": 200.0,
    "empty_rate": 0.10,  # 上限
}

_WS_RE = re.compile(r"\s+")


class CaseResult(BaseModel):
    id: str
    type: str
    query: str
    recall_at_10: float | None = None
    # 主指标：以检索单元（父级 section）计算排序质量
    ndcg_at_10: float | None = None
    # 参考指标：把父级展开成 section 序列后的更严格视角（同一父级内的兄弟节会互相稀释）
    ndcg_at_10_sections: float | None = None
    exact_hit: bool | None = None
    must_include_coverage: float | None = None
    forbidden_hit: list[str] = Field(default_factory=list)
    empty: bool = False
    confidence: str = "none"
    gaps: list[str] = Field(default_factory=list)
    latency_ms: float = 0.0
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_sections: list[str] = Field(default_factory=list)
    knowledge_version: str = ""


class EvalReport(BaseModel):
    generated_at: datetime
    golden_path: str
    domains: list[str]
    case_count: int
    metrics: dict
    targets: dict = Field(default_factory=lambda: dict(TARGETS))
    failures: list[str] = Field(default_factory=list)
    cases: list[CaseResult] = Field(default_factory=list)


def evaluate(
    cases: Sequence[GoldenCase],
    *,
    settings: Settings | None = None,
    retriever: PgRetriever | None = None,
    default_domains: Sequence[str] = (),
    golden_path: str = "",
    section_parent_map: dict[str, str] | None = None,
) -> EvalReport:
    """逐题检索并汇总指标。

    `section_parent_map`：gold section → 父级 section 的映射（chunk.parent_id）。
    提供时 nDCG 以"父级 section"为单位计算，与检索实际返回的单元一致；
    否则退化为按展开 section 序列计算。
    """
    engine = retriever or PgRetriever(settings)
    results: list[CaseResult] = []

    for case in cases:
        domains = list(case.gold_domains or default_domains)
        started = time.perf_counter()
        pack = engine.retrieve(
            case.query,
            domains=domains,
            user_groups=case.user_groups,
            as_of=case.as_of,
        )
        latency_ms = (time.perf_counter() - started) * 1000
        results.append(
            score_case(case, pack, latency_ms, engine.last_trace, section_parent_map)
        )

    metrics, failures = _aggregate(results)
    return EvalReport(
        generated_at=datetime.now().astimezone(),
        golden_path=golden_path,
        domains=list(default_domains),
        case_count=len(results),
        metrics=metrics,
        failures=failures,
        cases=results,
    )


def score_case(
    case: GoldenCase,
    pack: EvidencePack,
    latency_ms: float,
    trace: dict,
    section_parent_map: dict[str, str] | None = None,
) -> CaseResult:
    sections = _evidence_sections(pack)
    text = _normalized_text(pack)
    must_include = [item for item in case.must_include if item]
    coverage = (
        sum(1 for item in must_include if _contains(text, item)) / len(must_include)
        if must_include
        else None
    )
    forbidden = [item for item in case.must_not_include if _contains(text, item)]

    exact_hit: bool | None = None
    if case.type == "exact" and must_include:
        exact_hit = all(_contains(text, item) for item in must_include)

    retrieved_parents = [item.parent_id for item in pack.evidence]
    gold_parents: list[str] = []
    if section_parent_map is not None:
        for section_id in case.gold_section_ids:
            parent = section_parent_map.get(section_id, section_id)
            if parent not in gold_parents:
                gold_parents.append(parent)

    ndcg_primary = ndcg_at_k(retrieved_parents, gold_parents) if gold_parents else None
    ndcg_sections = ndcg_at_k(sections, case.gold_section_ids, k=10)

    return CaseResult(
        id=case.id,
        type=case.type,
        query=case.query,
        recall_at_10=recall_at_k(sections, case.gold_section_ids, k=10),
        ndcg_at_10=ndcg_primary if gold_parents else ndcg_sections,
        ndcg_at_10_sections=ndcg_sections,
        exact_hit=exact_hit,
        must_include_coverage=coverage,
        forbidden_hit=forbidden,
        empty=pack.is_empty,
        confidence=pack.confidence,
        gaps=pack.gaps,
        latency_ms=round(latency_ms, 2),
        evidence_ids=[item.evidence_id for item in pack.evidence],
        evidence_sections=sections,
        knowledge_version=pack.knowledge_version or (trace.get("knowledge_version") or ""),
    )


def _evidence_sections(pack: EvidencePack) -> list[str]:
    """按证据顺序展开 section 序列（去重保序），作为 Recall/nDCG 的检索结果。"""
    ordered: list[str] = []
    seen: set[str] = set()
    for evidence in pack.evidence:
        for section_id in evidence.section_ids or [evidence.parent_id]:
            if section_id and section_id not in seen:
                seen.add(section_id)
                ordered.append(section_id)
    return ordered


def _normalized_text(pack: EvidencePack) -> str:
    return _WS_RE.sub(" ", " ".join(item.text for item in pack.evidence)).casefold()


def _contains(haystack: str, needle: str) -> bool:
    return _WS_RE.sub(" ", needle).strip().casefold() in haystack


def _aggregate(results: list[CaseResult]) -> tuple[dict, list[str]]:
    answerable = [result for result in results if result.type != "unanswerable"]
    unanswerable = [result for result in results if result.type == "unanswerable"]
    exact_cases = [result for result in results if result.exact_hit is not None]
    coverage_cases = [
        result for result in results if result.must_include_coverage is not None
    ]

    latencies = [result.latency_ms for result in results]
    metrics: dict = {
        "cases": len(results),
        "answerable_cases": len(answerable),
        "unanswerable_cases": len(unanswerable),
        "recall_at_10": _round(mean([r.recall_at_10 for r in answerable])),
        "ndcg_at_10": _round(mean([r.ndcg_at_10 for r in answerable])),
        "ndcg_at_10_sections": _round(mean([r.ndcg_at_10_sections for r in answerable])),
        "exact_hit": _round(mean([1.0 if r.exact_hit else 0.0 for r in exact_cases])),
        "exact_cases": len(exact_cases),
        "must_include_coverage": _round(
            mean([r.must_include_coverage for r in coverage_cases])
        ),
        "coverage_cases": len(coverage_cases),
        "empty_rate": _round(
            sum(1 for r in answerable if r.empty) / len(answerable) if answerable else None
        ),
        "unanswerable_empty_rate": _round(
            sum(1 for r in unanswerable if r.empty) / len(unanswerable)
            if unanswerable
            else None
        ),
        # 不可答题的正确行为是"空召回或低置信度"（低置信度由回答层弃答），
        # 而不是给出一个具体数值。见 README 4.4 的 OR 兜底取舍。
        "unanswerable_pass_rate": _round(
            sum(
                1
                for r in unanswerable
                if (r.empty or r.confidence == "low") and not r.forbidden_hit
            )
            / len(unanswerable)
            if unanswerable
            else None
        ),
        "forbidden_hits": sum(len(r.forbidden_hit) for r in results),
        "p50_latency_ms": _round(percentile(latencies, 50)),
        "p95_latency_ms": _round(percentile(latencies, 95)),
    }

    failures: list[str] = []
    if metrics["recall_at_10"] is not None and metrics["recall_at_10"] < TARGETS["recall_at_10"]:
        failures.append(
            f"Recall@10 {metrics['recall_at_10']} < 目标 {TARGETS['recall_at_10']}"
        )
    if metrics["ndcg_at_10"] is not None and metrics["ndcg_at_10"] < TARGETS["ndcg_at_10"]:
        failures.append(f"nDCG@10 {metrics['ndcg_at_10']} < 目标 {TARGETS['ndcg_at_10']}")
    if metrics["exact_hit"] is not None and metrics["exact_hit"] < TARGETS["exact_hit"]:
        failures.append(f"exact hit {metrics['exact_hit']} < 目标 {TARGETS['exact_hit']}")
    if (
        metrics["must_include_coverage"] is not None
        and metrics["must_include_coverage"] < TARGETS["must_include_coverage"]
    ):
        failures.append(
            f"要点覆盖 {metrics['must_include_coverage']} < 目标 "
            f"{TARGETS['must_include_coverage']}"
        )
    if (
        metrics["empty_rate"] is not None
        and metrics["empty_rate"] > TARGETS["empty_rate"]
    ):
        failures.append(f"空召回率 {metrics['empty_rate']} > 上限 {TARGETS['empty_rate']}")
    if (
        metrics["p95_latency_ms"] is not None
        and metrics["p95_latency_ms"] > TARGETS["p95_latency_ms"]
    ):
        failures.append(
            f"p95 {metrics['p95_latency_ms']} ms > 目标 {TARGETS['p95_latency_ms']} ms"
        )
    if metrics["forbidden_hits"]:
        failures.append(f"出现 must_not_include 命中 {metrics['forbidden_hits']} 次")
    return metrics, failures


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), 4)


# --------------------------------------------------------------------------- #
# 输出
# --------------------------------------------------------------------------- #
def write_report(report: EvalReport, directory: str | Path) -> tuple[Path, Path]:
    """写出 JSON + Markdown 两份报告，返回路径。"""
    out_dir = Path(directory)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.generated_at.strftime("%Y%m%d-%H%M%S")
    json_path = out_dir / f"report-{stamp}.json"
    md_path = out_dir / f"report-{stamp}.md"
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def render_markdown(report: EvalReport) -> str:
    metrics = report.metrics
    lines: list[str] = [
        "# 方案 A 检索评估报告",
        "",
        f"- 生成时间：{report.generated_at.isoformat(timespec='seconds')}",
        f"- 金标集：`{report.golden_path}`（{report.case_count} 题）",
        f"- 默认域：{', '.join(report.domains) or '-'}",
        "",
        "## 指标",
        "",
        "| 指标 | 实测 | 目标 | 结论 |",
        "|---|---:|---:|---|",
    ]
    rows = [
        ("Recall@10", "recall_at_10", TARGETS["recall_at_10"], ">="),
        ("nDCG@10", "ndcg_at_10", TARGETS["ndcg_at_10"], ">="),
        ("exact hit", "exact_hit", TARGETS["exact_hit"], ">="),
        ("要点覆盖", "must_include_coverage", TARGETS["must_include_coverage"], ">="),
        ("p95 延迟 (ms)", "p95_latency_ms", TARGETS["p95_latency_ms"], "<="),
        ("空召回率", "empty_rate", TARGETS["empty_rate"], "<="),
    ]
    for label, key, target, direction in rows:
        value = metrics.get(key)
        if value is None:
            lines.append(f"| {label} | - | {target} | 无样本 |")
            continue
        ok = value >= target if direction == ">=" else value <= target
        lines.append(f"| {label} | {value} | {target} | {'✅' if ok else '❌'} |")

    lines.extend(
        [
            "",
            (
                f"- 可答题 {metrics['answerable_cases']} 题，"
                f"不可答题 {metrics['unanswerable_cases']} 题"
            ),
            (
                f"- exact 用例 {metrics['exact_cases']} 题；"
                f"要点覆盖用例 {metrics['coverage_cases']} 题"
            ),
            f"- nDCG@10（按父级 section 计算，主指标）：{metrics.get('ndcg_at_10')}",
            (
                "- nDCG@10（展开到 section 的更严格视角，参考值）："
                f"{metrics.get('ndcg_at_10_sections')}"
            ),
            f"- 不可答题空召回率：{metrics.get('unanswerable_empty_rate')}",
            (
                "- 不可答题合格率（空召回或低置信度且未泄露）："
                f"{metrics.get('unanswerable_pass_rate')}"
            ),
            f"- p50 延迟：{metrics.get('p50_latency_ms')} ms",
            f"- must_not_include 命中：{metrics['forbidden_hits']} 次",
            "",
        ]
    )

    if report.failures:
        lines.append("## 未达标项")
        lines.append("")
        lines.extend(f"- {item}" for item in report.failures)
        lines.append("")
    else:
        lines.extend(["## 未达标项", "", "无。", ""])

    lines.extend(
        [
            "## 逐题结果",
            "",
            "| ID | 类型 | Recall@10 | nDCG@10 | 覆盖 | 空召回 | 延迟(ms) |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for case in report.cases:
        recall = "-" if case.recall_at_10 is None else f"{case.recall_at_10:.2f}"
        ndcg = "-" if case.ndcg_at_10 is None else f"{case.ndcg_at_10:.2f}"
        coverage = (
            "-" if case.must_include_coverage is None else f"{case.must_include_coverage:.2f}"
        )
        lines.append(
            f"| {case.id} | {case.type} | {recall} | {ndcg} | {coverage} | "
            f"{'是' if case.empty else '否'} | {case.latency_ms:.1f} |"
        )
    lines.append("")
    lines.append(
        "> Recall@10 的分母是 gold section；分子取「证据按父级展开后的 section 序列」前 10 个。"
    )
    lines.append("")
    return "\n".join(lines)

