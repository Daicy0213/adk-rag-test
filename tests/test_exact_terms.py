"""exact_terms 与查询归一化测试（计划 11.1）。"""

from __future__ import annotations

from adk_rag.ingest.exact_terms import compact_term, extract_exact_terms, normalize_term
from adk_rag.retrieve.normalize import fts_terms, normalize_query, or_fallback_query


def test_document_number_normalization_is_bidirectional():
    doc_terms = extract_exact_terms("Subject to Circular [2022] No. 5 and the product terms.")
    query_terms = extract_exact_terms("circular [2022] NO. 5?")

    assert "circular [2022] no. 5" in doc_terms
    assert "circular [2022] no. 5" in query_terms
    # 任意一侧的任一种写法都能命中
    assert set(doc_terms) & set(query_terms)


def test_product_code_and_space_variant():
    terms = extract_exact_terms("Product CD-2026-001 is available.")
    assert "cd-2026-001" in terms
    assert compact_term("cd-2026-001") in terms


def test_identifiers_are_not_stemmed_or_split():
    terms = extract_exact_terms("Large-denomination CD under Circular [2022] No. 5.")
    # 不能出现 "circular [2022] no" 这种被截断的形式
    assert "circular [2022] no. 5" in terms
    assert all("withdraw" not in term for term in terms)


def test_money_date_percent_patterns():
    text = "Minimum USD 100,000 from 2026-01-01 at 3.25%, about 20 million in total, on January 1, 2026."
    terms = set(extract_exact_terms(text))
    assert "usd 100,000" in terms
    assert "2026-01-01" in terms
    assert "3.25%" in terms
    assert "20 million" in terms


def test_fullwidth_characters_are_normalized():
    # 全角百分号与全角数字（NFKC 归一化）
    terms = extract_exact_terms("Rate ３.２５％ and USD １００，０００")
    assert "3.25%" in terms
    assert "usd 100,000" in terms


def test_terms_are_deduplicated_and_sorted():
    terms = extract_exact_terms("USD 100,000 and USD 100,000 again, CD-2026-001 twice CD-2026-001.")
    assert len(terms) == len(set(terms))
    assert terms == sorted(terms)


def test_empty_text_returns_no_terms():
    assert extract_exact_terms("") == []
    assert extract_exact_terms("no identifiers here") == []


def test_normalize_term_collapses_whitespace_and_case():
    assert normalize_term("  Circular   [2022]   No. 5 ") == "circular [2022] no. 5"


# --------------------------------------------------------------------------- #
# 查询归一化
# --------------------------------------------------------------------------- #
def test_normalize_query_keeps_case_for_websearch_operators():
    query = "early withdrawal  OR   redemption"
    normalized = normalize_query(query)
    assert normalized == "early withdrawal OR redemption"
    # 小写化会破坏 websearch_to_tsquery 的 OR 操作符，所以必须保留大小写
    assert "OR" in normalized


def test_normalize_query_nfkc_and_whitespace():
    assert normalize_query("  Ｅａｒｌｙ\t withdrawal  ") == "Early withdrawal"
    assert normalize_query("") == ""


def test_negation_words_are_kept():
    terms = fts_terms("early withdrawal without notice unless except no")
    lowered = [term.lower() for term in terms]
    for word in ("without", "notice", "unless", "except"):
        assert word in lowered


def test_or_fallback_query_is_joined_with_or():
    query = or_fallback_query("What documents are required for early withdrawal?")
    assert " OR " in query
    assert "documents" in query
    # 2 字符以下且非否定词的片段应被丢弃
    assert " OR a " not in f" {query} "


def test_fts_terms_dedupes_and_limits():
    terms = fts_terms("cd cd cd early early withdrawal " + " ".join(f"w{i}" for i in range(30)))
    assert len(terms) <= 12
    assert len(terms) == len(set(term.casefold() for term in terms))
