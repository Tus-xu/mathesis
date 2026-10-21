"""M3 教师端：Today Projection（§11 真数据聚合，禁止虚构统计）。

从存量班级/Assignment/Submission/Insight 聚合：
- next_lesson：最近即将开始的课（取最近的已发布/进行中 assignment 或教案）；
- grading_queue：待批数量；
- deadlines：临近截止任务；
- actionable_insights：最多 3 条高价值可行动洞察（每项含 evidence/action/时间窗）；
数据源缺失 → 空态 + degraded=true，不生成虚构统计。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.teacher.insights import MAX_INSIGHTS, compute_class_insights
from app.models.class_ import Class
from app.models.class_member import ClassMember
from app.models.coursework import Assignment, Submission, SubmissionItem


def _now_utc() -> datetime:
    return datetime.now(UTC)


async def teacher_class_ids(db: AsyncSession, teacher_id: uuid.UUID) -> list[uuid.UUID]:
    owned = (
        await db.execute(
            select(Class.id).where(
                Class.owner_id == teacher_id,
                Class.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    member = (
        await db.execute(
            select(ClassMember.class_id)
            .join(Class, Class.id == ClassMember.class_id)
            .where(
                ClassMember.user_id == teacher_id,
                ClassMember.member_role.in_(("teacher", "admin")),
                ClassMember.confirmed.is_(True),
                ClassMember.deleted_at.is_(None),
                Class.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    return list(dict.fromkeys([*owned, *member]))


async def _pending_grading_total(
    db: AsyncSession, class_ids: list[uuid.UUID]
) -> int:
    if not class_ids:
        return 0
    sub_ids = select(Submission.id).where(
        Submission.assignment_id.in_(
            select(Assignment.id).where(Assignment.class_id.in_(class_ids))
        )
    )
    cnt = await db.scalar(
        select(func.count(SubmissionItem.id)).where(
            SubmissionItem.submission_id.in_(sub_ids),
            (
                (SubmissionItem.needs_review.is_(True))
                | (SubmissionItem.verdict == "pending_review")
            ),
        )
    )
    return int(cnt or 0)


async def _next_lesson(
    db: AsyncSession, class_ids: list[uuid.UUID]
) -> dict:
    """最近即将开始的课：优先取 deadline 在未来最近的已发布 assignment。

    同时附加该班最新教案的备课完成度与缺口（无教案时 prep_completion 为 None，
    前端隐藏"备课准备度"块而不是显示 0%）。
    """
    if not class_ids:
        return {}
    now = _now_utc()
    a = (
        await db.execute(
            select(Assignment)
            .where(
                Assignment.class_id.in_(class_ids),
                Assignment.deadline.is_not(None),
                Assignment.deadline >= now,
                Assignment.status.in_(("published", "open")),
            )
            .order_by(Assignment.deadline.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if a is None:
        return {}
    prep = await _lesson_prep_state(db, a.class_id)
    clazz = await db.get(Class, a.class_id)
    return {
        "class_id": str(a.class_id),
        "class_name": clazz.name if clazz else None,
        "lesson_id": prep.get("lesson_id"),
        "topic": a.title,
        "starts_at": a.deadline.isoformat() if a.deadline else None,
        "prep_completion": prep.get("prep_completion"),
        "missing_items": prep.get("missing_items"),
        "duration_minutes": prep.get("duration_minutes"),
        "location": clazz.room if clazz and getattr(clazz, "room", None) else None,
    }


async def _lesson_prep_state(db: AsyncSession, class_id: uuid.UUID) -> dict:
    """备课完成度：该班最近一份已确认教案的环节完备度 + 缺口名称 + 授课时长。"""
    from sqlalchemy import select as sa_select

    from app.models.teacher import TeachingArtifact

    art = (
        (
            await db.execute(
                sa_select(TeachingArtifact)
                .where(
                    TeachingArtifact.class_id == class_id,
                    TeachingArtifact.artifact_type == "lesson_plan",
                    TeachingArtifact.status.in_(("confirmed", "published")),
                )
                .order_by(TeachingArtifact.updated_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if art is None:
        return {"lesson_id": None, "prep_completion": None, "missing_items": [], "duration_minutes": None}
    payload = art.payload or {}
    segments = payload.get("segments") or []
    if not isinstance(segments, list):
        return {"lesson_id": None, "prep_completion": None, "missing_items": [], "duration_minutes": None}
    defined = 0
    missing: list[str] = []
    total_min = 0
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        total_min += seg.get("duration_min") or 0
        ok = bool((seg.get("content") or "").strip()) and bool((seg.get("teacher_action") or "").strip())
        if ok:
            defined += 1
        else:
            missing.append(seg.get("title") or "未命名环节")
    has_check = any((s.get("kind") == "check") for s in segments if isinstance(s, dict))
    if not has_check:
        missing.append("当堂检测(Exit Ticket)")
    completion = round(defined / len(segments) * 100) if segments else 0
    return {
        "lesson_id": str(art.id),
        "prep_completion": completion,
        "missing_items": missing[:3],
        "duration_minutes": total_min or None,
    }


async def _deadlines(db: AsyncSession, class_ids: list[uuid.UUID]) -> list[dict]:
    if not class_ids:
        return []
    now = _now_utc()
    soon = now + timedelta(hours=48)
    rows = (
        await db.execute(
            select(Assignment)
            .where(
                Assignment.class_id.in_(class_ids),
                Assignment.deadline.is_not(None),
                Assignment.deadline >= now,
                Assignment.deadline <= soon,
            )
            .order_by(Assignment.deadline.asc())
        )
    ).scalars().all()
    return [
        {
            "id": str(a.id),
            "kind": "assignment",
            "title": a.title,
            "due_at": a.deadline.isoformat() if a.deadline else None,
        }
        for a in rows
    ]


async def build_today(
    db: AsyncSession, teacher_id: uuid.UUID, *, date: str | None = None
) -> dict:
    """聚合教师今日工作台。degraded = 关键数据源缺失但给出空态（非错误）。"""
    class_ids = await teacher_class_ids(db, teacher_id)
    degraded = len(class_ids) == 0

    # 待批数量
    grading_count = await _pending_grading_total(db, class_ids)

    # 可行动洞察（最多 3 条）
    actionable_insights: list[dict] = []
    for cid in class_ids:
        if len(actionable_insights) >= MAX_INSIGHTS:
            break
        try:
            ins = await compute_class_insights(db, teacher_id, cid, actionable=True)
        except Exception:  # noqa: BLE001 —— 单班失败不阻断 Today
            ins = []
        actionable_insights.extend(ins)

    return {
        "next_lesson": await _next_lesson(db, class_ids),
        "grading_queue": {
            "count": grading_count,
            "action": "open_grading" if grading_count else None,
        },
        "deadlines": await _deadlines(db, class_ids),
        "actionable_insights": actionable_insights[:MAX_INSIGHTS],
        "source_missing": not class_ids,
        "degraded": degraded,
    }
