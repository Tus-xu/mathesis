"""聚合所有 ORM 模型（供 Alembic autogenerate 与业务层导入）。"""
from __future__ import annotations

from app.domains.catalog import models as _catalog_models  # noqa: F401
from app.domains.classroom import models as _classroom_models  # noqa: F401
from app.domains.contents import models as _contents_models  # noqa: F401
from app.domains.figures import models as _figures_models  # noqa: F401
from app.domains.grading import models as _grading_models  # noqa: F401
from app.domains.recognition import models as _recognition_models  # noqa: F401

# R11.5-C 可靠性正式表（Job/JobEvent/Outbox/Inbox/Idempotency；所有权进迁移 e26a6b7c8d9e）
from app.reliability import models as _reliability_models  # noqa: F401

# R14：Teacher Prep 会话/蓝图骨架（表名前缀 tp_；8100 事实权威）
from app.teacher_prep import models as _teacher_prep_models  # noqa: F401

# R17：Conversation-first 编排（消息/run/上下文快照，tp_ 前缀；additive）
from app.teacher_prep.conversation import models as _conversation_models  # noqa: F401

# R18：学前诊断任务（草稿/发布事实 + 不可变版本快照，tp_ 前缀；additive）
from app.teacher_prep.diagnostic import models as _diagnostic_models  # noqa: F401

# R19：教师侧教学解释证据（typed refs，零 PII 行复制；tp_ 前缀；additive）
from app.teacher_prep.profile import evidence as _profile_evidence_models  # noqa: F401

# R20：版本化班级学习画像 + 双 Spec 投影事实表（tp_ 前缀；additive）
from app.teacher_prep.profile import models as _profile_models  # noqa: F401
from app.teacher_prep.specs import models as _specs_models  # noqa: F401

# 体验测试留痕（tp_trial_feedback；additive；作品验收用）
from app.teacher_prep import trial_models as _trial_models  # noqa: F401

# 教材资源库 V2（R05 新命名空间，独立于旧混合集合；表名前缀 textbook_*）
from app.textbook_v2 import models as _textbook_v2_models  # noqa: F401

# T24：PPT Studio V2 编辑/修订/逐页 QA 事实（ppt_v2_* 前缀；additive）
from app.ppt_v2 import persistence as _ppt_v2_persistence  # noqa: F401

# T24：产物投递（artifact 注册 / 下载票据 / 下载审计；teacher_artifact* 前缀）
from app.storage import models as _storage_models  # noqa: F401

from ..base import Base
from .butler import ButlerMessageRow  # noqa: F401

# rescue 整合（2026-09-07）：B2/B4 域模型并入聚合点——
# 此前 knowledge.py 与 domains/*/models.py 不在本包导入面内，autogenerate 看不到，
# 导致域表从未进迁移、只能运行时自建（MIGRATION_REQUEST-001 的根因之一）。
from .knowledge import (  # noqa: F401
    KnowledgeChunk,
    KnowledgeDocument,
    Memory,
    WebSearchCache,
)
from .platform import (
    Classes,
    ClassroomEvents,
    ExportJobs,
    IdempotencyRecords,
    TaskEvents,
    Tasks,
    Users,
)

__all__ = [
    "Base",
    "Users",
    "Classes",
    "Tasks",
    "TaskEvents",
    "IdempotencyRecords",
    "ExportJobs",
    "ClassroomEvents",
    "KnowledgeDocument",
    "KnowledgeChunk",
    "WebSearchCache",
    "Memory",
]
