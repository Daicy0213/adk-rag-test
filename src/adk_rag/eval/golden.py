"""金标集加载与校验。

格式（JSONL，一行一题；计划 11.3）：

    {"id": "dep-01", "type": "fact", "query": "...",
     "gold_domains": ["deposit"], "gold_section_ids": ["sec-..."],
     "must_include": ["24 hours"], "must_not_include": ["72 hours"],
     "reference_answer": "...", "user_groups": ["role:retail_business"],
     "as_of": "2026-11-08"}

`gold_section_ids` 必须是数据库里真实存在的 section id；评估脚本不做模糊匹配，
因为"猜一个相近的 section"会让 Recall 失去意义。加载后可用 `--strict` 校验存在性。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

CaseType = Literal["fact", "list", "exact", "table", "unanswerable", "cross_domain", "followup"]

DEFAULT_USER_GROUPS = ["role:retail_business"]


class GoldenCase(BaseModel):
    id: str
    type: CaseType
    query: str
    gold_domains: list[str] = Field(default_factory=list)
    gold_section_ids: list[str] = Field(default_factory=list)
    must_include: list[str] = Field(default_factory=list)
    must_not_include: list[str] = Field(default_factory=list)
    reference_answer: str = ""
    user_groups: list[str] = Field(default_factory=lambda: list(DEFAULT_USER_GROUPS))
    as_of: date | None = None

    @property
    def is_answerable(self) -> bool:
        return self.type != "unanswerable"


def load_golden(path: str | Path) -> list[GoldenCase]:
    """读取 JSONL 金标集；格式错误直接报出文件名与行号。"""
    golden_path = Path(path)
    if not golden_path.exists():
        raise FileNotFoundError(f"金标集不存在：{golden_path}")

    cases: list[GoldenCase] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(
        golden_path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{golden_path}:{line_number} 不是合法 JSON：{exc}") from exc
        try:
            case = GoldenCase.model_validate(payload)
        except ValidationError as exc:
            raise ValueError(f"{golden_path}:{line_number} 字段不合法：{exc}") from exc
        if case.id in seen:
            raise ValueError(f"{golden_path}:{line_number} 重复的 case id：{case.id}")
        seen.add(case.id)
        cases.append(case)

    if not cases:
        raise ValueError(f"金标集为空：{golden_path}")
    return cases
