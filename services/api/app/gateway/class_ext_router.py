"""班级扩展聚合路由（M2 迭代16 第二批 · 模块7，方案 §5）

端点：
- GET /api/classes/{class_id}/feed — 班级动态（本班成员近 14 天学习事件 + 新成员加入，倒序 ≤20）
- GET /api/classes/{class_id}/hot-errors — 班级高频错题 Top N（按 kp 聚合，错因众数）
- GET /api/student/resources/recommend — 资源推荐（指定 kp 或取掌握度最低 kp）

约定对齐 growth_router：信封 {code:0, message:"ok", data}；鉴权 get_current_user；
越权对齐 classroom/router.py 纪律：非本班成员一律 40400（不泄露班级存在性）；
无数据一律返回空列表/空态默认值，绝不抛 500。本批零迁移：只读既有表。
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.auth import get_current_user
from app.models.class_member import ClassMember
from app.models.coursework import ErrorRecord, Submission
from app.models.database import get_db
from app.models.event import Event
from app.models.knowledge_point import KnowledgePoint
from app.models.user import User

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api", tags=["class-ext"])

# 班级动态窗口（天）
_FEED_DAYS = 14
# 班级动态最大条数
_FEED_LIMIT = 20
# 动态事件白名单 → 文案（events 表 event 名，迭代15/16 埋点 + 前瞻约定）
_FEED_EVENT_TEXT = {
    "review_done": "完成了错题复习",
    "practice_done": "完成了一组练习",
    "exam_submit": "完成了一次模拟考试",
    "loop_step_done": "完成了一个学习闭环步骤",
    "error_type_corrected": "订正了错题错因",
}


# ==================== 通用小工具 ====================


def _ok(data) -> dict:
    """统一成功信封"""
    return {"code": 0, "message": "ok", "data": data}


async def _require_member(
    db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID
) -> ClassMember | None:
    """本班成员校验（未软删即算，含待确认——对齐 members 端点可读口径）"""
    rs = await db.execute(
        select(ClassMember).where(
            ClassMember.class_id == class_id,
            ClassMember.user_id == user_id,
            ClassMember.deleted_at.is_(None),
        )
    )
    return rs.scalar_one_or_none()


async def _member_name_map(
    db: AsyncSession, class_id: uuid.UUID, students_only: bool = False
) -> dict[uuid.UUID, str]:
    """class_id → {user_id: 显示名}（班内昵称 > 用户昵称 > "同学"）"""
    q = select(ClassMember, User.nickname).join(
        User, User.id == ClassMember.user_id
    ).where(
        ClassMember.class_id == class_id,
        ClassMember.deleted_at.is_(None),
    )
    if students_only:
        q = q.where(ClassMember.member_role == "student")
    rs = await db.execute(q)
    name_map: dict[uuid.UUID, str] = {}
    for member, nickname in rs.all():
        name_map[member.user_id] = (
            member.nickname_in_class or nickname or "同学"
        )
    return name_map


# ==================== 班级动态 ====================


@router.get("/classes/{class_id}/feed")
async def class_feed(
    class_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """班级动态：本班成员近 14 天学习事件 + 新成员加入，时间倒序 ≤20 条"""
    user_id = uuid.UUID(user["sub"])

    # 越权不泄露存在性（对齐 classroom/router.py 纪律）
    if await _require_member(db, class_id, user_id) is None:
        return {"code": 40400, "message": "班级不存在"}

    since = datetime.now(UTC) - timedelta(days=_FEED_DAYS)
    name_map = await _member_name_map(db, class_id)
    member_ids = list(name_map.keys())

    feed: list[dict] = []
    if member_ids:
        # ① 学习事件（白名单内）
        ev_rs = await db.execute(
            select(Event)
            .where(
                Event.user_id.in_(member_ids),
                Event.event.in_(_FEED_EVENT_TEXT.keys()),
                Event.created_at >= since,
            )
            .order_by(Event.created_at.desc())
            .limit(100)
        )
        for ev in ev_rs.scalars().all():
            actor = name_map.get(ev.user_id, "同学")
            feed.append(
                {
                    "kind": "event",
                    "event": ev.event,
                    "actor_name": actor,
                    "text": f"{actor} {_FEED_EVENT_TEXT[ev.event]}",
                    "created_at": ev.created_at.isoformat() if ev.created_at else None,
                }
            )

        # ② 练习/考试提交（submissions 是直接数据源，不依赖埋点覆盖度）
        sub_rs = await db.execute(
            select(Submission)
            .where(
                Submission.user_id.in_(member_ids),
                Submission.deleted_at.is_(None),
                Submission.created_at >= since,
            )
            .order_by(Submission.created_at.desc())
            .limit(100)
        )
        for sub in sub_rs.scalars().all():
            actor = name_map.get(sub.user_id, "同学")
            score_txt = (
                f"，得分 {round(float(sub.total_score), 1)}"
                if sub.total_score is not None
                else ""
            )
            feed.append(
                {
                    "kind": "practice",
                    "actor_name": actor,
                    "text": f"{actor} 完成了一组练习{score_txt}",
                    "created_at": sub.created_at.isoformat() if sub.created_at else None,
                }
            )

        # ③ 新成员加入（joined_at 在近 14 天内）
        mem_rs = await db.execute(
            select(ClassMember).where(
                ClassMember.class_id == class_id,
                ClassMember.deleted_at.is_(None),
                ClassMember.joined_at >= since,
            )
        )
        for m in mem_rs.scalars().all():
            actor = name_map.get(m.user_id, "同学")
            feed.append(
                {
                    "kind": "member_join",
                    "actor_name": actor,
                    "text": f"{actor} 加入了班级",
                    "created_at": m.joined_at.isoformat() if m.joined_at else None,
                }
            )

    # 时间倒序，截断到上限；无数据返回空列表（前端空态）
    feed.sort(key=lambda f: f["created_at"] or "", reverse=True)
    return _ok({"days": _FEED_DAYS, "items": feed[:_FEED_LIMIT]})


# ==================== 班级高频错题 ====================


@router.get("/classes/{class_id}/hot-errors")
async def class_hot_errors(
    class_id: uuid.UUID,
    days: int = Query(default=30, ge=1, le=180),
    limit: int = Query(default=5, ge=1, le=20),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """班级高频错题：本班学生 error_records 按 kp 聚合 count 降序 Top N（错因取众数）"""
    user_id = uuid.UUID(user["sub"])

    # 越权不泄露存在性
    if await _require_member(db, class_id, user_id) is None:
        return {"code": 40400, "message": "班级不存在"}

    since = datetime.now(UTC) - timedelta(days=days)
    # 只统计学生成员（教师错题不进班级学情）
    name_map = await _member_name_map(db, class_id, students_only=True)
    member_ids = list(name_map.keys())
    if not member_ids:
        return _ok({"days": days, "items": []})

    err_rs = await db.execute(
        select(ErrorRecord.user_id, ErrorRecord.kp_code, ErrorRecord.error_type).where(
            ErrorRecord.user_id.in_(member_ids),
            ErrorRecord.deleted_at.is_(None),
            ErrorRecord.kp_code.is_not(None),
            ErrorRecord.created_at >= since,
        )
    )

    # 按 kp 聚合：错题数 / 涉及人数 / 错因众数
    agg: dict[str, dict] = {}
    for uid, kp_code, error_type in err_rs.all():
        cell = agg.setdefault(kp_code, {"count": 0, "users": set(), "types": Counter()})
        cell["count"] += 1
        cell["users"].add(uid)
        if error_type:
            cell["types"][error_type] += 1

    if not agg:
        return _ok({"days": days, "items": []})

    # kp 名称批量映射（孤儿码 → kp_name=None，前端兜底）
    kp_rs = await db.execute(
        select(KnowledgePoint.code, KnowledgePoint.name).where(
            KnowledgePoint.code.in_(agg.keys())
        )
    )
    kp_names = dict(kp_rs.all())

    top = sorted(agg.items(), key=lambda kv: kv[1]["count"], reverse=True)[:limit]
    items = [
        {
            "kp_code": code,
            "kp_name": kp_names.get(code),
            "error_count": cell["count"],
            "member_count": len(cell["users"]),
            "top_error_type": cell["types"].most_common(1)[0][0] if cell["types"] else None,
        }
        for code, cell in top
    ]
    return _ok({"days": days, "items": items})

