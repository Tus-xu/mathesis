"""RV17 · 科研工作区后端域（项目级文献 / 笔记 / 文件树 / 编译 / 知识检索 / Agent 任务）。

对外入口是 `core/api/workspace_routes.py` 的 `workspace_router`；本包只暴露
域服务与 ORM，不直接依赖 FastAPI。
"""

from __future__ import annotations

from . import models  # noqa: F401  register tables on import

__all__ = ["models"]
