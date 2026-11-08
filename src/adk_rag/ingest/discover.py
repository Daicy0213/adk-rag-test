"""知识源发现：目录约定、域卡片、文档清单。

约定（对应计划 10.1 / Research 4.2）：
    knowledge/_catalog.yaml          # 全域清单
    knowledge/<domain>/_domain.yaml  # 域卡片
    knowledge/<domain>/*.md          # 文档（下划线开头的文件不是文档）

“一个目录 = 一个域，目录名 = domain_id”，新增域只加目录和 YAML，不改代码。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class DomainConfig:
    id: str
    name: str
    description: str
    acl_groups: list[str] = field(default_factory=list)
    default_classification: str = "internal"
    card: dict = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeDoc:
    domain_id: str
    path: Path       # 绝对路径
    rel_path: str    # 相对工程根，写库/报告用


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"YAML 必须是映射：{path}")
    return raw


def load_catalog(knowledge_root: Path) -> dict:
    """读取全域目录；不存在时返回空（POC 允许只有一个域目录）。"""
    return _read_yaml(knowledge_root / "_catalog.yaml")


def load_domain(knowledge_root: Path, domain_id: str) -> DomainConfig:
    """读取域卡片；`_catalog.yaml` 提供兜底值，域卡片优先。"""
    domain_dir = knowledge_root / domain_id
    if not domain_dir.is_dir():
        raise FileNotFoundError(f"找不到域目录：{domain_dir}")

    catalog = load_catalog(knowledge_root)
    catalog_domains = catalog.get("domains") or {}
    catalog_entry = catalog_domains.get(domain_id) or {}

    card = _read_yaml(domain_dir / "_domain.yaml")
    if not card:
        # 没有域卡片时仍然允许 ingest：目录名就是域 ID，但要在报告里提醒补齐。
        card = {}

    acl_groups = card.get("acl_groups")
    if acl_groups is None:
        acl_groups = catalog_entry.get("acl_groups") or []
    classification = (
        card.get("default_classification")
        or catalog_entry.get("default_classification")
        or "internal"
    )
    return DomainConfig(
        id=domain_id,
        name=str(card.get("name") or catalog_entry.get("name") or domain_id),
        description=str(card.get("description") or catalog_entry.get("description") or ""),
        acl_groups=[str(g) for g in acl_groups],
        default_classification=str(classification),
        card=card,
    )


def discover_docs(knowledge_root: Path, domain_id: str) -> list[KnowledgeDoc]:
    """列出域内 Markdown 文档，按文件名排序（保证 ingest 可复现）。"""
    domain_dir = knowledge_root / domain_id
    docs: list[KnowledgeDoc] = []
    for path in sorted(domain_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        docs.append(
            KnowledgeDoc(
                domain_id=domain_id,
                path=path,
                rel_path=path.relative_to(knowledge_root.parent).as_posix(),
            )
        )
    return docs
