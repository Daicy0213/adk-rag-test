"""结构感知分块：把 section 内容块切成 child chunk。

规则来源：计划 9.3
- 普通段落按句子边界切，不按固定字符硬切；
- 列表保留引导句，长列表按 5–8 项分组并在后续组重复引导句；
- 表格按行组切，每组重复表头；
- 代码块整体保留；
- FAQ 一个问题 + 一个答案为一组，标题作为问题前缀；
- chunk 目标 250–400 tokens，上限 500，下限 100，overlap 只在句子边界。

设计取舍：
- 先"展开单元"（explode）再"装箱"（pack）。展开保证每个单元不超上限，
  装箱只做相邻合并；这样"某一块超长"和"很多小段"两类问题分开处理，逻辑可测。
- overlap 只发生在被切开的段落内部。跨段落做重叠会让同一段文字在证据包里重复出现，
  既浪费 token 也干扰排序，收益不抵成本。
"""

from __future__ import annotations

import re

from ..config import IngestSettings
from ..tokens import estimate_tokens
from .parse_md import Block, SectionDraft, render_table

CHUNKER_VERSION = "md-struct-v1"

# 句子边界：标点后接空格 + 大写/引号/括号开头。
# 刻意不在数字前切分，否则 "Circular [2022] No. 5" 会被拆开。
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'\[—])")


class ChunkDraft:
    """尚未分配 ID 的 chunk 草稿。"""

    __slots__ = ("text", "ordinal", "source_start", "source_end")

    def __init__(
        self,
        text: str,
        ordinal: int,
        source_start: int | None,
        source_end: int | None,
    ) -> None:
        self.text = text
        self.ordinal = ordinal
        self.source_start = source_start
        self.source_end = source_end

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"ChunkDraft(ordinal={self.ordinal}, tokens={estimate_tokens(self.text)})"


def chunk_section(
    section: SectionDraft,
    settings: IngestSettings,
    *,
    doc_type: str = "policy",
) -> list[ChunkDraft]:
    """把一个 section 切成一到多个 child chunk。"""
    if section.is_empty:
        return []

    units = _explode(section.blocks, settings)
    grouped = _pack(units, settings)

    prefix = f"Q: {section.heading}" if doc_type == "faq" and section.heading else None

    drafts: list[ChunkDraft] = []
    for ordinal, blocks in enumerate(grouped, start=1):
        text = "\n\n".join(b.text for b in blocks if b.text.strip()).strip()
        if not text:
            continue
        if prefix and not text.startswith("Q:"):
            text = f"{prefix}\n\n{text}"
        starts = [b.source_start for b in blocks if b.source_start is not None]
        ends = [b.source_end for b in blocks if b.source_end is not None]
        drafts.append(
            ChunkDraft(
                text=text,
                ordinal=ordinal,
                source_start=min(starts) if starts else section.source_start,
                source_end=max(ends) if ends else section.source_end,
            )
        )
    return drafts


# --------------------------------------------------------------------------- #
# 展开：保证每个单元不超过 max_chunk_tokens
# --------------------------------------------------------------------------- #
def _explode(blocks: list[Block], settings: IngestSettings) -> list[Block]:
    units: list[Block] = []
    for index, block in enumerate(blocks):
        previous = blocks[index - 1] if index > 0 else None
        lead_in = _lead_in(previous)
        if block.kind == "list":
            units.extend(_explode_list(block, lead_in, settings))
        elif block.kind == "table":
            units.extend(_explode_table(block, settings))
        elif block.kind == "paragraph" and block.tokens > settings.max_chunk_tokens:
            units.extend(_split_text_block(block, settings))
        elif block.tokens > settings.max_chunk_tokens and block.kind not in {"code"}:
            # quote/subheading 超长也不该出现；兜底切分，避免污染 token 预算。
            units.extend(_split_text_block(block, settings))
        else:
            units.append(block)
    return units


def _lead_in(previous: Block | None) -> str | None:
    """列表前的引导句（"The depositor must provide:"）。"""
    if previous is None or previous.kind != "paragraph":
        return None
    text = previous.text.strip()
    if text.endswith(":"):
        return text
    return None


def _explode_list(block: Block, lead_in: str | None, settings: IngestSettings) -> list[Block]:
    items = block.items or []
    if not items:
        return [block]

    groups: list[list[str]] = []
    size = max(1, settings.list_items_per_chunk)
    for start in range(0, len(items), size):
        groups.append(items[start : start + size])

    # 组内仍超上限时继续对半拆，直到单条为止。
    expanded: list[list[str]] = []
    queue = list(groups)
    while queue:
        group = queue.pop(0)
        text = "\n".join(group)
        if estimate_tokens(text) > settings.max_chunk_tokens and len(group) > 1:
            mid = (len(group) + 1) // 2
            queue.insert(0, group[mid:])
            queue.insert(0, group[:mid])
        else:
            expanded.append(group)

    units: list[Block] = []
    for index, group in enumerate(expanded):
        body = "\n".join(group)
        # 第一组保持原样（引导句通常与它同块或就在前面）；
        # 后续组重复引导句，避免"材料清单只有后半截"。
        text = body if index == 0 or not lead_in else f"{lead_in}\n\n{body}"
        units.append(
            Block(
                kind="list",
                text=text,
                source_start=block.source_start,
                source_end=block.source_end,
                items=group,
            )
        )
    return units


