# 评估口径与基线结果

> 环境：Windows + Docker（PostgreSQL 17）+ Python 3.12；语料为 `knowledge/deposit/` 的 8 篇英文 Markdown。
> 日期：2026-11-08；检索配置见 `config/default.yaml`。

## 1. 金标集

`eval/golden.jsonl`，20 题，按计划 11.3 的配比：

| 类型 | 数量 | 说明 |
|---|---:|---|
| fact | 10 | 单点事实，如冷却期、最低余额、审批人 |
| list | 3 | 列举题（材料清单、处理步骤、拒办理由） |
| exact | 3 | 文号/产品代码（`Circular [2022] No. 5`、`CD-2026-003`、`Notice 2023-12`） |
| table | 2 | 表格题（费率表、12 个月靠档利率） |
| unanswerable | 2 | 不可答：2035 年利率（知识库未收录）、私行产品金额（ACL 禁止） |

每题字段：`query`、`gold_domains`、`gold_section_ids`、`must_include`、`must_not_include`、
`reference_answer`、`user_groups`、`as_of`。

`gold_section_ids` 用真实 section ID，`--validate-gold` 会校验它们存在于当前活跃批次，
避免金标写错导致 Recall 假阴性。

运行：

```bash
uv run python -m adk_rag.eval --golden eval/golden.jsonl --domains deposit --validate-gold
```

## 2. 指标定义

| 指标 | 口径 |
|---|---|
| Recall@10 | gold section 是否出现在"证据按父级展开后的 section 序列"前 10 个；分母是 gold section 数 |
| nDCG@10（主） | 以**父级 section（检索单元）**排序，二值相关性 |
| nDCG@10（section） | 把父级展开成 section 序列后的严格视角，作为参考值一并报告 |
| exact hit | `type=exact` 的题目中，`must_include` 全部出现在 top-k 证据文本里的比例 |
| 要点覆盖 | 所有带 `must_include` 的题目，要点出现在证据文本中的平均比例 |
| 空召回率 | 可答题中证据为空的占比 |
| 不可答题合格率 | 不可答题中"空召回或低置信度且无 `must_not_include` 泄露"的占比 |
| p95 延迟 | 单次 `retrieve` 的端到端耗时（含 SQL 与装配），Python 侧计时 |

## 3. 基线结果（A1：结构分块 + FTS + exact + 父级聚合）

| 指标 | 实测 | 计划目标（11.4） | 结论 |
|---|---:|---:|---|
| Recall@10 | **1.000** | ≥ 0.85 | ✅ |
| nDCG@10（父级） | **0.906** | ≥ 0.75 | ✅ |
| nDCG@10（section 视角） | 0.748 | 参考值 | — |
| exact hit | **1.000**（3 题） | ≥ 0.95 | ✅ |
| 要点覆盖 | **1.000**（18 题） | ≥ 0.85 | ✅ |
| 空召回率 | **0.000** | < 0.10 | ✅ |
| 不可答题空召回率 | 0.000 | — | ⚠️ 见 7.1 |
| must_not_include 命中 | 0 | 0 | ✅ |
| p50 / p95 延迟 | 15.3 / 34.5 ms | p95 < 200 ms | ✅ |

结论：**deposit 单域的 18 道可答题全部命中且要点齐全**，延迟远低于预算（35 ms vs 200 ms）。
语料只有 8 篇 46 chunks，所以这些数字说明"链路正确"，**不能**外推成"20 域也这样"。

## 4. 消融实验

### 4.1 关闭 exact 通道（A2 的反面）

```bash
uv run python -m adk_rag.eval --golden eval/golden.jsonl --domains deposit \
  --config eval/ablation/a1-no-exact.yaml --report-dir eval/reports/ablation
```

| 指标 | 基线（含 exact） | 关闭 exact | 差异 |
|---|---:|---:|---|
| Recall@10 | 1.000 | 1.000 | 0 |
| nDCG@10（父级） | 0.906 | 0.914 | +0.008 |
| exact hit | 1.000 | 1.000 | 0 |
| 要点覆盖 | 1.000 | 1.000 | 0 |
| p50 延迟 | 15.3 ms | 14.1 ms | −1.2 ms |

**结论（重要，且与直觉相反）：在本语料上，exact 通道没有带来可测量的召回/排序增益。**
原因是 20 道题的查询都带足够的自然语言上下文，`english` FTS 已经能把目标条款排到第一；
`CD-2026-003` 这类编号在 `tsvector` 里只是被拆成 `cd/2026/003`，但依然能 AND 命中。

那 exact 通道还有什么用？逐条对比"纯编号查询"可以看到它的真实价值是**置信度与精度**：

| 查询 | 含 exact | 关闭 exact |
|---|---|---|
| `CD-2026-003` | confidence=high | confidence=medium |
| `Circular [2022] No. 5` | high，首位是政策原文 | medium，首位相同 |
| `USD 5,000,000 minimum amount product` | high | medium |
| `Notice 2023-12` | 首位=FAQ（更贴近用户问法） | 首位=Interest 规则章节 |

