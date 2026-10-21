"""M3 教师端领域包。

- router.py       纯 HTTP 解析/鉴权/响应
- schemas.py      Pydantic 请求/响应（extra="forbid"）
- scope.py        teacher role + class_scope
- artifacts.py    Artifact 版本与状态机
- today.py        Today Projection
- insights.py     可行动洞察
- lessons.py      教案/课件/讲解
- assessment.py   题集与作业
- grading.py      建议与教师确认
- classroom.py    课堂模式与聚合洞察
- resources.py    资源/预处理/理解
- capability_gateway.py  Capability → Butler/本地/星辰
- registry.py     teacher-only ToolRegistry
- workflow_adapter.py  不外改星辰 YAML 的映射层
"""

from app.domains.teacher import scope  # noqa: F401
