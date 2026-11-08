"""检索 SQL 集中定义。

把列清单与过滤条件放在一处，是为了避免"FTS 路过滤了 ACL、exact 路忘了过滤"这种
越权漏洞——计划 12.5 要求 ACL 在 SQL 里硬过滤，两条通道必须完全一致。

所有过滤都在 SQL 完成，不做后置过滤：
    c.domain_id = ANY(...)          域
    c.status = 'active'             状态
    effective_from/to               时间（左闭右开）
    c.acl_groups && user_groups     ACL
    dm.active_batch_id = c.batch_id 只读活跃批次
"""

from __future__ import annotations

CANDIDATE_COLUMNS = """
    c.id            AS chunk_id,
    c.batch_id      AS batch_id,
    c.section_id    AS section_id,
    c.parent_id     AS parent_id,
    c.domain_id     AS domain_id,
    c.document_id   AS document_id,
    d.doc_id        AS doc_id,
    d.title         AS title,
    d.version       AS version,
    c.heading_path  AS heading_path,
    c.text          AS text,
    c.token_count   AS token_count,
    c.status        AS status,
    c.effective_from AS effective_from,
    c.effective_to  AS effective_to,
    c.classification AS classification,
    c.acl_groups    AS acl_groups,
    c.exact_terms   AS exact_terms,
    d.source_url    AS source_url
"""

FROM_CLAUSE = """
    FROM chunk c
    JOIN document d ON d.id = c.document_id AND d.batch_id = c.batch_id
    JOIN domain dm ON dm.id = c.domain_id AND dm.active_batch_id = c.batch_id
"""

FILTERS = """
    c.domain_id = ANY(%(domains)s::text[])
    AND c.status = 'active'
    AND (c.effective_from IS NULL OR c.effective_from <= %(as_of)s)
    AND (c.effective_to IS NULL OR c.effective_to > %(as_of)s)
    AND c.acl_groups && %(user_groups)s::text[]
"""

TSQUERY_NUMNODE = """
SELECT numnode(websearch_to_tsquery('english', %(query)s)) AS numnode
"""

# ts_rank_cd 的第三个参数是 normalization flag（不是阈值）；用 CTE 只解析一次 tsquery。
FTS_SEARCH = f"""
WITH q AS (
    SELECT websearch_to_tsquery('english', %(query)s) AS tsq
)
SELECT {CANDIDATE_COLUMNS},
       ts_rank_cd(c.tsv, q.tsq, %(normalization)s::int) AS score
{FROM_CLAUSE}
CROSS JOIN q
WHERE c.tsv @@ q.tsq
  AND {FILTERS}
ORDER BY score DESC, c.id
LIMIT %(limit)s
"""

# exact 通道按"命中多少个精确词"排序；同样词数时用 id 保证结果稳定。
EXACT_SEARCH = f"""
SELECT {CANDIDATE_COLUMNS},
       (SELECT count(*) FROM unnest(c.exact_terms) AS term
         WHERE term = ANY(%(terms)s::text[]))::float AS score
{FROM_CLAUSE}
WHERE c.exact_terms && %(terms)s::text[]
  AND {FILTERS}
ORDER BY score DESC, c.id
LIMIT %(limit)s
"""

# 父级上下文：父 section 自身的文本 + 其直接子 section 的文本，按文档顺序拼装。
SECTION_CONTEXT = """
SELECT id, batch_id, parent_id, heading_path, level, ordinal, text, token_count
FROM section
WHERE batch_id = %(batch_id)s
  AND (id = ANY(%(ids)s::text[]) OR parent_id = ANY(%(ids)s::text[]))
ORDER BY ordinal
"""

ACTIVE_VERSIONS = """
SELECT id, active_batch_id
FROM domain
WHERE id = ANY(%(domains)s::text[])
"""
