"""`adk web` / `adk run` 入口测试。

跳过条件：未装 `adk` extra（`uv sync --extra adk`）时自动 skip，
这样 `uv sync` 的默认环境仍然能跑完整测试套件。
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("google.adk")
pytest.importorskip("litellm")

from adk_rag.adk_adapter import KnowledgeInjector  # noqa: E402
from google.adk.cli.utils.agent_loader import AgentLoader  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_root_agent_has_injector_wired():
    from knowledge_agent.agent import root_agent

    assert root_agent.name == "deposit_knowledge_agent"
    assert isinstance(root_agent.before_model_callback, KnowledgeInjector)
    assert root_agent.before_model_callback.domains == ["deposit"]
    assert root_agent.before_model_callback.token_budget == 6000


def test_agent_directory_is_discoverable_by_adk():
    """`adk web .` 用同一套 AgentLoader 扫描子目录。"""
    loader = AgentLoader(str(PROJECT_ROOT))
    assert "knowledge_agent" in loader.list_agents()


def test_loader_can_load_the_agent():
    loader = AgentLoader(str(PROJECT_ROOT))
    agent = loader.load_agent("knowledge_agent")
    assert agent.name == "deposit_knowledge_agent"
    assert callable(agent.before_model_callback)
