"""配置加载：YAML 文件 + 环境变量，缺省值内置。

设计要点：
- 配置是**数据**，不是代码常量，方便按域/环境调整；
- 环境变量只覆盖连接串这类部署差异，避免把秘密写进仓库；
- 读到不合法的组合（例如 min_chunk_tokens > max_chunk_tokens）时立刻报错，
  不要等到分块阶段才出现难以解释的空块。
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

# src/adk_rag/config.py -> adk-rag/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.yaml"


def project_root() -> Path:
    """工程根目录；允许用 ADK_RAG_ROOT 覆盖（测试/容器里很有用）。"""
    override = os.getenv("ADK_RAG_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return PROJECT_ROOT


class PostgresSettings(BaseModel):
    dsn_env: str = Field(default="PG_DSN", description="存放连接串的环境变量名")
    default_dsn: str = "postgresql://adk_rag:adk_rag@localhost:5433/adk_rag"
    schema_name: str = Field(default="public", alias="schema")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _public_only(self) -> "PostgresSettings":
        # 迁移与所有 SQL 都按 public 写死；允许填别的 schema 只会让人误以为生效。
        if self.schema_name != "public":
            raise ValueError(
                "POC 只支持 public schema；自定义 schema 需要同步修改 migrations 与所有 SQL"
            )
        return self


class TimeoutSettings(BaseModel):
    query: int = Field(default=1500, description="单次检索的 statement_timeout（毫秒）")


class RetrievalSettings(BaseModel):
    fts_top_k: int = 80
    exact_top_k: int = 20
    final_parents: int = 8
    per_domain_parents: int = 4
    max_evidence_tokens: int = 6000
    ts_rank_normalization: int = 32
    rrf_k: int = 60
    exact_boost: float = 1.0
    parent_score: str = Field(default="max", description="max 取最强子块；sum 累加全部子块")
    or_fallback_min_candidates: int = 3
    timeouts_ms: TimeoutSettings = Field(default_factory=TimeoutSettings)

    @model_validator(mode="after")
    def _check(self) -> "RetrievalSettings":
        if self.rrf_k <= 0:
            raise ValueError("retrieval.rrf_k 必须为正数（常用 60）")
        if self.parent_score not in {"max", "sum"}:
            raise ValueError("retrieval.parent_score 只能是 max 或 sum")
        if self.final_parents <= 0 or self.per_domain_parents <= 0:
            raise ValueError("final_parents / per_domain_parents 必须为正数")
        return self


class IngestSettings(BaseModel):
    knowledge_root: str = "knowledge"
    default_doc_type: str = "policy"
    target_chunk_tokens: int = 350
    max_chunk_tokens: int = 500
    min_chunk_tokens: int = 100
    overlap_tokens: int = 40
    list_items_per_chunk: int = 6
    table_rows_per_chunk: int = 6
    parent_target_tokens: int = 1000
    parent_max_tokens: int = 1200

    @model_validator(mode="after")
    def _check(self) -> "IngestSettings":
        if not (self.min_chunk_tokens < self.target_chunk_tokens < self.max_chunk_tokens):
            raise ValueError("需要满足 min_chunk_tokens < target_chunk_tokens < max_chunk_tokens")
        if self.overlap_tokens >= self.min_chunk_tokens:
            raise ValueError("overlap_tokens 必须明显小于 min_chunk_tokens")
        return self


class Settings(BaseModel):
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)

    def dsn(self) -> str:
        """连接串：优先环境变量，其次配置文件里的兜底值。"""
        env_name = self.postgres.dsn_env
        value = os.getenv(env_name) or self.postgres.default_dsn
        if not value:
            raise RuntimeError(f"未配置 PostgreSQL 连接串：请设置环境变量 {env_name}")
        return value

    def knowledge_root(self) -> Path:
        return project_root() / self.ingest.knowledge_root


def load_settings(config_path: str | Path | None = None) -> Settings:
    """读取配置；文件不存在时退回内置默认值（并允许纯环境变量运行）。"""
    path = Path(config_path) if config_path else Path(
        os.getenv("ADK_RAG_CONFIG") or DEFAULT_CONFIG_PATH
    )
    data: dict = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"配置文件必须是 YAML 映射：{path}")
        data = raw
    return Settings.model_validate(data)
