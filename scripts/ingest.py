"""脚本入口：等价于 `python -m adk_rag.ingest.cli`。

保留 scripts/ 目录是为了兼容计划 10.1 的目录约定；实现只有一行转发，
避免出现两套参数解析。
"""

from __future__ import annotations

import sys
from pathlib import Path

# 允许不安装包也能直接从源码运行：python scripts/ingest.py ...
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adk_rag.ingest.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
