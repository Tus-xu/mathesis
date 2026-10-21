# coding: utf-8
"""体验测试留痕路由（作品验收：试用教师结构化反馈 + 使用档案 + 导出）。

留痕源（全部既有表，零复制）：tp_sessions（备课会话）/ teacher_artifacts（导出产物）/
ai_runs（AI 调用台账，原生 SQL——表所有权在迁移 d17b2c3e4f5a）/
本表 tp_trial_feedback（结构化反馈）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_db
from app.core.response import ok
from app.core.security import UserToken
from app.storage.models import TeacherArtifact
from app.teacher_prep.models import TeachingSessionRow
from app.teacher_prep.trial_models import TrialFeedbackRow

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/trial", tags=["trial"])

CurrentUser = Annotated[UserToken, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]

_RATING_KEYS = ("usability", "usefulness", "speed", "accuracy", "overall")


def _mask(user_id: str) -> str:
    return f"teacher-{user_id[:8]}"


class TrialFeedbackIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    identity_label: str = Field(..., min_length=2, max_length=128, description='身份标注，如 "XX中学 高中数学 教师"')
    school: str = Field("", max_length=128)
    ratings: dict[str, int] = Field(default_factory=dict)
    nps: int | None = Field(None, ge=0, le=10)
    comment: str = Field("", max_length=4000)
    duration_minutes: int | None = Field(None, ge=0, le=100_000)
    allow_showcase: bool = True


def _fb_dict(fb: TrialFeedbackRow) -> dict:
    return {
        "name": fb.name,
        "identity_label": fb.identity_label,
        "school": fb.school,
        "ratings": fb.ratings or {},
        "nps": fb.nps,
        "comment": fb.comment,
        "duration_minutes": fb.duration_minutes,
        "allow_showcase": bool(fb.allow_showcase),
        "submitted_at": fb.created_at.isoformat() if fb.created_at else None,
    }


async def _profile_bundle(db: AsyncSession, teacher_id: uuid.UUID) -> dict:
    """聚合试用教师在既有留痕表中的行为数据（只读）"""
    sessions_total = (
        await db.execute(
            select(func.count())
            .select_from(TeachingSessionRow)
            .where(TeachingSessionRow.teacher_id == teacher_id)
        )
    ).scalar() or 0

    artifacts_total = (
        await db.execute(
            select(func.count())
            .select_from(TeacherArtifact)
            .where(TeacherArtifact.owner_id == teacher_id)
        )
    ).scalar() or 0

    ai_runs_total = (
        await db.execute(
            text("SELECT count(*) FROM ai_runs WHERE user_id = :tid"),
            {"tid": str(teacher_id)},
        )
    ).scalar() or 0

    fb_rows = (
        await db.execute(
            select(TrialFeedbackRow)
            .where(TrialFeedbackRow.teacher_id == teacher_id)
            .order_by(TrialFeedbackRow.created_at.desc())
            .limit(1)
        )
    ).scalars().all()
    latest = fb_rows[0] if fb_rows else None

    return {
        "sessions_total": sessions_total,
        "artifacts_total": artifacts_total,
        "ai_runs_total": ai_runs_total,
        "feedback": _fb_dict(latest) if latest else None,
    }


@router.post("/feedback")
async def submit_feedback(body: TrialFeedbackIn, user: CurrentUser, db: DbSession):
    """试用教师提交/更新结构化反馈（每次新增一份，报告取最新）"""
    tid = uuid.UUID(user.id)
    ratings = {}
    for k in _RATING_KEYS:
        v = body.ratings.get(k)
        if v is None:
            continue
        ratings[k] = max(1, min(5, int(v)))
    fb = TrialFeedbackRow(
        teacher_id=tid,
        name=body.name.strip(),
        identity_label=body.identity_label.strip(),
        school=body.school.strip(),
        ratings=ratings,
        nps=body.nps,
        comment=body.comment.strip(),
        duration_minutes=body.duration_minutes,
        allow_showcase=body.allow_showcase,
    )
    db.add(fb)
    await db.commit()
    logger.info("trial.feedback_submitted", teacher_id=user.id, identity=fb.identity_label)
    return ok({"feedback_id": str(fb.id)})


@router.get("/metrics")
async def my_metrics(user: CurrentUser, db: DbSession):
    """当前试用教师的使用档案（本人可见）"""
    data = await _profile_bundle(db, uuid.UUID(user.id))
    return ok(data)


@router.get("/export", response_class=PlainTextResponse)
async def export_report(
    user: CurrentUser,
    db: DbSession,
    scope: str = Query("all", pattern="^(all|self)$"),
):
    """导出 Markdown 测试记录（评审材料：身份标注 + 行为档案 + 反馈）。

    scope=self：本人档案；scope=all：全库试用教师（教师平台用户均为
    role=teacher，导出视为评审运营动作）。
    """
    if scope == "self":
        tid = uuid.UUID(user.id)
        teachers = [{"user_id": user.id, "name": user.name or "（未命名）", "teacher_id": tid}]
    else:
        session_tids = (
            await db.execute(select(TeachingSessionRow.teacher_id).distinct().limit(100))
        ).scalars().all()
        fb_tids = (
            await db.execute(select(TrialFeedbackRow.teacher_id).distinct())
        ).scalars().all()
        artifact_tids = (
            await db.execute(select(TeacherArtifact.owner_id).distinct().limit(100))
        ).scalars().all()
        merged = list(dict.fromkeys([*session_tids, *fb_tids, *artifact_tids]))
        teachers = [{"user_id": str(t), "name": "（未命名）", "teacher_id": t} for t in merged]

    lines: list[str] = []
    lines.append("# 教师端体验测试记录" + ("（本人）" if scope == "self" else ""))
    lines.append("")
    lines.append(f"> 生成时间：{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}｜数据源：教师平台数据库实时聚合（备课会话/导出产物/AI 调用/试用反馈）")
    lines.append("")
    lines.append("## 一、试用教师概览")
    lines.append("")
    lines.append("| # | 身份标注 | 标识 | 备课会话 | 导出产物 | 反馈 |")
    lines.append("|---|---|---|---|---|---|")
    idx = 0
    for t in teachers:
        bundle = await _profile_bundle(db, t["teacher_id"])
        fb = bundle.get("feedback")
        if not (bundle["sessions_total"] or bundle["artifacts_total"] or fb):
            continue
        idx += 1
        identity = (fb and fb["identity_label"]) or "（未提交反馈）"
        name = (fb and fb["name"]) or t["name"]
        lines.append(
            f"| {idx} | {name}·{identity} | {_mask(t['user_id'])} | "
            f"{bundle['sessions_total']} | {bundle['artifacts_total']} | {'✅' if fb else '—'} |"
        )
    lines.append("")
    lines.append("## 二、试用教师档案明细")
    lines.append("")
    detail = 0
    for t in teachers:
        bundle = await _profile_bundle(db, t["teacher_id"])
        fb = bundle.get("feedback")
        if not (bundle["sessions_total"] or bundle["artifacts_total"] or fb):
            continue
        detail += 1
        name = (fb and fb["name"]) or t["name"]
        identity = (fb and fb["identity_label"]) or "（未提交反馈）"
        lines.append(f"### {detail}. {name}（{identity}）")
        lines.append("")
        lines.append(
            f"- 行为：备课会话 {bundle['sessions_total']} 个；导出产物 {bundle['artifacts_total']} 个；"
            f"AI 调用 {bundle['ai_runs_total']} 次"
        )
        if fb:
            rating = "、".join(f"{k}={v}" for k, v in fb["ratings"].items()) or "未评分"
            lines.append(
                f"- 反馈：{rating}；NPS={fb['nps'] if fb['nps'] is not None else '—'}；"
                f"自报时长={fb['duration_minutes'] if fb['duration_minutes'] is not None else '—'} 分钟"
            )
            if fb["comment"]:
                lines.append(f"- 评语：{fb['comment']}")
        lines.append("")
    lines.append("---")
    lines.append("*本报告由教师平台自动聚合生成，行为数据均为数据库真实留痕，可复核。*")
    return PlainTextResponse("\n".join(lines), media_type="text/markdown; charset=utf-8")
