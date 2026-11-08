"""质量校验：front matter、分块结果、文档级一致性。

分级约定（计划 9.7）：
- `error` → 整篇文档进 quarantine，不进入批次；
- `warning` → 记录到报告，仍然入库。

为什么默认"缺元数据就隔离"：
把缺 `status`/`acl_groups` 的文档静默标成 active，在生产里就是越权或过期版本事故。
POC 里宁可少进几篇，也不要让脏数据进索引。
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from pydantic import ValidationError

from ..config import IngestSettings
from ..models import ChunkRecord, DocFrontMatter, SectionRecord, ValidationIssue

REQUIRED_FIELDS = ("doc_id", "domain", "title", "version", "status", "classification")

_NUMERIC_HEADING = re.compile(r"^\s*(\d+(?:\.\d+)*)")


def validate_front_matter(
    metadata: dict,
    *,
    domain_id: str,
    rel_path: str,
) -> tuple[DocFrontMatter | None, list[ValidationIssue]]:
    """校验并构造 front matter；返回 None 表示该文档必须隔离。"""
    issues: list[ValidationIssue] = []
    missing = [name for name in REQUIRED_FIELDS if metadata.get(name) in (None, "")]
    if missing:
        issues.append(
            ValidationIssue(
                level="error",
                code="front_matter_missing",
                message=f"缺少必填字段：{', '.join(missing)}",
                path=rel_path,
            )
        )
        return None, issues

    try:
        front = DocFrontMatter.model_validate(metadata)
    except ValidationError as exc:
        for detail in exc.errors():
            location = ".".join(str(part) for part in detail["loc"])
            issues.append(
                ValidationIssue(
                    level="error",
                    code="front_matter_invalid",
                    message=f"{location}: {detail['msg']}",
                    path=rel_path,
                    doc_id=str(metadata.get("doc_id") or "") or None,
                )
            )
        return None, issues

    if front.domain != domain_id:
        issues.append(
            ValidationIssue(
                level="error",
                code="domain_mismatch",
                message=f"front matter domain={front.domain} 与目录域名 {domain_id} 不一致",
                path=rel_path,
                doc_id=front.doc_id,
            )
        )
        return None, issues

    if not front.acl_groups:
        issues.append(
            ValidationIssue(
                level="error",
                code="acl_groups_empty",
                message=(
                    "acl_groups 为空：空 ACL 不匹配任何用户（公开文档请显式写 public），"
                    "按计划 6.4 的约定这里按错误处理"
                ),
                path=rel_path,
                doc_id=front.doc_id,
            )
        )
        return None, issues

    if front.effective_from and front.effective_to and front.effective_from >= front.effective_to:
        issues.append(
            ValidationIssue(
                level="error",
                code="effective_range_invalid",
                message=(
                    f"effective_from({front.effective_from}) 必须早于 "
                    f"effective_to({front.effective_to})"
                ),
                path=rel_path,
                doc_id=front.doc_id,
            )
        )
        return None, issues

    if front.effective_from is None:
        issues.append(
            ValidationIssue(
                level="warning",
                code="effective_from_missing",
                message="未声明 effective_from：按“长期有效”处理，但无法做过期版本判断",
                path=rel_path,
                doc_id=front.doc_id,
            )
        )

    return front, issues


def validate_sections(sections: list[SectionRecord], *, rel_path: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for section in sections:
        if not section.heading_path.strip():
            issues.append(
                ValidationIssue(
                    level="warning",
                    code="heading_path_empty",
                    message=f"section {section.id} 的 heading_path 为空",
                    path=rel_path,
                )
            )
    issues.extend(_check_clause_numbers(sections, rel_path))
    return issues


def validate_chunks(
    chunks: list[ChunkRecord],
    sections: list[SectionRecord],
    settings: IngestSettings,
    *,
    rel_path: str,
    doc_id: str,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if not chunks:
        issues.append(
            ValidationIssue(
                level="error",
                code="document_empty",
                message="文档没有产生任何 chunk（可能只有标题或全为空段落）",
                path=rel_path,
                doc_id=doc_id,
            )
        )
        return issues

    empty_sections = [s for s in sections if not s.text.strip()]
    # H1 根 section 只有子标题、没有正文是正常结构，不该反复告警；
    # 只有"叶子 section 没有正文"才是真的空块。
    parents = {section.parent_id for section in sections if section.parent_id}
    for section in empty_sections:
        if section.id in parents:
            continue
        issues.append(
            ValidationIssue(
                level="warning",
                code="empty_section",
                message=f"section {section.id} 只有标题、没有正文",
                path=rel_path,
                doc_id=doc_id,
            )
        )

    # 只有"切分产生的残块"才值得报短块告警：整个 section 只有一块时，
    # 短是文档本身的形态（FAQ 问答、单句条款），不应反复告警。
    chunks_per_section = Counter(chunk.section_id for chunk in chunks)

    for chunk in chunks:
        tokens = chunk.token_count
        if tokens > settings.max_chunk_tokens:
            issues.append(
                ValidationIssue(
                    level="warning",
                    code="chunk_too_long",
                    message=(
                        f"chunk {chunk.id} 估计 {tokens} tokens，超过上限 "
                        f"{settings.max_chunk_tokens}（可能是无标点的长表格/代码块）"
                    ),
                    path=rel_path,
                    doc_id=doc_id,
                )
            )
        elif (
            tokens < settings.min_chunk_tokens
            and chunk.doc_type != "faq"
            and chunks_per_section[chunk.section_id] > 1
        ):
            issues.append(
                ValidationIssue(
                    level="warning",
                    code="chunk_too_short",
                    message=(
                        f"chunk {chunk.id} 只有 {tokens} tokens，低于下限 "
                        f"{settings.min_chunk_tokens}"
                    ),
                    path=rel_path,
                    doc_id=doc_id,
                )
            )

        if chunk.doc_type == "product" and "|" in chunk.text and "| --- " not in chunk.text:
            issues.append(
                ValidationIssue(
                    level="warning",
                    code="table_header_missing",
                    message=f"chunk {chunk.id} 看起来是表格，但没有重复表头",
                    path=rel_path,
                    doc_id=doc_id,
                )
            )

        if "| --- " in chunk.text:
            # 只比较"看起来是表格行"的文本：同一 chunk 里可能既有段落又有表格。
            table_lines = [
                line.strip()
                for line in chunk.text.splitlines()
                if line.strip().startswith("|") and line.strip().endswith("|")
            ]
            widths = {line.count("|") for line in table_lines}
            if len(widths) > 1:
                issues.append(
                    ValidationIssue(
                        level="warning",
                        code="table_column_mismatch",
                        message=f"chunk {chunk.id} 的表格列数不一致：{sorted(widths)}",
                        path=rel_path,
                        doc_id=doc_id,
                    )
                )

    # 同一文档内重复 chunk（例如复制粘贴整段）；重复不致命，但会浪费证据预算。
    by_text: dict[str, list[str]] = defaultdict(list)
    for chunk in chunks:
        by_text[chunk.text.strip()].append(chunk.id)
    for duplicated in by_text.values():
        if len(duplicated) > 1:
            issues.append(
                ValidationIssue(
                    level="warning",
                    code="duplicate_chunk",
                    message=f"同一文档内出现内容完全相同的 chunk：{', '.join(duplicated)}",
                    path=rel_path,
                    doc_id=doc_id,
                )
            )

    return issues


def _check_clause_numbers(
    sections: list[SectionRecord], rel_path: str
) -> list[ValidationIssue]:
    """条款编号连续性（同级 "3.1 / 3.2 / 3.3" 不应跳号）。

    只对**同级、同前缀**的编号做检查，避免把 "第 3 章下属 3.1" 这种跨层级情况误报。
    """
    issues: list[ValidationIssue] = []
    siblings: dict[str, list[tuple[int, str, str]]] = defaultdict(list)
    for section in sections:
        heading = section.heading_path.rsplit(" > ", 1)[-1]
        match = _NUMERIC_HEADING.match(heading)
        if not match:
            continue
        parts = tuple(int(p) for p in match.group(1).split("."))
        prefix = parts[:-1]
        siblings[str(prefix)].append((parts[-1], section.id, heading))

    for prefix, items in siblings.items():
        items.sort()
        numbers = [item[0] for item in items]
        if len(numbers) < 2:
            continue
        expected = list(range(numbers[0], numbers[0] + len(numbers)))
        if numbers != expected:
            issues.append(
                ValidationIssue(
                    level="warning",
                    code="clause_number_gap",
                    message=(
                        f"同级条款编号不连续（前缀 {prefix or 'root'}）： "
                        f"实际 {numbers}，期望 {expected}"
                    ),
                    path=rel_path,
                )
            )
    return issues
