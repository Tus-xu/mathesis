"""契约 16 · FrontendStateAndCopyContract（P00）。

字段（任务书原文）：
server/stream/client state 分界、localStorage 允许项、stable i18n copy 与
dynamic business content 分界，以及全 Workspace 共用**反模式规则**。

语义（DECISIONS §7/§8，总手册 §9.3）：
- **Server State**（revision/Blueprint/Job/Artifact）以服务端 snapshot + revision 为权威；
- **Stream State** 临时，完成后归并到 server snapshot；
- **Client UI State** 可丢失但**不得改变业务真相**；
- `localStorage` 只允许 UI 偏好（草稿/主题/折叠），**不得存业务真相**；
- 稳定通用文案必须进 i18n/copy catalog；
  **不将"禁止硬编码"误解为所有 UI 不能有稳定文案**。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .base import VersionedModel
from .errors import ContractPolicyViolation, ContractValidationFailed

CATEGORY_ORDER: tuple[str, ...] = ("server", "stream", "client")

ALLOWED_LOCALSTORAGE_KINDS: tuple[str, ...] = (
    "DRAFT_INPUT",
    "THEME",
    "PANEL_COLLAPSED",
    "LOCALE",
    "LAST_ROUTE",
    "UI_PREFERENCE",
)

FORBIDDEN_LOCALSTORAGE_KINDS: tuple[str, ...] = (
    "BUSINESS_TRUTH",
    "AUTHORIZATION",
    "CAPABILITY",
    "JOB_STATUS",
    "SESSION_REVISION",
    "ARTIFACT_CONTENT",
    "EVIDENCE",
)

ANTI_PATTERN_IDS: tuple[str, ...] = (
    "HARDCODED_DYNAMIC_BUSINESS_COPY",
    "FAKE_EVIDENCE",
    "FAKE_PROGRESS_TIMER",
    "LOCALSTORAGE_BUSINESS_AUTHORITY",
    "CLIENT_SIDE_PERMISSION_DECISION",
    "STEPPER_OR_WIZARD",
    "ARBITRARY_MODEL_HTML_CSS_JS",
    "RAW_COLOR_IN_COMPONENT",
    "NEW_GLOBAL_PURPLE_THEME",
    "PER_CARD_SSE_STREAM",
)


class StateBoundary(VersionedModel):
    """三类状态的权威归属。"""

    category: Literal["server", "stream", "client"]
    examples: list[str] = Field(min_length=1)
    authority: Literal["SERVER", "STREAM_TRANSIENT", "CLIENT_UI_ONLY"]
    localStorage_allowed: bool = False
    may_change_business_truth: bool = False
    refresh_recoverable: bool = False

    @model_validator(mode="after")
    def _authority_matches_category(self) -> StateBoundary:
        expected = {
            "server": "SERVER",
            "stream": "STREAM_TRANSIENT",
            "client": "CLIENT_UI_ONLY",
        }[self.category]
        if self.authority != expected:
            raise ContractValidationFailed(
                f"state[{self.category}].authority 必须是 {expected}"
            )
        return self

    @model_validator(mode="after")
    def _client_only_must_not_change_truth(self) -> StateBoundary:
        if self.may_change_business_truth:
            raise ContractPolicyViolation(
                f"state[{self.category}] 不得改变业务真相（server snapshot + revision 才是权威）"
            )
        return self

    @model_validator(mode="after")
    def _localstorage_only_for_client_ui(self) -> StateBoundary:
        if self.localStorage_allowed and self.category != "client":
            raise ContractPolicyViolation(
                f"localStorage 只允许 UI 偏好；state[{self.category}] 不得声明可持久化"
            )
        return self


class LocalStoragePolicy(VersionedModel):
    allowed_kinds: list[str] = Field(default_factory=list)
    forbidden_kinds: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _allowed_must_be_ui_only(self) -> LocalStoragePolicy:
        for kind in self.allowed_kinds:
            if kind in FORBIDDEN_LOCALSTORAGE_KINDS or kind == "BUSINESS_TRUTH":
                raise ContractPolicyViolation(
                    f"localStorage 不得保存 {kind}（业务真相必须在服务端）"
                )
        return self

    @model_validator(mode="after")
    def _forbidden_must_cover_truth(self) -> LocalStoragePolicy:
        missing = [k for k in ("BUSINESS_TRUTH", "AUTHORIZATION") if k not in self.forbidden_kinds]
        if missing:
            raise ContractValidationFailed(f"forbidden_kinds 必须显式包含 {missing}")
        return self


class CopyPolicy(VersionedModel):
    """stable i18n copy 与 dynamic business content 的分界。"""

    i18n_root: str = Field(default="src/i18n", max_length=256)
    stable_copy_catalog_id: str = Field(min_length=1, max_length=128)
    dynamic_business_content_source: Literal["SERVER_SNAPSHOT", "SERVER_EVENT", "JOB", "CONTRACT"] = (
        "SERVER_SNAPSHOT"
    )
    hardcoding_all_ui_text_forbidden: bool = Field(
        default=False,
        description="禁止把'禁止硬编码'误读为所有 UI 不能有稳定文案",
    )
    stable_copy_examples: list[str] = Field(default_factory=list)
    dynamic_content_examples: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _must_not_forbid_all_ui_text(self) -> CopyPolicy:
        if self.hardcoding_all_ui_text_forbidden:
            raise ContractValidationFailed(
                "稳定通用文案（取消/重试/可访问性标签/通用空状态）允许来自 i18n/copy catalog；"
                "不得把'禁止硬编码'扩大为所有 UI 不能有稳定文案"
            )
        return self


class AntiPatternRule(VersionedModel):
    rule_id: str = Field(min_length=3, max_length=64)
    description: str = Field(min_length=3, max_length=512)
    severity: Literal["BLOCKING", "WARNING"] = "BLOCKING"
    detection: Literal["STATIC_SCAN", "REVIEW", "RUNTIME"] = "STATIC_SCAN"


class FrontendStateAndCopyContract(VersionedModel):
    states: list[StateBoundary] = Field(min_length=3)
    localStorage_policy: LocalStoragePolicy
    copy_policy: CopyPolicy
    anti_patterns: list[AntiPatternRule] = Field(min_length=1)
    stream_reuse_per_workspace: bool = Field(
        default=True,
        description="每个 tab/workspace 复用一条 SSE 连接",
        json_schema_extra={"default": True},
    )

    @model_validator(mode="after")
    def _all_three_categories_present(self) -> FrontendStateAndCopyContract:
        cats = {s.category for s in self.states}
        missing = [c for c in CATEGORY_ORDER if c not in cats]
        if missing:
            raise ContractValidationFailed(f"缺少状态类别: {missing}")
        return self

    @model_validator(mode="after")
    def _must_reuse_stream(self) -> FrontendStateAndCopyContract:
        if not self.stream_reuse_per_workspace:
            raise ContractPolicyViolation(
                "SSE 必须每个 tab/workspace 复用一条连接（禁止每张卡片各开一条流）"
            )
        return self

    @model_validator(mode="after")
    def _anti_patterns_must_cover_required(self) -> FrontendStateAndCopyContract:
        ids = {r.rule_id for r in self.anti_patterns}
        missing = [p for p in ANTI_PATTERN_IDS if p not in ids]
        if missing:
            raise ContractValidationFailed(f"反模式规则必须覆盖: {missing}")
        return self


__all__ = [
    "FrontendStateAndCopyContract",
    "StateBoundary",
    "LocalStoragePolicy",
    "CopyPolicy",
    "AntiPatternRule",
    "ALLOWED_LOCALSTORAGE_KINDS",
    "FORBIDDEN_LOCALSTORAGE_KINDS",
    "ANTI_PATTERN_IDS",
    "CATEGORY_ORDER",
]
