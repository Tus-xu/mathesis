"""artifact 字节内容读取（8200）：登记行 → object_key → 本地字节，并做 sha256 完整性闸门。

契约：docs/compose/spec/t24-artifact-edit-export.md S2.6 —— 8100 导出链路从 8200 取 DECK_PPTX
字节；字节与登记 sha256 不一致时**拒绝发出**（宁可 500 也不让漂移内容进入下游 MinIO/artifact）。
"""
from __future__ import annotations

import hashlib
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.artifact_store import LocalArtifactStore
from app.errors import ApiError, ErrorCode
from app.persistence.models import PptV2Artifact

DEFAULT_MEDIA_TYPE = "application/octet-stream"

# kind → media_type（按 kind 推导；未登记 kind 一律 octet-stream，不猜）
_MEDIA_TYPES: dict[str, str] = {
    "DECK_PPTX": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    # PAGE_FRAGMENT 同为 OOXML pptx 字节（engine builder 产物），按包类型声明
    "PAGE_FRAGMENT": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "PAGE_PNG": "image/png",
    "CONTACT_SHEET": "image/png",
    # 契约 S2.6 提到的 json 产物（当前 CHECK 约束未放开该 kind，映射先行，不改约束）
    "PLAN_JSON": "application/json",
}


def media_type_for_kind(kind: str) -> str:
    return _MEDIA_TYPES.get(kind, DEFAULT_MEDIA_TYPE)


async def read_artifact_bytes(session: AsyncSession, artifact_id: str) -> tuple[PptV2Artifact, bytes]:
    """按 id 取登记行 + 校验后字节；任何不满足不变量的情形抛 ApiError（不返回可疑字节）。"""
    try:
        uid = uuid.UUID(artifact_id)
    except (ValueError, AttributeError, TypeError):
        raise ApiError(ErrorCode.NOT_FOUND, "artifact 不存在", error_key="artifact_not_found") from None
    row = await session.get(PptV2Artifact, uid)
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "artifact 不存在", error_key="artifact_not_found")
    data = LocalArtifactStore().get(row.object_key)
    if data is None:
        raise ApiError(
            ErrorCode.NOT_FOUND,
            f"artifact {row.id} 字节缺失（object_key={row.object_key[:80]}）",
            error_key="artifact_bytes_missing",
        )
    actual = hashlib.sha256(data).hexdigest()
    if actual != row.sha256:
        # 完整性闸门：漂移字节绝不外发（下游 8100 会按 hash 落 MinIO）
        raise ApiError(
            ErrorCode.INTERNAL,
            f"artifact {row.id} 字节与登记 sha256 不一致（实际 {actual[:12]}… != 登记 {row.sha256[:12]}…），拒绝发出",
            error_key="artifact_hash_mismatch",
        )
    return row, data
