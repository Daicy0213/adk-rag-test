"""集成测试：需要真实 PostgreSQL（`pytest -m integration`）。

设计：
- 用独立的 `itest` 域和临时知识目录，不碰 `deposit` 的活跃批次；
- 测试自己 ingest / promote / rollback，并在开始与结束时清理 `itest` 数据；
- 连不上数据库时整体 skip，保证 `pytest` 在无容器环境下也能通过。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from adk_rag.config import load_settings
from adk_rag.db import apply_migration, connection, ping, transaction
from adk_rag.ingest.pipeline import ingest, promote_batch, rollback
from adk_rag.retrieve.retriever import PgRetriever

pytestmark = pytest.mark.integration

DOMAIN = "itest"
AS_OF = date(2026, 11, 8)


DOC_A = """---
doc_id: itest-a
domain: itest
title: Withdrawal Test Policy
version: "1.0"
status: active
effective_from: 2026-01-01
classification: internal
acl_groups:
  - role:retail_business
doc_type: policy
---

# Withdrawal Test Policy

## 1 Early Withdrawal

Early withdrawal of product IT-2026-001 requires the original certificate and
Circular [2022] No. 77 approval. The minimum amount is USD 100,000.

## 2 Fees

| Scenario | Fee |
| --- | --- |
| Within 3 months | 1.50% |
| After 3 months | 0.75% |
"""

DOC_B = """---
doc_id: itest-b
domain: itest
title: Restricted Private Terms
version: "1.0"
status: active
effective_from: 2026-01-01
classification: confidential
acl_groups:
  - role:private_banking
doc_type: policy
---

# Restricted Private Terms

## 1 Minimum Investment

The private minimum investment is USD 9,999,999 per certificate.
"""

DOC_C = """---
doc_id: itest-c
domain: itest
title: Expired Legacy Terms
version: "1.0"
status: active
effective_from: 2024-01-01
effective_to: 2025-12-31
classification: internal
acl_groups:
  - role:retail_business
doc_type: policy
---

# Expired Legacy Terms

## 1 Legacy Grace Period

