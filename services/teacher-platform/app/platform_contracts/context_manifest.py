"""契约 12 · ContextManifest（P00）。

字段（任务书原文）：
request context、recent conversation window、versioned session summary、
domain state refs、preference refs、evidence refs、trust label、token count、
truncation reason、policy version。

语义（总手册 §12.2，DECISIONS §4/§7）：
- 每次 AI Run **只组装必要层**，禁止每次塞全历史/全文；
- 上下文组装顺序固定：Request → Conversation → Summary → Domain State → Preferences → Evidence；
- **用户上传/检索文本始终是 untrusted data**（`trust_labels.evidence_refs` 必须是
  `UNTRUSTED_DATA`）；把它标成 `SYSTEM_INSTRUCTION` 必须被拒绝（prompt injection 防线）；
- `truncated=True` 必须给出 `reason`（裁剪原因），不得静默截断。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import VersionedModel
from .errors import ContractPolicyViolation, ContractValidationFailed

TrustLabel = Literal[
    "TRUSTED_SYSTEM",
    "TRUSTED_POLICY",
    "TRUSTED_USER_INSTRUCTION",
    "UNTRUSTED_DATA",
]
TruncationReason = Literal[
    "TOKEN_BUDGET",
    "WINDOW_LIMIT",
    "RETENTION_EXPIRED",
    "POLICY_FILTER",
    "NONE",
]

# 上下文组装顺序（固定；改变顺序须走 ADR）
CONTEXT_ASSEMBLY_ORDER: tuple[str, ...] = (
    "request_context",
    "recent_conversation",
    "session_summary",
    "domain_state_refs",
    "preference_refs",
    "evidence_refs",
)


class RequestContext(VersionedModel):
    command_type: str = Field(min_length=1, max_length=64)
    budget: dict = Field(default_factory=dict)
    locale: str | None = Field(default=None, max_length=16)


class ConversationWindow(VersionedModel):
    window_size: int = Field(ge=0)
    items: list[dict] = Field(default_factory=list)
    truncated: bool = False


class SessionSummary(VersionedModel):
    version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=8000)
    updated_at: datetime
    pending_items: list[str] = Field(default_factory=list)


class TrustLabels(VersionedModel):
    """每个 section 的信任标签（prompt injection 防线）。"""

    request_context: TrustLabel = "TRUSTED_SYSTEM"
    recent_conversation: TrustLabel = "UNTRUSTED_DATA"
    session_summary: TrustLabel = "TRUSTED_POLICY"
    domain_state_refs: TrustLabel = "TRUSTED_POLICY"
    preference_refs: TrustLabel = "TRUSTED_POLICY"
    evidence_refs: TrustLabel = "UNTRUSTED_DATA"

    @model_validator(mode="after")
    def _evidence_and_conversation_must_be_untrusted(self) -> TrustLabels:
        for field_name in ("evidence_refs", "recent_conversation"):
            label = getattr(self, field_name)
            if label != "UNTRUSTED_DATA":
                raise ContractPolicyViolation(
                    f"trust_labels.{field_name} 必须是 UNTRUSTED_DATA"
                    "（用户上传/检索文本始终 untrusted，不得提升为系统指令）"
                )
        return self


class TruncationInfo(VersionedModel):
    truncated: bool
    reason: TruncationReason = "NONE"

    @model_validator(mode="after")
    def _truncated_requires_reason(self) -> TruncationInfo:
        if self.truncated and self.reason == "NONE":
            raise ContractValidationFailed("truncated=True 必须给出裁剪原因（不得静默截断）")
        if not self.truncated and self.reason != "NONE":
            raise ContractValidationFailed("truncated=False 时 reason 必须为 NONE")
        return self


class ContextManifest(VersionedModel):
    manifest_id: str = Field(min_length=1, max_length=256)
    request_context: RequestContext
    recent_conversation: ConversationWindow
    session_summary: SessionSummary | None = None
    domain_state_refs: list[str] = Field(default_factory=list)
    preference_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    trust_labels: TrustLabels = Field(default_factory=TrustLabels)
    token_count: int = Field(ge=0)
    truncation: TruncationInfo
    policy_version: str = Field(min_length=1, max_length=32)
    built_at: datetime
    raw_conversation_retained: bool = False

    @model_validator(mode="after")
    def _no_full_raw_conversation_by_default(self) -> ContextManifest:
        if self.raw_conversation_retained:
            raise ContractPolicyViolation(
                "不默认保存全量原始对话（raw_conversation_retained 必须为 False）"
            )
        return self

    @model_validator(mode="after")
    def _assembly_order_documented(self) -> ContextManifest:
        # 组装顺序是契约的一部分；此处仅断言各段可枚举（顺序常量见 CONTEXT_ASSEMBLY_ORDER）
        present = [name for name in CONTEXT_ASSEMBLY_ORDER if getattr(self, name) is not None]
        if present != [n for n in CONTEXT_ASSEMBLY_ORDER if n in present]:
            raise ContractValidationFailed("上下文段落顺序不得重排")
        return self


__all__ = [
    "ContextManifest",
    "RequestContext",
    "ConversationWindow",
    "SessionSummary",
    "TrustLabels",
    "TruncationInfo",
    "CONTEXT_ASSEMBLY_ORDER",
]
