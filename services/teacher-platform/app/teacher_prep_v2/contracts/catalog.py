"""契约目录（R13）：名称 ↔ 模型 ↔ 版本 ↔ fixture 路径 的机器可读注册表。

- 唯一权威：本目录 + Pydantic 模型；任何新契约必须先在此登记再做 schema/TS 生成；
- `PreClassContractsBundle` 只在 schema 生成时使用（把六个根模型压进一个 JSON Schema，
  使 TS 一次性产出全部接口与根引用），不承载业务语义。
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from .base import CONTRACT_KINDS, _ContractModel
from .models import (
    PREP_CONTRACTS_SCHEMA_VERSION,
    ClassLearningProfile,
    ClassroomTask,
    Evidence,
    PreClassDiagnosticTask,
    StudentSubmission,
    TaskAssignment,
)


class PreClassContractsBundle(_ContractModel):
    """六个根契约的打包容器（仅用于 JSON Schema / TS 生成）。"""

    schema_version: str = Field(default=PREP_CONTRACTS_SCHEMA_VERSION)
    pre_class_diagnostic_task: PreClassDiagnosticTask | None = None
    classroom_task: ClassroomTask | None = None
    task_assignment: TaskAssignment | None = None
    student_submission: StudentSubmission | None = None
    evidence: Evidence | None = None
    class_learning_profile: ClassLearningProfile | None = None


CONTRACT_MODELS: dict[str, type[_ContractModel]] = {
    "PreClassDiagnosticTask": PreClassDiagnosticTask,
    "ClassroomTask": ClassroomTask,
    "TaskAssignment": TaskAssignment,
    "StudentSubmission": StudentSubmission,
    "Evidence": Evidence,
    "ClassLearningProfile": ClassLearningProfile,
}

# 契约名顺序（文档/schema/TS 输出稳定顺序，与 CONTRACT_KINDS 一致）
REGISTRY_ORDER: tuple[str, ...] = CONTRACT_KINDS


def contract_model(kind: str) -> type[_ContractModel]:
    if kind not in CONTRACT_MODELS:
        raise KeyError(f"未知契约 {kind!r}；允许: {', '.join(CONTRACT_MODELS)}")
    return CONTRACT_MODELS[kind]


def bundle_schema() -> dict[str, Any]:
    """打包后六契约的 JSON Schema（$defs 含全部子模型，根为 Bundle）。"""
    return PreClassContractsBundle.model_json_schema()
