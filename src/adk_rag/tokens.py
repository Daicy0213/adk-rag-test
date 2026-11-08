"""token 计数与按 token 预算截断。

为什么不直接用模型 SDK 的 tokenizer：
- 方案 A 的 POC 不绑定具体回答模型（DeepSeek/Gemini 都可能），引入某个 SDK 只为计数
  会把依赖和模型选择绑死；
- 这里的用途只有两个：控制 chunk 大小、控制证据包预算。两者都不需要精确值，
  只需要**稳定的、偏保守的**估计。

估算方法与误差：
- 单位数（单词 + 标点）与字符数/4 取较大者，再乘 1.15 安全系数；
- 英文技术文本实测误差约 ±20%，所以配置文件里的 500 上限实际约 435 个真 token；
- **不要**用这个数字做计费或硬性上下文限制，生产环境应换成目标模型的分词器。
"""

from __future__ import annotations

import math
import re

# 一个“词”或一个标点都算一个单位：英文里 token 通常落在两者之间。
_UNIT_RE = re.compile(r"[A-Za-z0-9]+(?:[._'-][A-Za-z0-9]+)*|[^\sA-Za-z0-9]")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?;:])\s+")

SAFETY_MARGIN = 1.15


def raw_token_estimate(text: str) -> int:
    """不含安全系数的估计值，便于测试和校准。"""
    if not text or not text.strip():
        return 0
    by_units = len(_UNIT_RE.findall(text))
    by_chars = math.ceil(len(text) / 4)
    return max(by_units, by_chars)


def estimate_tokens(text: str) -> int:
    """带安全系数的 token 估计；空文本返回 0。"""
    if not text or not text.strip():
        return 0
    return math.ceil(raw_token_estimate(text) * SAFETY_MARGIN)


def truncate_to_tokens(text: str, max_tokens: int, *, suffix: str = " …") -> str:
    """按 token 预算截断，尽量收在句子边界。

    证据包必须能说清“为什么这里截断了”，所以调用方通常会把 suffix 写进 gaps。
    """
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text

    # 给后缀预留预算，避免截断后刚好超出上限。
    budget = max(1, max_tokens - estimate_tokens(suffix))
    out = ""
    for sentence in _SENTENCE_END_RE.split(text):
        candidate = f"{out} {sentence}".strip()
        if estimate_tokens(candidate) > budget:
            break
        out = candidate
    if not out:
        # 单个超长句子（例如一行没有标点的表格）：按词回退，保证不超预算。
        for word in text.split():
            candidate = f"{out} {word}".strip()
            if estimate_tokens(candidate) > budget:
                break
            out = candidate
        if not out:
            out = text[: max(1, len(text) // 10)].rstrip()
    return out.rstrip() + suffix