def _explode_table(block: Block, settings: IngestSettings) -> list[Block]:
    header = block.header or []
    rows = block.rows or []
    if not rows:
        return [block]

    size = max(1, settings.table_rows_per_chunk)
    groups: list[list[list[str]]] = []
    for start in range(0, len(rows), size):
        groups.append(rows[start : start + size])

    expanded: list[list[list[str]]] = []
    queue = list(groups)
    while queue:
        group = queue.pop(0)
        text = render_table(header, group)
        if estimate_tokens(text) > settings.max_chunk_tokens and len(group) > 1:
            mid = (len(group) + 1) // 2
            queue.insert(0, group[mid:])
            queue.insert(0, group[:mid])
        else:
            expanded.append(group)

    return [
        Block(
            kind="table",
            text=render_table(header, group),
            source_start=block.source_start,
            source_end=block.source_end,
            header=header,
            rows=group,
        )
        for group in expanded
    ]


def _split_text_block(block: Block, settings: IngestSettings) -> list[Block]:
    pieces = _split_paragraph(block.text, settings)
    return [
        Block(
            kind=block.kind,
            text=piece,
            source_start=block.source_start,
            source_end=block.source_end,
        )
        for piece in pieces
    ]


def _split_paragraph(text: str, settings: IngestSettings) -> list[str]:
    """按句子边界切分段落，并在切点保留少量 overlap。"""
    sentences = [s for s in _SENTENCE_RE.split(text) if s.strip()]
    if not sentences:
        return [text]

    pieces: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            pieces.append(" ".join(current).strip())
            current.clear()

    for sentence in sentences:
        candidate = " ".join([*current, sentence])
        if current and estimate_tokens(candidate) > settings.target_chunk_tokens:
            flush()
            carry = _overlap_tail(pieces[-1], settings.overlap_tokens)
            current = [carry, sentence] if carry else [sentence]
            continue
        current.append(sentence)
    flush()

    # 任何仍然超上限的片段（例如没有标点的长表格行）做硬切。
    result: list[str] = []
    for piece in pieces:
        if estimate_tokens(piece) <= settings.max_chunk_tokens:
            result.append(piece)
        else:
            result.extend(_hard_split(piece, settings.max_chunk_tokens, settings.overlap_tokens))
    return result


def _overlap_tail(text: str, overlap_tokens: int) -> str:
    """从上一段尾部取不超过 overlap_tokens 的完整句子。"""
    sentences = [s for s in _SENTENCE_RE.split(text) if s.strip()]
    carry: list[str] = []
    for sentence in reversed(sentences):
        candidate = " ".join([sentence, *carry])
        if estimate_tokens(candidate) > overlap_tokens:
            break
        carry.insert(0, sentence)
    return " ".join(carry)


def _hard_split(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    words = text.split()
    pieces: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join([*current, word])
        if current and estimate_tokens(candidate) > max_tokens:
            pieces.append(" ".join(current))
            keep = max(0, overlap_tokens // 2)
            current = current[-keep:] if keep else []
        current.append(word)
    if current:
        pieces.append(" ".join(current))
    # 极端情况（单个词就超限）仍然返回原片段，交给质量校验报告。
    return pieces or [text]


# --------------------------------------------------------------------------- #
# 装箱：把单元合并成目标大小的 chunk
# --------------------------------------------------------------------------- #
def _pack(units: list[Block], settings: IngestSettings) -> list[list[Block]]:
    chunks: list[list[Block]] = []
    current: list[Block] = []
    current_tokens = 0

    def flush() -> None:
        nonlocal current, current_tokens
        if current:
            chunks.append(list(current))
            current = []
            current_tokens = 0

    for unit in units:
        if current and current_tokens + unit.tokens > settings.max_chunk_tokens:
            flush()
        current.append(unit)
        current_tokens += unit.tokens
        if current_tokens >= settings.target_chunk_tokens:
            flush()
    flush()

    return _merge_small_tail(chunks, settings)


def _merge_small_tail(chunks: list[list[Block]], settings: IngestSettings) -> list[list[Block]]:
    """把不足下限的尾部块并入前一块（前提是不超上限）。"""
    merged: list[list[Block]] = []
    for chunk in chunks:
        tokens = sum(b.tokens for b in chunk)
        if (
            merged
            and tokens < settings.min_chunk_tokens
            and sum(b.tokens for b in merged[-1]) + tokens <= settings.max_chunk_tokens
        ):
            merged[-1].extend(chunk)
            continue
        merged.append(chunk)
    return merged
