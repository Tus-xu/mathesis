"""体验测试留痕路由（作品验收：真实用户试用反馈 + 结构化测试记录）

三件套（复用既有留痕表，零行为数据复制）：
- POST /api/trial/feedback   试用者提交结构化反馈（身份标注 + 评分 + NPS + 评语）
- GET  /api/trial/metrics    当前用户"使用档案"聚合（注册/对话/消息/引导解题/错题/学习事件）
- GET  /api/trial/feedbacks  全部试用者反馈列表（teacher/admin）
- GET  /api/trial/export     一键导出 Markdown 测试记录（teacher/admin，可直接作为评审材料）

留痕源（全部既有表，部署到服务器后天然持续累积）：
users（注册）/ conversations+messages（对话全量）/ tutor_sessions（引导解题全链）
error_records（错题本）/ learning_events（quiz_judge/error_recorded/login 等学习事件）
events（行为流水）/ trial_feedback（结构化反馈）
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.auth import get_current_user
from app.models.conversation import Conversation
from app.models.coursework import ErrorRecord
from app.models.database import get_db
from app.models.event import Event
from app.models.learning_event import LearningEvent
from app.models.message import Message
from app.models.trial import TrialFeedback
from app.models.tutor_session import TutorSession
from app.models.user import User

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/trial", tags=["trial"])

_RATING_KEYS = ("usability", "usefulness", "speed", "accuracy", "overall")


def _mask_phone(phone: str | None) -> str:
    if not phone or len(phone) < 7:
        return "（未绑定手机）"
    return f"{phone[:3]}****{phone[-4:]}"


def _active_role(user: dict) -> str:
    return user.get("active_role") or user.get("role") or "student"


# ========== 1. 提交结构化反馈 ==========


class TrialFeedbackIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    identity_label: str = Field(
        ..., min_length=2, max_length=128, description='身份标注，如 "XX中学 高二 学生"'
    )
    school: str = Field("", max_length=128)
    grade: str = Field("", max_length=32)
    ratings: dict[str, int] = Field(
        default_factory=dict, description="usability/usefulness/speed/accuracy/overall，1-5"
    )
    nps: int | None = Field(None, ge=0, le=10)
    comment: str = Field("", max_length=4000)
    duration_minutes: int | None = Field(None, ge=0, le=100_000)
    allow_showcase: bool = True


@router.post("/feedback")
async def submit_feedback(
    body: TrialFeedbackIn,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """试用者提交/更新结构化反馈（每次提交新增一份，报告取最新）"""
    uid = uuid.UUID(str(user["sub"]))
    ratings = {}
    for k in _RATING_KEYS:
        v = body.ratings.get(k)
        if v is None:
            continue
        v = max(1, min(5, int(v)))
        ratings[k] = v
    fb = TrialFeedback(
        user_id=uid,
        name=body.name.strip(),
        identity_label=body.identity_label.strip(),
        school=body.school.strip(),
        grade=body.grade.strip(),
        ratings=ratings,
        nps=body.nps,
        comment=body.comment.strip(),
        duration_minutes=body.duration_minutes,
        allow_showcase=body.allow_showcase,
    )
    db.add(fb)
    db.add(
        Event(
            user_id=uid,
            event="trial_feedback_submitted",
            props={"overall": ratings.get("overall"), "nps": body.nps},
        )
    )
    await db.commit()
    logger.info("trial.feedback_submitted", user_id=str(uid), identity=fb.identity_label)
    return {"code": 0, "message": "ok", "data": {"feedback_id": str(fb.id)}}


# ========== 2. 用户使用档案聚合 ==========


async def _user_profile_bundle(db: AsyncSession, uid: uuid.UUID) -> dict:
    """聚合单个用户在全部既有留痕表中的行为数据（只读，零复制）"""
    reg = (await db.execute(select(User.created_at).where(User.id == uid))).scalar_one_or_none()

    conv_rows = (
        (
            await db.execute(
                select(Conversation.id).where(
                    Conversation.user_id == uid, Conversation.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    conv_count = len(conv_rows)
    msg_count = 0
    student_msg_count = 0
    if conv_count:
        msg_count = (
            await db.execute(
                select(func.count(Message.id)).where(Message.conversation_id.in_(conv_rows))
            )
        ).scalar() or 0
        student_msg_count = (
            await db.execute(
                select(func.count(Message.id)).where(
                    Message.conversation_id.in_(conv_rows), Message.role == "user"
                )
            )
        ).scalar() or 0

    tutor_rows = (
        (
            await db.execute(
                select(TutorSession.status).where(
                    TutorSession.user_id == uid, TutorSession.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    tutor_by_status: dict[str, int] = {}
    for s in tutor_rows:
        tutor_by_status[s] = tutor_by_status.get(s, 0) + 1

    error_total = (
        await db.execute(
            select(func.count(ErrorRecord.id)).where(
                ErrorRecord.user_id == uid, ErrorRecord.deleted_at.is_(None)
            )
        )
    ).scalar() or 0

    # 迭代21 防御（对齐 butler_router「绝不抛 500」纪律）：learning_events 属
    # stu_002_learning_ledger_tables 迁移补齐的四表之一——干净环境迁移未跑到 head
    # 时缺表，裸 select 会 500 整个 /api/trial/metrics。降级为空计数 + warning，
    # 其余档案字段照常返回。
    try:
        event_rows = (
            await db.execute(
                select(LearningEvent.event_type, func.count(LearningEvent.id))
                .where(LearningEvent.user_id == uid)
                .group_by(LearningEvent.event_type)
            )
        ).all()
    except ProgrammingError as e:
        logger.warning(
            "trial_metrics_learning_events_missing",
            error=str(e.orig if hasattr(e, "orig") else e)[:150],
        )
        await db.rollback()
        event_rows = []

    last_activity = (
        await db.execute(
            select(func.max(Message.created_at)).where(
                Message.conversation_id.in_(conv_rows) if conv_rows else sa_false()
            )
        )
    ).scalar()

    feedback_rows = (
        (
            await db.execute(
                select(TrialFeedback)
                .where(TrialFeedback.user_id == uid)
                .order_by(TrialFeedback.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .all()
    )
    latest_fb = feedback_rows[0] if feedback_rows else None

    return {
        "registered_at": reg.isoformat() if reg else None,
        "conversations": conv_count,
        "messages_total": msg_count,
        "messages_from_user": student_msg_count,
        "tutor_sessions_total": len(tutor_rows),
        "tutor_sessions_by_status": tutor_by_status,
        "error_records": error_total,
        "learning_events": dict(event_rows),
        "last_message_at": last_activity.isoformat() if last_activity else None,
        "feedback": _fb_dict(latest_fb) if latest_fb else None,
    }


def sa_false():
    """永远为假的可移植布尔条件（空 in_ 列表时替代，避免非法 SQL）"""
    from sqlalchemy import false

    return false()


def _fb_dict(fb: TrialFeedback) -> dict:
    return {
        "name": fb.name,
        "identity_label": fb.identity_label,
        "school": fb.school,
        "grade": fb.grade,
        "ratings": fb.ratings or {},
        "nps": fb.nps,
        "comment": fb.comment,
        "duration_minutes": fb.duration_minutes,
        "allow_showcase": bool(fb.allow_showcase),
        "submitted_at": fb.created_at.isoformat() if fb.created_at else None,
    }


@router.get("/metrics")
async def my_metrics(user: dict = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """当前试用者的使用档案（自己可见，前端"我的"页渲染）"""
    uid = uuid.UUID(str(user["sub"]))
    data = await _user_profile_bundle(db, uid)
    return {"code": 0, "message": "ok", "data": data}


# ========== 3. 管理视角：反馈列表 + 导出 ==========


async def _require_staff(user: dict) -> None:
    if _active_role(user) not in ("teacher", "admin"):
        from fastapi import HTTPException

        raise HTTPException(status_code=403, detail="仅教师/管理员可查看全部试用记录")


@router.get("/feedbacks")
async def list_feedbacks(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
):
    """全部试用者反馈（最新在前）"""
    await _require_staff(user)
    rows = (
        (
            await db.execute(
                select(TrialFeedback).order_by(TrialFeedback.created_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return {
        "code": 0,
        "message": "ok",
        "data": [_fb_dict(f) | {"user_id": str(f.user_id)} for f in rows],
    }


@router.get("/export", response_class=PlainTextResponse)
async def export_report(
    scope: str = Query("all", pattern="^(all|self)$"),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """一键导出 Markdown 测试记录（评审材料：身份标注 + 行为档案 + 反馈）。

    scope=all：全部试用者（teacher/admin）；scope=self：本人档案（任何登录用户，
    评审演示时学生可直接下载自己的留痕记录，无需教师账号）。
    """
    if scope == "self":
        target_user_id = uuid.UUID(str(user["sub"]))
        target_users = (
            (await db.execute(select(User).where(User.id == target_user_id))).scalars().all()
        )
        title = "学生端体验测试记录（本人）"
    else:
        await _require_staff(user)
        target_user_id = None
        # 迭代20 门禁修复：导出取「最近 200 个试用者」（created_at DESC）。
        # 原 ASC+LIMIT 200 意味着一旦平台累计超过 200 个账号，**新试用者永远进不了
        # 评审报告**（报告被最早的 200 个账号永久占满）——与同为评审材料的
        # /trial/feedback 列表（:250 created_at DESC）语义相悖，也使本端点在
        # 账号量增长后功能性失效。改为最近优先，容量上限 200 不变。
        target_users = (
            (
                await db.execute(
                    select(User)
                    .where(User.deleted_at.is_(None))
                    .order_by(User.created_at.desc())
                    .limit(200)
                )
            )
            .scalars()
            .all()
        )
        title = "学生端体验测试记录"

    lines: list[str] = []
    lines.append(f"# {title}")
    lines.append("")
    lines.append(
        f"> 生成时间：{datetime.now(UTC).astimezone().isoformat(timespec='seconds')}｜数据源：平台数据库实时聚合（对话/引导解题/错题/学习事件/试用反馈）"
    )
    lines.append("")
    lines.append("## 一、试用者概览")
    lines.append("")
    lines.append(
        "| # | 身份标注 | 手机号（脱敏） | 注册时间 | 会话 | 消息 | 引导解题 | 错题 | 反馈 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    idx = 0
    for u in target_users:
        bundle = await _user_profile_bundle(db, u.id)
        fb = bundle.get("feedback")
        if not (bundle["conversations"] or bundle["tutor_sessions_total"] or fb):
            continue  # 无任何试用行为的账号不进报告
        idx += 1
        identity = (fb and fb["identity_label"]) or "（未提交反馈）"
        name = (fb and fb["name"]) or (u.nickname or "匿名")
        lines.append(
            f"| {idx} | {name}·{identity} | {_mask_phone(u.phone)} | "
            f"{(bundle['registered_at'] or '')[:10]} | {bundle['conversations']} | "
            f"{bundle['messages_total']} | {bundle['tutor_sessions_total']} | "
            f"{bundle['error_records']} | {'✅' if fb else '—'} |"
        )
    lines.append("")
    lines.append("## 二、试用者档案明细")
    lines.append("")
    detail_idx = 0
    for u in target_users:
        bundle = await _user_profile_bundle(db, u.id)
        fb = bundle.get("feedback")
        if not (bundle["conversations"] or bundle["tutor_sessions_total"] or fb):
            continue
        detail_idx += 1
        name = (fb and fb["name"]) or (u.nickname or "匿名")
        identity = (fb and fb["identity_label"]) or "（未提交反馈）"
        lines.append(f"### {detail_idx}. {name}（{identity}）")
        lines.append("")
        lines.append(
            f"- 手机号（脱敏）：{_mask_phone(u.phone)}；注册：{(bundle['registered_at'] or '')[:19]}；最近消息：{(bundle['last_message_at'] or '')[:19]}"
        )
        lines.append(
            f"- 行为：会话 {bundle['conversations']} 个；消息 {bundle['messages_total']} 条（学生发送 {bundle['messages_from_user']}）；"
            f"引导解题 {bundle['tutor_sessions_total']} 次（{bundle['tutor_sessions_by_status']}）；错题 {bundle['error_records']} 条"
        )
        if bundle["learning_events"]:
            ev = "、".join(
                f"{k}×{v}"
                for k, v in sorted(bundle["learning_events"].items(), key=lambda x: -x[1])
            )
            lines.append(f"- 学习事件：{ev}")
        if fb:
            rating = "、".join(f"{k}={v}" for k, v in fb["ratings"].items()) or "未评分"
            lines.append(
                f"- 反馈：{rating}；NPS={fb['nps'] if fb['nps'] is not None else '—'}；"
                f"自报时长={fb['duration_minutes'] if fb['duration_minutes'] is not None else '—'} 分钟；允许展示={'是' if fb['allow_showcase'] else '否'}"
            )
            if fb["comment"]:
                lines.append(f"- 评语：{fb['comment']}")
        lines.append("")
    lines.append("---")
    lines.append("*本报告由平台自动聚合生成，行为数据均为数据库真实留痕，可复核。*")
    return PlainTextResponse("\n".join(lines), media_type="text/markdown; charset=utf-8")
