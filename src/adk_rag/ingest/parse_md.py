"""Markdown 结构解析：front matter、section 树、可分组的内容块。

为什么不用"按标题正则切分"：
- 需要保留 `token.map` 的原始行号（引用要能定位到文件行）；
- 需要区分段落/列表/表格/代码块，才能按元素类型分块（计划 9.3）；
- 需要在 H4 以下并入最近 H3、在空 section 上给出质量警告。

这里的产物是"结构草稿"（SectionDraft/Block），不含 ID 与哈希——那些属于
pipeline：ID 依赖 doc_id/version，属于文档级信息，不该混进解析器。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter
from markdown_it import MarkdownIt
from markdown_it.token import Token

from ..tokens import estimate_tokens

PARSER_VERSION = "markdown-it-py-3+struct-v1"

# GFM 的表格与删除线在 markdown-it-py 核心规则里；linkify 需要额外依赖，
# 本 POC 不需要自动链接，所以不启用 gfm-like 预设。
_MD = MarkdownIt("commonmark").enable("table").enable("strikethrough")

# 最多支持到 H3 作为 section 边界；H4+ 并入最近 H3（计划 9.2）。
MAX_SECTION_LEVEL = 3

_WS_RE = re.compile(r"\s+")


@dataclass
class Block:
    """一个可独立分组的内容块。"""

    kind: str  # paragraph | list | table | code | quote | subheading
    text: str
    source_start: int | None = None
    source_end: int | None = None
    # list：已带编号/符号的条目
    items: list[str] | None = None
    # table：表头 + 数据行
    header: list[str] | None = None
    rows: list[list[str]] | None = None

    @property
    def tokens(self) -> int:
        return estimate_tokens(self.text)


@dataclass
class SectionDraft:
    """一个标题（H1/H2/H3）以下的 section 草稿。"""

    level: int
    heading: str
    ordinal: int
    blocks: list[Block] = field(default_factory=list)
    heading_path: str = ""
    parent: "SectionDraft | None" = None
    children: list["SectionDraft"] = field(default_factory=list)
    source_start: int | None = None
    source_end: int | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks if b.text.strip()).strip()

    @property
    def token_count(self) -> int:
        return estimate_tokens(self.text)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()


@dataclass
class ParsedFile:
    """front matter 解析结果：元数据、正文、正文行号偏移。"""

    metadata: dict
    body: str
    line_offset: int


def split_front_matter(raw: str) -> ParsedFile:
    """把 YAML front matter 与正文分开，并给出正文相对原文的行偏移。

    行偏移用于把 markdown-it 的 0 基行号换算成源文件的 1 基行号，
    这样 `source_start/source_end` 在编辑器里能直接跳转。
    """
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return ParsedFile(metadata={}, body=raw, line_offset=0)

    closing = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() in {"---", "..."}:
            closing = idx
            break
    if closing is None:
        # 没有闭合标记：当作普通正文，避免把整篇文档吞掉。
        return ParsedFile(metadata={}, body=raw, line_offset=0)

    metadata = dict(frontmatter.loads(raw).metadata or {})
    body = "\n".join(lines[closing + 1 :])
    return ParsedFile(metadata=metadata, body=body, line_offset=closing + 1)


def first_heading(body: str) -> str | None:
    """正文里的第一个标题，用于 front matter 缺 title 时的兜底提示。"""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
        if stripped:
            return None
    return None


def parse_markdown(body: str, *, line_offset: int = 0, title: str = "") -> list[SectionDraft]:
    """把 Markdown 正文解析成 section 树（按文档顺序，H1/H2/H3 建树）。"""
    tokens = _MD.parse(body)
    roots: list[SectionDraft] = []
    stack: list[SectionDraft] = []
    ordinal = 0
    fallback_root: SectionDraft | None = None
    index = 0
    total = len(tokens)

    while index < total:
        token = tokens[index]

        if token.type == "heading_open":
            level = int(token.tag[1])
            heading = _inline_text(tokens, index + 1)
            start, end = _source_range(token, line_offset)
            if level <= MAX_SECTION_LEVEL:
                while stack and stack[-1].level >= level:
                    stack.pop()
                section = SectionDraft(
                    level=level,
                    heading=heading,
                    ordinal=ordinal,
                    source_start=start,
                    source_end=end,
                )
                ordinal += 1
                if stack:
                    section.parent = stack[-1]
                    stack[-1].children.append(section)
                else:
                    roots.append(section)
                stack.append(section)
            else:
                # H4 以下并入最近 section：保留为普通块，便于 FTS 命中子标题词。
                block = Block(
                    kind="subheading",
                    text=f"{'#' * level} {heading}".strip(),
                    source_start=start,
                    source_end=end,
                )
                if stack:
                    stack[-1].blocks.append(block)
                else:
                    fallback_root, ordinal = _ensure_fallback_root(
                        roots, fallback_root, title, ordinal
                    )
                    fallback_root.blocks.append(block)
            index += 3  # heading_open / inline / heading_close
            continue

        block, next_index = _parse_block(tokens, index, line_offset)
        if block is not None:
            if stack:
                stack[-1].blocks.append(block)
            else:
                # 首个标题之前的内容：挂到一个合成的根 section，避免静默丢内容。
                fallback_root, ordinal = _ensure_fallback_root(
                    roots, fallback_root, title, ordinal
                )
                fallback_root.blocks.append(block)
        index = next_index if next_index > index else index + 1

    _assign_heading_paths(roots, title)
    return roots


def flatten_sections(roots: list[SectionDraft]) -> list[SectionDraft]:
    """按文档顺序（ordinal）展平，父节点在子节点之前。"""
    flat: list[SectionDraft] = []

    def walk(nodes: list[SectionDraft]) -> None:
        for node in nodes:
            flat.append(node)
            walk(node.children)

    walk(roots)
    flat.sort(key=lambda s: s.ordinal)
    return flat


def parse_file(path: Path) -> ParsedFile:
    """读取文件并拆出 front matter；不做元数据校验。"""
    raw = path.read_text(encoding="utf-8")
    return split_front_matter(raw)


# --------------------------------------------------------------------------- #
# 内部实现
# --------------------------------------------------------------------------- #
def _ensure_fallback_root(
    roots: list[SectionDraft],
    current: SectionDraft | None,
    title: str,
    ordinal: int,
) -> tuple[SectionDraft, int]:
    """返回（合成根 section, 下一个可用 ordinal）。已有则原样返回。"""
    if current is not None:
        return current, ordinal
    root = SectionDraft(level=1, heading=title, ordinal=ordinal)
    roots.insert(0, root)
    return root, ordinal + 1


def _inline_text(tokens: list[Token], index: int) -> str:
    if index < len(tokens) and tokens[index].type == "inline":
        return _clean(tokens[index].content)
    return ""


def _source_range(token: Token | None, line_offset: int) -> tuple[int | None, int | None]:
    if token is None or not token.map:
        return None, None
    return line_offset + token.map[0] + 1, line_offset + token.map[1]


def _clean(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _parse_block(
    tokens: list[Token], index: int, line_offset: int
) -> tuple[Block | None, int]:
    token = tokens[index]
    start, end = _source_range(token, line_offset)

    if token.type == "paragraph_open":
        text = _inline_text(tokens, index + 1)
        if not text:
            return None, index + 3
        return Block("paragraph", text, start, end), index + 3

    if token.type in {"bullet_list_open", "ordered_list_open"}:
        return _parse_list(tokens, index, line_offset)

    if token.type == "table_open":
        return _parse_table(tokens, index, line_offset)

    if token.type in {"fence", "code_block"}:
        body = token.content.rstrip("\n")
        # 代码块整体保留，不切（计划 9.3）。
        text = f"```\n{body}\n```" if token.type == "fence" else body
        return Block("code", text, start, end), index + 1

    if token.type == "blockquote_open":
        return _parse_blockquote(tokens, index, line_offset)

    # html_block / hr / 其他：POC 不处理，直接跳过。
    return None, index + 1


def _parse_list(tokens: list[Token], index: int, line_offset: int) -> tuple[Block, int]:
    ordered = tokens[index].type == "ordered_list_open"
    raw_start = tokens[index].attrGet("start")
    number = int(raw_start) if raw_start is not None else 1

    items: list[str] = []
    depth = 0
    cursor = index
    total = len(tokens)
    start, end = _source_range(tokens[index], line_offset)

    while cursor < total:
        token = tokens[cursor]
        if token.type in {"bullet_list_open", "ordered_list_open"}:
            depth += 1
        elif token.type in {"bullet_list_close", "ordered_list_close"}:
            depth -= 1
            if depth == 0:
                cursor += 1
                break
        elif token.type == "list_item_open" and depth == 1:
            item_text, cursor = _collect_list_item(tokens, cursor)
            marker = f"{number}." if ordered else "-"
            items.append(f"{marker} {item_text}".strip())
            if ordered:
                number += 1
            continue
        cursor += 1

    return Block("list", "\n".join(items), start, end, items=items), cursor


def _collect_list_item(tokens: list[Token], index: int) -> tuple[str, int]:
    """收集一个列表项的文本；嵌套列表的文本会被展开到同一项里（POC 简化）。"""
    depth = 0
    parts: list[str] = []
    cursor = index
    total = len(tokens)
    while cursor < total:
        token = tokens[cursor]
        if token.type == "list_item_open":
            depth += 1
        elif token.type == "list_item_close":
            depth -= 1
            if depth == 0:
                return _clean(" ".join(parts)), cursor + 1
        elif token.type == "inline" and depth >= 1:
            parts.append(token.content)
        cursor += 1
    return _clean(" ".join(parts)), cursor


def _parse_table(tokens: list[Token], index: int, line_offset: int) -> tuple[Block, int]:
    header: list[str] = []
    rows: list[list[str]] = []
    current: list[str] = []
    in_head = False
    depth = 0
    cursor = index
    total = len(tokens)
    start, end = _source_range(tokens[index], line_offset)

    while cursor < total:
        token = tokens[cursor]
        if token.type == "table_open":
            depth += 1
        elif token.type == "table_close":
            depth -= 1
            if depth == 0:
                cursor += 1
                break
        elif token.type == "thead_open":
            in_head = True
        elif token.type == "thead_close":
            in_head = False
        elif token.type == "tr_open":
            current = []
        elif token.type == "tr_close":
            if in_head:
                header = current
            elif current:
                rows.append(current)
        elif token.type in {"th_open", "td_open"}:
            current.append(_inline_text(tokens, cursor + 1))
            cursor += 1
        cursor += 1

    return (
        Block("table", render_table(header, rows), start, end, header=header, rows=rows),
        cursor,
    )


def render_table(header: list[str], rows: list[list[str]]) -> str:
    """渲染成 Markdown 表格：重复表头，列数不一致时补空单元格。"""
    width = max([len(header)] + [len(r) for r in rows]) if (header or rows) else 0
    if width == 0:
        return ""
    lines: list[str] = []
    if header:
        padded = header + [""] * (width - len(header))
        lines.append("| " + " | ".join(padded) + " |")
        lines.append("|" + "|".join([" --- "] * width) + "|")
    for row in rows:
        padded = row + [""] * (width - len(row))
        lines.append("| " + " | ".join(padded) + " |")
    return "\n".join(lines)


def _parse_blockquote(tokens: list[Token], index: int, line_offset: int) -> tuple[Block, int]:
    texts: list[str] = []
    depth = 0
    cursor = index
    total = len(tokens)
    start, end = _source_range(tokens[index], line_offset)
    while cursor < total:
        token = tokens[cursor]
        if token.type == "blockquote_open":
            depth += 1
        elif token.type == "blockquote_close":
            depth -= 1
            if depth == 0:
                cursor += 1
                break
        elif token.type == "inline" and token.content.strip():
            texts.append(_clean(token.content))
        cursor += 1
    return Block("quote", "\n".join(f"> {t}" for t in texts), start, end), cursor


def _assign_heading_paths(sections: list[SectionDraft], title: str) -> None:
    for section in sections:
        if section.level <= 1:
            section.heading_path = title or section.heading
        else:
            names: list[str] = []
            node: SectionDraft | None = section
            while node is not None and node.level > 1:
                names.append(node.heading)
                node = node.parent
            section.heading_path = " > ".join([title or "Document", *reversed(names)])
        _assign_heading_paths(section.children, title)
