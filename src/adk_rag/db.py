"""PostgreSQL 访问层：连接、事务、迁移。

全部 SQL 走参数化（psycopg 的 %s 占位符），不拼字符串（计划 12.5）。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg import Connection
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from .config import Settings


def connect(settings: Settings) -> Connection:
    """打开一个连接。调用方负责关闭（一般用 `with closing(...)` 或本模块的 helper）。"""
    return psycopg.connect(settings.dsn(), row_factory=dict_row)


@contextmanager
def connection(settings: Settings) -> Iterator[Connection]:
    conn = connect(settings)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: Connection, *, statement_timeout_ms: int | None = None) -> Iterator[Connection]:
    """显式事务；可选设置 statement_timeout（计划 10.5 的 timeouts_ms.query）。

    `SET LOCAL` 不支持参数占位符，所以用 `set_config(..., is_local=true)` 代替。

    **不要嵌套调用**：psycopg3 在已有事务里调用 `conn.transaction()` 会退化成
    savepoint，外层事务不会因为这里的 exit 而提交——曾经因此出现"批次状态一直停在
    staging"的静默失败。这里用显式检查把误用变成可见错误。
    """
    if conn.info.transaction_status == TransactionStatus.INTRANS:
        raise RuntimeError(
            "transaction() 不能在已有事务中嵌套调用：psycopg3 会退化成 savepoint，"
            "外层事务不会被提交。请把写入合并进同一个 transaction()，或在读语句后先 commit()。"
        )
    with conn.transaction():
        if statement_timeout_ms:
            conn.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (str(int(statement_timeout_ms)),),
            )
        yield conn


def ping(settings: Settings) -> bool:
    try:
        with connection(settings) as conn:
            conn.execute("SELECT 1")
        return True
    except psycopg.Error:
        return False


def apply_migration(settings: Settings, migration_path: str | Path) -> Path:
    """执行 SQL 迁移文件。文件本身要求幂等（见 migrations/001_init.sql）。"""
    path = Path(migration_path)
    if not path.is_absolute():
        from .config import project_root

        path = project_root() / path
    ddl = path.read_text(encoding="utf-8")
    with connection(settings) as conn:
        conn.execute(ddl)
        conn.commit()
    return path

