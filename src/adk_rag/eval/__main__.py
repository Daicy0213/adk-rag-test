"""评估 CLI（计划 10.4）：

    python -m adk_rag.eval --golden eval/golden.jsonl --domains deposit

附加参数：
    --report-dir  报告目录（默认 eval/reports）
    --limit       只跑前 N 题（调试用）
    --validate-gold  校验 gold_section_ids 是否真实存在于活跃批次
    --json        把完整报告打印到 stdout
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from ..config import load_settings, project_root
from ..db import connection
from ..eval.golden import load_golden
from ..eval.runner import evaluate, write_report
from ..retrieve.retriever import PgRetriever


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m adk_rag.eval",
        description="用金标集评估稀疏检索（Recall@10 / nDCG@10 / exact hit / p95）。",
    )
    parser.add_argument("--golden", default="eval/golden.jsonl", help="金标集 JSONL 路径")
    parser.add_argument(
        "--domains",
        default="deposit",
        help="默认检索域，逗号分隔（题目里写了 gold_domains 时以题目为准）",
    )
    parser.add_argument("--config", default=None, help="配置文件路径")
    parser.add_argument(
        "--report-dir", default="eval/reports", help="报告输出目录（相对工程根）"
    )
    parser.add_argument("--limit", type=int, default=None, help="只跑前 N 题")
    parser.add_argument(
        "--validate-gold",
        action="store_true",
        help="校验 gold_section_ids 是否存在于当前活跃批次",
    )
    parser.add_argument("--json", action="store_true", help="输出完整 JSON 报告")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = project_root()
    golden_path = _resolve(args.golden, root)
    domains = [item.strip() for item in args.domains.split(",") if item.strip()]

    try:
        cases = load_golden(golden_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"金标集加载失败：{exc}", file=sys.stderr)
        return 2

    if args.limit:
        cases = cases[: args.limit]

    settings = load_settings(args.config)
    retriever = PgRetriever(settings)

    if args.validate_gold:
        problems = _validate_gold(cases, settings)
        if problems:
            print("gold_section_ids 校验失败：", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 2
        print(f"gold_section_ids 校验通过（{len(cases)} 题）")

    parent_map = _gold_parent_map(cases, settings)

    try:
        report = evaluate(
            cases,
            retriever=retriever,
            default_domains=domains,
            golden_path=str(golden_path),
            section_parent_map=parent_map,
        )
    except psycopg.Error as exc:
        print(f"数据库错误：{exc}", file=sys.stderr)
        print("请确认 PostgreSQL 已启动、迁移已执行、域批次已 promote。", file=sys.stderr)
        return 2

    json_path, md_path = write_report(report, _resolve(args.report_dir, root))
    if args.json:
        print(report.model_dump_json(indent=2))
    else:
        print(_summary(report))
    print(f"\n报告：{md_path}")
    print(f"JSON：{json_path}")
    return 0 if not report.failures else 1


def _summary(report) -> str:
    metrics = report.metrics
    lines = [
        "评估结果（20 题金标）" if report.case_count != 20 else "评估结果",
        (
            f"  cases={report.case_count} answerable={metrics['answerable_cases']} "
            f"unanswerable={metrics['unanswerable_cases']}"
        ),
        f"  Recall@10={metrics['recall_at_10']}  nDCG@10={metrics['ndcg_at_10']}",
        f"  exact_hit={metrics['exact_hit']}  要点覆盖={metrics['must_include_coverage']}",
        (
            f"  empty_rate={metrics['empty_rate']}  "
            f"unanswerable_empty_rate={metrics['unanswerable_empty_rate']}"
        ),
        f"  p50={metrics['p50_latency_ms']}ms  p95={metrics['p95_latency_ms']}ms",
    ]
    if report.failures:
        lines.append("  未达标：")
        lines.extend(f"    - {item}" for item in report.failures)
    else:
        lines.append("  全部指标达到计划 11.4 的目标值")
    return "\n".join(lines)


def _validate_gold(cases, settings) -> list[str]:
    """确认每个 gold_section_id 都在对应的活跃批次里，避免金标写错导致 Recall 假阴性。"""
    wanted = {section for case in cases for section in case.gold_section_ids}
    if not wanted:
        return []
    with connection(settings) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT s.id
            FROM section s
            JOIN document d ON d.id = s.document_id AND d.batch_id = s.batch_id
            JOIN domain dm ON dm.id = d.domain_id AND dm.active_batch_id = s.batch_id
            WHERE s.id = ANY(%s::text[])
            """,
            (sorted(wanted),),
        ).fetchall()
    found = {row["id"] for row in rows}
    return [f"活跃批次里找不到 section：{section}" for section in sorted(wanted - found)]


def _gold_parent_map(cases, settings) -> dict[str, str] | None:
    """gold section → chunk.parent_id（父级 section），用于按检索单元计算 nDCG。"""
    wanted = {section for case in cases for section in case.gold_section_ids}
    if not wanted:
        return None
    with connection(settings) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT section_id, parent_id
            FROM chunk
            WHERE section_id = ANY(%s::text[]) AND parent_id IS NOT NULL
            """,
            (sorted(wanted),),
        ).fetchall()
    mapping = {row["section_id"]: row["parent_id"] for row in rows}
    return mapping or None


def _resolve(path: str, root) -> object:
    from pathlib import Path

    candidate = Path(path)
    return candidate if candidate.is_absolute() else root / candidate


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
