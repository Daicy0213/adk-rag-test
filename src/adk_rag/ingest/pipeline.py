"""ingest 流水线：解析 → 分块 → 校验 → 写 staging 批次 → 切换/回滚。

批次语义（计划 7.x、12.1）：
- 每次 ingest 生成一个 `ingest_batch`，状态先 `staging`；
- 质量门禁通过后变 `validated`；
- `promote` 只改 `domain.active_batch_id` 指针（事务内），查询立刻切到新批次；
- 旧批次行保留，`rollback` 指回去即可，不需要重建索引；
- 查询必须 `JOIN domain ... active_batch_id = chunk.batch_id`，所以 staging 行天然不可见。
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import psycopg

from ..config import IngestSettings, Settings
from ..db import connection, transaction
from ..models import (
    ChunkRecord,
    DocFrontMatter,
    DocumentRecord,
    IngestReport,
    ParsedDocument,
    SectionRecord,
    ValidationIssue,
)
from ..tokens import estimate_tokens
from .chunker import CHUNKER_VERSION, ChunkDraft, chunk_section
from .discover import KnowledgeDoc, discover_docs, load_domain
from .exact_terms import extract_exact_terms
from .parse_md import PARSER_VERSION, SectionDraft, flatten_sections, parse_file, parse_markdown
from .validate import validate_chunks, validate_front_matter, validate_sections

# 报告里最多保留多少条 warning，避免 stats JSONB 被超大文档撑爆。
MAX_RECORDED_WARNINGS = 200


@dataclass
class BuildResult:
    document: ParsedDocument
    errors: list[ValidationIssue]
    warnings: list[ValidationIssue]

    @property
    def ok(self) -> bool:
        return not self.errors


# --------------------------------------------------------------------------- #
# 纯函数部分（不碰数据库，便于单测）
# --------------------------------------------------------------------------- #
def build_document(
    front: DocFrontMatter,
    *,
    rel_path: str,
    raw_text: str,
    roots: Sequence[SectionDraft],
    settings: IngestSettings,
) -> BuildResult:
    """把 front matter + section 树组装成可入库记录（含 ID / 哈希 / exact_terms）。"""
    document_id = f"{front.doc_id}@{front.version}"
    document_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    document = DocumentRecord(
        id=document_id,
        doc_id=front.doc_id,
        domain_id=front.domain,
        path=rel_path,
        title=front.title,
        version=front.version,
        doc_type=front.doc_type,
        status=front.status,
        effective_from=front.effective_from,
        effective_to=front.effective_to,
        classification=front.classification,
        acl_groups=list(front.acl_groups),
        content_hash=document_hash,
        parser_version=PARSER_VERSION,
        source_url=front.source_url,
    )

    kept = [section for section in flatten_sections(list(roots)) if _survives(section)]
    kept_ids = {id(section) for section in kept}
    section_ids = {
        id(section): section_id_for(document_id, section.ordinal, section.heading)
        for section in kept
    }

    sections: list[SectionRecord] = []
    for section in kept:
        parent = _nearest_kept(section.parent, kept_ids)
        sections.append(
            SectionRecord(
                id=section_ids[id(section)],
                document_id=document_id,
                parent_id=section_ids[id(parent)] if parent is not None else None,
                heading_path=section.heading_path,
                level=section.level,
                ordinal=section.ordinal,
                text=section.text,
                token_count=section.token_count,
            )
        )

    chunks: list[ChunkRecord] = []
    for section in kept:
        drafts = chunk_section(section, settings, doc_type=front.doc_type)
        aggregation_parent = _aggregation_parent(section, kept_ids)
        parent_id = section_ids[id(aggregation_parent)] if aggregation_parent else section_ids[id(section)]
        for draft in drafts:
            chunks.append(
                _build_chunk(
                    front=front,
                    document_id=document_id,
                    section_id=section_ids[id(section)],
                    parent_id=parent_id,
                    heading_path=section.heading_path,
                    draft=draft,
                )
            )

    parsed = ParsedDocument(
        front=front,
        path=rel_path,
        document=document,
        sections=sections,
        chunks=chunks,
    )

    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []
    for issue in [*validate_sections(sections, rel_path=rel_path), *validate_chunks(
        chunks, sections, settings, rel_path=rel_path, doc_id=front.doc_id
    )]:
        (errors if issue.level == "error" else warnings).append(issue)

    return BuildResult(document=parsed, errors=errors, warnings=warnings)


def section_id_for(document_id: str, ordinal: int, heading: str) -> str:
    slug = slugify(heading)[:32] or "root"
    return f"sec-{document_id}-{ordinal:02d}-{slug}"


def slugify(value: str) -> str:
    """标题 → ID 片段：只保留 [a-z0-9]，其余折成连字符。"""
    text = unicodedata.normalize("NFKC", value).casefold()
    out: list[str] = []
    last_dash = False
    for char in text:
        if char.isalnum():
            out.append(char)
            last_dash = False
        elif not last_dash:
            out.append("-")
            last_dash = True
    return "".join(out).strip("-")


def chunk_id_for(
    domain_id: str, document_id: str, section_id: str, ordinal: int, content_hash: str
) -> str:
    """chunk ID：内容不变 ID 不变；内容变 ID 变（计划 9.6）。"""
    payload = "|".join([domain_id, document_id, section_id, str(ordinal), content_hash])
    return "chunk-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _build_chunk(
    *,
    front: DocFrontMatter,
    document_id: str,
    section_id: str,
    parent_id: str,
    heading_path: str,
    draft: ChunkDraft,
) -> ChunkRecord:
    content_hash = hashlib.sha256(f"{heading_path}\n{draft.text}".encode("utf-8")).hexdigest()
    chunk_id = chunk_id_for(front.domain, document_id, section_id, draft.ordinal, content_hash)
    return ChunkRecord(
        id=chunk_id,
        domain_id=front.domain,
        document_id=document_id,
        section_id=section_id,
        parent_id=parent_id,
        ordinal=draft.ordinal,
        doc_type=front.doc_type,
        heading_path=heading_path,
        text=draft.text,
        token_count=estimate_tokens(draft.text),
        status=front.status,
        effective_from=front.effective_from,
        effective_to=front.effective_to,
        classification=front.classification,
        acl_groups=list(front.acl_groups),
        # 计划 6.7：从 heading_path + text 抽取，不 stemming
        exact_terms=extract_exact_terms(f"{heading_path}\n{draft.text}"),
        content_hash=content_hash,
        chunker_version=CHUNKER_VERSION,
        source_start=draft.source_start,
        source_end=draft.source_end,
    )


def _survives(section: SectionDraft) -> bool:
    """只保留有正文或有正文后代的 section（纯标题的空 section 不建行）。"""
    if not section.is_empty:
        return True
    return any(_survives(child) for child in section.children)


def _nearest_kept(
    section: SectionDraft | None, kept_ids: set[int]
) -> SectionDraft | None:
    node = section
    while node is not None and id(node) not in kept_ids:
        node = node.parent
    return node


def _aggregation_parent(
    section: SectionDraft, kept_ids: set[int]
) -> SectionDraft | None:
    """父级聚合的 group key：最近的 H2 及以上的存活祖先；没有就是自己。"""
    node: SectionDraft | None = section
    while node is not None:
        if node.level <= 2 and id(node) in kept_ids:
            return node
        node = node.parent
    return section


# --------------------------------------------------------------------------- #
# 读取 → 构建（仍不碰数据库）
# --------------------------------------------------------------------------- #
def load_and_build(
    settings: Settings,
    domain_id: str,
    knowledge_root: Path,
) -> tuple[list[ParsedDocument], list[ValidationIssue]]:
    """遍历域内文档并构建记录；返回（可入库文档，全部问题）。"""
    docs = discover_docs(knowledge_root, domain_id)
    if not docs:
        return [], [
            ValidationIssue(
                level="error",
                code="no_documents",
                message=f"{knowledge_root / domain_id} 下没有 .md 文档",
                path=str(knowledge_root / domain_id),
            )
        ]

    parsed: list[ParsedDocument] = []
    issues: list[ValidationIssue] = []
    for doc in docs:
        parsed_doc, doc_issues = _build_one(doc, settings)
        issues.extend(doc_issues)
        if parsed_doc is not None:
            parsed.append(parsed_doc)
    return parsed, issues


def _build_one(
    doc: KnowledgeDoc, settings: Settings
) -> tuple[ParsedDocument | None, list[ValidationIssue]]:
    parsed_file = parse_file(doc.path)
    front, issues = validate_front_matter(
        parsed_file.metadata, domain_id=doc.domain_id, rel_path=doc.rel_path
    )
    if front is None:
        return None, issues

    roots = parse_markdown(
        parsed_file.body,
        line_offset=parsed_file.line_offset,
        title=front.title,
    )
    if not roots:
        issues.append(
            ValidationIssue(
                level="error",
                code="no_heading",
                message="文档没有任何标题，无法建 section 树",
                path=doc.rel_path,
                doc_id=front.doc_id,
            )
        )
        return None, issues

    result = build_document(
        front,
        rel_path=doc.rel_path,
        raw_text=doc.path.read_text(encoding="utf-8"),
        roots=roots,
        settings=settings.ingest,
    )
    issues.extend(result.errors)
    issues.extend(result.warnings)
    if not result.ok:
        return None, issues
    return result.document, issues


# --------------------------------------------------------------------------- #
# 数据库写入
# --------------------------------------------------------------------------- #
def ingest(
    settings: Settings,
    domain_id: str,
    *,
    batch_label: str | None = None,
    promote: bool = False,
) -> IngestReport:
    knowledge_root = settings.knowledge_root()
    try:
        domain = load_domain(knowledge_root, domain_id)
    except FileNotFoundError as exc:
        return IngestReport(
            domain_id=domain_id,
            batch_label=batch_label,
            batch_status="failed",
            issues=[
                ValidationIssue(
                    level="error",
                    code="domain_missing",
                    message=str(exc),
                    path=str(knowledge_root / domain_id),
                )
            ],
        )
    parsed_docs, issues = load_and_build(settings, domain_id, knowledge_root)

    report = IngestReport(
        domain_id=domain_id,
        batch_label=batch_label,
        accepted_docs=[doc.document.id for doc in parsed_docs],
        quarantined_docs=sorted(
            {
                issue.doc_id or issue.path or "?"
                for issue in issues
                if issue.level == "error"
            }
        ),
        issues=issues,
    )

    if not parsed_docs:
        report.batch_status = "failed"
        return report

    report.document_count = len(parsed_docs)
    report.section_count = sum(len(doc.sections) for doc in parsed_docs)
    report.chunk_count = sum(len(doc.chunks) for doc in parsed_docs)
    report.token_total = sum(chunk.token_count for doc in parsed_docs for chunk in doc.chunks)

    with connection(settings) as conn:
        # 一个事务里完成：写 staging 批次 → 质量门禁 → 定状态。
        # 门禁失败时数据仍保留（查询看不到，因为 active_batch_id 没切），
        # 便于事后排查；整套操作要么全部可见，要么全部不可见。
        with transaction(conn):
            _upsert_domain(conn, domain)
            batch_id = _insert_batch(conn, domain_id, batch_label)
            _insert_documents(conn, batch_id, parsed_docs)

            gate_issues = _quality_gate(conn, batch_id, report)
            gate_errors = [issue for issue in gate_issues if issue.level == "error"]
            report.issues.extend(gate_issues)
            stats = _build_stats(report, parsed_docs)
            _finish_batch(
                conn,
                batch_id,
                status="failed" if gate_errors else "validated",
                stats=stats,
            )

        report.batch_id = batch_id
        report.batch_status = "failed" if gate_errors else "validated"
        if gate_errors:
            return report

    if promote:
        promote_batch(settings, domain_id, batch_id)
        report.promoted = True
        report.batch_status = "active"

    return report


def _upsert_domain(conn: psycopg.Connection, domain) -> None:
    conn.execute(
        """
        INSERT INTO domain (id, name, description, card, acl_groups)
        VALUES (%s, %s, %s, %s::jsonb, %s::text[])
        ON CONFLICT (id) DO UPDATE SET
            name = EXCLUDED.name,
            description = EXCLUDED.description,
            card = EXCLUDED.card,
            acl_groups = EXCLUDED.acl_groups,
            updated_at = now()
        """,
        (
            domain.id,
            domain.name,
            domain.description,
            json.dumps(domain.card, ensure_ascii=False, default=str),
            domain.acl_groups,
        ),
    )


def _insert_batch(conn: psycopg.Connection, domain_id: str, label: str | None) -> int:
    row = conn.execute(
        """
        INSERT INTO ingest_batch (domain_id, label, status)
        VALUES (%s, %s, 'staging')
        RETURNING id
        """,
        (domain_id, label),
    ).fetchone()
    return int(row["id"])


def _insert_documents(
    conn: psycopg.Connection, batch_id: int, documents: Iterable[ParsedDocument]
) -> None:
    docs = list(documents)
    # psycopg3 的 Connection 只有 execute()，批量写入要走 cursor.executemany。
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO document (
                id, batch_id, doc_id, domain_id, path, title, version, doc_type, status,
                effective_from, effective_to, classification, acl_groups, content_hash,
                parser_version, source_url
            ) VALUES (
                %(id)s, %(batch_id)s, %(doc_id)s, %(domain_id)s, %(path)s, %(title)s,
                %(version)s, %(doc_type)s, %(status)s, %(effective_from)s, %(effective_to)s,
                %(classification)s, %(acl_groups)s, %(content_hash)s, %(parser_version)s,
                %(source_url)s
            )
            """,
            [{**doc.document.model_dump(), "batch_id": batch_id} for doc in docs],
        )
        cur.executemany(
            """
            INSERT INTO section (
                id, batch_id, document_id, parent_id, heading_path, level, ordinal, text, token_count
            ) VALUES (
                %(id)s, %(batch_id)s, %(document_id)s, %(parent_id)s, %(heading_path)s,
                %(level)s, %(ordinal)s, %(text)s, %(token_count)s
            )
            """,
            [
                {**section.model_dump(), "batch_id": batch_id}
                for doc in docs
                for section in doc.sections
            ],
        )
        cur.executemany(
            """
            INSERT INTO chunk (
                id, batch_id, domain_id, document_id, section_id, parent_id, ordinal, doc_type,
                heading_path, text, token_count, status, effective_from, effective_to,
                classification, acl_groups, exact_terms, content_hash, chunker_version,
                source_start, source_end
            ) VALUES (
                %(id)s, %(batch_id)s, %(domain_id)s, %(document_id)s, %(section_id)s,
                %(parent_id)s, %(ordinal)s, %(doc_type)s, %(heading_path)s, %(text)s,
                %(token_count)s, %(status)s, %(effective_from)s, %(effective_to)s,
                %(classification)s, %(acl_groups)s, %(exact_terms)s, %(content_hash)s,
                %(chunker_version)s, %(source_start)s, %(source_end)s
            )
            """,
            [
                {**chunk.model_dump(), "batch_id": batch_id}
                for doc in docs
                for chunk in doc.chunks
            ],
        )


