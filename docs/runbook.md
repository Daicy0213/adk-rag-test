# 运行手册（runbook）

适用对象：第一次接手 `adk-rag` 的工程师/SRE。命令都在 `adk-rag/` 目录下执行。

---

## 1. 前置检查

```bash
docker version        # Docker 服务可用
uv --version          # 依赖管理用 uv
python --version      # 3.12（uv 会按 requires-python 选择）
```

PG 镜像：默认 `pgvector/pgvector:0.8.6-pg17`（任何 PG 17 镜像均可）。
内网无法拉镜像时，先 `docker pull` 到本机，或用 `ADK_RAG_PG_IMAGE` 指定已有镜像。

## 2. 首次启动

```bash
docker compose up -d                 # 起 PG（宿主端口 5433）
uv sync                              # 建 .venv
uv run python -m adk_rag.ingest.cli init-db    # 建表 + GIN + trigger
uv run python -m adk_rag.ingest.cli --domain deposit --batch-label deposit-poc-001 --promote
uv run python -m adk_rag.eval --golden eval/golden.jsonl --domains deposit --validate-gold
```

确认三件事：

```bash
docker ps --filter name=adk-rag-pg                  # healthy
uv run python -m adk_rag.ingest.cli report --batch-id <id>   # 批次 active
uv run python -m adk_rag.eval --limit 3              # 能跑出指标
```

## 3. 知识更新（不发应用版）

```bash
# 1) 改/加 knowledge/deposit/*.md（front matter 是元数据唯一来源）
# 2) 先 dry-run 看分块与告警
uv run python -m adk_rag.ingest.cli --domain deposit --dry-run

# 3) 写入新的 staging 批次（不影响线上）
uv run python -m adk_rag.ingest.cli --domain deposit --batch-label deposit-2026-12

# 4) 看质量报告：行数、每篇 chunk 数、抽样、告警
uv run python -m adk_rag.ingest.cli report --batch-id <new_id>

# 5) 冒烟：用 3–5 条典型问题确认新批次可答
# 6) 原子切换（只改指针，不搬运数据）
uv run python -m adk_rag.ingest.cli promote --domain deposit --batch-id <new_id>
```

`promote` 完成后新查询立刻生效，旧批次保留为 `retired`。

## 4. 回滚（目标 < 5 分钟）

```bash
uv run python -m adk_rag.ingest.cli rollback --domain deposit
```

回滚只把 `domain.active_batch_id` 指回 `previous_batch_id`，不重建索引。
注意：回滚一次后 `previous_batch_id` 会被清空（再回滚需要显式 `promote` 到目标批次）。

验证：

```bash
uv run python -m adk_rag.ingest.cli report --batch-id <old_id>   # 显示"该批次当前是活跃批次"
```

## 5. 日常巡检

| 项 | 命令 | 关注点 |
|---|---|---|
| 批次状态 | `SELECT id,label,status,stats->>'chunks' FROM ingest_batch ORDER BY id DESC LIMIT 5;` | 是否有 failed/长期 staging |
| 活跃指针 | `SELECT id, active_batch_id, previous_batch_id FROM domain;` | 指针是否符合预期 |
| 残留 staging | `SELECT * FROM ingest_batch WHERE status='staging' AND started_at < now() - interval '1 hour';` | 进程中断留下的半成品批次 |
| tsv 完整性 | `SELECT count(*) FROM chunk WHERE tsv IS NULL;` | 非 0 说明 trigger 异常 |
| GIN 体积 | `SELECT pg_size_pretty(pg_relation_size('chunk_tsv_gin'));` | 与 chunk 数同步增长 |
| 索引使用 | `EXPLAIN (ANALYZE, BUFFERS) <查询>` | 小表走 seq scan 是正常的 |
| 慢查询 | `log_min_duration_statement=200`（compose 已开） | 超过 200ms 的 SQL |
| 批次膨胀 | `SELECT batch_id, count(*) FROM chunk GROUP BY 1 ORDER BY 1 DESC;` | 旧批次是否该归档 |

