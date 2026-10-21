"""学情聚合服务（M2 迭代16）

定位：聚合层 —— 复用 mastery_records / mastery_snapshots / error_records /
submissions / tutor_sessions / events 等既有数据，为 growth_router 提供
纯计算能力。不引入新的判分/出题逻辑（底层 100% 复用 student_router）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coursework import (
    ErrorRecord,
    MasteryRecord,
    Streak,
    Submission,
    SubmissionItem,
)
from app.models.event import Event
from app.models.knowledge_point import KnowledgePoint
from app.models.mastery_snapshot import MasterySnapshot
from app.models.tutor_session import TutorSession
from app.services import fsrs

# 五类错因中文名（ADR-M2B-002 权威分类）
ERROR_TYPE_ZH = {
    "concept": "概念不清",
    "formula": "公式记错",
    "calculation": "计算失误",
    "logic": "思路卡壳",
    "reading": "审题偏差",
}

# 12 类思维漏洞细分（展示层，parent 映射五类权威分类；Eedi misconception 体系本土化）
# (subtype, 中文名, parent_type, 关键词)
ERROR_SUBTYPE_RULES: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("concept_def", "概念理解偏差", "concept", ("概念", "定义", "理解")),
    ("concept_transfer", "迁移应用薄弱", "concept", ("迁移", "应用", "综合")),
    ("formula_confused", "公式记混", "formula", ("记混", "记错", "混淆")),
    ("formula_condition", "公式用错条件", "formula", ("条件", "适用范围")),
    ("calc_arithmetic", "计算失误", "calculation", ("算错", "计算", "粗心")),
    ("calc_symbol", "符号错误", "calculation", ("符号", "正负", "负号")),
    ("logic_jump", "推理跳步", "logic", ("跳步", "跳跃")),
    ("logic_hidden", "隐含条件漏读", "logic", ("隐含", "漏", "端点", "忽略")),
    ("reading_deviation", "审题偏差", "reading", ("审题", "看错", "误解")),
    ("reading_graph", "图形信息误读", "reading", ("图", "图像", "图形")),
    ("strategy_method", "方法选择不当", "logic", ("方法", "策略", "选择")),
    ("strategy_step", "关键步骤遗漏", "logic", ("步骤", "遗漏")),
]


# ==================== 掌握度/提示依赖 ====================


async def load_mastery_rows(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    """用户全部掌握度记录：[{kp_code, kp_name, mastery, practice_count, correct_count, hint_count}]"""
    rs = await db.execute(
        select(MasteryRecord, KnowledgePoint)
        .join(KnowledgePoint, MasteryRecord.kp_id == KnowledgePoint.id)
        .where(MasteryRecord.user_id == user_id)
    )
    rows = []
    for mr, kp in rs.all():
        rows.append(
            {
                "kp_code": kp.code,
                "kp_name": kp.name,
                "mastery": float(mr.mastery or 0),
                "practice_count": int(mr.practice_count or 0),
                "correct_count": int(mr.correct_count or 0),
                "hint_count": int(mr.hint_count or 0),
            }
        )
    return rows


async def hint_dependency(db: AsyncSession, user_id: uuid.UUID) -> float:
    """提示依赖度（复用 mastery_summary 口径：练习提示 + 引导提示 / 做题数 + 引导会话数）"""
    totals = await db.execute(
        select(
            func.coalesce(func.sum(MasteryRecord.hint_count), 0),
            func.coalesce(func.sum(MasteryRecord.practice_count), 0),
        ).where(MasteryRecord.user_id == user_id)
    )
    hint_total, practice_total = totals.one()
    socratic_hints = 0
    socratic_sessions = 0
    try:
        ts_rows = await db.execute(
            select(TutorSession.hint_counts, TutorSession.answer_requests)
            .where(TutorSession.user_id == user_id, TutorSession.deleted_at.is_(None))
            .limit(200)
        )
        for hc, ar in ts_rows.all():
            socratic_sessions += 1
            socratic_hints += sum(int(v or 0) for v in (hc or {}).values()) + int(ar or 0)
    except Exception:
        pass
    return float(hint_total + socratic_hints) / max(1, int(practice_total) + socratic_sessions)


async def current_streak(db: AsyncSession, user_id: uuid.UUID) -> int:
    row = await db.execute(select(Streak.current_streak).where(Streak.user_id == user_id))
    return int(row.scalar() or 0)


# ==================== 综合分 ====================


def composite_score(avg_mastery: float, hint_dep: float, streak_days: int) -> int:
    """综合分公式（迭代16 §3.0.1）：100*(0.6*掌握度 + 0.25*独立性 + 0.15*连击系数)"""
    raw = 100 * (
        0.6 * avg_mastery + 0.25 * (1 - min(hint_dep, 1.0)) + 0.15 * min(1.0, streak_days / 14)
    )
    return max(0, min(100, round(raw)))


async def week_answer_stats(db: AsyncSession, user_id: uuid.UUID, days: int = 7) -> tuple[int, int]:
    """近 N 天作答数/答对数（submission_items 聚合）"""
    since = datetime.combine(date.today() - timedelta(days=days), time.min, tzinfo=UTC)
    rs = await db.execute(
        select(func.count(SubmissionItem.id), SubmissionItem.verdict)
        .join(Submission, SubmissionItem.submission_id == Submission.id)
        .where(
            Submission.user_id == user_id,
            Submission.deleted_at.is_(None),
            SubmissionItem.deleted_at.is_(None),
            SubmissionItem.created_at >= since,
        )
        .group_by(SubmissionItem.verdict)
    )
    total = correct = 0
    for cnt, verdict in rs.all():
        total += int(cnt)
        if verdict == "correct":
            correct += int(cnt)
    return total, correct


# ==================== 错题 + FSRS ====================


def enrich_error_fsrs(rec: ErrorRecord, now: datetime | None = None) -> dict:
    """单条错题的 FSRS 视图（读取时计算；优先用缓存列）"""
    now = now or datetime.now(UTC)
    # 第二批已接入 error_records.wrong_count（列默认 1，forgotten 复习时 +1 回填）
    wrong_count = int(rec.wrong_count or 1)
    stability = (
        float(rec.fsrs_stability)
        if rec.fsrs_stability is not None
        else fsrs.estimate_stability(int(rec.review_count or 0), wrong_count)
    )
    # 以最近一次活动（复习时间推不动，用 updated_at 近似 last review）起算衰减
    elapsed = fsrs.days_since(rec.updated_at or rec.created_at, now)
    r = fsrs.retrievability(elapsed, stability)
    remain_days = max(0.0, fsrs.days_until(stability, fsrs.DECAY_THRESHOLD) - elapsed)
    return {
        "record": rec,
        "stability": stability,
        "retrievability": r,
        "level": fsrs.fsrs_level(stability),
        "is_due": r < fsrs.DUE_THRESHOLD,
        "hours_to_forget": round(remain_days * 24, 1),
        "wrong_count": wrong_count,
    }


async def load_errors_fsrs(db: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    """用户全部未删错题 + FSRS 视图"""
    rs = await db.execute(
        select(ErrorRecord).where(
            ErrorRecord.user_id == user_id, ErrorRecord.deleted_at.is_(None)
        )
    )
    return [enrich_error_fsrs(rec) for rec in rs.scalars().all()]


async def kp_name_map(db: AsyncSession, codes: list[str | None]) -> dict[str, str]:
    valid = list({c for c in codes if c})
    if not valid:
        return {}
    rs = await db.execute(select(KnowledgePoint.code, KnowledgePoint.name).where(KnowledgePoint.code.in_(valid)))
    return dict(rs.all())


# ==================== 掌握度趋势 ====================


async def daily_mastery_avg(db: AsyncSession, user_id: uuid.UUID, days: int) -> list[dict]:
    """近 N 天掌握度日均值：[{date, mastery}]（mastery_snapshots 聚合，复用 trend 口径）"""
    since = date.today() - timedelta(days=days - 1)
    rs = await db.execute(
        select(MasterySnapshot.date, func.avg(MasterySnapshot.mastery))
        .where(MasterySnapshot.user_id == user_id, MasterySnapshot.date >= since)
        .group_by(MasterySnapshot.date)
        .order_by(MasterySnapshot.date)
    )
    by_date = {d: float(v) for d, v in rs.all()}
    out = []
    for i in range(days):
        d = since + timedelta(days=i)
        out.append({"date": d.isoformat(), "mastery": round(by_date.get(d, 0.0), 4) if d in by_date else None})
    return out


# ==================== 12 类错因分布 ====================


def classify_subtype(error_type: str | None, text: str) -> tuple[str, str, str]:
    """(五类错因, 文本) → (subtype, subtype_zh, parent)；无关键词命中时取 parent 默认细分类"""
    parent = error_type if error_type in ERROR_TYPE_ZH else "logic"
    for subtype, zh, p, keywords in ERROR_SUBTYPE_RULES:
        if p == parent and any(k in text for k in keywords):
            return subtype, zh, parent
    # 默认：该 parent 的第一个细分类
    for subtype, zh, p, _ in ERROR_SUBTYPE_RULES:
        if p == parent:
            return subtype, zh, parent
    return "logic_jump", "推理跳步", "logic"


# ==================== 行为埋点统计 ====================


async def event_count(db: AsyncSession, user_id: uuid.UUID, names: list[str], days: int) -> int:
    since = datetime.combine(date.today() - timedelta(days=days), time.min, tzinfo=UTC)
    rs = await db.execute(
        select(func.count(Event.id)).where(
            Event.user_id == user_id, Event.event.in_(names), Event.created_at >= since
        )
    )
    return int(rs.scalar() or 0)