def _quality_gate(
    conn: psycopg.Connection, batch_id: int, report: IngestReport
) -> list[ValidationIssue]:
    """入库后的门禁：行数一致、tsv 已生成、父级引用完整。"""
    errors: list[ValidationIssue] = []
    counts = conn.execute(
        """
        SELECT
          (SELECT count(*) FROM document WHERE batch_id = %(b)s) AS documents,
          (SELECT count(*) FROM section  WHERE batch_id = %(b)s) AS sections,
          (SELECT count(*) FROM chunk    WHERE batch_id = %(b)s) AS chunks,
          (SELECT count(*) FROM chunk    WHERE batch_id = %(b)s AND tsv IS NULL) AS null_tsv,
          (SELECT count(*) FROM chunk    WHERE batch_id = %(b)s AND cardinality(exact_terms) = 0) AS no_terms
        """,
        {"b": batch_id},
    ).fetchone()

    if counts["documents"] != report.document_count:
        errors.append(
            ValidationIssue(
                level="error",
                code="gate_document_count",
                message=f"document 行数 {counts['documents']} != 预期 {report.document_count}",
            )
        )
    if counts["chunks"] != report.chunk_count:
        errors.append(
            ValidationIssue(
                level="error",
                code="gate_chunk_count",
                message=f"chunk 行数 {counts['chunks']} != 预期 {report.chunk_count}",
            )
        )
    if counts["null_tsv"]:
        errors.append(
            ValidationIssue(
                level="error",
                code="gate_tsv_missing",
                message=f"{counts['null_tsv']} 个 chunk 的 tsv 为空，trigger 可能没生效",
            )
        )
    if counts["sections"] != report.section_count:
        errors.append(
            ValidationIssue(
                level="warning",
                code="gate_section_count",
                message=f"section 行数 {counts['sections']} != 预期 {report.section_count}",
            )
        )
    if counts["no_terms"]:
        errors.append(
            ValidationIssue(
                level="warning",
                code="gate_no_exact_terms",
                message=f"{counts['no_terms']} 个 chunk 没有抽到 exact_terms（可能确实不含编号/金额）",
            )
        )
    return errors


