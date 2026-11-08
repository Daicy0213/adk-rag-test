"""检索子包：normalize、FTS、exact、融合、父级聚合、上下文装配。"""

from .aggregate import ParentGroup, aggregate_parents, fuse_rrf
from .exact import search_exact
from .fts import search_fts, search_fts_with_fallback, tsquery_numnode
from .normalize import fts_terms, normalize_query, or_fallback_query
from .pack import build_pack
from .retriever import PgRetriever

__all__ = [
    "ParentGroup",
    "PgRetriever",
    "aggregate_parents",
    "build_pack",
    "fts_terms",
    "fuse_rrf",
    "normalize_query",
    "or_fallback_query",
    "search_exact",
    "search_fts",
    "search_fts_with_fallback",
    "tsquery_numnode",
]
