"""M3 教师端：班级可行动洞察（§11 真数据聚合）。

洞察数据一律来自确定性 SQL 聚合（提交率变化/错误集中/低掌握度/待复核/备课缺口），
不编造 evidence；LLM 只允许润色 summary/recommended_actions 文案。
默认不返回学生逐人敏感明细，只返回聚合证据。

tel D5/A4-A5 增量：新增 class_roster（每生花名册统计）与 class_learning_overview
（班级聚合）两个确定性 SQL 聚合端点的服务层——纯查询、不落新表、无 LLM、
空数据诚实空态、MAX 条数限制，风格对齐本文件既有洞察口径。
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.class_member import ClassMember
from app.models.coursework import (
    Assignment,
    ErrorRecord,
    MasteryRecord,
    QuizItem,
    Submission,
    SubmissionItem,
)
from app.models.knowledge_point import KnowledgePoint
from app.models.teacher import ActionableInsight
from app.models.user import User

MAX_INSIGHTS = 3
# tel A4：每生薄弱知识点条数上限（mastery < WEAK_KPS_THRESHOLD 视为薄弱）
MAX_WEAK_KPS = 3
WEAK_KPS_THRESHOLD = 0.6
# tel A5：KP 热度 / 错误聚类条数上限
MAX_KP_HEAT = 10
MAX_ERROR_CLUSTERS = 5


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _evidence_summary(kind: str, evidence: dict) -> str:
    """将确定性聚合保留为面向教师的证据句，而非内部键值调试输出。"""
    if kind == "review_backlog":
        return f"待复核作答 {int(evidence.get('count') or 0)} 份"
    if kind == "low_mastery":
        total = int(evidence.get("total") or 0)
        low_count = int(evidence.get("low_count") or 0)
        ratio = float(evidence.get("ratio") or 0)
        return f"已形成掌握度记录 {total} 人，其中 {low_count} 人低于 0.50（{ratio:.0%}）"
    if kind == "submission_trend":
        previous = int(evidence.get("previous_count") or 0)
        recent = int(evidence.get("recent_count") or 0)
        return f"最近两份作业的提交量：{previous} 份 → {recent} 份"
    if kind == "error_cluster":
        label = evidence.get("question_label") or evidence.get("question") or "该题"
        prev = int(evidence.get("previous_count") or 0)
        recent = int(evidence.get("recent_count") or 0)
        repeat = int(evidence.get("repeat_count") or 0)
        title = evidence.get("window_title") or ""
        return f"「{label}」在上一份作业与「{title}」连续失分：{prev} 人 → {recent} 人，其中 {repeat} 人重复出现。"
    return "见数据窗口内的班级聚合证据"


def _serialize(ins: ActionableInsight) -> dict:
    """对齐前端 ActionableInsight 契约（evidence 摘要为字符串，动作为标签数组）。"""
    evidence = ins.evidence or {}
    evidence_text = _evidence_summary(ins.kind, evidence)
    actions = [
        (a.get("label") or a.get("action") or str(a)) if isinstance(a, dict) else str(a)
        for a in (ins.recommended_actions or [])
    ]
    return {
        "insight_id": str(ins.id),
        "kind": ins.kind,
        "summary": ins.summary,
        "evidence": evidence_text,
        "data_window": {
            "from": ins.window_start.isoformat() if ins.window_start else "",
            "to": ins.window_end.isoformat() if ins.window_end else "",
        },
        "recommended_actions": actions,
        "confidence": float(ins.confidence or 0.0),
        "applied": ins.status == "applied",
    }


async def _review_backlog(db, class_id: uuid.UUID) -> ActionableInsight | None:
    # 待复核批改：该班提交的 needs_review 或 pending_review 作答数
    sub_ids = select(Submission.id).join(
        Assignment, Assignment.id == Submission.assignment_id
    ).where(Assignment.class_id == class_id)
    cnt = await db.scalar(
        select(func.count(SubmissionItem.id)).where(
            SubmissionItem.submission_id.in_(sub_ids),
            (
                (SubmissionItem.needs_review.is_(True))
                | (SubmissionItem.verdict == "pending_review")
            ),
        )
    )
    if not cnt:
        return None
    return ActionableInsight(
        class_id=class_id,
        kind="review_backlog",
        summary=f"待复核批改 {cnt} 份，建议按题集中确认",
        evidence={"count": int(cnt)},
        recommended_actions=[{"action": "open_grading", "label": "开始批改"}],
        confidence=0.9,
        window_end=_now_utc(),
    )


async def _low_mastery(db, class_id: uuid.UUID) -> ActionableInsight | None:
    # 低掌握度群体比例：该班学生成员中，平均掌握度 < 0.5 的人数占比（确定性聚合）
    student_ids = select(ClassMember.user_id).where(
        ClassMember.class_id == class_id,
        ClassMember.member_role == "student",
        ClassMember.confirmed.is_(True),
    )
    rows = (
        await db.execute(
            select(MasteryRecord.user_id, func.avg(MasteryRecord.mastery).label("m"))
            .where(MasteryRecord.user_id.in_(student_ids))
            .group_by(MasteryRecord.user_id)
        )
    ).all()
    low = [r for r in rows if (r[1] or 0) < 0.5]
    total = len(rows)
    if not total or len(low) / total < 0.2:
        return None
    return ActionableInsight(
        class_id=class_id,
        kind="low_mastery",
        summary=f"约 {int(len(low) / total * 100)}% 学生当前掌握度偏低",
        evidence={"total": total, "low_count": len(low), "ratio": round(len(low) / total, 2)},
        recommended_actions=[{"action": "open_class_detail", "label": "查看班级"}],
        confidence=0.75,
        window_end=_now_utc(),
    )


async def _error_cluster(
    db: AsyncSession, class_id: uuid.UUID
) -> ActionableInsight | None:
    """错误集中洞察（§11 调研版）：跨最近两次已发布作业的"共同错题 + 重复犯错人数"。

    确定性 SQL 聚合（不调用 LLM、不编造 evidence）：
    1. 取该班最近两份已发布/已截止作业 A(最近)、B；
    2. 逐题（QuizItem.item_no → question_text）统计 verdict=="wrong" 的学生集合；
    3. 两道作业中都出现失分的题目（按题目文本归一化匹配）即为"连续失分"；
    4. repeat = 在两次作业中同一题均错的学生数；证据句呈现最近/上次/重复人数。
    无共同错题或无 quiz 挂载 → 返回 None（不生成虚构结论）。
    """
    # 最近两份已发布/已截止作业
    rows = (
        (
            await db.execute(
                select(Assignment)
                .where(
                    Assignment.class_id == class_id,
                    Assignment.status.in_(("published", "closed")),
                    Assignment.deleted_at.is_(None),
                )
                .order_by(Assignment.created_at.desc())
                .limit(2)
            )
        )
        .scalars()
        .all()
    )
    if len(rows) < 2 or rows[0].quiz_id is None or rows[1].quiz_id is None:
        return None

    async def _stats(assignment: Assignment) -> dict[str, dict]:
        """question_text(归一化) -> {count, students:set}；仅确定性判错口径 verdict==wrong。"""
        result: dict[str, dict] = {}
        item_rows = (
            (
                await db.execute(
                    select(QuizItem.item_no, QuizItem.question_text).where(
                        QuizItem.quiz_id == assignment.quiz_id,
                        QuizItem.deleted_at.is_(None),
                    )
                )
            )
            .all()
        )
        qmap = dict(item_rows)
        wrong_rows = (
            await db.execute(
                select(Submission.user_id, SubmissionItem.item_no)
                .join(Submission, Submission.id == SubmissionItem.submission_id)
                .where(
                    Submission.assignment_id == assignment.id,
                    Submission.deleted_at.is_(None),
                    SubmissionItem.verdict == "wrong",
                    SubmissionItem.deleted_at.is_(None),
                )
            )
        ).all()
        for user_id, item_no in wrong_rows:
            text = qmap.get(item_no)
            if not text:
                continue
            key = _normalize_question(text)
            slot = result.setdefault(key, {"count": 0, "students": set()})
            slot["count"] += 1
            slot["students"].add(str(user_id))
        return result

    recent_stats = await _stats(rows[0])  # 最近
    prev_stats = await _stats(rows[1])  # 上一次

    shared: list[tuple[str, int]] = []
    for key, rslot in recent_stats.items():
        if key in prev_stats:
            shared.append((key, len(rslot["students"]) + len(prev_stats[key]["students"])))
    if not shared:
        return None
    shared.sort(key=lambda t: t[1], reverse=True)
    top_key = shared[0][0]
    rslot = recent_stats[top_key]
    pslot = prev_stats[top_key]
    repeat = len(rslot["students"] & pslot["students"])

    label = _shorten_question(top_key)
    # 依据仅在证据句中呈现，不暴露内部键值
    return ActionableInsight(
        class_id=class_id,
        kind="error_cluster",
        summary=(
            f"「{label}」这道题连续两次作业失分：{len(pslot['students'])} 人 → "
            f"{len(rslot['students'])} 人，其中 {repeat} 人重复"
        ),
        evidence={
            "question": top_key,
            "question_label": label,
            "recent_count": len(rslot["students"]),
            "previous_count": len(pslot["students"]),
            "repeat_count": repeat,
            "window_title": rows[0].title,
        },
        recommended_actions=[
            {"action": "adapt_lesson", "label": "加入下节课"},
            {"action": "generate_practice", "label": "出巩固题"},
            {"action": "open_evidence", "label": "看典型作答"},
        ],
        confidence=0.85,
        window_start=rows[1].created_at,
        window_end=rows[0].created_at,
    )


def _normalize_question(text: str) -> str:
    """题目文本归一化：去空白/标点变化导致的同题误判。"""
    cleaned = re.sub(r"[，。；、,.;:：\s\n\t]+", "", text or "")
    return cleaned[:80]


def _shorten_question(question: str, limit: int = 18) -> str:
    compressed = "".join(question.split())
    if len(compressed) <= limit:
        return compressed
    return compressed[:limit] + "…"


async def _submission_trend(db, class_id: uuid.UUID) -> ActionableInsight | None:
    # 提交率变化：比较最近两个已发布作业的提交数（确定性聚合）
    recent = (
        await db.execute(
            select(Assignment.id, Assignment.title, Assignment.created_at)
            .where(Assignment.class_id == class_id, Assignment.status.in_(("published", "closed")))
            .order_by(Assignment.created_at.desc())
            .limit(2)
        )
    ).all()
    if len(recent) < 2:
        return None
    counts = []
    for a in recent:
        c = await db.scalar(
            select(func.count(Submission.id)).where(
                Submission.assignment_id == a[0], Submission.deleted_at.is_(None)
            )
        )
        counts.append(int(c or 0))
    if counts[0] == counts[1]:
        return None
    sign = "上升" if counts[0] > counts[1] else "下降"
    return ActionableInsight(
        class_id=class_id,
        kind="submission_trend",
        summary=f"较上份作业，最近作业提交量{sign}（{counts[1]} → {counts[0]}）",
        evidence={"recent_count": counts[0], "previous_count": counts[1]},
        recommended_actions=[{"action": "open_assignment", "label": "查看作业"}],
        confidence=0.8,
        window_start=recent[1][2],
        window_end=recent[0][2],
    )


async def compute_class_insights(
    db: AsyncSession, teacher_id: uuid.UUID, class_id: uuid.UUID, *, actionable: bool = True
) -> list[dict]:
    """聚合该班洞察，最多 MAX_INSIGHTS 条；优先取已持久化 active 洞察，其次现算。"""
    from app.domains.teacher.scope import assert_teacher_in_class

    await assert_teacher_in_class(db, teacher_id, class_id)

    stored = (
        (
            await db.execute(
                select(ActionableInsight)
                .where(
                    ActionableInsight.class_id == class_id,
                    ActionableInsight.status == "active",
                )
                .order_by(ActionableInsight.confidence.desc())
                .limit(MAX_INSIGHTS)
            )
        )
        .scalars()
        .all()
    )
    if stored:
        return [_serialize(i) for i in stored]

    candidates = []
    for fn in (_error_cluster, _submission_trend, _low_mastery, _review_backlog):
        ins = await fn(db, class_id)
        if ins is not None:
            candidates.append(ins)
    if not candidates:
        return []

    candidates.sort(key=lambda i: i.confidence, reverse=True)
    top = candidates[:MAX_INSIGHTS]
    db.add_all(top)
    await db.flush()
    for i in top:  # 触发主键生成后再序列化
        await db.refresh(i)
    return [_serialize(i) for i in top]


# ==================== tel D5/A4-A5：班级学情聚合（roster / learning-overview） ====================


def _pending_item_condition() -> object:
    """待批口径：与 _review_backlog 一致（needs_review 或 verdict=pending_review）。"""
    return (SubmissionItem.needs_review.is_(True)) | (SubmissionItem.verdict == "pending_review")


async def _class_student_rows(db: AsyncSession, class_id: uuid.UUID) -> list:
    """班级学生成员（含 pending，confirmed 状态透出供教师花名册区分）。"""
    return (
        await db.execute(
            select(
                ClassMember.user_id,
                ClassMember.confirmed,
                User.nickname,
                ClassMember.nickname_in_class,
            )
            .join(User, User.id == ClassMember.user_id)
            .where(
                ClassMember.class_id == class_id,
                ClassMember.member_role == "student",
                ClassMember.deleted_at.is_(None),
            )
            .order_by(ClassMember.joined_at.asc(), ClassMember.user_id.asc())
        )
    ).all()


async def _class_submission_stats(
    db: AsyncSession, class_id: uuid.UUID
) -> tuple[dict, dict]:
    """按学生聚合（提交数, 待批题数）——只统计本班作业的提交。"""
    sub_rows = (
        await db.execute(
            select(Submission.user_id, func.count(Submission.id))
            .join(Assignment, Assignment.id == Submission.assignment_id)
            .where(
                Assignment.class_id == class_id,
                Assignment.deleted_at.is_(None),
                Submission.deleted_at.is_(None),
            )
            .group_by(Submission.user_id)
        )
    ).all()
    sub_counts = {user_id: int(cnt) for user_id, cnt in sub_rows}

    pending_rows = (
        await db.execute(
            select(Submission.user_id, func.count(SubmissionItem.id))
            .join(SubmissionItem, SubmissionItem.submission_id == Submission.id)
            .join(Assignment, Assignment.id == Submission.assignment_id)
            .where(
                Assignment.class_id == class_id,
                Assignment.deleted_at.is_(None),
                Submission.deleted_at.is_(None),
                SubmissionItem.deleted_at.is_(None),
                _pending_item_condition(),
            )
            .group_by(Submission.user_id)
        )
    ).all()
    pending_counts = {user_id: int(cnt) for user_id, cnt in pending_rows}
    return sub_counts, pending_counts


async def class_roster(
    db: AsyncSession, teacher_id: uuid.UUID, class_id: uuid.UUID
) -> dict:
    """tel A4：每生花名册 + 掌握度/薄弱 KP/待批数（确定性 SQL，无 LLM）。

    数据源：class_members + users + submissions/submission_items + mastery_records
    + knowledge_points。薄弱 KP 定义：mastery < WEAK_KPS_THRESHOLD(0.6)，
    升序取前 MAX_WEAK_KPS(3) 条；无记录 → 空数组（诚实空态）。
    """
    from app.domains.teacher.scope import assert_teacher_in_class

    await assert_teacher_in_class(db, teacher_id, class_id)

    students = await _class_student_rows(db, class_id)
    if not students:
        return {"class_id": str(class_id), "students": []}
    student_ids = [row[0] for row in students]

    submission_counts, pending_counts = await _class_submission_stats(db, class_id)

    mastery_rows = (
        await db.execute(
            select(MasteryRecord.user_id, func.avg(MasteryRecord.mastery))
            .where(MasteryRecord.user_id.in_(student_ids))
            .group_by(MasteryRecord.user_id)
        )
    ).all()
    avg_mastery = {user_id: float(avg) for user_id, avg in mastery_rows}

    weak_rows = (
        await db.execute(
            select(
                MasteryRecord.user_id,
                MasteryRecord.mastery,
                KnowledgePoint.code,
                KnowledgePoint.name,
            )
            .join(KnowledgePoint, KnowledgePoint.id == MasteryRecord.kp_id)
            .where(
                MasteryRecord.user_id.in_(student_ids),
                MasteryRecord.mastery < WEAK_KPS_THRESHOLD,
            )
            .order_by(MasteryRecord.user_id.asc(), MasteryRecord.mastery.asc())
        )
    ).all()
    weak_by_user: dict[uuid.UUID, list[dict]] = {}
    for user_id, mastery, code, name in weak_rows:
        bucket = weak_by_user.setdefault(user_id, [])
        if len(bucket) < MAX_WEAK_KPS:
            bucket.append({"code": code, "name": name, "mastery": round(float(mastery), 4)})

    items = []
    for user_id, confirmed, nickname, nickname_in_class in students:
        items.append(
            {
                "user_id": str(user_id),
                "nickname": nickname_in_class or nickname or "",
                "confirmed": bool(confirmed),
                "submission_count": submission_counts.get(user_id, 0),
                "pending_count": pending_counts.get(user_id, 0),
                "avg_mastery": avg_mastery.get(user_id),
                "weak_kps": weak_by_user.get(user_id, []),
            }
        )
    return {"class_id": str(class_id), "students": items}


async def class_learning_overview(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID,
    *,
    window_days: int = 14,
) -> dict:
    """tel A5：班级学情聚合卡（确定性 SQL，无 LLM，纯查询不落新表）。

    - submission_trend：窗口内逐日提交数；total = confirmed 学生数（提交率分母），
      无提交的日期也如实返回 submitted=0（趋势可绘制，不编造）；
    - kp_heat：窗口内已判作答（correct/wrong）按知识点聚合错误率，降序 ≤10，
      仅统计 graded ≥ 1 的知识点；
    - pending_review_count：当前待批积压（全期口径，同 _review_backlog）；
    - avg_mastery：confirmed 学生当前掌握度均值（无记录 → None）；
    - error_clusters：窗口内 error_records 按知识点聚类 ≤5（错题自动收录产物）。
    """
    from app.domains.teacher.scope import assert_teacher_in_class

    await assert_teacher_in_class(db, teacher_id, class_id)

    now = datetime.now(UTC)
    window_start = now - timedelta(days=window_days)

    confirmed_ids = (
        await db.execute(
            select(ClassMember.user_id).where(
                ClassMember.class_id == class_id,
                ClassMember.member_role == "student",
                ClassMember.confirmed.is_(True),
                ClassMember.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    confirmed_total = len(confirmed_ids)

    # 逐日提交趋势（date(created_at) 分组；窗口内全日期覆盖，缺日补 0）
    trend_rows = (
        await db.execute(
            select(func.date(Submission.created_at), func.count(Submission.id))
            .join(Assignment, Assignment.id == Submission.assignment_id)
            .where(
                Assignment.class_id == class_id,
                Assignment.deleted_at.is_(None),
                Submission.deleted_at.is_(None),
                Submission.created_at >= window_start,
            )
            .group_by(func.date(Submission.created_at))
            .order_by(func.date(Submission.created_at).asc())
        )
    ).all()
    counts_by_date = {str(d): int(c) for d, c in trend_rows}
    submission_trend = []
    for offset in range(window_days):
        day = (now - timedelta(days=window_days - 1 - offset)).date()
        key = day.isoformat()
        submission_trend.append(
            {"date": key, "submitted": counts_by_date.get(key, 0), "total": confirmed_total}
        )

    # KP 热度（错误率）：submission_items → submissions(quiz_id) → quiz_items(item_no) 关联 kp_code
    heat_rows = (
        await db.execute(
            select(
                QuizItem.kp_code,
                func.sum(case((SubmissionItem.verdict == "wrong", 1), else_=0)).label("wrong"),
                func.sum(
                    case(
                        (SubmissionItem.verdict.in_(("correct", "wrong")), 1),
                        else_=0,
                    )
                ).label("graded"),
            )
            .join(Submission, Submission.quiz_id == QuizItem.quiz_id)
            .join(
                SubmissionItem,
                (SubmissionItem.submission_id == Submission.id)
                & (SubmissionItem.item_no == QuizItem.item_no),
            )
            .join(Assignment, Assignment.id == Submission.assignment_id)
            .where(
                Assignment.class_id == class_id,
                Assignment.deleted_at.is_(None),
                Submission.deleted_at.is_(None),
                SubmissionItem.deleted_at.is_(None),
                QuizItem.deleted_at.is_(None),
                QuizItem.kp_code.isnot(None),
                Submission.created_at >= window_start,
            )
            .group_by(QuizItem.kp_code)
        )
    ).all()
    codes = [row[0] for row in heat_rows]
    kp_name_map: dict[str, str] = {}
    if codes:
        kp_rows = (
            await db.execute(
                select(KnowledgePoint.code, KnowledgePoint.name).where(
                    KnowledgePoint.code.in_(codes)
                )
            )
        ).all()
        kp_name_map = dict(kp_rows)
    kp_heat = []
    for code, wrong, graded in heat_rows:
        graded = int(graded or 0)
        if graded <= 0:
            continue
        kp_heat.append(
            {
                "code": code,
                "name": kp_name_map.get(code, code),
                "wrong_rate": round(int(wrong or 0) / graded, 4),
            }
        )
    kp_heat.sort(key=lambda r: (-r["wrong_rate"], r["code"]))
    kp_heat = kp_heat[:MAX_KP_HEAT]

    # 待批积压（全期，口径同 _review_backlog）
    sub_ids = select(Submission.id).join(
        Assignment, Assignment.id == Submission.assignment_id
    ).where(Assignment.class_id == class_id)
    pending_review_count = int(
        await db.scalar(
            select(func.count(SubmissionItem.id)).where(
                SubmissionItem.submission_id.in_(sub_ids),
                SubmissionItem.deleted_at.is_(None),
                _pending_item_condition(),
            )
        )
        or 0
    )

    # 掌握度均值（confirmed 学生当前值）
    avg_mastery: float | None = None
    if confirmed_ids:
        value = await db.scalar(
            select(func.avg(MasteryRecord.mastery)).where(
                MasteryRecord.user_id.in_(confirmed_ids)
            )
        )
        avg_mastery = round(float(value), 4) if value is not None else None

    # 错误聚类：窗口内错题记录按 kp_code 聚类（错题自动收录产物，不编造）
    error_clusters: list[dict] = []
    if confirmed_ids:
        cluster_rows = (
            await db.execute(
                select(ErrorRecord.kp_code, func.count(ErrorRecord.id))
                .where(
                    ErrorRecord.user_id.in_(confirmed_ids),
                    ErrorRecord.deleted_at.is_(None),
                    ErrorRecord.kp_code.isnot(None),
                    ErrorRecord.created_at >= window_start,
                )
                .group_by(ErrorRecord.kp_code)
                .order_by(func.count(ErrorRecord.id).desc())
                .limit(MAX_ERROR_CLUSTERS)
            )
        ).all()
        cluster_codes = [code for code, _cnt in cluster_rows]
        cluster_names: dict[str, str] = {}
        if cluster_codes:
            kp_rows = (
                await db.execute(
                    select(KnowledgePoint.code, KnowledgePoint.name).where(
                        KnowledgePoint.code.in_(cluster_codes)
                    )
                )
            ).all()
            cluster_names = dict(kp_rows)
        error_clusters = [
            {
                "kp_code": code,
                "label": cluster_names.get(code, code),
                "count": int(cnt),
            }
            for code, cnt in cluster_rows
        ]

    return {
        "class_id": str(class_id),
        "window_days": window_days,
        "submission_trend": submission_trend,
        "kp_heat": kp_heat,
        "pending_review_count": pending_review_count,
        "avg_mastery": avg_mastery,
        "error_clusters": error_clusters,
    }