The legacy grace period is 99 days for certificates issued before 2025.
"""


def _write_docs(knowledge_root: Path) -> None:
    domain_dir = knowledge_root / DOMAIN
    domain_dir.mkdir(parents=True, exist_ok=True)
    (knowledge_root / "_catalog.yaml").write_text(
        "version: 1\ndomains:\n  itest:\n    name: Integration Test\n"
        "    acl_groups:\n      - role:retail_business\n      - role:private_banking\n",
        encoding="utf-8",
    )
    (domain_dir / "_domain.yaml").write_text(
        "id: itest\nname: Integration Test\ndescription: integration test domain\n",
        encoding="utf-8",
    )
    (domain_dir / "a-policy.md").write_text(DOC_A, encoding="utf-8")
    (domain_dir / "b-restricted.md").write_text(DOC_B, encoding="utf-8")
    (domain_dir / "c-expired.md").write_text(DOC_C, encoding="utf-8")


def _cleanup(settings) -> None:
    with connection(settings) as conn:
        with transaction(conn):
            conn.execute(
                "DELETE FROM chunk WHERE batch_id IN "
                "(SELECT id FROM ingest_batch WHERE domain_id = %s)",
                (DOMAIN,),
            )
            conn.execute(
                "DELETE FROM section WHERE batch_id IN "
                "(SELECT id FROM ingest_batch WHERE domain_id = %s)",
                (DOMAIN,),
            )
            conn.execute(
                "DELETE FROM document WHERE batch_id IN "
                "(SELECT id FROM ingest_batch WHERE domain_id = %s)",
                (DOMAIN,),
            )
            conn.execute("DELETE FROM ingest_batch WHERE domain_id = %s", (DOMAIN,))
            conn.execute("DELETE FROM domain WHERE id = %s", (DOMAIN,))


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    current = load_settings()
    if not ping(current):
        pytest.skip("PostgreSQL 不可达：请先 docker compose up -d 并执行 init-db")

    apply_migration(current, "migrations/001_init.sql")

    knowledge_root = tmp_path_factory.mktemp("itest-knowledge")
    _write_docs(knowledge_root)
    # knowledge_root 设为绝对路径：Path / 绝对路径 会取绝对路径，从而绕开工程根。
    current.ingest.knowledge_root = str(knowledge_root)

    _cleanup(current)
    yield current
    _cleanup(current)


def _chunk_id_sets(settings) -> dict[int, set[str]]:
    with connection(settings) as conn:
        rows = conn.execute(
            "SELECT batch_id, array_agg(id ORDER BY id) AS ids FROM chunk "
            "WHERE domain_id = %s GROUP BY batch_id",
            (DOMAIN,),
        ).fetchall()
    return {row["batch_id"]: set(row["ids"]) for row in rows}


def test_end_to_end_ingest_query_switch_rollback(settings):
    # --- 1) staging 批次对查询不可见 -------------------------------------
    first = ingest(settings, DOMAIN, batch_label="itest-batch-1")
    assert first.batch_status == "validated", first.issues
    assert first.document_count == 3
    assert first.chunk_count > 0

    retriever = PgRetriever(settings)
    pack = retriever.retrieve(
        "early withdrawal minimum amount", domains=[DOMAIN], user_groups=["role:retail_business"], as_of=AS_OF
    )
    assert pack.is_empty, "staging 批次不应被查询到"

    # --- 2) promote 后立即可查 -------------------------------------------
    promote_batch(settings, DOMAIN, first.batch_id)
    pack = retriever.retrieve(
        "early withdrawal minimum amount", domains=[DOMAIN], user_groups=["role:retail_business"], as_of=AS_OF
    )
    assert not pack.is_empty
    assert pack.knowledge_version.startswith(f"{DOMAIN}@batch-")
    assert any("IT-2026-001" in evidence.text for evidence in pack.evidence)
    assert pack.confidence in {"high", "medium"}

    # tsv 由 trigger 生成
    with connection(settings) as conn:
        row = conn.execute(
            "SELECT count(*) AS total, count(tsv) AS with_tsv FROM chunk WHERE batch_id = %s",
            (first.batch_id,),
        ).fetchone()
    assert row["total"] == row["with_tsv"] > 0

    # --- 3) 新批次 + 原子切换 + 回滚 -------------------------------------
    second = ingest(settings, DOMAIN, batch_label="itest-batch-2", promote=True)
    assert second.batch_status == "active"

    with connection(settings) as conn:
        active = conn.execute(
            "SELECT active_batch_id, previous_batch_id FROM domain WHERE id = %s", (DOMAIN,)
        ).fetchone()
    assert active["active_batch_id"] == second.batch_id
    assert active["previous_batch_id"] == first.batch_id

    result = rollback(settings, DOMAIN)
    assert result["active_batch_id"] == first.batch_id

    pack = retriever.retrieve(
        "early withdrawal minimum amount", domains=[DOMAIN], user_groups=["role:retail_business"], as_of=AS_OF
    )
    assert not pack.is_empty, "回滚后旧批次必须仍然可查"

    # 回滚后还能"再切回来"（retired 批次允许重新 promote）
    promote_batch(settings, DOMAIN, second.batch_id)
    with connection(settings) as conn:
        active = conn.execute(
            "SELECT active_batch_id, previous_batch_id FROM domain WHERE id = %s", (DOMAIN,)
        ).fetchone()
    assert active["active_batch_id"] == second.batch_id
    assert active["previous_batch_id"] == first.batch_id

    # --- 4) 幂等：相同内容重复 ingest 产生相同 chunk ID ------------------
    id_sets = _chunk_id_sets(settings)
    assert id_sets[first.batch_id] == id_sets[second.batch_id]


def test_acl_and_effective_date_filters(settings):
    report = ingest(settings, DOMAIN, batch_label="itest-acl", promote=True)
    assert report.batch_status == "active"

    retriever = PgRetriever(settings)

    # 无 private_banking 角色：敏感金额不能出现
    retail = retriever.retrieve(
        "private minimum investment certificate", domains=[DOMAIN], user_groups=["role:retail_business"], as_of=AS_OF
    )
    assert all("9,999,999" not in evidence.text for evidence in retail.evidence)

    # 有 private_banking 角色：可以查到
    private = retriever.retrieve(
        "private minimum investment certificate",
        domains=[DOMAIN],
        user_groups=["role:private_banking"],
        as_of=AS_OF,
    )
    assert any("9,999,999" in evidence.text for evidence in private.evidence)

    # 过期文档：effective_to 之后不得返回
    expired = retriever.retrieve(
        "legacy grace period 99 days", domains=[DOMAIN], user_groups=["role:retail_business"], as_of=AS_OF
    )
    assert all("99 days" not in evidence.text for evidence in expired.evidence)

    # 而在有效期内仍可返回（文档 2025-12-31 失效）
    in_period = retriever.retrieve(
        "legacy grace period 99 days",
        domains=[DOMAIN],
        user_groups=["role:retail_business"],
        as_of=date(2025, 6, 1),
    )
    assert any("99 days" in evidence.text for evidence in in_period.evidence)


def test_exact_channel_hits_identifiers(settings):
    report = ingest(settings, DOMAIN, batch_label="itest-exact", promote=True)
    assert report.batch_status == "active"

    retriever = PgRetriever(settings)
    pack = retriever.retrieve(
        "What does Circular [2022] No. 77 require for product IT-2026-001?",
        domains=[DOMAIN],
        user_groups=["role:retail_business"],
        as_of=AS_OF,
    )

    assert not pack.is_empty
    assert retriever.last_trace["exact_terms"]
    assert retriever.last_trace["exact_candidates"] >= 1
    assert any("Circular [2022] No. 77" in evidence.text for evidence in pack.evidence)
    assert pack.confidence == "high"


def test_empty_domain_is_reported(settings):
    report = ingest(settings, "missing-domain", batch_label="nope")
    assert report.batch_status == "failed"
    assert any(issue.code == "domain_missing" for issue in report.errors)
