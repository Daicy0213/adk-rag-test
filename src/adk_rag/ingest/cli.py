"""ingest CLI。

计划 10.4 约定的命令形态（保持不变）：

    python -m adk_rag.ingest.cli --domain deposit --knowledge-root knowledge --batch-label poc-001
    python -m adk_rag.ingest.cli report --batch-id 1
    python -m adk_rag.ingest.cli promote --domain deposit --batch-id 1
    python -m adk_rag.ingest.cli rollback --domain deposit

额外提供 `init-db`（执行迁移）和 `--dry-run`（只解析不写库），两者都不改变上面的契约。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ..config import load_settings
from ..db import apply_migration
from ..models import IngestReport, ValidationIssue
from .discover import load_catalog
from .pipeline import batch_report, ingest, promote_batch, rollback

COMMANDS = {"ingest", "report", "promote", "rollback", "init-db"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m adk_rag.ingest.cli",
        description="方案 A POC 的 ingest：解析 Markdown、建 staging 批次、原子切换与回滚。",
    )
    parser.add_argument("--config", help="配置文件路径（默认 config/default.yaml）")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest_parser = sub.add_parser("ingest", help="解析域内文档并写入 staging 批次")
    ingest_parser.add_argument("--domain", required=True, help="域 ID，例如 deposit")
    ingest_parser.add_argument(
        "--knowledge-root",
        default=None,
        help="知识根目录（默认取配置 ingest.knowledge_root，相对工程根）",
    )
    ingest_parser.add_argument("--batch-label", default=None, help="人可读批次名")
    ingest_parser.add_argument(
        "--promote", action="store_true", help="质量门禁通过后立即切换为活跃批次"
    )
    ingest_parser.add_argument(
        "--dry-run", action="store_true", help="只解析与校验，不连接数据库"
    )

    report_parser = sub.add_parser("report", help="查看某个批次的统计与抽样")
    report_parser.add_argument("--batch-id", type=int, required=True)
    report_parser.add_argument("--json", action="store_true", help="输出完整 JSON")

    promote_parser = sub.add_parser("promote", help="把批次原子切换为域活跃批次")
    promote_parser.add_argument("--domain", required=True)
    promote_parser.add_argument("--batch-id", type=int, required=True)

    rollback_parser = sub.add_parser("rollback", help="回滚到上一个活跃批次")
    rollback_parser.add_argument("--domain", required=True)

    init_parser = sub.add_parser("init-db", help="执行 migrations/001_init.sql")
    init_parser.add_argument(
        "--migration",
        default="migrations/001_init.sql",
        help="迁移文件（相对工程根）",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    # 兼容"没有子命令 = ingest"的用法（计划 10.4 的写法）。
    if not args_list or args_list[0] not in COMMANDS | {"-h", "--help"}:
        args_list.insert(0, "ingest")

    args = build_parser().parse_args(args_list)
    settings = load_settings(args.config)

    if args.command == "ingest":
        return _cmd_ingest(args, settings)
    if args.command == "report":
        return _cmd_report(args, settings)
    if args.command == "promote":
        try:
            promote_batch(settings, args.domain, args.batch_id)
        except ValueError as exc:
            print(f"promote 失败：{exc}", file=sys.stderr)
            return 1
        print(f"已切换：domain={args.domain} active_batch_id={args.batch_id}")
        return 0
    if args.command == "rollback":
        try:
            result = rollback(settings, args.domain)
        except ValueError as exc:
            print(f"rollback 失败：{exc}", file=sys.stderr)
            return 1
        print(
            f"已回滚：domain={result['domain_id']} "
            f"active_batch_id={result['active_batch_id']} "
            f"(离开批次 {result['rolled_back_from']})"
        )
        return 0
    if args.command == "init-db":
        path = apply_migration(settings, args.migration)
        print(f"迁移已执行：{path}")
        return 0
    return 2


def _cmd_ingest(args: argparse.Namespace, settings) -> int:
    knowledge_root = (
        Path(args.knowledge_root).expanduser().resolve()
        if args.knowledge_root
        else settings.knowledge_root()
    )
    if not knowledge_root.is_dir():
        print(f"知识根目录不存在：{knowledge_root}", file=sys.stderr)
        return 1

    # 域必须存在目录，否则后面报错信息会很含糊。
    catalog = load_catalog(knowledge_root)
    if catalog.get("domains") and args.domain not in catalog["domains"]:
        print(
            f"提示：{args.domain} 不在 _catalog.yaml 的 domains 里；"
            "只要目录存在仍会继续（POC 允许先建目录）。",
            file=sys.stderr,
        )

    if args.dry_run:
        from .pipeline import load_and_build

        documents, issues = load_and_build(settings, args.domain, knowledge_root)
        print(f"dry-run：可入库文档 {len(documents)} 篇")
        print(f"  sections：{sum(len(d.sections) for d in documents)}")
        print(f"  chunks：{sum(len(d.chunks) for d in documents)}")
        print(f"  tokens：{sum(c.token_count for d in documents for c in d.chunks)}")
        _print_issues(issues)
        return 0 if documents else 1

    report = ingest(
        settings,
        args.domain,
        batch_label=args.batch_label,
        promote=args.promote,
    )
    _print_report(report)
    return 0 if report.batch_status in {"validated", "active"} else 1


def _cmd_report(args: argparse.Namespace, settings) -> int:
    try:
        data = batch_report(settings, args.batch_id)
    except ValueError as exc:
        print(f"report 失败：{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, default=str))
        return 0

    batch = data["batch"]
    counts = data["counts"]
    print(f"batch {batch['id']}  domain={batch['domain']}  status={batch['status']}")
    if batch["label"]:
        print(f"  label：{batch['label']}")
    print(
        "  documents={documents} sections={sections} chunks={chunks} tokens={tokens}".format(
            **{key: (value or 0) for key, value in counts.items()}
        )
    )
    if batch.get("active_batch_id") == batch["id"]:
        print("  该批次当前是活跃批次")
    warnings = (batch.get("stats") or {}).get("warnings") or []
    if warnings:
        print(f"  警告 {len(warnings)} 条（前 10 条）：")
        for warning in warnings[:10]:
            print(f"    - [{warning['code']}] {warning['message']}")
    print("  文档明细：")
    for doc in data["documents"]:
        print(
            f"    - {doc['doc_id']}@{doc['version']} [{doc['status']}/{doc['doc_type']}] "
            f"chunks={doc['chunks']} tokens={doc['tokens']}  {doc['title']}"
        )
    print("  随机抽样：")
    for sample in data["samples"]:
        print(
            f"    - {sample['id']} ({sample['token_count']} tokens, "
            f"{sample['exact_count']} exact_terms) {sample['heading_path']}"
        )
        print(f"      {sample['preview'].replace(chr(10), ' ')[:120]}…")
    return 0


def _print_report(report: IngestReport) -> None:
    print(
        f"域 {report.domain_id}：批次 {report.batch_id} 状态 {report.batch_status}"
        + (f"（label={report.batch_label}）" if report.batch_label else "")
    )
    print(
        f"  文档 {report.document_count} 篇 / section {report.section_count} 个 / "
        f"chunk {report.chunk_count} 个 / 约 {report.token_total} tokens"
    )
    if report.promoted:
        print("  已执行 promote：查询立即切到新批次")
    if report.quarantined_docs:
        print(f"  隔离文档：{', '.join(report.quarantined_docs)}")
    _print_issues(report.issues)


def _print_issues(issues: list[ValidationIssue], limit: int = 20) -> None:
    errors = [issue for issue in issues if issue.level == "error"]
    warnings = [issue for issue in issues if issue.level == "warning"]
    if errors:
        print(f"  错误 {len(errors)} 条：")
        for issue in errors[:limit]:
            print(f"    - [{issue.code}] {issue.message} ({issue.path or '-'})")
    if warnings:
        print(f"  警告 {len(warnings)} 条" + ("（前 10 条）：" if len(warnings) > 10 else "："))
        for issue in warnings[:10]:
            print(f"    - [{issue.code}] {issue.message}")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