def _build_stats(report: IngestReport, documents: list[ParsedDocument]) -> dict:
    warnings = report.warnings[:MAX_RECORDED_WARNINGS]
    return {
        "documents": len(documents),
        "sections": report.section_count,
        "chunks": report.chunk_count,
        "tokens": report.token_total,
        "accepted_docs": report.accepted_docs,
        "quarantined_docs": report.quarantined_docs,
        "warnings": [issue.model_dump(mode="json") for issue in warnings],
        "warning_total": len(report.warnings),
    }


def _finish_batch(
    conn: psycopg.Connection, batch_id: int, *, status: str, stats: dict
) -> None:
    conn.execute(
        """
        UPDATE ingest_batch
        SET status = %s, stats = %s::jsonb, finished_at = now()
        WHERE id = %s
        """,
        (status, json.dumps(stats, ensure_ascii=False, default=str), batch_id),
    )


# --------------------------------------------------------------------------- #
# 批次切换 / 回滚 / 报告
# --------------------------------------------------------------------------- #
def promote_batch(settings: Settings, domain_id: str, batch_id: int) -> None:
    """原子切换：只改 domain.active_batch_id，不搬运 chunk 行。"""
    with connection(settings) as conn:
        with transaction(conn):
            batch = conn.execute(
                "SELECT id, status FROM ingest_batch WHERE id = %s AND domain_id = %s FOR UPDATE",
                (batch_id, domain_id),
            ).fetchone()
            if batch is None:
                raise ValueError(f"批次 {batch_id} 不存在或不属于域 {domain_id}")
            if batch["status"] == "failed":
                raise ValueError(f"批次 {batch_id} 状态为 failed，不能切换上线")
            if batch["status"] == "staging":
                # 进程中断会留下半成品批次；这种批次不能上线，需要重新 ingest。
                raise ValueError(
                    f"批次 {batch_id} 仍是 staging（可能上次 ingest 中断），请重新 ingest"
                )
            # retired 允许重新 promote：rollback 之后"再切回来"是正常的运维动作。

            current = conn.execute(
                "SELECT active_batch_id FROM domain WHERE id = %s FOR UPDATE", (domain_id,)
            ).fetchone()
            previous = current["active_batch_id"] if current else None

            conn.execute(
                "UPDATE ingest_batch SET status = 'active' WHERE id = %s", (batch_id,)
            )
            if previous and int(previous) != int(batch_id):
                conn.execute(
                    "UPDATE ingest_batch SET status = 'retired' WHERE id = %s", (previous,)
                )
            conn.execute(
                """
                UPDATE domain
                SET active_batch_id = %s, previous_batch_id = %s, updated_at = now()
                WHERE id = %s
                """,
                (batch_id, previous if previous != batch_id else None, domain_id),
            )
        # 新批次写入后 GIN 可能有 pending list；POC 规模小，ANALYZE 足够（计划 7.3）。
        conn.execute("ANALYZE chunk")
        conn.commit()


