# ADR-001：方案 A POC 的边界与实现偏差

- 状态：已接受（POC）
- 日期：2026-11-08
- 依据：[方案A实施计划.md](方案A实施计划.md)、[方案讨论报告.md](方案讨论报告.md)

本文件回答两个问题：**哪些计划决策被原样实现了？哪些地方刻意不同、为什么？**
后者比前者更重要——接手的人需要知道"看起来像 bug 的地方其实是有意的"。

---

## 1. 原样接受的决策

| 计划决策 | 实现位置 | 备注 |
|---|---|---|
| D1 主索引用 PostgreSQL FTS + GIN | `migrations/001_init.sql` | `tsvector` + GIN，`english` 配置，无额外扩展 |
| D2 正文 FTS、文号/代码/金额/日期走 `exact_terms` | `ingest/exact_terms.py`、`retrieve/exact.py` | 两路并行，RRF 融合 |
| D3 `ts_rank_cd` 起步，不上 BM25 | `retrieve/queries.py` | normalization=32（只影响分数尺度，不改排序） |
| D4 结构分块 + 父子块 | `ingest/parse_md.py`、`ingest/chunker.py` | child 检索，父级 section 生成 |
| D5 每域 staging batch + 原子切换 | `ingest/pipeline.py` | `domain.active_batch_id` 单指针 |
| D6 Tantivy/Whoosh 不进 baseline | 全局 | 仅保留 `SparseRetriever` 接口形状 |
| D7 不引入 embedding | 全局 | `chunk` 表没有向量列 |
| ACL 在 SQL 里硬过滤 | `retrieve/queries.py` 的 `FILTERS` | 两条通道共用同一段过滤条件 |
| 检索由代码决定，ADK 只做注入 | `adk_adapter.py` | `before_model_callback`，非 FunctionTool |

## 2. 刻意的实现偏差

### 2.1 `document` / `section` / `chunk` 主键改为 `(id, batch_id)`

计划 6.3 写的是单列 `id` 主键。POC 实现改成复合主键，理由：

- `chunk_id` 由内容哈希决定（计划 9.6：内容不变 → ID 不变）；
- 如果单列主键，重新 ingest 相同内容时会与旧批次行冲突，只能 UPDATE 覆盖；
- 一旦覆盖，`rollback` 指回旧批次就**没有数据可查**，直接违背计划 12.1 的"回滚只改指针"。

代价：所有查询都要带 `batch_id`（已由 `JOIN domain ... active_batch_id = c.batch_id` 保证），
以及"同一 chunk 在多个批次各有一行"的存储冗余。POC 规模下可忽略；
生产若要归档，按 `batch_id` 分区即可（计划 6.9 的建议）。

### 2.2 `domain.previous_batch_id`

计划没有这一列。加它是为了让 `rollback` 有确定的回退目标：
不靠 `ingest_batch` 的时间顺序猜测（多个失败批次、并发 ingest 会让猜测出错）。

注意语义：**回滚一次后该列被清空**，避免"连续回滚"把线上切到很久以前的批次。

### 2.3 `document.source_url`

计划 9.5 的 front matter 有 `source_url`，但 6.3 的 `document` 表没有对应列。
证据包需要可点击来源，所以补了这一列（可空）。

### 2.4 `Evidence.section_ids`（POC 扩展）

计划附录 A 的 `Evidence` 没有这个字段。加它是为了：
- 评估时判断"父级证据覆盖了哪些 section"（Recall@10 的口径）；
- 审计时能定位到具体小节。

它是只读派生字段，不改变 `EvidencePack` 的其他契约。

### 2.5 父级聚合键取"最近的 H2"

计划 8.8.2 的示例里 child 的 `parent_id` 等于它自己的 section。实现改成
"最近的 H2 祖先（没有则用自身）"，原因：
同一 H2 下的 3.1/3.2 经常是一问的两半（标准材料 vs 代办材料），
按 H2 聚合才能一次给全，符合计划 8.8.4 里"避免只答一半"的意图。

### 2.6 FTS 的 OR 兜底

`websearch_to_tsquery` 是 AND 语义，一句话问句里只要有个别词不在目标条款就整体漏召回。
实现上先 AND，候选少于 `or_fallback_min_candidates`（默认 3）时再 `<term> OR <term>` 兜底，
并把结果按 "AND 在前、OR 补充在后" 合并。

代价：**不可答题更容易返回弱相关证据**（评估里 dep-19/20 就是这种情况）。
缓解：`EvidencePack.gaps` 会带 `or_fallback_used`，`confidence` 会降为 `medium`；
回答层必须据此弃答。这是有意的取舍，不是 bug。

### 2.7 nDCG 以父级（检索单元）计算

检索返回的是父级 section，所以主指标 `ndcg_at_10` 按父级算；
同时保留 `ndcg_at_10_sections`（把父级展开成 section 序列）作为更严格的参考值。
两者都写进报告，避免"只挑好看的那个"。

### 2.8 置信度是启发式

`confidence` 只看证据形态（exact 命中 / 是否用了 OR 兜底 / 有无 FTS 命中），
不依赖 RRF 的绝对数值（它会随 k 和权重变化）。
它只应作为"要不要弃答"的输入之一，不应作为质量评分。

## 3. 明确没做的部分

| 计划内容 | 状态 | 原因 |
|---|---|---|
| 5 域 / 60 题 | 未做 | 本次范围是 Phase 0–3 的 deposit 单域；域列表由 `knowledge/_catalog.yaml` 数据驱动，加域不改代码 |
| 消融 A0–A5 | 部分 | 只跑了"A1（关掉 exact 通道）"作为示例；A0 固定长度、A5 BM25 未实现（见 `docs/evaluation.md`） |
| chunk 级增量复用 | 未做 | 每批全量重切；`content_hash` 已落库，Phase 4 可按哈希复用旧批次行 |
| 缓存 / 连接池 / 监控 | 未做 | POC 范围外（计划 1.4） |
| 清洗旧批次 | 未做 | 只保留最近批次可查；归档脚本见 runbook |
| 中文分词 / 多语言 | 未做 | 计划前提是英文 Markdown（D8 待确认） |
| 多租户 / RLS / HA | 未做 | POC 范围外 |

## 4. 复审触发条件

| 触发 | 需要重新考虑 |
|---|---|
| Recall@10 掉到 0.85 以下，且分块与金标都没问题 | 排序策略（BM25 / `pg_search`）或换检索范式 |
| 查询里"换说法"占比超过 30% | 引入 dense 通道（方案 B/C），`SparseRetriever` 接口已留 |
| chunk 数超过 10 万 | 旧批次归档/分区，或换 Tantivy/ES |
| 需要中文/多语言 | 自定义 text search 配置或换引擎（D8） |
| 需要"知识更新秒级生效" | 加增量 ingest + 缓存失效 |
