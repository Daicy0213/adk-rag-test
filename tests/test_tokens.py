"""token 估算与预算截断测试（计划 11.1 的 token 计数）。"""

from __future__ import annotations

from adk_rag.tokens import estimate_tokens, raw_token_estimate, truncate_to_tokens


def test_empty_text_is_zero():
    assert estimate_tokens("") == 0
    assert estimate_tokens("   \n\t ") == 0
    assert raw_token_estimate("") == 0


def test_estimate_grows_with_text():
    short = estimate_tokens("Early withdrawal.")
    long = estimate_tokens("Early withdrawal of a large-denomination certificate of deposit.")
    assert long > short > 0


def test_estimate_is_conservative_for_english_prose():
    # 计划 9.3 的经验值：约 4 字符/token；估算应落在同一量级（带 15% 余量）
    text = "The depositor must provide the original certificate and a completed application form."
    tokens = estimate_tokens(text)
    assert len(text) / 6 <= tokens <= len(text) / 3


def test_truncate_respects_budget_and_keeps_sentence_boundary():
    text = (
        "First sentence about the cooling-off period. "
        "Second sentence about the minimum balance. "
        "Third sentence about the early withdrawal fee. "
        "Fourth sentence about notice requirements."
    )
    truncated = truncate_to_tokens(text, 25)
    assert estimate_tokens(truncated) <= 26  # 允许后缀 1 token 的浮动
    assert truncated.endswith(" …")
    assert truncated.startswith("First sentence")


def test_truncate_returns_text_when_within_budget():
    text = "Short text."
    assert truncate_to_tokens(text, 100) == text


def test_truncate_with_zero_budget_returns_empty():
    assert truncate_to_tokens("anything", 0) == ""


def test_truncate_handles_text_without_sentence_breaks():
    text = "| a | b | " * 50
    truncated = truncate_to_tokens(text, 20)
    assert estimate_tokens(truncated) <= 21
