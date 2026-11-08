"""评估子包：金标集加载、指标计算、报告生成。

指标定义见计划 11.3/11.4；实现上有两个必须写清楚的取舍：
1. **Recall@10 的分母是 gold section（子节或父节）**，分子是"证据覆盖的 section 序列"
   的前 10 个。因为检索按父级返回，父级展开后的 section 序列才是用户真正看到的内容。
2. **nDCG 用二值相关性**（命中 gold 记 1），不做多级标注——POC 金标集只标注"是否相关"。
"""

from __future__ import annotations

from .golden import GoldenCase, load_golden
from .metrics import ndcg_at_k, percentile, recall_at_k
from .runner import CaseResult, EvalReport, evaluate, render_markdown

__all__ = [
    "CaseResult",
    "EvalReport",
    "GoldenCase",
    "evaluate",
    "load_golden",
    "ndcg_at_k",
    "percentile",
    "recall_at_k",
    "render_markdown",
]
