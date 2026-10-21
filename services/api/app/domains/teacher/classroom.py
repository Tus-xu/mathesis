"""M3 教师端：课堂模式与视频/课堂洞察（§14）。

- 课堂模式状态由本地缓存持有，带 TTL、教师与 class scope；启停属于 WRITE，
  要求确认与审计（写 teacher_action，幂等）。
- 视频数据未接入时返回空洞察与 degraded=true，不得伪造参与度。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.teacher.scope import assert_teacher_in_class
from app.models.class_ import Class
from app.models.class_member import ClassMember
from app.models.teacher import ClassroomMode, TeacherAction

ERR_NOT_FOUND = 40400
# 课堂模式 TTL（秒）
CLASSROOM_TTL_S = 4 * 3600

def _now() -> datetime:
    return datetime.now(UTC)


def _state_dict(row: ClassroomMode | None) -> dict | None:
    if row is None:
        return None
    return {
        "enabled": row.enabled,
        "lesson_id": str(row.lesson_id) if row.lesson_id else None,
        "teacher_id": str(row.teacher_id),
        "expires_at": row.expires_at,
        "updated_at": row.updated_at,
    }


async def set_classroom_mode(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID,
    *,
    enabled: bool,
    lesson_id: uuid.UUID | None,
    duration_minutes: int | None,
    client_request_id: str,
    idempotency_key: str | None,
    request_id: str | None,
) -> dict:
    await assert_teacher_in_class(db, teacher_id, class_id)

    row = await db.get(ClassroomMode, class_id)
    cached = _state_dict(row)
    if (
        cached
        and cached.get("expires_at")
        and cached["expires_at"] > _now()
        and cached.get("enabled") == enabled
        and cached.get("lesson_id") == (str(lesson_id) if lesson_id else None)
    ):
        # 幂等重放：状态一致则直接返回
        return {"class_id": str(class_id), "enabled": cached["enabled"], "replayed": True}

    # 确认 + 幂等键审计
    if idempotency_key:
        existing = (
            await db.execute(
                select(TeacherAction).where(TeacherAction.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            prior = _state_dict(await db.get(ClassroomMode, class_id)) or {}
            return {
                "class_id": str(class_id),
                "enabled": prior.get("enabled", enabled),
                "replayed": True,
            }

    ttl = (duration_minutes * 60) if duration_minutes else CLASSROOM_TTL_S
    expires_at = _now() + timedelta(seconds=ttl)
    if row is None:
        row = ClassroomMode(
            class_id=class_id,
            teacher_id=teacher_id,
            lesson_id=lesson_id,
            enabled=enabled,
            expires_at=expires_at,
        )
        db.add(row)
    else:
        row.teacher_id = teacher_id
        row.lesson_id = lesson_id
        row.enabled = enabled
        row.expires_at = expires_at
    db.add(
        TeacherAction(
            teacher_id=teacher_id,
            class_id=class_id,
            artifact_id=lesson_id,
            action_type="classroom.mode.set",
            client_request_id=client_request_id,
            idempotency_key=idempotency_key,
            request_id=request_id,
            details={"enabled": enabled, "lesson_id": str(lesson_id) if lesson_id else None},
        )
    )
    await db.flush()
    await db.refresh(row)
    return _serialize_state(class_id, _state_dict(row))


def _serialize_state(class_id: uuid.UUID, state: dict | None) -> dict:
    """对齐前端 ClassroomModeState 契约。"""
    if state is None:
        return {
            "class_id": str(class_id),
            "enabled": False,
            "lesson_id": None,
            "ttl_seconds": 0,
            "updated_at": "",
            "degraded": False,
        }
    return {
        "class_id": str(class_id),
        "enabled": bool(state["enabled"]),
        "lesson_id": state.get("lesson_id"),
        "ttl_seconds": max(0, int((state["expires_at"] - _now()).total_seconds())),
        "updated_at": state.get("updated_at").isoformat() if state.get("updated_at") else "",
        "degraded": False,
    }


async def classroom_state(
    db: AsyncSession, teacher_id: uuid.UUID, class_id: uuid.UUID
) -> dict:
    await assert_teacher_in_class(db, teacher_id, class_id)
    st = _state_dict(await db.get(ClassroomMode, class_id))
    if st and st.get("expires_at") and st["expires_at"] <= _now():
        st = None
    return _serialize_state(class_id, st)


async def classroom_state_for_member(
    db: AsyncSession, user_id: uuid.UUID, class_id: uuid.UUID
) -> dict | None:
    """已确认班级成员读取课堂状态；不可见统一返回 None。"""
    clazz = await db.get(Class, class_id)
    if clazz is None or clazz.deleted_at is not None or clazz.status != "active":
        return None
    if clazz.owner_id != user_id:
        member = (
            await db.execute(
                select(ClassMember).where(
                    ClassMember.class_id == class_id,
                    ClassMember.user_id == user_id,
                    ClassMember.confirmed.is_(True),
                    ClassMember.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if member is None:
            return None
    state = _state_dict(await db.get(ClassroomMode, class_id))
    if state and state.get("expires_at") and state["expires_at"] <= _now():
        state = None
    return _serialize_state(class_id, state)


async def video_insights(
    db: AsyncSession, teacher_id: uuid.UUID, class_id: uuid.UUID, *, lesson_id: uuid.UUID | None
) -> dict:
    """视频/课堂洞察：数据源未接入 → 空数据 + degraded=true，不伪造参与度。"""
    await assert_teacher_in_class(db, teacher_id, class_id)
    return {
        "class_id": str(class_id),
        "lesson_id": str(lesson_id) if lesson_id else None,
        "participation": {},
        "timeline_events": [],
        "actionable_recommendations": [],
        "degraded": True,
        "reason": "video_data_source_not_connected",
    }
