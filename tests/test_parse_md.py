"""Markdown 结构解析测试（计划 11.1 的 heading 解析）。"""

from __future__ import annotations

from adk_rag.ingest.parse_md import (
    first_heading,
    flatten_sections,
    parse_markdown,
    render_table,
    split_front_matter,
)

DOC = """# CD Policy

## 1 Scope

First paragraph.

### 1.1 Detail

Second paragraph.

#### 1.1.1 Deep heading

Third paragraph.

## 2 Fees

| Scenario | Fee |
| --- | --- |
| Early | 1.50% |
| Late | 0.75% |
"""


def test_heading_tree_and_paths():
    roots = parse_markdown(DOC, title="CD Policy")
    flat = flatten_sections(roots)

    assert [section.heading for section in flat] == [
        "CD Policy",
        "1 Scope",
        "1.1 Detail",
        "2 Fees",
    ]
    assert flat[0].heading_path == "CD Policy"
    assert flat[1].heading_path == "CD Policy > 1 Scope"
    assert flat[2].heading_path == "CD Policy > 1 Scope > 1.1 Detail"
    assert flat[2].level == 3
    assert flat[2].parent is flat[1]
    # ordinal 是文档顺序
    assert [section.ordinal for section in flat] == [0, 1, 2, 3]


def test_h4_merged_into_nearest_h3():
    roots = parse_markdown(DOC, title="CD Policy")
    flat = flatten_sections(roots)
    detail = flat[2]
    kinds = [block.kind for block in detail.blocks]
    assert "subheading" in kinds
    # H4 不单独建 section，只作为内容块
    assert all(section.level <= 3 for section in flat)
    assert any("1.1.1 Deep heading" in block.text for block in detail.blocks)


def test_blocks_kinds_and_table_structure():
    roots = parse_markdown(DOC, title="CD Policy")
    flat = flatten_sections(roots)
    fees = flat[3]
    table = next(block for block in fees.blocks if block.kind == "table")
    assert table.header == ["Scenario", "Fee"]
    assert table.rows == [["Early", "1.50%"], ["Late", "0.75%"]]
    assert "| Scenario | Fee |" in table.text


def test_section_text_excludes_child_sections():
    roots = parse_markdown(DOC, title="CD Policy")
    flat = flatten_sections(roots)
    scope = flat[1]
    assert scope.text == "First paragraph."
    assert "Second paragraph" not in scope.text


def test_source_lines_include_front_matter_offset():
    raw = "---\ntitle: X\n---\n\n# Doc\n\n## 1 A\n\nBody.\n"
    parsed = split_front_matter(raw)
    assert parsed.line_offset == 3
    roots = parse_markdown(parsed.body, line_offset=parsed.line_offset, title="Doc")
    section = flatten_sections(roots)[1]
    # "## 1 A" 在原始文件的第 7 行（1 基）；正文块在第 9 行。
    assert section.source_start == 7
    body = section.blocks[0]
    assert body.source_start == 9


def test_file_without_front_matter():
    parsed = split_front_matter("# Title\n\nBody.\n")
    assert parsed.metadata == {}
    assert parsed.line_offset == 0
    assert first_heading(parsed.body) == "Title"


def test_unclosed_front_matter_treated_as_body():
    raw = "---\ntitle: X\n\n# Doc\n"
    parsed = split_front_matter(raw)
    assert parsed.metadata == {}
    assert parsed.line_offset == 0


def test_content_before_first_heading_is_kept():
    roots = parse_markdown("Intro text.\n\n## 1 Section\n\nBody.\n", title="Doc")
    flat = flatten_sections(roots)
    assert flat[0].heading == "Doc"
    assert flat[0].text == "Intro text."


def test_render_table_pads_ragged_rows():
    text = render_table(["A", "B", "C"], [["1", "2", "3"], ["4"]])
    assert text.splitlines()[0] == "| A | B | C |"
    assert text.splitlines()[-1] == "| 4 |  |  |"


def test_list_items_keep_markers_and_nested_content():
    body = "# Doc\n\n## 1 Steps\n\n1. First step.\n2. Second step with **emphasis**.\n"
    roots = parse_markdown(body, title="Doc")
    section = flatten_sections(roots)[1]
    block = section.blocks[0]
    assert block.kind == "list"
    assert block.items[0] == "1. First step."
    # 有序列表保持原始编号，markdown 标记原样保留（由 FTS 处理，不做渲染）
    assert block.items[1].startswith("2. Second step")