即：exact 通道把"文件里确实有这个编号"变成显式信号，提高置信度并把编号类查询排到更贴切的段落。
但它**不是召回瓶颈**——不要指望它解决 FTS 的词汇鸿沟。

### 4.2 没做的消融

| 实验 | 状态 | 原因 |
|---|---|---|
| A0 固定长度切分 | 未做 | 需要另一套 chunker；当前语料太小，差异会被单文档主导 |
| A3 关掉父级聚合 | 未做（有替代观察） | `nDCG@10(section)=0.748` vs `nDCG@10(父级)=0.906` 说明"按父级返回"本身提升了排序质量 |
| A5 BM25 | 未做 | 计划 D3：`ts_rank_cd` 不满足时再换；当前 nDCG 已达标 |

做到 5 域 / 60 题时再补 A0/A3/A5，否则结论不可信（样本太小）。

## 5. 语料与分块质量

| 项 | 数值 |
|---|---:|
| 文档数 | 8（policy 5 / product 1 / faq 1 / notice 1） |
| section 数 | 54 |
| chunk 数 | 46 |
| chunk token 数 | min 48 / avg 109 / max 248（上限 500） |
| 带 `exact_terms` 的 chunk | 31 / 46（其余块确实不含编号/金额） |
| 有 `source_start/end` 行号的 chunk | 46 / 46 |
| 质量校验 error | 0（warning 仅 1 条：15 个块无精确词，属正常） |

chunk 偏小是**有意**的：chunk 不跨 section 合并，短 section（FAQ 问答、单条款）本身就是原子单位，
上下文由父级聚合补齐（nDCG@10 父级 0.906 vs 展开 section 0.748 就是这个设计的体现）。
抽查过的代表块：条款+材料清单（3.1）、费率表（3.4）、FAQ 问答、产品目录表，均表头/引导句完整。

## 6. 对计划 DoD 的对照（Phase 1：1 个域）

| 检查项 | 状态 | 证据 |
|---|---|---|
| 1 个域 20 条英文金标 | ✅ | `eval/golden.jsonl`，`--validate-gold` 通过 |
| parser/chunker 通过 8 类测试 | ✅ | `tests/test_chunker.py` 的 test_1 ~ test_8 |
| 50 个 chunk 人工抽检合格 | ⚠️ 部分 | 语料只产生 46 个 chunk（规模不足 50）；自动校验 0 error，人工抽查 4 类代表块 |
| `001_init.sql` 可重复执行 | ✅ | 连续执行两次 `init-db` 均通过 |
| ingest 1 域成功 | ✅ | 批次 41：8 篇 / 54 sections / 46 chunks / ~5034 tokens |
| FTS + exact 查询可用 | ✅ | 集成测试 + 评估全部通过 |
| Recall@10 有基线 | ✅ | 1.000（父级 nDCG 0.906） |
| p95 < 200ms | ✅ | 34.5 ms |
| ACL/版本至少各 1 个测试 | ✅ | `test_acl_and_effective_date_filters` |

## 7. 已知问题

### 7.1 不可答题会被"部分召回"（OR 兜底带来）

`dep-19`（2035 年利率）和 `dep-20`（私行金额，ACL 禁止）都返回了非空证据、`confidence=medium`，
所以不可答题合格率是 0%。两个原因不同：

- **dep-19**：AND 查询 `'2035' & 'cd' & 'rate'` 无命中，触发 OR 兜底后 `cd`/`rate` 命中了产品条款——
  这是 OR 兜底换来的召回率的代价（见 ADR 2.6）。正确做法是**回答层**依据
  `pack.gaps=["or_fallback_used"]` 与 `confidence` 弃答，而不是让"没查到"看起来像"查到了"。
- **dep-20**：ACL 生效（证据里没有 `USD 10,000,000`），但"minimum investment"本身能匹配到
  零售产品条款。它的 `must_not_include` 校验通过，说明**没有越权泄露**。

结论：ACL 是可靠的；"不可答"要靠置信度/弃答策略，不能只靠检索空。

### 7.2 其他

- 语料是 POC 自造的英文示例（8 篇、46 chunks），阈值达标不代表真实语料也达标；
- `token_count` 是估算值（见 README 4.6），p95 未包含 LLM 生成时间；
- 单机、无并发压测：p95 是串行请求下的数字，并发 5–10 时的表现未测（计划 11.6 要求）。

## 8. 复现清单

```bash
docker compose up -d
uv run python -m adk_rag.ingest.cli init-db
uv run python -m adk_rag.ingest.cli --domain deposit --batch-label deposit-poc-001 --promote
uv run python -m adk_rag.eval --golden eval/golden.jsonl --domains deposit --validate-gold
uv run python -m adk_rag.eval --golden eval/golden.jsonl --domains deposit \
  --config eval/ablation/a1-no-exact.yaml --report-dir eval/reports/ablation
```

报告写入 `eval/reports/`（JSON + Markdown），每次运行带时间戳，便于横向对比。
