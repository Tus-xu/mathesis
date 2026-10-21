"""M3 教师端：Artifact 版本与状态机（§9 唯一服务，禁止 endpoint 自拼状态）。

状态机：draft → confirmed → published（仅可发布类型）；任意有效态 → archived。
- 修改 confirmed/published 产物 → 创建新 draft 版本（共享 logical_id），不原地篡改；
- 发布/确认/归档使用幂等键，乐观版本；版本冲突 40901，未确认 42210；
- 降级 Artifact 仍可确认，但必须保留 degraded=true / engine / warnings；
- 正式写操作写 teacher_actions 审计（仅 digest/脱敏摘要）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.teacher.scope import raise_http
from app.models.teacher import TeacherAction, TeachingArtifact

# 稳定错误码
ERR_VERSION_CONFLICT = 40901
ERR_DUPLICATE_REQUEST = 40902
ERR_CONFIRMATION_REQUIRED = 42210
ERR_NOT_FOUND = 40400
ERR_VALIDATION = 40001

ARTIFACT_STATUS = ("draft", "confirmed", "published", "archived")
# 有学生可见出口、可正式发布的产物类型
PUBLISHABLE_TYPES = frozenset({"lesson_plan", "slide_deck", "quiz_set"})


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _serialize_artifact(a: TeachingArtifact) -> dict:
    return {
        "artifact_id": str(a.id),
        "artifact_type": a.artifact_type,
        "scene": a.scene,
        "class_id": str(a.class_id) if a.class_id else None,
        "owner_id": str(a.owner_id),
        "status": a.status,
        "version": a.version,
        "content": a.payload,
        "source_refs": a.source_refs or [],
        "validation": a.validation or {},
        "warnings": a.warnings or [],
        "degraded": a.degraded,
        "engine": a.engine,
        "parent_artifact_id": str(a.parent_artifact_id) if a.parent_artifact_id else None,
        "confirmed_by": str(a.confirmed_by) if a.confirmed_by else None,
        "confirmed_at": a.confirmed_at.isoformat() if a.confirmed_at else None,
        "published_at": a.published_at.isoformat() if a.published_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "updated_at": a.updated_at.isoformat() if a.updated_at else None,
    }


async def _replay_existing(db: AsyncSession, idempotency_key: str) -> dict | None:
    """幂等键已存在 → 返回首次动作记录（重放），否则 None。"""
    rs = await db.execute(
        select(TeacherAction).where(TeacherAction.idempotency_key == idempotency_key)
    )
    action = rs.scalar_one_or_none()
    if action is None:
        return None
    details = dict(action.details or {})
    # 返回业务主体（复杂对象以 artifact_id 维度点时加载）
    return {"replayed": True, "action_type": action.action_type, **details}


async def _write_action(
    db: AsyncSession,
    *,
    teacher_id: uuid.UUID,
    action_type: str,
    class_id: uuid.UUID | None,
    artifact_id: uuid.UUID | None,
    client_request_id: str,
    idempotency_key: str | None,
    before: Any,
    after: Any,
    request_id: str | None,
    extra: dict | None = None,
) -> None:
    db.add(
        TeacherAction(
            teacher_id=teacher_id,
            class_id=class_id,
            artifact_id=artifact_id,
            action_type=action_type,
            client_request_id=client_request_id,
            idempotency_key=idempotency_key,
            before_digest=_digest(before) if before is not None else None,
            after_digest=_digest(after) if after is not None else None,
            request_id=request_id,
            details={**(extra or {}), "artifact_id": str(artifact_id) if artifact_id else None},
        )
    )


async def get_owned_artifact(
    db: AsyncSession, teacher_id: uuid.UUID, artifact_id: uuid.UUID
) -> TeachingArtifact:
    """按 ID 读取，只允许 owner（跨教师不可见）。"""
    a = await db.get(TeachingArtifact, artifact_id)
    if a is None or a.deleted_at is not None:
        raise_http(ERR_NOT_FOUND, status.HTTP_404_NOT_FOUND, "not_found", recoverable=False)
    if a.owner_id != teacher_id:
        raise_http(ERR_NOT_FOUND, status.HTTP_404_NOT_FOUND, "not_found", recoverable=False)
    return a


async def create_artifact(
    db: AsyncSession,
    *,
    owner_id: uuid.UUID,
    artifact_type: str,
    scene: str,
    class_id: uuid.UUID | None,
    payload: dict | None = None,
    source_refs: list | None = None,
    engine: str = "local",
    degraded: bool = False,
    warnings: list | None = None,
    validation: dict | None = None,
    parent_artifact_id: uuid.UUID | None = None,
) -> TeachingArtifact:
    """创建一个新逻辑产物的 v1 draft。"""
    a = _new_version_row(
        owner_id=owner_id,
        artifact_type=artifact_type,
        scene=scene,
        class_id=class_id,
        logical_id=None,  # 占位
        payload=payload,
        source_refs=source_refs,
        engine=engine,
        degraded=degraded,
        warnings=warnings,
        validation=validation,
        parent_artifact_id=parent_artifact_id,
        version=1,
    )
    a.id = uuid.uuid4()  # 先行确定主键，使 logical_id（root）可用
    a.logical_id = a.id
    db.add(a)
    return a


def _new_version_row(
    *,
    owner_id: uuid.UUID,
    artifact_type: str,
    scene: str,
    class_id: uuid.UUID | None,
    logical_id: uuid.UUID,
    payload: dict | None,
    source_refs: list | None,
    engine: str,
    degraded: bool,
    warnings: list | None,
    validation: dict | None,
    parent_artifact_id: uuid.UUID | None,
    version: int,
) -> TeachingArtifact:
    return TeachingArtifact(
        logical_id=logical_id,
        artifact_type=artifact_type,
        owner_id=owner_id,
        class_id=class_id,
        scene=scene,
        version=version,
        status="draft",
        payload=payload or {},
        source_refs=source_refs or [],
        validation=validation or {},
        warnings=warnings or [],
        engine=engine,
        degraded=degraded,
        parent_artifact_id=parent_artifact_id,
    )


async def update_artifact(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    artifact_id: uuid.UUID,
    *,
    version: int,
    payload: dict,
    source_refs: list | None = None,
) -> tuple[TeachingArtifact, bool]:
    """编辑产物。

    - draft：乐观版本校验（不一致 → 40901），原地更新 payload；
    - confirmed/published：创建新 draft 版本（共享 logical_id），不原地篡改。
    返回 (artifact, created_new: bool)。
    """
    a = await get_owned_artifact(db, teacher_id, artifact_id)

    if a.status in ("confirmed", "published"):
        created = await _new_version_from(db, a, payload=payload, source_refs=source_refs)
        return created, True

    if a.status != "draft":
        raise_http(ERR_CONFIRMATION_REQUIRED, 422, "confirmation_required", recoverable=True)

    if a.version != version:
        raise_http(ERR_VERSION_CONFLICT, 409, "version_conflict", recoverable=True)
    a.payload = payload
    if source_refs is not None:
        a.source_refs = source_refs
    return a, False


async def _new_version_from(
    db: AsyncSession,
    source: TeachingArtifact,
    *,
    payload: dict,
    source_refs: list | None,
) -> TeachingArtifact:
    max_ver = await db.scalar(
        select(func.max(TeachingArtifact.version)).where(
            TeachingArtifact.logical_id == source.logical_id
        )
    )
    next_ver = (max_ver or 0) + 1
    created = _new_version_row(
        owner_id=source.owner_id,
        artifact_type=source.artifact_type,
        scene=source.scene,
        class_id=source.class_id,
        logical_id=source.logical_id,
        payload=payload,
        source_refs=source_refs if source_refs is not None else source.source_refs,
        engine=source.engine,
        degraded=source.degraded,
        warnings=source.warnings,
        validation=source.validation,
        parent_artifact_id=source.id,
        version=next_ver,
    )
    db.add(created)
    return created


async def confirm_artifact(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    artifact_id: uuid.UUID,
    *,
    client_request_id: str,
    idempotency_key: str | None,
    request_id: str | None = None,
) -> dict:
    """draft → confirmed（仅教师显式动作，idempotent，写审计）。"""
    a = await get_owned_artifact(db, teacher_id, artifact_id)
    before = _serialize_artifact(a)

    # 幂等重放
    if idempotency_key:
        replay = await _replay_existing(db, idempotency_key)
        if replay is not None:
            return {"artifact": _serialize_artifact(a), **replay}

    if a.status != "draft":
        raise_http(ERR_VALIDATION, 422, "invalid_state", current_status=a.status, recoverable=True)

    a.status = "confirmed"
    a.confirmed_by = teacher_id
    a.confirmed_at = _now()
    # 降级产物仍保留降级信息
    await _write_action(
        db,
        teacher_id=teacher_id,
        action_type="artifact.confirm",
        class_id=a.class_id,
        artifact_id=a.id,
        client_request_id=client_request_id,
        idempotency_key=idempotency_key,
        before=before,
        after=_serialize_artifact(a),
        request_id=request_id,
    )
    return {"artifact": _serialize_artifact(a), "replayed": False}


async def publish_artifact(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    artifact_id: uuid.UUID,
    *,
    client_request_id: str,
    idempotency_key: str | None,
    request_id: str | None = None,
) -> dict:
    """confirmed → published（仅可发布类型；幂等；写审计）。"""
    a = await get_owned_artifact(db, teacher_id, artifact_id)
    before = _serialize_artifact(a)

    if idempotency_key:
        replay = await _replay_existing(db, idempotency_key)
        if replay is not None:
            return {"artifact": _serialize_artifact(a), **replay}

    if a.artifact_type not in PUBLISHABLE_TYPES:
        raise_http(
            ERR_VALIDATION, 422, "artifact_type_not_publishable", recoverable=True
        )
    if a.status != "confirmed":
        raise_http(ERR_CONFIRMATION_REQUIRED, 422, "confirmation_required", recoverable=True)

    a.status = "published"
    a.published_at = _now()
    await _write_action(
        db,
        teacher_id=teacher_id,
        action_type="artifact.publish",
        class_id=a.class_id,
        artifact_id=a.id,
        client_request_id=client_request_id,
        idempotency_key=idempotency_key,
        before=before,
        after=_serialize_artifact(a),
        request_id=request_id,
    )
    return {"artifact": _serialize_artifact(a), "replayed": False}


async def archive_artifact(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    artifact_id: uuid.UUID,
    *,
    client_request_id: str,
    idempotency_key: str | None,
    request_id: str | None = None,
) -> dict:
    """任意有效状态 → archived（不物理删除审计记录；幂等）。"""
    a = await get_owned_artifact(db, teacher_id, artifact_id)
    before = _serialize_artifact(a)

    if idempotency_key:
        replay = await _replay_existing(db, idempotency_key)
        if replay is not None:
            return {"artifact": _serialize_artifact(a), **replay}

    if a.status == "archived":
        return {"artifact": _serialize_artifact(a), "replayed": False}

    a.status = "archived"
    await _write_action(
        db,
        teacher_id=teacher_id,
        action_type="artifact.archive",
        class_id=a.class_id,
        artifact_id=a.id,
        client_request_id=client_request_id,
        idempotency_key=idempotency_key,
        before=before,
        after=_serialize_artifact(a),
        request_id=request_id,
    )
    return {"artifact": _serialize_artifact(a), "replayed": False}


__all__ = [
    "create_artifact",
    "get_owned_artifact",
    "update_artifact",
    "confirm_artifact",
    "publish_artifact",
    "archive_artifact",
    "_serialize_artifact",
    "PUBLISHABLE_TYPES",
]
