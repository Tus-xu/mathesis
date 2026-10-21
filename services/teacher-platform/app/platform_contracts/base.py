"""共享平台契约公共基座（P00）：ID / 版本 / 租户 / trace / 未知字段策略。

与 `app/teacher_prep_v2/contracts/base.py` 同构（复用同一套 pydantic 约定），
但职责不同：本文件只服务**跨域平台契约**，不承载学生跨端六契约。

关键设计：
- `_PlatformModel`：`extra="allow"`，未知**字段**保留为观察项（写入 `model_extra`），
  由 reader 汇总为 `unknown_fields`；**未知枚举值一律由各契约的 validator 拒绝**。
- 所有 ID 为小写 UUID 字符串；sha256 统一 64 hex 小写。
- `schema_version` 语义见 `app/platform_contracts/versioning.py`。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 契约目录版本；任何 major 断裂必须 bump（reader 对 major>current fail closed）
PLATFORM_CONTRACTS_SCHEMA_VERSION = "1.0.0"
PLATFORM_CONTRACTS_BUNDLE_VERSION = PLATFORM_CONTRACTS_SCHEMA_VERSION

UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
SHA256_RE = r"^[0-9a-f]{64}$"
TRACE_ID_RE = r"^[0-9a-f]{32}$"
SPAN_ID_RE = r"^[0-9a-f]{16}$"
TRACEPARENT_RE = (
    r"^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$"
)
OPAQUE_REF_RE = r"^[A-Za-z0-9._:/@-]{1,512}$"
REQUEST_ID_RE = r"^[A-Za-z0-9._:-]{8,128}$"

UuidStr = Annotated[str, Field(pattern=UUID_RE)]
Sha256Hex = Annotated[str, Field(pattern=SHA256_RE)]
TraceId = Annotated[str, Field(pattern=TRACE_ID_RE)]
SpanId = Annotated[str, Field(pattern=SPAN_ID_RE)]
Traceparent = Annotated[str, Field(pattern=TRACEPARENT_RE)]
OpaqueRef = Annotated[str, Field(pattern=OPAQUE_REF_RE)]
RequestId = Annotated[str, Field(pattern=REQUEST_ID_RE)]

# 平台 Workspace（registry 登记值；未知值不得放行）
WorkspaceName = str
KNOWN_WORKSPACES = ("student", "teacher", "research", "admin")


class _PlatformModel(BaseModel):
    """平台契约基类：未知字段保留为观察项（向前兼容），不做静默语义升级。"""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)


class VersionedModel(_PlatformModel):
    schema_version: str = Field(default=PLATFORM_CONTRACTS_SCHEMA_VERSION)


def utc_now() -> datetime:
    return datetime.now(UTC)


def normalize_sha256(value: str) -> str:
    """sha256 统一小写；大小写混写视为同一 hash（避免指纹漂移）。"""
    return value.strip().lower()


class _Sha256Mixin(_PlatformModel):
    """为含 hash 字段的契约提供统一归一化（子类覆盖字段名）。"""

    @field_validator("*", check_fields=False)
    @classmethod
    def _noop(cls, v):  # pragma: no cover - 占位，子类自行声明
        return v


__all__ = [
    "PLATFORM_CONTRACTS_SCHEMA_VERSION",
    "PLATFORM_CONTRACTS_BUNDLE_VERSION",
    "UuidStr",
    "Sha256Hex",
    "TraceId",
    "SpanId",
    "Traceparent",
    "OpaqueRef",
    "RequestId",
    "WorkspaceName",
    "KNOWN_WORKSPACES",
    "VersionedModel",
    "utc_now",
    "normalize_sha256",
]
