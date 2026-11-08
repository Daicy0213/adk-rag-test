-- 方案 A POC 的初始 schema。
--
-- 设计来源：docs/方案A实施计划.md 第 6 章（索引定义）。
-- 本文件必须**可重复执行**（幂等）：不要写不可重入的 DDL。
--
-- 与计划文档的差异（已记入 docs/adr-001-poc-boundaries.md）：
--   1. document / section / chunk 的主键改为 (id, batch_id)。
--      原因：计划要求"每域 staging batch + 原子切换 + 5 分钟内回滚"，旧批次必须与新批次
--      同时存在于表里；若按单列 id 做主键，重新 ingest 相同内容会因 chunk_id 稳定而冲突，
--      只能覆盖旧行，回滚就无数据可指。业务 ID（chunk_id / doc_id@version / section_id）
--      仍然稳定，只是物理行多了 batch 维度。
--   2. domain 表增加 previous_batch_id，让 `rollback` 有确定的回退目标，不靠时间猜测。
--   3. document 表增加 source_url，证据包需要可核验的引用来源。
--
-- 这里不用 `CREATE EXTENSION`：方案 A 只依赖 PostgreSQL 内置的 tsvector/GIN 与 english 配置。

BEGIN;

CREATE TABLE IF NOT EXISTS domain (
  id                text PRIMARY KEY,
  name              text NOT NULL,
  description       text NOT NULL DEFAULT '',
  -- 域卡片：scope_in/scope_out/keywords/sample_queries 等，运行时只读 DB。
  card              jsonb NOT NULL DEFAULT '{}'::jsonb,
  acl_groups        text[] NOT NULL DEFAULT '{}',
  -- 唯一活跃批次；查询用 JOIN domain ... active_batch_id = chunk.batch_id 过滤。
  active_batch_id   bigint,
  -- 上一个活跃批次，供 rollback 使用；不依赖 ingest_batch 的时间顺序推断。
  previous_batch_id bigint,
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ingest_batch (
  id          bigserial PRIMARY KEY,
  domain_id   text NOT NULL REFERENCES domain(id),
  label       text,
  -- staging: 写入中；validated: 质量门禁通过；active: 线上；retired: 已下线但保留；failed: 门禁失败
  status      text NOT NULL
              CHECK (status IN ('staging', 'validated', 'active', 'retired', 'failed')),
  stats       jsonb NOT NULL DEFAULT '{}'::jsonb,
  started_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz
);

CREATE INDEX IF NOT EXISTS ingest_batch_domain_btree ON ingest_batch (domain_id, id DESC);

CREATE TABLE IF NOT EXISTS document (
  id             text NOT NULL,               -- 物理行 ID：doc_id@version
  batch_id       bigint NOT NULL REFERENCES ingest_batch(id),
  doc_id         text NOT NULL,               -- 业务稳定 ID（跨版本不变）
  domain_id      text NOT NULL REFERENCES domain(id),
  path           text NOT NULL,               -- knowledge/deposit/xx.md（相对工程根）
  title          text NOT NULL,
  version        text NOT NULL,
  doc_type       text NOT NULL,               -- policy | faq | product | notice
  status         text NOT NULL
                 CHECK (status IN ('draft', 'scheduled', 'active', 'archived')),
  effective_from date,
  effective_to   date,
  classification text NOT NULL,
  acl_groups     text[] NOT NULL DEFAULT '{}',
  content_hash   text NOT NULL,
  parser_version text NOT NULL,
  source_url     text,
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id, batch_id),
  UNIQUE (domain_id, doc_id, version, batch_id)
);

CREATE TABLE IF NOT EXISTS section (
  id           text NOT NULL,
  batch_id     bigint NOT NULL REFERENCES ingest_batch(id),
  document_id  text NOT NULL,
  parent_id    text,
  heading_path text NOT NULL,
  level        int NOT NULL,
  -- 文档内全局顺序（不是同级序号），用于还原原文顺序。
  ordinal      int NOT NULL,
  -- 只存本节自身内容；父级上下文由 parent_id + ordinal 在检索时拼装。
  text         text NOT NULL,
  token_count  int NOT NULL,
  PRIMARY KEY (id, batch_id),
  FOREIGN KEY (document_id, batch_id) REFERENCES document (id, batch_id) ON DELETE CASCADE,
  FOREIGN KEY (parent_id, batch_id) REFERENCES section (id, batch_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS section_parent_btree ON section (parent_id);
CREATE INDEX IF NOT EXISTS section_document_btree ON section (document_id);

CREATE TABLE IF NOT EXISTS chunk (
  id              text NOT NULL,
  batch_id        bigint NOT NULL REFERENCES ingest_batch(id),
  domain_id       text NOT NULL REFERENCES domain(id),
  document_id     text NOT NULL,
  section_id      text NOT NULL,
  -- 父级聚合的 group key：通常是最近的 H2 祖先 section，没有则等于 section_id。
  parent_id       text,
  ordinal         int NOT NULL,
  doc_type        text NOT NULL,
  heading_path    text NOT NULL,
  text            text NOT NULL,
  token_count     int NOT NULL,
  status          text NOT NULL
                  CHECK (status IN ('draft', 'scheduled', 'active', 'archived')),
  effective_from  date,
  effective_to    date,
  classification  text NOT NULL,
  acl_groups      text[] NOT NULL DEFAULT '{}',
  -- keyword-like 通道：文号/产品代码/金额/日期/比例，只有归一化后的形式，不做 stemming。
  exact_terms     text[] NOT NULL DEFAULT '{}',
  content_hash    text NOT NULL,
  chunker_version text NOT NULL,
  source_start    int,
  source_end      int,
  tsv             tsvector,
  PRIMARY KEY (id, batch_id),
  FOREIGN KEY (document_id, batch_id) REFERENCES document (id, batch_id) ON DELETE CASCADE,
  FOREIGN KEY (section_id, batch_id) REFERENCES section (id, batch_id) ON DELETE CASCADE
);

-- 倒排索引：正文全文检索
CREATE INDEX IF NOT EXISTS chunk_tsv_gin ON chunk USING gin (tsv);
-- keyword-like：精确词
CREATE INDEX IF NOT EXISTS chunk_exact_gin ON chunk USING gin (exact_terms);
-- 结构化过滤
CREATE INDEX IF NOT EXISTS chunk_filter_btree
  ON chunk (domain_id, status, effective_from, effective_to);
-- ACL 过滤
CREATE INDEX IF NOT EXISTS chunk_acl_gin ON chunk USING gin (acl_groups);
-- 父级聚合
CREATE INDEX IF NOT EXISTS chunk_parent_btree ON chunk (parent_id);
-- 批次清理/切换
CREATE INDEX IF NOT EXISTS chunk_batch_btree ON chunk (batch_id);

-- tsv 由 trigger 维护：heading_path 权重 A，正文权重 B。
-- 用 trigger 而不是 generated column：权重/字段可随时调整，回填也简单（计划 6.6）。
CREATE OR REPLACE FUNCTION update_chunk_tsv() RETURNS trigger AS $$
BEGIN
  NEW.tsv :=
    setweight(to_tsvector('english', coalesce(NEW.heading_path, '')), 'A') ||
    setweight(to_tsvector('english', coalesce(NEW.text, '')), 'B');
  RETURN NEW;
END
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_chunk_tsv ON chunk;
CREATE TRIGGER trg_chunk_tsv
BEFORE INSERT OR UPDATE OF heading_path, text ON chunk
FOR EACH ROW EXECUTE FUNCTION update_chunk_tsv();

COMMIT;
