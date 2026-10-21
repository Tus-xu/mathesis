"""R14 Teacher Prep 领域骨架（8100 事实权威）。

命名空间约定（与 R13 的 `teacher_prep_v2` 契约/适配层分离）：
- `app/teacher_prep/`：实体事实层（ORM 表 + 确定性领域服务 + 状态机 + API 路由）；
- `app/teacher_prep_v2/`：跨端契约 + 8000 适配器（R13 边界守卫禁止 ORM——不扫描本目录）。

本包只做会话/蓝图骨架，不实现诊断、学情、PPT 或前端大页面（R14 范围）。
"""

from __future__ import annotations
