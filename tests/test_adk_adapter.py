"""ADK 适配层测试（需要 `uv sync --extra adk`，未安装时自动 skip）。

用真实的 `LlmRequest` / `types.Content` 验证注入位置与幂等，用假 service 避免依赖数据库。
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

pytest.importorskip("google.adk")

from google.adk.models import LlmRequest  # noqa: E402
from google.genai import types  # noqa: E402

from adk_rag.adk_adapter import (  # noqa: E402
    EVIDENCE_MARKER,
    STATE_CITATIONS,
    STATE_KNOWLEDGE_VERSION,
    KnowledgeInjector,
)
from adk_rag.models import Evidence, EvidencePack  # noqa: E402


class FakeContext:
    def __init__(self, state: dict | None = None) -> None:
        self.state = state if state is not None else {}


class FakeService:
    def __init__(self, pack: EvidencePack | None = None, error: Exception | None = None) -> None:
        self.pack = pack if pack is not None else make_pack()
        self.error = error
        self.calls: list[dict] = []

    async def retrieve(self, query: str, *, domains, user_groups, as_of=None, max_evidence_tokens=None):
        self.calls.append(
            {
                "query": query,
                "domains": list(domains),
                "user_groups": list(user_groups),
                "as_of": as_of,
                "max_evidence_tokens": max_evidence_tokens,
            }
        )
        if self.error is not None:
            raise self.error
        return self.pack


def make_pack() -> EvidencePack:
    return EvidencePack(
        query="q",
        rewritten_query="q",
        evidence=[
            Evidence(
                evidence_id="E1",
                chunk_id="chunk-1",
                parent_id="sec-1",
                domain_id="deposit",
                doc_id="deposit-cd-001",
                title="Large-Denomination CD Policy",
                version="3.2",
                heading_path="Policy > 3 Early Withdrawal",
                text="The depositor must provide the original certificate.",
                source_url="https://intranet.example.com/deposit/cd-001",
                score=0.02,
                section_ids=["sec-1"],
            )
        ],
        citation_ids=["E1"],
        knowledge_version="deposit@batch-3",
        token_count=30,
        confidence="high",
    )


def make_request(user_texts: list[str]) -> LlmRequest:
    return LlmRequest(
        contents=[
            types.Content(role="user", parts=[types.Part.from_text(text=text)])
            for text in user_texts
        ]
    )


def run(coro):
    return asyncio.run(coro)


def test_injects_evidence_into_request():
    service = FakeService()
    injector = KnowledgeInjector(service, domains=["deposit"], user_groups=["role:retail_business"])
    context = FakeContext()
    request = make_request(["What documents are required for early withdrawal?"])

    result = run(injector(context, request))

    assert result is None  # 不短路模型调用
    assert len(request.contents) == 2
    injected = request.contents[-1].parts[0].text
    assert EVIDENCE_MARKER in injected
    assert "[E1]" in injected
    assert context.state[STATE_CITATIONS] == ["E1"]
    assert context.state[STATE_KNOWLEDGE_VERSION] == "deposit@batch-3"
    assert service.calls[0]["domains"] == ["deposit"]
    assert service.calls[0]["user_groups"] == ["role:retail_business"]


def test_same_turn_is_idempotent():
    service = FakeService()
    injector = KnowledgeInjector(service, domains=["deposit"], user_groups=["role:retail_business"])
    context = FakeContext()
    request = make_request(["What documents are required?"])

    run(injector(context, request))
    run(injector(context, request))  # 模拟工具调用后的第二次模型调用

    assert len(request.contents) == 2
    assert len(service.calls) == 1


def test_new_user_turn_injects_again():
    service = FakeService()
    injector = KnowledgeInjector(service, domains=["deposit"], user_groups=["role:retail_business"])
    context = FakeContext()
    request = make_request(["First question about documents?"])

    run(injector(context, request))
    # 用户追问：request.contents 增加一条 user 消息
    request.contents.append(
        types.Content(role="user", parts=[types.Part.from_text(text="What if a representative handles it?")])
    )
    run(injector(context, request))

    assert len(service.calls) == 2
    assert service.calls[1]["query"] == "What if a representative handles it?"


def test_empty_pack_does_not_inject():
    service = FakeService(pack=EvidencePack(query="q", rewritten_query="q", gaps=["no_match"]))
    injector = KnowledgeInjector(service, domains=["deposit"], user_groups=["role:retail_business"])
    context = FakeContext()
    request = make_request(["What is the CD rate for 2035?"])

    run(injector(context, request))

    assert len(request.contents) == 1
    assert EVIDENCE_MARKER not in request.contents[0].parts[0].text


def test_retrieval_error_degrades_silently():
    service = FakeService(error=RuntimeError("database down"))
    injector = KnowledgeInjector(service, domains=["deposit"], user_groups=["role:retail_business"])
    context = FakeContext()
    request = make_request(["What documents are required?"])

    run(injector(context, request))  # 不应抛出

    assert len(request.contents) == 1


def test_small_talk_skips_retrieval():
    service = FakeService()
    injector = KnowledgeInjector(service, domains=["deposit"], user_groups=["role:retail_business"])
    context = FakeContext()

    run(injector(context, make_request(["hello"])))

    assert service.calls == []


def test_active_domains_from_state_override_default():
    service = FakeService()
    injector = KnowledgeInjector(service, domains=["deposit"], user_groups=["role:retail_business"])
    context = FakeContext({"rag:active_domains": ["fx", "aml"]})

    run(injector(context, make_request(["What is the remittance limit?"])))

    assert service.calls[0]["domains"] == ["fx", "aml"]


def test_user_groups_callable_receives_context():
    service = FakeService()
    injector = KnowledgeInjector(
        service,
        domains=["deposit"],
        user_groups=lambda ctx: ctx.state.get("user_groups", []),
    )
    context = FakeContext({"user_groups": ["role:branch_teller"]})

    run(injector(context, make_request(["What documents are required?"])))

    assert service.calls[0]["user_groups"] == ["role:branch_teller"]


def test_injector_requires_domains():
    with pytest.raises(ValueError):
        KnowledgeInjector(FakeService(), domains=[], user_groups=["role:retail_business"])


def test_as_of_is_configurable():
    service = FakeService()
    injector = KnowledgeInjector(
        service,
        domains=["deposit"],
        user_groups=["role:retail_business"],
        as_of=date(2026, 11, 8),
    )
    run(injector(FakeContext(), make_request(["What documents are required?"])))

    assert service.calls[0]["as_of"] == date(2026, 11, 8)
