"""分块测试：覆盖计划 9.8 要求的 8 类用例。"""

from __future__ import annotations

from adk_rag.ingest.chunker import chunk_section
from adk_rag.ingest.parse_md import flatten_sections, parse_markdown


def _sections(body: str, title: str = "Doc"):
    roots = parse_markdown(body, title=title)
    return flatten_sections(roots)


def _chunks(section, settings, doc_type="policy"):
    return chunk_section(section, settings, doc_type=doc_type)


def test_1_one_h2_with_multiple_h3(small_ingest_settings):
    body = (
        "# Doc\n\n## 1 Parent\n\nIntro paragraph under the parent.\n\n"
        "### 1.1 Child A\n\nContent A.\n\n"
        "### 1.2 Child B\n\nContent B.\n"
    )
    sections = _sections(body)
    parent, child_a, child_b = sections[1], sections[2], sections[3]

    # 每个 section 独立出块，父节只包含自己的段落
    assert len(_chunks(parent, small_ingest_settings)) == 1
    assert _chunks(child_a, small_ingest_settings)[0].text == "Content A."
    assert _chunks(child_b, small_ingest_settings)[0].text == "Content B."


def test_2_long_list_grouped_with_repeated_lead_in(small_ingest_settings):
    # 20 条远超 max_chunk_tokens(90)，必须分组；组内重复引导句
    items = "\n".join(f"- Document {index} of the list." for index in range(1, 21))
    body = (
        "# Doc\n\n## 1 Materials\n\nThe depositor must provide:\n\n"
        f"{items}\n"
    )
    sections = _sections(body)
    section = sections[1]
    chunks = _chunks(section, small_ingest_settings)

    assert len(chunks) >= 2
    # 第一块保留引导句（与段落同块），后续组必须重复引导句
    assert "The depositor must provide:" in chunks[0].text
    for chunk in chunks[1:]:
        assert chunk.text.startswith("The depositor must provide:")
    # 条目不能丢、不能重复
    all_items = [line for chunk in chunks for line in chunk.text.splitlines() if line.startswith("- ")]
    assert all_items == [f"- Document {index} of the list." for index in range(1, 21)]


def test_3_table_groups_repeat_header(small_ingest_settings):
    rows = "\n".join(f"| CD-2026-00{index} | Product {index} | USD 100,000 |" for index in range(1, 6))
    body = (
        "# Doc\n\n## 1 Catalogue\n\n"
        "| Product code | Name | Minimum |\n| --- | --- | --- |\n"
        f"{rows}\n"
    )
    sections = _sections(body)
    chunks = _chunks(sections[1], small_ingest_settings)

    assert len(chunks) >= 2
    for chunk in chunks:
        lines = chunk.text.splitlines()
        assert lines[0] == "| Product code | Name | Minimum |"
        assert lines[1].startswith("| --- ")
        # 列数一致
        assert {line.count("|") for line in lines if line.startswith("|")} == {4}


def test_4_faq_question_and_answer_stay_together(small_ingest_settings):
    body = (
        "# FAQ\n\n## Can a partial withdrawal be paid in cash?\n\n"
        "No. Partial withdrawals are credited to the original funding account only.\n"
    )
    sections = _sections(body, title="FAQ")
    chunks = _chunks(sections[1], small_ingest_settings, doc_type="faq")

    assert len(chunks) == 1
    assert chunks[0].text.startswith("Q: Can a partial withdrawal be paid in cash?")
    assert "credited to the original funding account" in chunks[0].text


def test_5_heading_only_section_produces_no_chunk(small_ingest_settings):
    body = "# Doc\n\n## 1 Empty Section\n\n## 2 Real Section\n\nContent.\n"
    sections = _sections(body)
    empty, real = sections[1], sections[2]

    assert empty.is_empty
    assert _chunks(empty, small_ingest_settings) == []
    assert len(_chunks(real, small_ingest_settings)) == 1


