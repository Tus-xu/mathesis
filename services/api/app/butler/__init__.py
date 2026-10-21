"""AI 管家（Butler Orchestrator）核心包

职责分层（对齐《AI 管家化重构总方案 v3.0》§2.2）：
- event_bus：学习事件总线（LearningEvent 持久化 + 幂等）
- tools：工具集（封装学情/错题/出题/图谱/路由等业务能力，供 AI 调用）
- state：学生状态记忆（长期画像 + 短期会话态）
- llm：管家 LLM 生成层（超时/缓存/回退纪律，复用 copy_polish 范式）
- orchestrator：管家调度器（事件 → 决策 → 生成 → 落库推荐）
- skills：管家技能（今日计划/周报/错因解读/路径规划，规则骨架 + LLM 润色）

铁律：任何异常吞掉记日志、回退规则兜底，绝不阻塞主链路（同 learning_profile 设计纪律）。
"""

from app.butler.event_bus import LearningEventBus, get_event_bus
from app.butler.orchestrator import ButlerOrchestrator, get_orchestrator

__all__ = [
    "LearningEventBus",
    "get_event_bus",
    "ButlerOrchestrator",
    "get_orchestrator",
]
