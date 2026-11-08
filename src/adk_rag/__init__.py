"""adk_rag：方案 A POC —— PostgreSQL FTS 稀疏倒排索引的英文知识检索。

对外入口（计划 12.6 的约定）：
    service.RagService.retrieve(...)   -> EvidencePack
    retrieve.retriever.PgRetriever     -> 同步实现（评估/测试直接用）
    adk_adapter.KnowledgeInjector      -> ADK before_model_callback 适配

不包含：embedding、向量索引、Tantivy/Whoosh、UI、多租户。
"""

from .config import Settings, load_settings
from .models import Evidence, EvidencePack

__all__ = ["Evidence", "EvidencePack", "Settings", "load_settings"]