> `ingest` 进程被杀会留下 `status='staging'` 的批次（数据仍在 staging，查询看不到）。
> 处理方式是重新 ingest 一个新批次，或把残留批次标为 failed：
> `UPDATE ingest_batch SET status='failed', finished_at=now() WHERE id=<id>;`
> `promote` 会拒绝 staging 批次，不会误把半成品切上线。

GIN 写入后会进入 pending list；大量更新后执行：

```sql
SELECT gin_clean_pending_list('chunk_tsv_gin');
VACUUM (ANALYZE) chunk;
```

索引膨胀时：`REINDEX INDEX CONCURRENTLY chunk_tsv_gin;`（生产用 CONCURRENTLY）。

## 6. 排错

### 6.1 查询一直是空的

按顺序排查：

```sql
-- 域是否存在、指针是否为空
SELECT id, active_batch_id FROM domain;
-- 批次里到底有没有行
SELECT batch_id, count(*) FROM chunk GROUP BY 1;
-- 过滤条件是不是太严（ACL / status / effective）
SELECT status, count(*) FROM chunk GROUP BY 1;
SELECT count(*) FROM chunk WHERE 'role:retail_business' = ANY(acl_groups);
```

常见原因：

| 现象 | 原因 | 处理 |
|---|---|---|
| 域存在但 `active_batch_id` 为 NULL | 只 ingest 没 promote | `promote` |
| chunk 有行但查不到 | `status` 不是 active / `effective_to` 已过期 | 检查 front matter |
| 只有某类用户查不到 | `acl_groups` 里没有该用户组 | 补 ACL 或换测试用户组 |
| 全是停用词 | 查询解析后 `numnode=0` | 见 `pack.gaps=["fts_query_empty"]` |

### 6.2 明明文档里有，却查不到

1. 看 `rag.last_trace["fts_candidates"]`：0 说明词项没对上（同义/换说法，方案 A 的已知弱项）；
2. 看 `exact_terms` 是否抽到（`SELECT exact_terms FROM chunk WHERE ...`）；
3. 检查是不是被版本/ACL 过滤（临时用 `domain_id`/`status` 直接查一次）；
4. 用 `websearch_to_tsquery('english', '<query>')` 在 psql 里看解析出的 tsquery。

### 6.3 ingest 失败

```bash
uv run python -m adk_rag.ingest.cli --domain deposit --dry-run   # 先看解析问题
```

- `front_matter_missing`：必填字段缺失（`doc_id/domain/title/version/status/classification`）；
- `domain_mismatch`：front matter 的 `domain` 与目录名不一致；
- `acl_groups_empty`：空 ACL 不匹配任何用户，按错误处理；
- `document_empty`：只有标题或全空段落；
- `gate_tsv_missing`：trigger 没生效（检查是否跳过 `init-db` 直接建表）；
- `gate_chunk_count`：写入与统计不一致，通常是并发/手工改库。

### 6.4 token 预算不够

证据包被截断时 `pack.gaps` 会包含 `evidence_truncated` / `evidence_budget_exhausted`。
先调 `retrieval.max_evidence_tokens`，再考虑减少 `final_parents`；不要直接放大到 20k+
（计划要求 4–8k）。

## 7. 备份与清理

```bash
# 逻辑备份（POC 规模很小）
docker exec adk-rag-pg pg_dump -U adk_rag adk_rag > backup.sql

# 清理旧批次（确认不再需要回滚后）
psql "$PG_DSN" -c "DELETE FROM chunk   WHERE batch_id = <old>;
                    DELETE FROM section WHERE batch_id = <old>;
                    DELETE FROM document WHERE batch_id = <old>;
                    DELETE FROM ingest_batch WHERE id = <old>;"
```

生产建议把旧批次移到 `chunk_archive` 或按 `batch_id` 分区，避免 GIN 长期包含过期行
（计划 6.9）。

## 8. 不可用时的降级

- PostgreSQL 不可用：`RagService.retrieve` 会抛 `psycopg.Error`；ADK 适配层会捕获并跳过注入，
  主对话继续（`KnowledgeInjector` 的降级路径）。
- 检索超时：`SET LOCAL statement_timeout` 默认 1500ms，超时同样走降级。
- 明确**不要**用 LLM 常识补答：证据为空时 prompt 已要求模型说明"知识库中没有"。
