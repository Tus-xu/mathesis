"""契约 1 · AccessContext / Capability（P00）。

事实权威：**8000 是唯一身份与 capability 权威**（DECISIONS §2、总手册 §5.1）。
8100/8200/8300 只消费本契约，不得自签面向用户的长期令牌，不得本地派生 capability 真值。

字段（任务书原文）：
actor / tenant / workspace / domain / resource scopes / security_version / trace。

不可让步的语义：
- `security_version` 必填 —— 它是撤权检查锚点（网关每次校验）；
  缺失即 fail，绝不允许默认 0 后放行。
- `workspace` 只允许 registry 登记值；未知值 fail closed。
- `resource_scopes` 是**对象级授权**载体：命令涉及具体资源时必须非自引用，
  禁止"只比较 JWT identity"。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .base import (
    KNOWN_WORKSPACES,
    UuidStr,
    VersionedModel,
)
from .errors import ContractUnknownEnumValue, ContractValidationFailed

WorkspaceLiteral = Literal["student", "teacher", "research", "admin"]
ScopeKind = Literal["tenant", "school", "class", "session", "project", "artifact", "job", "self"]


class ResourceScope(VersionedModel):
    """资源作用域（对象级授权的最小单元）。

    `self` 表示"仅本人自身"，**不得同时携带其它对象的 id**——
    否则等价于用 identity 冒充对象归属校验。
    """

    kind: ScopeKind
    id: UuidStr | None = None
    role: Literal["owner", "editor", "reviewer", "viewer", "member"] | None = None

    @model_validator(mode="after")
    def _self_scope_must_not_carry_object_id(self) -> ResourceScope:
        if self.kind == "self" and self.id is not None:
            raise ContractValidationFailed(
                "scope.kind='self' 不得携带 object id（防止以 identity 冒充对象归属校验）"
            )
        if self.kind != "self" and self.id is None:
            raise ContractValidationFailed(f"scope.kind={self.kind!r} 必须携带 object id")
        return self


class CapabilityRef(VersionedModel):
    """capability 引用（`workspace.<name>.enter` / `<domain>.<verb>` 形态）。"""

    name: str = Field(min_length=3, max_length=128, pattern=r"^[a-z][a-z0-9_.]*$")
    source: Literal["ROLE_BINDING", "ENTITLEMENT", "POLICY"] = "ROLE_BINDING"


class AccessContext(VersionedModel):
    """Workspace 入口返回的访问上下文（8000 唯一权威）。"""

    actor_id: UuidStr
    tenant_id: UuidStr
    session_id: UuidStr | None = None
    workspace: WorkspaceLiteral
    domain: str = Field(min_length=1, max_length=64)
    capabilities: list[str] = Field(default_factory=list)
    resource_scopes: list[ResourceScope] = Field(default_factory=list)
    security_version: int = Field(ge=1)
    policy_version: str = Field(min_length=1, max_length=32)
    trace_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    expires_at: datetime | None = None
    authority: Literal["api"] = "api"

    @field_validator("workspace")
    @classmethod
    def _workspace_must_be_registered(cls, v: str) -> str:
        if v not in KNOWN_WORKSPACES:
            raise ContractUnknownEnumValue(
                f"未登记的 workspace {v!r}；允许 {KNOWN_WORKSPACES}（未知值不得默认放行）"
            )
        return v

    @field_validator("capabilities")
    @classmethod
    def _capabilities_shape(cls, v: list[str]) -> list[str]:
        for name in v:
            if not name or name != name.strip() or len(name) > 128:
                raise ContractValidationFailed(f"非法 capability 名 {name!r}")
            if name.startswith("admin.") and len(name.split(".")) < 2:
                raise ContractValidationFailed("admin capability 必须带具体动作")
        return v


__all__ = ["AccessContext", "ResourceScope", "CapabilityRef"]
