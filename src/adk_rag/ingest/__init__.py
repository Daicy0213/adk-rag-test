"""ingest 子包：发现、解析、分块、精确词抽取、校验、写批次。"""

from .discover import DomainConfig, KnowledgeDoc, discover_docs, load_catalog, load_domain
from .exact_terms import extract_exact_terms, normalize_term
from .parse_md import Block, SectionDraft, parse_file, parse_markdown
from .pipeline import (
    active_batches,
    batch_report,
    build_document,
    ingest,
    promote_batch,
    rollback,
    section_id_for,
)

__all__ = [
    "Block",
    "DomainConfig",
    "KnowledgeDoc",
    "SectionDraft",
    "active_batches",
    "batch_report",
    "build_document",
    "discover_docs",
    "extract_exact_terms",
    "ingest",
    "load_catalog",
    "load_domain",
    "normalize_term",
    "parse_file",
    "parse_markdown",
    "promote_batch",
    "rollback",
    "section_id_for",
]
