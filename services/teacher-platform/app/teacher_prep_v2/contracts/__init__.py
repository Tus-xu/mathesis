"""Teacher Prep V2 契约包（R13）。

契约命名空间 = 唯一公开跨端契约（DECISIONS §6）：
  PreClassDiagnosticTask / ClassroomTask / TaskAssignment / StudentSubmission /
  Evidence / ClassLearningProfile
旧名 `TaskContract / Submission` 只能作为内部兼容别名（reader.ALIAS_KINDS），
不得形成第二套事实。

单一权威源 = Pydantic 模型（本包）；JSON Schema 与 TypeScript 由
`app/teacher_prep_v2/contracts/gen/` 生成并提交；前端同步副本在
`src/features/teacher-prep-v2/`，任一侧漂移由 contract test 即刻拦截。
"""

from __future__ import annotations

from .base import PREP_CONTRACTS_SCHEMA_VERSION
from .catalog import CONTRACT_MODELS, REGISTRY_ORDER, PreClassContractsBundle
from .models import (
    ClassLearningProfile,
    ClassroomTask,
    Evidence,
    PreClassDiagnosticTask,
    StudentSubmission,
    TaskAssignment,
)
from .reader import ALIAS_KINDS, read_any, read_contract

__all__ = [
    "PREP_CONTRACTS_SCHEMA_VERSION",
    "PreClassDiagnosticTask",
    "ClassroomTask",
    "TaskAssignment",
    "StudentSubmission",
    "Evidence",
    "ClassLearningProfile",
    "PreClassContractsBundle",
    "CONTRACT_MODELS",
    "REGISTRY_ORDER",
    "ALIAS_KINDS",
    "read_contract",
    "read_any",
]
