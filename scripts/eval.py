"""脚本入口：等价于 `python -m adk_rag.eval`。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from adk_rag.eval.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