def test_6_duplicate_chunk_is_detected_by_validation(settings):
    from adk_rag.ingest.parse_md import parse_markdown
    from adk_rag.ingest.pipeline import build_document
    from adk_rag.ingest.validate import validate_chunks
    from adk_rag.models import DocFrontMatter

    # 同一段文字出现在两个 section：会产生两个文本完全相同的 chunk
    body = (
        "# Doc\n\n## 1 Section One\n\nSame paragraph.\n\n"
        "## 2 Section Two\n\nSame paragraph.\n"
    )
    front = DocFrontMatter(
        doc_id="doc-dup",
        domain="deposit",
        title="Doc",
        version="1.0",
        status="active",
        classification="internal",
        acl_groups=["public"],
    )
    result = build_document(
        front,
        rel_path="knowledge/deposit/dup.md",
        raw_text=body,
        roots=parse_markdown(body, title="Doc"),
        settings=settings.ingest,
    )
    assert result.ok
    issues = validate_chunks(
        result.document.chunks,
        result.document.sections,
        settings.ingest,
        rel_path="knowledge/deposit/dup.md",
        doc_id="doc-dup",
    )
    assert any(issue.code == "duplicate_chunk" for issue in issues)


def test_7_identifiers_survive_chunking(small_ingest_settings, settings):
    from adk_rag.ingest.pipeline import build_document
    from adk_rag.models import DocFrontMatter

    body = (
        "# Doc\n\n## 1 Terms\n\n"
        "Early withdrawal follows Circular [2022] No. 5 and product CD-2026-001 "
        "with a minimum of USD 100,000 from 2026-01-01 at 3.25%.\n"
    )
    front = DocFrontMatter(
        doc_id="deposit-code-001",
        domain="deposit",
        title="Doc",
        version="1.0",
        status="active",
        classification="internal",
        acl_groups=["public"],
    )
    result = build_document(
        front,
        rel_path="knowledge/deposit/code.md",
        raw_text=body,
        roots=parse_markdown(body, title="Doc"),
        settings=settings.ingest,
    )
    terms = set(result.document.chunks[0].exact_terms)
    assert "circular [2022] no. 5" in terms
    assert "circular[2022]no.5" in terms
    assert "cd-2026-001" in terms
    assert "usd 100,000" in terms
    assert "2026-01-01" in terms
    assert "3.25%" in terms


def test_8_long_section_is_split_within_limit(small_ingest_settings):
    sentence = (
        "Early withdrawal of a large-denomination certificate of deposit requires the "
        "original certificate and a completed application form. "
    )
    body = f"# Doc\n\n## 1 Long\n\n{sentence * 12}\n"
    sections = _sections(body)
    chunks = _chunks(sections[1], small_ingest_settings)

    assert len(chunks) > 1
    for chunk in chunks:
        # 允许 15% 的估算误差余量
        assert chunk.text
        assert len(chunk.text) > 0
    assert max(len(chunk.text) for chunk in chunks) < len(sentence * 12)

    from adk_rag.tokens import estimate_tokens

    assert all(
        estimate_tokens(chunk.text) <= small_ingest_settings.max_chunk_tokens * 1.15
        for chunk in chunks
    )


def test_overlap_only_inside_split_section(small_ingest_settings):
    first = "First sentence about certificates of deposit and withdrawal rules. "
    second = "Second sentence about required documents and fees. "
    body = f"# Doc\n\n## 1 Long\n\n{(first + second) * 6}\n"
    sections = _sections(body)
    chunks = _chunks(sections[1], small_ingest_settings)

    assert len(chunks) >= 2
    # 相邻块之间应有少量重复（overlap 只发生在被切开的段落内部）
    assert chunks[1].text[:40] in chunks[0].text
    assert chunks[0].text != chunks[1].text
    # 不是整段复制：后一块仍包含新内容
    assert chunks[1].text not in chunks[0].text
