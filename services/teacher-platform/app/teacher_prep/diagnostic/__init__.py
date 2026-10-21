"""R18 学前诊断任务（教师侧闭环）。

范围（DECISIONS.md §6 业务主线「TeachingBlueprint confirmed → 学前诊断建议」）：
- 只实现教师侧「建议 → 审阅/编辑 → 确认发布 / 明确跳过」；
- 学生端零页面改造（本轮只落 8100 权威事实 + 经 8000 Coursework Adapter 创建
  TaskAssignment，不做任何学生 UI / 迁移）。
"""

from __future__ import annotations

__all__: list[str] = []
