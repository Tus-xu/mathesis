"""契约 14 · WorkspaceLayoutContract（P00）。

字段（任务书原文）：
用**通用 named slots/regions** 定义 navigation-or-conversation、artifact canvas、
evidence rail、toolbar/status 与响应式折叠；
**不在平台类型中写死 Teacher/Research 具体 artifact 枚举**。

语义（DECISIONS §7，总手册 §9.2）：
- Conversation-first Workspace：禁止 Form-first / Stepper / Wizard；
- 左：navigation-or-conversation；中：artifact canvas；右：evidence rail；
- 响应式折叠规则必须是**数据**（可被 shell 消费），不是散落在页面的魔法断点；
- 平台类型**只**声明通用 region 名，域（teacher/research）通过 manifest 注册 region 内容。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import VersionedModel
from .errors import ContractValidationFailed

RegionRole = Literal[
    "navigation",
    "navigation-or-conversation",
    "conversation",
    "artifact-canvas",
    "evidence-rail",
    "toolbar",
    "status",
]

PLATFORM_REGION_ROLES: tuple[str, ...] = (
    "navigation",
    "navigation-or-conversation",
    "conversation",
    "artifact-canvas",
    "evidence-rail",
    "toolbar",
    "status",
)

CollapseBehaviour = Literal["COLLAPSE_TO_RAIL", "OVERLAY_DRAWER", "HIDE", "STACK", "KEEP"]


class RegionSpec(VersionedModel):
    """通用 named region（平台只定义角色与折叠行为，不定义业务内容）。"""

    role: RegionRole
    slot_name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    order: int = Field(ge=0, le=99)
    collapsible: bool = True
    default_collapsed: bool = False
    min_width_px: int | None = Field(default=None, ge=0)
    max_width_px: int | None = Field(default=None, ge=1)
    scrollable: bool = True

    @model_validator(mode="after")
    def _width_range_sane(self) -> RegionSpec:
        if (
            self.min_width_px is not None
            and self.max_width_px is not None
            and self.min_width_px > self.max_width_px
        ):
            raise ContractValidationFailed(f"{self.slot_name}: min_width 不得大于 max_width")
        return self


class ResponsiveRule(VersionedModel):
    """断点折叠规则（数据化，供 shell 消费）。"""

    breakpoint_px: int = Field(ge=320, le=3840)
    name: str = Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9-]*$")
    collapse: dict[str, CollapseBehaviour] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _collapse_keys_must_be_known_roles(self) -> ResponsiveRule:
        for role in self.collapse:
            if role not in PLATFORM_REGION_ROLES:
                raise ContractValidationFailed(
                    f"响应式规则引用了未登记的 region role {role!r}（平台只认通用 named slots）"
                )
        return self


class WorkspaceLayoutContract(VersionedModel):
    layout_id: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    regions: list[RegionSpec] = Field(min_length=1)
    responsive_rules: list[ResponsiveRule] = Field(default_factory=list)
    conversation_first: bool = True
    stepper_allowed: bool = False
    wizard_allowed: bool = False
    domain_artifact_enum_embedded: bool = Field(
        default=False, description="平台类型不得写死 Teacher/Research 具体 artifact 枚举"
    )

    @model_validator(mode="after")
    def _must_be_conversation_first(self) -> WorkspaceLayoutContract:
        if not self.conversation_first:
            raise ContractValidationFailed("Workspace 必须 conversation-first（DECISIONS §7）")
        return self

    @model_validator(mode="after")
    def _no_stepper_or_wizard(self) -> WorkspaceLayoutContract:
        if self.stepper_allowed:
            raise ContractValidationFailed("禁止 Stepper（DECISIONS §7）")
        if self.wizard_allowed:
            raise ContractValidationFailed("禁止 Wizard（DECISIONS §7）")
        return self

    @model_validator(mode="after")
    def _no_domain_artifact_enum(self) -> WorkspaceLayoutContract:
        if self.domain_artifact_enum_embedded:
            raise ContractValidationFailed(
                "平台 WorkspaceLayoutContract 不得内嵌域 artifact 枚举"
                "（域通过 manifest 注册）"
            )
        return self

    @model_validator(mode="after")
    def _slot_names_unique_and_roles_known(self) -> WorkspaceLayoutContract:
        seen: set[str] = set()
        for region in self.regions:
            if region.slot_name in seen:
                raise ContractValidationFailed(f"重复 slot_name {region.slot_name!r}")
            seen.add(region.slot_name)
            if region.role not in PLATFORM_REGION_ROLES:
                raise ContractValidationFailed(f"未登记的 region role {region.role!r}")
        return self

    @model_validator(mode="after")
    def _must_have_core_regions(self) -> WorkspaceLayoutContract:
        roles = {r.role for r in self.regions}
        if "navigation-or-conversation" not in roles:
            raise ContractValidationFailed("必须包含 navigation-or-conversation region")
        if "artifact-canvas" not in roles:
            raise ContractValidationFailed("必须包含 artifact-canvas region")
        if "evidence-rail" not in roles:
            raise ContractValidationFailed("必须包含 evidence-rail region")
        return self


__all__ = [
    "WorkspaceLayoutContract",
    "RegionSpec",
    "ResponsiveRule",
    "RegionRole",
    "CollapseBehaviour",
    "PLATFORM_REGION_ROLES",
]
