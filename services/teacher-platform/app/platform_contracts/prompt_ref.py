"""契约 10 · PromptRef（P00）。

字段（任务书原文）：
prompt_id / version / hash / input_schema / output_schema / model_policy /
eval_set / release_status / rollback version。

语义（任务书 ADR：Prompt 版本发布/回滚和 held-out eval Gate）：
- **生产不使用未发布草稿 prompt**（`release_status=DRAFT` 的 prompt 不得进入生产运行）；
- `RELEASED` 必须同时具备 `eval_set_id`（held-out eval gate 证据）与 `rollback_version`；
- `DRAFT` **不得**携带 `rollback_version`（否则等于把草稿伪装成可回滚发布版）；
- prompt 字符串不得散落在 Vue/Python 页面里 —— 只能通过本契约引用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import Sha256Hex, VersionedModel
from .errors import ContractPolicyViolation, ContractValidationFailed

ReleaseStatus = Literal["DRAFT", "CANDIDATE", "RELEASED", "DEPRECATED", "ROLLED_BACK"]


class ModelPolicy(VersionedModel):
    provider: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=128)
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=None, ge=1)
    fallback_model_id: str | None = Field(default=None, max_length=128)


class EvalSetRef(VersionedModel):
    eval_set_id: str = Field(min_length=1, max_length=128)
    held_out: bool = True
    passed_at: datetime | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    report_ref: str | None = Field(default=None, max_length=512)


class PromptRef(VersionedModel):
    prompt_id: str = Field(min_length=3, max_length=128, pattern=r"^[a-z][a-z0-9_.]*$")
    version: str = Field(min_length=1, max_length=32)
    hash: Sha256Hex
    input_schema_id: str = Field(min_length=1, max_length=128)
    output_schema_id: str = Field(min_length=1, max_length=128)
    model_policy: ModelPolicy
    eval_set_id: str | None = Field(default=None, max_length=128)
    eval_set: EvalSetRef | None = None
    release_status: ReleaseStatus
    rollback_version: str | None = Field(default=None, max_length=32)
    released_at: datetime | None = None
    deprecated_at: datetime | None = None
    owner: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _released_requires_eval_and_rollback(self) -> PromptRef:
        if self.release_status == "RELEASED":
            if not self.rollback_version:
                raise ContractPolicyViolation(
                    "RELEASED prompt 必须携带 rollback_version（回滚契约）"
                )
            if not (self.eval_set_id or self.eval_set):
                raise ContractPolicyViolation(
                    "RELEASED prompt 必须携带 held-out eval_set（未过 eval gate 不得发布）"
                )
            if self.eval_set is not None and self.eval_set.held_out is False:
                raise ContractPolicyViolation("发布所依据的 eval set 必须是 held-out")
        return self

    @model_validator(mode="after")
    def _draft_must_not_carry_rollback(self) -> PromptRef:
        if self.release_status == "DRAFT" and self.rollback_version is not None:
            raise ContractPolicyViolation(
                "DRAFT prompt 不得携带 rollback_version（草稿不得伪装成可回滚发布版）"
            )
        return self

    @model_validator(mode="after")
    def _rollback_target_must_differ(self) -> PromptRef:
        if self.rollback_version is not None and self.rollback_version == self.version:
            raise ContractValidationFailed("rollback_version 不得等于当前 version")
        return self


__all__ = ["PromptRef", "ReleaseStatus", "ModelPolicy", "EvalSetRef"]
