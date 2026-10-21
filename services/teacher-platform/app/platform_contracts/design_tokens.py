"""契约 13 · PlatformDesignTokenContract（P00）。

字段（任务书原文）：
foundation 与 semantic token 的名称、类型、alias、deprecated/兼容规则；
包含 text/surface/action/status/focus/elevation/spacing/radius/typography
和可选 research-accent，**不在契约中重复业务页颜色值**。

语义（DECISIONS §9，M00 §"视觉设计系统"）：
- 契约只描述**token 的抽象形状与别名图**，不写死具体业务页 hex；
  具体值由 `TOK` owner 的单一语义入口（`teacher-tokens.css` 定案）承载；
- `footer`/`deprecated` 规则机器化：deprecated token 必须有 `replaced_by` 与 `deprecated_since`；
- 字体：**不做脱离现有 token 的禁用清单**；保留当前中文字体主顺序与
  Inter/Arial/system fallback 作用（见 ADR-FONT-POLICY）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import VersionedModel
from .errors import ContractValidationFailed

TokenTier = Literal["FOUNDATION", "SEMANTIC"]
TokenCategory = Literal[
    "text",
    "surface",
    "action",
    "status",
    "focus",
    "elevation",
    "spacing",
    "radius",
    "typography",
    "research-accent",
]
TokenValueType = Literal["color", "dimension", "shadow", "font-family", "font-weight", "number", "alias"]

REQUIRED_CATEGORIES: tuple[str, ...] = (
    "text",
    "surface",
    "action",
    "status",
    "focus",
    "elevation",
    "spacing",
    "radius",
    "typography",
)


class DesignToken(VersionedModel):
    """单个 token 的定义（名称 + 类型 + 别名 + 弃用规则）。"""

    name: str = Field(min_length=3, max_length=128, pattern=r"^--[a-z][a-z0-9-]*$")
    tier: TokenTier
    category: TokenCategory
    value_type: TokenValueType
    alias_of: str | None = Field(default=None, max_length=128)
    literal_value: str | None = Field(default=None, max_length=256)
    deprecated: bool = False
    deprecated_since: str | None = Field(default=None, max_length=32)
    replaced_by: str | None = Field(default=None, max_length=128)
    compat_note: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def _alias_or_literal_exactly_one(self) -> DesignToken:
        if self.value_type == "alias":
            if not self.alias_of:
                raise ContractValidationFailed(f"{self.name}: value_type=alias 必须给出 alias_of")
            if self.literal_value is not None:
                raise ContractValidationFailed(f"{self.name}: alias token 不得同时给 literal_value")
        else:
            if self.literal_value is None:
                raise ContractValidationFailed(
                    f"{self.name}: value_type={self.value_type} 必须给出 literal_value"
                )
        return self

    @model_validator(mode="after")
    def _foundation_must_be_literal(self) -> DesignToken:
        if self.tier == "FOUNDATION" and self.value_type == "alias":
            raise ContractValidationFailed("FOUNDATION token 必须是字面值（不得 alias）")
        return self

    @model_validator(mode="after")
    def _deprecated_requires_replacement_and_date(self) -> DesignToken:
        if self.deprecated:
            if not self.replaced_by:
                raise ContractValidationFailed(f"{self.name}: deprecated token 必须给出 replaced_by")
            if not self.deprecated_since:
                raise ContractValidationFailed(
                    f"{self.name}: deprecated token 必须给出 deprecated_since（兼容规则）"
                )
        return self


class FontStack(VersionedModel):
    """字体栈（**保留**当前中文字体主顺序 + Inter/Arial/system fallback）。

    本契约不定义禁用清单；如需改字体必须先有跨 Windows/macOS/Linux 字形、
    性能、版权与视觉回归 ADR（见 ADR-FONT-POLICY）。
    """

    name: str = Field(min_length=1, max_length=64)
    stack: list[str] = Field(min_length=1)
    usage: Literal["global", "numeric", "code", "display"] = "global"
    network_font: bool = False

    @model_validator(mode="after")
    def _no_new_network_font(self) -> FontStack:
        if self.network_font:
            raise ContractValidationFailed("不得引入新网络字体（沿用项目已配置的中文 UI 字体栈）")
        return self


class PlatformDesignTokenContract(VersionedModel):
    tokens: list[DesignToken] = Field(min_length=1)
    font_stacks: list[FontStack] = Field(default_factory=list)
    token_entry_module: str = Field(
        default="src/styles/teacher-tokens.css",
        description="唯一语义入口（TOK owner）",
        max_length=256,
    )
    business_page_hex_included: bool = Field(
        default=False, description="契约不重复业务页颜色值"
    )

    @model_validator(mode="after")
    def _all_required_categories_present(self) -> PlatformDesignTokenContract:
        categories = {t.category for t in self.tokens}
        missing = [c for c in REQUIRED_CATEGORIES if c not in categories]
        if missing:
            raise ContractValidationFailed(f"缺少必需 token 类别: {missing}")
        return self

    @model_validator(mode="after")
    def _no_business_page_hex(self) -> PlatformDesignTokenContract:
        if self.business_page_hex_included:
            raise ContractValidationFailed(
                "平台 token 契约不得内联业务页颜色值（业务页 hex 属于 TOK 单一入口）"
            )
        return self

    @model_validator(mode="after")
    def _alias_targets_must_exist(self) -> PlatformDesignTokenContract:
        names = {t.name for t in self.tokens}
        for token in self.tokens:
            if token.alias_of and token.alias_of not in names:
                raise ContractValidationFailed(f"{token.name}: alias_of={token.alias_of} 不在 token 集合内")
            if token.replaced_by and token.replaced_by not in names:
                raise ContractValidationFailed(
                    f"{token.name}: replaced_by={token.replaced_by} 不在 token 集合内"
                )
        return self


__all__ = [
    "PlatformDesignTokenContract",
    "DesignToken",
    "FontStack",
    "TokenTier",
    "TokenCategory",
    "TokenValueType",
    "REQUIRED_CATEGORIES",
]
