"""测试公用 fixture。

原则：单元测试不依赖 PostgreSQL；需要数据库的测试统一打 `integration` 标记，
连不上时 skip，而不是让本地 `pytest` 变成"必须先起容器"。
"""

from __future__ import annotations

import pytest

from adk_rag.config import IngestSettings, Settings, load_settings


@pytest.fixture()
def settings() -> Settings:
    """默认配置（config/default.yaml），只读，不连库。"""
    return load_settings()


@pytest.fixture()
def small_ingest_settings() -> IngestSettings:
    """小阈值配置：让分块行为在几十个 token 内就能验证，测试跑得快。"""
    return IngestSettings(
        target_chunk_tokens=60,
        max_chunk_tokens=90,
        min_chunk_tokens=20,
        overlap_tokens=10,
        list_items_per_chunk=3,
        table_rows_per_chunk=2,
    )
