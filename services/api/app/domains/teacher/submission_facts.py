"""R19 教师侧学情证据只读投影：按作业导出「统计最小素材」（8100 Adapter 消费）。

边界（R19 DECISION-IMPLEMENTATION-DELTA D16）：
- 8000 继续唯一拥有 Assignment / StudentSubmission / ClassMember 事实；
- 本端点为 **additive 只读投影**，零既有端点改动、学生端零改动；
- PII 最小化在 8000 出口即完成：user_id 不出域，出口即 sha256 伪名化为
  `student_key`（固定域盐，UUID 空间不可字典化）；答案正文/附件/建议理由/
  昵称/文件 id 一律**不出域**；
- 鉴权：require_teacher_role（router 级 require_verified_teacher 之上）
  + assert_teacher_in_class（当前 ClassMember 事实裁决）；
- 载荷仅为确定性统计所需：提交 id / 伪名键 / item_no / verdict / score /
  teacher_final_score / suggestion_confidence / 是否有作答 / needs_review /
  error_type / created_at / 提交状态 / 班级当前学生伪名键清单（eligible）。

调用方：services/teacher-platform `app/teacher_prep/profile/facts.py`
（经 Api8000Adapter，Bearer passthrough 教师 JWT）。
"""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.teacher.scope import assert_teacher_in_class
from app.models.class_member import ClassMember
from app.models.coursework import Assignment, QuizItem, Submission, SubmissionItem

# student_key 伪名化域盐（固定；输入空间为 122bit UUID，不可字典化）
_STUDENT_KEY_SALT = "submission-facts-v1"


def student_key_of(user_id: uuid.UUID) -> str:
    """8000 出口伪名化：user_id → sha256(salt:user_id)[:32]；明文 id 不出域。"""
    digest = hashlib.sha256(f"{_STUDENT_KEY_SALT}:{user_id}".encode()).hexdigest()
    return digest[:32]


async def assignment_submission_facts(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    *,
    assignment_id: uuid.UUID,
) -> dict:
    """按作业导出统计最小素材（只读；无数据返回空 submissions + 空 eligible）。"""
    assignment = await db.get(Assignment, assignment_id)
    if assignment is None or assignment.deleted_at is not None:
        return {"assignment": None, "eligible_student_keys": [], "submissions": []}

    # 当前 ClassMember 事实裁决：教师必须仍属该班（跨班访问拒绝点）
    await assert_teacher_in_class(db, teacher_id, assignment.class_id)

    # eligible = 当前学生成员（role=student，未软删、未删除标记）
    member_rows = (
        (
            await db.execute(
                select(ClassMember)
                .where(
                    ClassMember.class_id == assignment.class_id,
                    ClassMember.member_role == "student",
                    ClassMember.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    eligible_keys: list[str] = []
    for m in member_rows:
        uid = getattr(m, "user_id", None)
        confirmed = getattr(m, "confirmed", True)
        if uid is None or confirmed is False:
            continue
        eligible_keys.append(student_key_of(uid))
    eligible_keys = sorted(set(eligible_keys))

    rows = (
        (
            await db.execute(
                select(SubmissionItem, Submission)
                .join(Submission, SubmissionItem.submission_id == Submission.id)
                .where(
                    Submission.assignment_id == assignment.id,
                    Submission.deleted_at.is_(None),
                    SubmissionItem.deleted_at.is_(None),
                )
                .order_by(Submission.created_at.asc(), SubmissionItem.id.asc())
            )
        )
        .all()
    )

    submissions: dict[uuid.UUID, dict] = {}
    for item, sub in rows:
        bucket = submissions.get(sub.id)
        if bucket is None:
            bucket = {
                "submission_id": str(sub.id),
                "student_key": student_key_of(sub.user_id),
                "client_submit_id": sub.client_submit_id,
                "status": sub.status,
                "created_at": sub.created_at.isoformat() if sub.created_at else None,
                "items": [],
            }
            submissions[sub.id] = bucket
        bucket["items"].append(
            {
                "item_no": item.item_no,
                "q_type": item.q_type,
                # 仅判定结果与分数素材；answer_text / attachments / 建议理由不出域
                "verdict": item.verdict,
                "score": float(item.score) if item.score is not None else None,
                "teacher_final_score": (
                    float(item.teacher_final_score)
                    if item.teacher_final_score is not None
                    else None
                ),
                "suggestion_confidence": (
                    float(item.suggestion_confidence)
                    if item.suggestion_confidence is not None
                    else None
                ),
                "has_answer": bool(
                    (item.answer_text or "").strip()
                    or item.attachments
                ),
                "needs_review": bool(item.needs_review),
                "error_type": item.error_type,
            }
        )

    # tel E2E 修复：随载荷导出 item→KP 映射（quiz_items.kp_code 同库确定性事实）——
    # 8100 画像的 mastery_by_kp 需要该映射（此前恒 missing_item_kp_map → 掌握度永空）。
    item_kp_map: dict[str, list[int]] = {}
    if assignment.quiz_id is not None:
        qi_rows = (
            await db.execute(
                select(QuizItem.item_no, QuizItem.kp_code).where(QuizItem.quiz_id == assignment.quiz_id)
            )
        ).all()
        for item_no, kp_code in qi_rows:
            if not kp_code:
                continue
            item_kp_map.setdefault(str(kp_code), []).append(int(item_no))
    item_kp_map = {k: sorted(set(v)) for k, v in sorted(item_kp_map.items())}

    return {
        "item_kp_map": item_kp_map,
        "assignment": {
            "assignment_id": str(assignment.id),
            "class_id": str(assignment.class_id),
            "title": assignment.title,
            "deadline": assignment.deadline.isoformat() if getattr(assignment, "deadline", None) else None,
            "status": assignment.status,
        },
        "eligible_student_keys": eligible_keys,
        "submissions": sorted(
            submissions.values(), key=lambda s: (s["created_at"] or "", s["submission_id"])
        ),
    }


__all__ = ["assignment_submission_facts", "student_key_of"]
