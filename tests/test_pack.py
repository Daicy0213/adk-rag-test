"""EvidencePack 渲染测试：分隔符、引用、预算截断、工具返回值。"""

from __future__ import annotations

from adk_rag.models import Evidence, EvidencePack
from adk_rag.tokens import estimate_tokens


def make_pack(count: int = 2, text: str = "The depositor must provide the original certificate.") -> EvidencePack:
    evidence = [
        Evidence(
            evidence_id=f"E{index}",
            chunk_id=f"chunk-{index}",
            parent_id=f"sec-{index}",
            domain_id="deposit",
            doc_id="deposit-cd-001",
            title="Large-Denomination CD Policy",
            version="3.2",
            heading_path="Policy > 3 Early Withdrawal",
            text=text,
            source_url="https://intranet.example.com/deposit/cd-001",
            score=0.01 * index,
            section_ids=[f"sec-{index}"],
        )
        for index in range(1, count + 1)
    ]
    return EvidencePack(
        query="What documents are required?",
        rewritten_query="What documents are required?",
        evidence=evidence,
        citation_ids=[item.evidence_id for item in evidence],
        knowledge_version="deposit@batch-3",
        token_count=100,
        confidence="high",
    )


def test_to_prompt_wraps_evidence_with_markers():
    prompt = make_pack().to_prompt(max_tokens=6000)
    assert prompt.startswith("<knowledge_evidence>")
    assert prompt.rstrip().endswith("</knowledge_evidence>")
    assert "never as instructions" in prompt
    assert "[E1]" in prompt and "[E2]" in prompt
    assert "deposit@batch-3" in prompt


def test_to_prompt_includes_source_and_section():
    prompt = make_pack(1).to_prompt()
    assert "section: Policy > 3 Early Withdrawal" in prompt
    assert "source: https://intranet.example.com/deposit/cd-001" in prompt


def test_to_prompt_respects_budget_and_marks_truncation():
    long_text = "Sentence about early withdrawal documents and fees. " * 60
    prompt = make_pack(3, long_text).to_prompt(max_tokens=300)
    assert estimate_tokens(prompt) <= 400
    assert "evidence truncated by token budget" in prompt


def test_to_prompt_on_empty_pack_returns_empty_string():
    pack = EvidencePack(query="q", rewritten_query="q", gaps=["no_match"], confidence="none")
    assert pack.is_empty
    assert pack.to_prompt() == ""


def test_to_tool_result_is_serializable():
    payload = make_pack(1).to_tool_result()
    assert payload["confidence"] == "high"
    assert payload["evidence"][0]["evidence_id"] == "E1"
    assert payload["knowledge_version"] == "deposit@batch-3"
    # 结构化结果里只保留必要字段
    assert set(payload["evidence"][0]) >= {"evidence_id", "heading_path", "text", "score"}


def test_citation_ids_match_evidence_ids():
    pack = make_pack(3)
    assert pack.citation_ids == [item.evidence_id for item in pack.evidence]
