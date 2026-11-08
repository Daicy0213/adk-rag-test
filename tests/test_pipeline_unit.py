"""文档组装与校验测试：ID 稳定性、父级聚合键、front matter 隔离。"""

from __future__ import annotations

from adk_rag.config import IngestSettings
from adk_rag.ingest.parse_md import parse_markdown
from adk_rag.ingest.pipeline import build_document, chunk_id_for, section_id_for, slugify
from adk_rag.ingest.validate import validate_front_matter
from adk_rag.models import DocFrontMatter

BODY = """# Large-Denomination CD Policy

## 3 Early Withdrawal

Intro paragraph about the process.

### 3.1 General Requirements

The depositor must provide the original certificate and a completed application form.

### 3.2 Authorized Representative

A notarized power of attorney signed by the depositor is required.
"""


def make_front(**overrides) -> DocFrontMatter:
    payload = dict(
        doc_id="deposit-cd-001",
        domain="deposit",
        title="Large-Denomination CD Policy",
        version="3.2",
        status="active",
        classification="internal",
        acl_groups=["role:retail_business"],
        doc_type="policy",
    )
    payload.update(overrides)
    return DocFrontMatter(**payload)


def build(body: str = BODY, front: DocFrontMatter | None = None, settings: IngestSettings | None = None):
    front = front or make_front()
    return build_document(
        front,
        rel_path="knowledge/deposit/01.md",
        raw_text=body,
        roots=parse_markdown(body, title=front.title),
        settings=settings or IngestSettings(),
    )


def test_document_and_section_ids_are_deterministic():
    first = build()
    second = build()

    assert first.ok and second.ok
    assert first.document.document.id == "deposit-cd-001@3.2"
    assert [section.id for section in first.document.sections] == [
        section.id for section in second.document.sections
    ]
    assert [chunk.id for chunk in first.document.chunks] == [
        chunk.id for chunk in second.document.chunks
    ]


def test_changing_content_changes_chunk_id():
    changed = BODY.replace("original certificate", "original certificate or statement")
    first = build()
    second = build(changed)

    ids_first = {chunk.id for chunk in first.document.chunks}
    ids_second = {chunk.id for chunk in second.document.chunks}
    assert ids_first & ids_second  # 未变化的 section 仍可复用 ID
    assert ids_first != ids_second


def test_chunk_id_depends_on_content_hash():
    a = chunk_id_for("deposit", "doc@1.0", "sec-1", 1, "hash-a")
    b = chunk_id_for("deposit", "doc@1.0", "sec-1", 1, "hash-a")
    c = chunk_id_for("deposit", "doc@1.0", "sec-1", 1, "hash-b")
    assert a == b and a != c
    assert a.startswith("chunk-") and len(a) == len("chunk-") + 24


def test_slugify_handles_punctuation_and_unicode():
    assert slugify("3.1 General Requirements") == "3-1-general-requirements"
    # 非 ASCII 字母（如中文）保留：Ingest 只要求 ID 稳定，不要求必须 ASCII
    assert slugify("大额存单") == "大额存单"
    assert slugify("Section 3.2 — Authorized Representative") == "section-3-2-authorized-representative"
    # 空标题用 ordinal 兜底
    assert section_id_for("doc@1.0", 4, "").endswith("-04-root")


def test_chunk_parent_is_nearest_h2_section():
    result = build()
    sections = {section.id: section for section in result.document.sections}
    h2 = next(section for section in result.document.sections if section.level == 2)

    for chunk in result.document.chunks:
        # 3.1/3.2 的 chunk 聚合到 H2 "3 Early Withdrawal"
        assert chunk.parent_id == h2.id
        assert sections[chunk.parent_id].level == 2


def test_chunk_inherits_document_metadata():
    result = build(front=make_front(status="scheduled", effective_from="2027-01-01"))
    for chunk in result.document.chunks:
        assert chunk.status == "scheduled"
        assert str(chunk.effective_from) == "2027-01-01"
        assert chunk.classification == "internal"
        assert chunk.acl_groups == ["role:retail_business"]
        assert chunk.doc_type == "policy"


def test_exact_terms_populated_from_heading_and_text():
    body = BODY + "\n\nSubject to Circular [2022] No. 5 and product CD-2026-001.\n"
    result = build(body)
    terms = {term for chunk in result.document.chunks for term in chunk.exact_terms}
    assert "circular [2022] no. 5" in terms
    assert "cd-2026-001" in terms


def test_front_matter_missing_required_field_is_error():
    front, issues = validate_front_matter(
        {"doc_id": "x", "domain": "deposit"}, domain_id="deposit", rel_path="a.md"
    )
    assert front is None
    assert any(issue.code == "front_matter_missing" and issue.level == "error" for issue in issues)


def test_front_matter_domain_mismatch_is_error():
    front, issues = validate_front_matter(
        {
            "doc_id": "x",
            "domain": "fx",
            "title": "T",
            "version": "1",
            "status": "active",
            "classification": "internal",
            "acl_groups": ["public"],
        },
        domain_id="deposit",
        rel_path="a.md",
    )
    assert front is None
    assert any(issue.code == "domain_mismatch" for issue in issues)


def test_empty_acl_groups_is_error():
    front, issues = validate_front_matter(
        {
            "doc_id": "x",
            "domain": "deposit",
            "title": "T",
            "version": "1",
            "status": "active",
            "classification": "internal",
            "acl_groups": [],
        },
        domain_id="deposit",
        rel_path="a.md",
    )
    assert front is None
    assert any(issue.code == "acl_groups_empty" for issue in issues)


def test_invalid_effective_range_is_error():
    front, issues = validate_front_matter(
        {
            "doc_id": "x",
            "domain": "deposit",
            "title": "T",
            "version": "1",
            "status": "active",
            "classification": "internal",
            "acl_groups": ["public"],
            "effective_from": "2026-06-01",
            "effective_to": "2026-01-01",
        },
        domain_id="deposit",
        rel_path="a.md",
    )
    assert front is None
    assert any(issue.code == "effective_range_invalid" for issue in issues)


def test_document_without_body_is_quarantined():
    body = "# Doc\n\n## 1 Empty\n"
    result = build(body)
    assert not result.ok
    assert any(issue.code == "document_empty" for issue in result.errors)


def test_empty_section_warning_only_for_leaf():
    body = "# Doc\n\n## 1 Parent\n\n### 1.1 Child\n\nContent.\n"
    result = build(body)
    # H1 与 H2 都有存活子节，不应触发 empty_section
    assert not [issue for issue in result.warnings if issue.code == "empty_section"]


def test_clause_number_gap_warning():
    body = (
        "# Doc\n\n## 1 Section\n\n### 1.1 First\n\nText.\n\n"
        "### 1.3 Third\n\nText.\n"
    )
    result = build(body)
    assert any(issue.code == "clause_number_gap" for issue in result.warnings)
