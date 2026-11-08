"""ADK 应用入口：把稀疏检索接到一个 LlmAgent 上。

为什么单独放一个包（而不是塞进 `src/adk_rag/`）：
`adk web [AGENTS_DIR]` 的发现规则是"AGENTS_DIR 下每个含 `agent.py` 的子目录 = 一个 agent"，
它扫描的是**工程目录结构**，不是已安装的 Python 包。所以入口必须是一个真实的目录。

这个文件是"检索库"与"Agent"之间唯一的粘合层：
- 检索由 `KnowledgeInjector`（before_model_callback）决定，**不交给模型自己决定查不查**；
- 回答模型用 DeepSeek（与仓库其它示例一致），通过 LiteLlm 接入；
- 检索失败/超时会降级为"不注入证据"，不会让对话崩掉。

运行前置：
1. `docker compose up -d` + `ingest ... --promote`（否则证据为空，Agent 会说没找到依据）；
2. 环境变量 `DEEPSEEK_API_KEY`。

可用环境变量覆盖默认值：
    ADK_RAG_MODEL=deepseek/deepseek-chat
    ADK_RAG_DOMAINS=deposit,aml
    ADK_RAG_USER_GROUPS=role:retail_business,role:branch_teller
    ADK_RAG_TOKEN_BUDGET=6000
"""

from __future__ import annotations

import os

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm

from adk_rag.adk_adapter import KnowledgeInjector
from adk_rag.service import RagService

INSTRUCTION = """You are the knowledge assistant for the bank's deposit products.

Rules:
1. Answer only from the <knowledge_evidence> block injected by the system.
2. Cite every factual claim with the evidence id, e.g. [E1].
3. If the evidence block is missing or does not contain the answer, say that the
   internal knowledge base does not cover it, and suggest what to check next.
   Never fill the gap with general knowledge, and never guess numbers, rates,
   dates, circular numbers or product codes.
4. When the evidence states a condition (for example "if an authorized
   representative handles the withdrawal"), repeat the condition rather than
   presenting it as the general rule.
5. Keep answers concise; use the user's language.
"""


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return list(default)
    items = [item.strip() for item in raw.split(",") if item.strip()]
    return items or list(default)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(raw) if raw else default
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数，实际为 {raw!r}") from exc


root_agent = Agent(
    name="deposit_knowledge_agent",
    model=LiteLlm(model=os.getenv("ADK_RAG_MODEL", "deepseek/deepseek-chat")),
    description="银行个人存款与大额存单知识助手（答案必须引用内部知识库证据）",
    instruction=INSTRUCTION,
    before_model_callback=KnowledgeInjector(
        RagService(),
        domains=_env_list("ADK_RAG_DOMAINS", ["deposit"]),
        user_groups=_env_list("ADK_RAG_USER_GROUPS", ["role:retail_business"]),
        token_budget=_env_int("ADK_RAG_TOKEN_BUDGET", 6000),
    ),
)
