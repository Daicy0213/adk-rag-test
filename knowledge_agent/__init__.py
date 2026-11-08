"""ADK 应用包：`adk web` / `adk run` 从这里加载 root_agent。

注意命名：`adk web <agents_dir>` 会把**每个子目录**当成一个 agent，
所以本目录名（`knowledge_agent`）就是 UI 里显示的应用名。
"""

from .agent import root_agent

__all__ = ["root_agent"]
