"""Research 侧 CommandEnvelope（**只消费** P00 契约 2，不修改）。

字段与 `app.platform_contracts.command.CommandEnvelope` 保持等价，
由 `tests/contracts/test_p00_conformance.py` 做字段等价 + schema 哈希锚定守卫。
**运行时零跨服务 import** —— 生产代码不依赖 teacher-platform。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

COMMAND_SCHEMA_VERSION = "1.0.0"


class ResourceScope(BaseModel):
    kind: Literal["tenant", "school", "class", "session", "project", "artifact", "job", "self"]
    id: str | None = None
    role: Literal["owner", "editor", "reviewer", "viewer", "member"] | None = None


class ResearchCommandEnvelope(BaseModel):
    """P00 CommandEnvelope 的 Research 侧镜像（字段等价，由契约测试守卫）。"""

    schema_version: str = COMMAND_SCHEMA_VERSION
    client_request_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{8,128}$")
    expected_revision: int = Field(ge=0)
    actor_id: str
    scope: ResourceScope
    traceparent: str
    deadline: datetime
    idempotency_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    command_type: str = Field(min_length=3, max_length=64)
    payload_ref: str = Field(min_length=1, max_length=512)
    tenant_id: str | None = None

    @field_validator("idempotency_fingerprint")
    @classmethod
    def _fp(cls, v: str) -> str:
        if len(v) != 64:
            raise ValueError("idempotency_fingerprint 必须是 64 位 sha256 hex")
        return v.lower()

    @field_validator("payload_ref")
    @classmethod
    def _no_newline(cls, v: str) -> str:
        if any(c in v for c in ("\n", "\r", "\t")):
            raise ValueError("payload_ref 不得包含换行/制表符")
        return v


class IncomingCommand(ResearchCommandEnvelope):
    """REST body：信封字段强校验 + 允许域专属 `payload` 透传。"""

    model_config = ConfigDict(extra="allow")

    @property
    def domain_payload(self) -> dict:
        return dict(self.model_extra or {}).get("payload", {})


class CommandRecord(BaseModel):
    """幂等记录：唯一键 (tenant_id, actor_id, client_request_id)。"""

    client_request_id: str
    tenant_id: str
    actor_id: str
    command_type: str
    idempotency_fingerprint: str
    session_id: str | None = None
    response_body: dict | None = None
    status: str = "EXECUTED"