def rollback(settings: Settings, domain_id: str) -> dict:
    """回滚到上一个活跃批次；没有可回滚目标时报错。"""
    with connection(settings) as conn:
        with transaction(conn):
            domain = conn.execute(
                """
                SELECT active_batch_id, previous_batch_id FROM domain
                WHERE id = %s FOR UPDATE
                """,
                (domain_id,),
            ).fetchone()
            if domain is None:
                raise ValueError(f"域 {domain_id} 不存在")
            current = domain["active_batch_id"]
            target = domain["previous_batch_id"]
            if target is None:
                raise ValueError(f"域 {domain_id} 没有可回滚的上一批次")

            conn.execute("UPDATE ingest_batch SET status = 'active' WHERE id = %s", (target,))
            if current is not None:
                conn.execute(
                    "UPDATE ingest_batch SET status = 'retired' WHERE id = %s", (current,)
                )
            conn.execute(
                """
                UPDATE domain
                SET active_batch_id = %s, previous_batch_id = NULL, updated_at = now()
                WHERE id = %s
                """,
                (target, domain_id),
            )
    return {"domain_id": domain_id, "active_batch_id": int(target), "rolled_back_from": current}


def batch_report(settings: Settings, batch_id: int) -> dict:
    """读取批次统计与抽样，供 `report` 子命令打印。"""
    with connection(settings) as conn:
        batch = conn.execute(
            """
            SELECT b.*, d.id AS domain, d.active_batch_id
            FROM ingest_batch b
            JOIN domain d ON d.id = b.domain_id
            WHERE b.id = %s
            """,
            (batch_id,),
        ).fetchone()
        if batch is None:
            raise ValueError(f"批次 {batch_id} 不存在")

        counts = conn.execute(
            """
            SELECT
              (SELECT count(*) FROM document WHERE batch_id = %(b)s) AS documents,
              (SELECT count(*) FROM section  WHERE batch_id = %(b)s) AS sections,
              (SELECT count(*) FROM chunk    WHERE batch_id = %(b)s) AS chunks,
              (SELECT sum(token_count) FROM chunk WHERE batch_id = %(b)s) AS tokens,
              (SELECT count(*) FROM chunk WHERE batch_id = %(b)s
                 AND cardinality(exact_terms) > 0) AS chunks_with_exact_terms
            """,
            {"b": batch_id},
        ).fetchone()

        samples = conn.execute(
            """
            SELECT id, heading_path, token_count, cardinality(exact_terms) AS exact_count,
                   left(text, 160) AS preview
            FROM chunk
            WHERE batch_id = %s
            ORDER BY random()
            LIMIT 5
            """,
            (batch_id,),
        ).fetchall()

        per_doc = conn.execute(
            """
            SELECT d.doc_id, d.version, d.status, d.doc_type, d.title,
                   count(c.id) AS chunks, coalesce(sum(c.token_count), 0) AS tokens
            FROM document d
            LEFT JOIN chunk c ON c.document_id = d.id AND c.batch_id = d.batch_id
            WHERE d.batch_id = %s
            GROUP BY d.doc_id, d.version, d.status, d.doc_type, d.title
            ORDER BY d.doc_id
            """,
            (batch_id,),
        ).fetchall()

    return {
        "batch": dict(batch),
        "counts": dict(counts),
        "documents": [dict(row) for row in per_doc],
        "samples": [dict(row) for row in samples],
    }


def active_batches(settings: Settings, domains: Sequence[str]) -> dict[str, int | None]:
    """各域当前活跃批次，用于 knowledge_version 和缓存 key。"""
    if not domains:
        return {}
    with connection(settings) as conn:
        rows = conn.execute(
            "SELECT id, active_batch_id FROM domain WHERE id = ANY(%s::text[])", (list(domains),)
        ).fetchall()
    return {row["id"]: row["active_batch_id"] for row in rows}
