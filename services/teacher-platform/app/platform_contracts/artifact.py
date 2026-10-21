"""契约 5 · ArtifactRef（P00）。

字段（任务书原文）：
id / version / hash / media_type / size / storage locator / owner / ACL /
lineage / created_by_run / retention。

语义（总手册 §2.5/§10.1，DECISIONS §5）：
- 数据库保存 metadata/hash/lineage/permission；对象存储保存字节；
- artifact 内容 schema 属于各 domain，本契约只管**信封**；
- 浏览器**不得**知道物理 MinIO/IP；`storage_locator` 是服务端内部字段，
  对外传输必须经 signed delivery（见 ADR-BROWSER-BOUNDARY）。
- 强制 `hash` + `acl` + `owner_id` + `storage_locator`；
  缺任一 → fail（防止出现无 hash 的可伪造产物）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .base import OpaqueRef, Sha256Hex, UuidStr, VersionedModel
from .errors import ContractValidationFailed

ArtifactVisibility = Literal["PRIVATE", "SESSION", "PROJECT", "CLASS", "PUBLIC_INTERNAL"]


class StorageLocator(VersionedModel):
    service: Literal["minio", "local_fs", "object_store", "external"] = "minio"
    bucket: str = Field(min_length=1, max_length=128)
    key: str = Field(min_length=1, max_length=1024)

    @field_validator("key")
    @classmethod
    def _no_traversal(cls, v: str) -> str:
        if ".." in v or v.startswith("/"):
            raise ContractValidationFailed("storage key 禁止路径穿越/绝对路径")
        return v


class ArtifactACL(VersionedModel):
    visibility: ArtifactVisibility = "PRIVATE"
    subject_ids: list[UuidStr] = Field(default_factory=list)
    tenant_id: UuidStr | None = None


class ArtifactLineage(VersionedModel):
    created_by_run_id: str = Field(min_length=1, max_length=128)
    derived_from: list[str] = Field(default_factory=list)
    inputs: list[str] = Field(default_factory=list)


class ArtifactRetention(VersionedModel):
    policy: Literal["TERM", "TERM_1YEAR", "ACADEMIC_CYCLE", "ANONYMIZED", "PERMANENT_UNTIL_DELETE"]
    ttl_days: int | None = Field(default=None, ge=1)
    owner_delete_allowed: bool = False


class ArtifactRef(VersionedModel):
    artifact_id: str = Field(min_length=1, max_length=128)
    version: int = Field(ge=1)
    hash: Sha256Hex
    media_type: str = Field(min_length=3, max_length=128, pattern=r"^[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+$")
    size_bytes: int = Field(ge=0)
    storage_locator: StorageLocator
    owner_id: UuidStr
    acl: ArtifactACL
    lineage: ArtifactLineage
    created_by_run_id: str = Field(min_length=1, max_length=128)
    retention: ArtifactRetention
    created_at: datetime | None = None
    content_schema_id: str | None = Field(default=None, max_length=128)

    @field_validator("hash")
    @classmethod
    def _hash_lower(cls, v: str) -> str:
        return v.lower()

    @model_validator(mode="after")
    def _lineage_run_must_match(self) -> ArtifactRef:
        if self.lineage.created_by_run_id != self.created_by_run_id:
            raise ContractValidationFailed(
                "lineage.created_by_run_id 与 created_by_run_id 必须一致（血缘不得自相矛盾）"
            )
        return self


__all__ = [
    "ArtifactRef",
    "StorageLocator",
    "ArtifactACL",
    "ArtifactLineage",
    "ArtifactRetention",
]
