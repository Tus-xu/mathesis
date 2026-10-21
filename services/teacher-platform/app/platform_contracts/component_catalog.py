"""契约 15 · ComponentCatalogContract（P00）。

字段（任务书原文）：
component_type / version / domain / props_schema / event_schema / risk / renderer /
accessibility / fallback / deprecated；
平台维护**共享原语**，各域通过 manifest 注册领域组件，**AI 只能产生已注册类型**。

语义（DECISIONS §7，总手册 §9.4）：
- 模型只能产生 `component_type` 属于已注册 catalog 的 typed spec；
  **不得**下发任意 HTML/CSS/JS（`renderer.kind=ARBITRARY_HTML` 必须被拒）；
- 每个组件必须声明 accessibility 与 fallback（渲染失败/未知版本的降级）；
- 领域组件（teacher/research）通过 manifest 注册，平台原语由 `PLAT`/`TOK` 维护。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import VersionedModel
from .errors import ContractPolicyViolation, ContractValidationFailed

ComponentDomain = Literal["platform", "teacher", "research", "student"]
RendererKind = Literal[
    "PLATFORM_PRIMITIVE",
    "REGISTERED_DOMAIN_COMPONENT",
    "TEXT_FALLBACK",
]
RiskLevel = Literal["LOW", "MEDIUM", "HIGH"]


class RendererSpec(VersionedModel):
    kind: RendererKind
    component_name: str = Field(min_length=1, max_length=128)
    # 允许的渲染器只能来自已注册实现；禁止任意 HTML/CSS/JS
    arbitrary_html: bool = False
    arbitrary_css: bool = False
    arbitrary_js: bool = False

    @model_validator(mode="after")
    def _no_arbitrary_markup(self) -> RendererSpec:
        if self.arbitrary_html or self.arbitrary_css or self.arbitrary_js:
            raise ContractPolicyViolation(
                "禁止任意模型 HTML/CSS/JS（模型只产生经 schema 校验的 component spec 数据）"
            )
        return self


class AccessibilitySpec(VersionedModel):
    role: str = Field(min_length=1, max_length=64)
    label_i18n_key: str | None = Field(default=None, max_length=128)
    focusable: bool = True
    keyboard_operable: bool = True
    aria_live: Literal["off", "polite", "assertive"] | None = None


class FallbackSpec(VersionedModel):
    strategy: Literal["TEXT_SUMMARY", "PLACEHOLDER", "HIDE_WITH_NOTICE", "RAW_JSON_LINK"]
    reason_i18n_key: str | None = Field(default=None, max_length=128)
    min_supported_version: str | None = Field(default=None, max_length=32)


class ComponentEntry(VersionedModel):
    component_type: str = Field(min_length=3, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9]*$")
    version: str = Field(min_length=1, max_length=32)
    domain: ComponentDomain
    props_schema: dict
    event_schema: dict
    risk: RiskLevel
    renderer: RendererSpec
    accessibility: AccessibilitySpec
    fallback: FallbackSpec
    deprecated: bool = False
    deprecated_since: str | None = Field(default=None, max_length=32)
    replaced_by: str | None = Field(default=None, max_length=128)
    registered_by_manifest: str | None = Field(default=None, max_length=128)
    ai_generatable: bool = True

    @model_validator(mode="after")
    def _domain_components_need_manifest(self) -> ComponentEntry:
        if self.domain != "platform" and not self.registered_by_manifest:
            raise ContractValidationFailed(
                f"{self.component_type}: 领域组件必须通过 manifest 注册（registered_by_manifest）"
            )
        return self

    @model_validator(mode="after")
    def _deprecated_requires_replacement(self) -> ComponentEntry:
        if self.deprecated and not (self.replaced_by or self.fallback):
            raise ContractValidationFailed(f"{self.component_type}: deprecated 必须给出替代或 fallback")
        return self

    @model_validator(mode="after")
    def _props_schema_must_be_object(self) -> ComponentEntry:
        if self.props_schema.get("type") != "object":
            raise ContractValidationFailed(f"{self.component_type}: props_schema 顶层必须是 object")
        return self


class ComponentCatalogContract(VersionedModel):
    catalog_id: str = Field(min_length=3, max_length=64, default="platform-component-catalog")
    components: list[ComponentEntry] = Field(min_length=1)
    ai_may_invent_types: bool = Field(default=False)

    @model_validator(mode="after")
    def _ai_cannot_invent_types(self) -> ComponentCatalogContract:
        if self.ai_may_invent_types:
            raise ContractPolicyViolation("AI 只能产生已注册类型（ai_may_invent_types 必须为 False）")
        return self

    @model_validator(mode="after")
    def _unique_types(self) -> ComponentCatalogContract:
        seen: set[tuple[str, str]] = set()
        for c in self.components:
            key = (c.component_type, c.version)
            if key in seen:
                raise ContractValidationFailed(f"重复组件 {key}")
            seen.add(key)
        return self

    def has_type(self, component_type: str) -> bool:
        return any(c.component_type == component_type for c in self.components)


__all__ = [
    "ComponentCatalogContract",
    "ComponentEntry",
    "RendererSpec",
    "AccessibilitySpec",
    "FallbackSpec",
    "ComponentDomain",
    "RendererKind",
]
