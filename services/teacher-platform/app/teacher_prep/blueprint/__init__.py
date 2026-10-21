"""R16 TeachingBlueprint proposal：上下文 → 可编辑 Blueprint proposal（schema 校验）。

命名空间约定（DECISIONS.md §6/§8）：
- 确定性工作流控权；LLM 只生成 proposal（projector 三重闸门：JSON 解析 +
  schema 校验 + grounding）；
- 教师确认前 proposal 不成为任何事实（确认/驳回闸门在 R14 teacher_prep service）；
- 本包不写库、不发布任务、不生成教案/PPT（R16 范围）。
"""

from __future__ import annotations

from .projector import (
    BlueprintProjectionError,
    BlueprintProposalInvalid,
    GroundingViolation,
    check_grounding,
    derived_bucket_quality,
    parse_and_validate,
    project_blueprint,
    proposal_dto,
    sanitize_title,
)
from .prompt import (
    DEFAULT_MAX_CONTEXT_TOKENS,
    BlueprintPrompt,
    ContextBudgetExceeded,
    build_blueprint_prompt,
    estimate_tokens,
)
from .schema import (
    BLUEPRINT_PROPOSAL_SCHEMA_VERSION,
    BlueprintItem,
    LessonStage,
    TeachingBlueprintProposal,
    collect_refs,
    resolvable_refs_of,
)

__all__ = [
    "BlueprintProjectionError",
    "BlueprintProposalInvalid",
    "BlueprintPrompt",
    "BlueprintItem",
    "ContextBudgetExceeded",
    "DEFAULT_MAX_CONTEXT_TOKENS",
    "GroundingViolation",
    "LessonStage",
    "TeachingBlueprintProposal",
    "BLUEPRINT_PROPOSAL_SCHEMA_VERSION",
    "build_blueprint_prompt",
    "check_grounding",
    "collect_refs",
    "derived_bucket_quality",
    "estimate_tokens",
    "parse_and_validate",
    "project_blueprint",
    "proposal_dto",
    "resolvable_refs_of",
    "sanitize_title",
]
