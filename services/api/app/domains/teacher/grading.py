"""M3 教师端：预批改与正式计分（§13）。

suggest_grade 只产生建议（draft），不写正式分：
- 客观题优先规则 / SymPy；主观题可用本地 ModelRouter 或 wf_solution_pregrade；
- 星辰失败 → 本地建议或人工复核；低置信/OCR 不清/Schema 非法 → needs_review=true；
- 写入 submission_items 建议字段 + 生成 grading_suggestion draft artifact。

confirm 为唯一写正式分数入口（幂等）：
- accept 与 override 都保存建议值与终值；
- 用 Idempotency-Key，重放不重复更新 mastery/ledger；
- 只有确认成功才同步 score / Submission 汇总；学情只消费已确认终值。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.teacher.artifacts import create_artifact, get_owned_artifact
from app.domains.teacher.scope import raise_http
from app.models.class_ import Class
from app.models.coursework import Assignment, QuizItem, Submission, SubmissionItem
from app.models.file import File
from app.models.teacher import TeacherAction, TeachingArtifact
from app.services import solution_pregrade

ERR_CONFIRMATION_REQUIRED = 42210
ERR_NOT_FOUND = 40400
ERR_VALIDATION = 40001
ERR_DUPLICATE = 40902


def _now() -> datetime:
    return datetime.now(UTC)


def _student_label(item_no: int) -> str:
    """匿名学生标签（批改页默认匿名，不解锁个体身份）。"""
    return f"匿名作答 #{item_no:03d}"


def _queue_status(item: SubmissionItem) -> str:
    """队列状态：unprocessed | low_confidence | confirmed（对齐前端 GradingQueueItem）。"""
    if item.suggestion_status in ("accepted", "overridden", "applied"):
        return "confirmed"
    if item.needs_review or item.verdict == "pending_review":
        return "low_confidence"
    return "unprocessed"


def _auto_graded(item: SubmissionItem) -> bool:
    """系统自动判分且即时终局（tel F3）：选择/判断题提交即判、无低置信标记、
    教师未做任何建议/确认动作。填空/解答不含（须人工批阅，愿景 #4）。"""
    return (
        item.suggestion_status == "draft"
        and not item.needs_review
        and item.q_type in ("choice", "judge")
        and item.verdict in ("correct", "wrong")
    )


def _serialize_queue_item(item: SubmissionItem) -> dict:
    # tel F3：自动判分的选择/判断题把系统分直接作为建议分快照下发——
    # 队列无需逐份打开详情即可见（PLAN §1.2③⑥）；批量确认 accept 走同一值。
    auto_score = (
        float(item.score)
        if item.suggested_score is None and _auto_graded(item) and item.score is not None
        else None
    )
    return {
        "submission_item_id": str(item.id),
        "student_label": _student_label(item.item_no),
        "status": _queue_status(item),
        "confidence": float(item.suggestion_confidence or 0.0),
        "suggestion_score": (
            float(item.suggested_score) if item.suggested_score is not None else auto_score
        ),
        "teacher_final_score": (
            float(item.teacher_final_score) if item.teacher_final_score is not None else None
        ),
    }


def _workspace_rubric(quiz_item: QuizItem | None) -> tuple[str, list[dict], float | None]:
    """Expose only explicit persisted score points to the V2 workstation."""
    if quiz_item is None or quiz_item.max_score is None:
        return "missing", [], None
    raw_items = quiz_item.grading_rubric
    if not isinstance(raw_items, list) or not raw_items:
        return "missing", [], float(quiz_item.max_score)

    normalized: list[dict] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            return "missing", [], float(quiz_item.max_score)
        rubric_id = raw.get("id")
        criterion = raw.get("criterion")
        points = raw.get("points")
        evidence_hint = raw.get("evidence_hint")
        if (
            not isinstance(rubric_id, str)
            or not rubric_id.strip()
            or not isinstance(criterion, str)
            or not criterion.strip()
            or not isinstance(points, (int, float))
            or isinstance(points, bool)
            or points < 0
            or not isinstance(evidence_hint, str)
        ):
            return "missing", [], float(quiz_item.max_score)
        normalized.append(
            {
                "id": rubric_id,
                "criterion": criterion,
                "points": float(points),
                "evidence_hint": evidence_hint,
            }
        )
    return "ready", normalized, float(quiz_item.max_score)


def _workspace_suggestion(suggestion: dict | None) -> dict:
    """V2 keeps suggestion evidence but never forwards confidence as a score signal."""
    data = suggestion or {}
    evidence = data.get("evidence")
    evidence_items = (
        [{"kind": "grading_evidence", "text": evidence}]
        if isinstance(evidence, str) and evidence.strip()
        else []
    )
    return {
        "suggestion_id": data.get("suggestion_id") or None,
        "version": data.get("version") or 1,
        "proposed_score": data.get("suggestion_score"),
        "review_needed": bool(data.get("review_needed")),
        "evidence": evidence_items,
    }


async def _manual_review_state_by_item(
    db: AsyncSession, teacher_id: uuid.UUID, submission_item_ids: list[uuid.UUID]
) -> dict[uuid.UUID, bool]:
    """Read the newest review action per item without changing automatic review facts."""
    wanted = {str(item_id): item_id for item_id in submission_item_ids}
    if not wanted:
        return {}
    actions = (
        await db.execute(
            select(TeacherAction)
            .where(
                TeacherAction.teacher_id == teacher_id,
                TeacherAction.action_type == "grading.review.set",
            )
            .order_by(TeacherAction.created_at.desc(), TeacherAction.id.desc())
        )
    ).scalars().all()
    states: dict[uuid.UUID, bool] = {}
    for action in actions:
        details = action.details or {}
        item_id = wanted.get(details.get("submission_item_id"))
        if item_id is None or item_id in states:
            continue
        states[item_id] = details.get("review_state") == "pending"
    return states


def _workspace_queue_state(item: SubmissionItem, manual_review: bool) -> str:
    if item.suggestion_status in ("accepted", "overridden", "applied"):
        return "confirmed"
    if manual_review or item.needs_review or item.verdict == "pending_review":
        return "review"
    if _auto_graded(item):
        # tel F3：选择/判断题系统已自动批阅（PLAN §1.2③⑥：队列直接可见、无需逐题人审）
        return "auto_graded"
    return "ungraded"


def _serialize_suggestion(
    item: SubmissionItem, *, suggestion_id: str | None = None, version: int = 1
) -> dict:
    """对齐前端 GradingSuggestion 契约。"""
    rationale = item.suggestion_rationale or {}
    # evidence 是面向教师的证据句，而非内部键值调试输出
    detail = rationale.get("detail")
    evidence = detail.strip() if isinstance(detail, str) and detail.strip() else ""
    return {
        "suggestion_id": suggestion_id or "",
        "submission_item_id": str(item.id),
        "student_label": _student_label(item.item_no),
        "original_answer": item.answer_text or "",
        "file_id": str(item.file_id) if item.file_id else None,
        "attachments": item.attachments or [],
        "scoring_standard": "按题目评分点逐项给分（客观题规则判定，主观题人工复核）",
        "suggestion_score": (
            float(item.suggested_score) if item.suggested_score is not None else None
        ),
        "confidence": float(item.suggestion_confidence or 0.0),
        "evidence": evidence or "本地规则/模型建议，待教师确认",
        "review_needed": bool(item.needs_review),
        "teacher_final_score": (
            float(item.teacher_final_score) if item.teacher_final_score is not None else None
        ),
        "teacher_feedback": item.teacher_feedback,
        "decision": item.suggestion_status,  # draft|accepted|overridden|applied
        "version": version,
    }


def _quiz_evidence(quiz_item: QuizItem | None) -> dict | None:
    """Immutable persisted facts that make an objective suggestion auditable."""
    if quiz_item is None:
        return None
    return {
        "quiz_item_id": str(quiz_item.id),
        "q_type": quiz_item.q_type,
        "standard_answer": quiz_item.answer,
        "question_text_digest": hashlib.sha256(
            quiz_item.question_text.encode("utf-8")
        ).hexdigest(),
        "kp_code": quiz_item.kp_code,
    }


def _suggestion_fingerprint(
    item: SubmissionItem, quiz_evidence: dict | None
) -> str:
    """Bind an artifact to the exact suggestion snapshot it was created from."""
    snapshot = {
        "submission_item_id": str(item.id),
        "q_type": item.q_type,
        "answer_text": item.answer_text,
        "file_id": str(item.file_id) if item.file_id else None,
        "suggested_score": str(item.suggested_score) if item.suggested_score is not None else None,
        "suggestion_rationale": item.suggestion_rationale or {},
        "suggestion_feedback": item.suggestion_feedback,
        "suggestion_confidence": (
            str(item.suggestion_confidence)
            if item.suggestion_confidence is not None
            else None
        ),
        "needs_review": bool(item.needs_review),
        "quiz_evidence": quiz_evidence,
    }
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _confirmation_binding(
    *,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID,
    submission_item_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    decision: str,
    final_score: float | None,
    teacher_feedback: str | None,
    version: int,
) -> dict:
    request = {
        "decision": decision,
        "final_score": str(final_score),
        "teacher_feedback": teacher_feedback,
        "version": version,
    }
    request_json = json.dumps(request, ensure_ascii=False, sort_keys=True)
    return {
        "teacher_id": str(teacher_id),
        "action": "grade.confirm",
        "class_id": str(class_id),
        "submission_item_id": str(submission_item_id),
        "suggestion_id": str(suggestion_id),
        "decision": decision,
        "request_fingerprint": hashlib.sha256(request_json.encode("utf-8")).hexdigest(),
    }


def _action_matches_binding(action: TeacherAction, binding: dict) -> bool:
    return (action.details or {}).get("binding") == binding


async def _lock_idempotency_key(db: AsyncSession, idempotency_key: str) -> None:
    """Serialize same-key confirms even before their audit row exists."""
    await db.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(idempotency_key)))
    )


async def lock_batch_submission_items(
    db: AsyncSession, submission_item_ids: list[uuid.UUID]
) -> None:
    """Acquire every batch resource lock in a global order before grading any item."""
    for submission_item_id in sorted(set(submission_item_ids), key=str):
        await db.execute(
            select(
                func.pg_advisory_xact_lock(
                    func.hashtext(f"grading-batch-item:{submission_item_id}")
                )
            )
        )


def _add_confirmation_action(
    db: AsyncSession,
    *,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    client_request_id: str,
    idempotency_key: str | None,
    request_id: str | None,
    binding: dict,
    final_score: float,
    replayed: bool,
) -> None:
    db.add(
        TeacherAction(
            teacher_id=teacher_id,
            class_id=class_id,
            artifact_id=suggestion_id,
            action_type="grade.confirm",
            client_request_id=client_request_id,
            idempotency_key=idempotency_key,
            before_digest=None,
            after_digest=None,
            request_id=request_id,
            details={
                "binding": binding,
                "decision": binding["decision"],
                "final_score": final_score,
                "replayed": replayed,
            },
        )
    )


async def _load_item_in_class(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    submission_item_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> tuple[SubmissionItem, Submission, Assignment]:
    from app.domains.teacher.scope import assert_teacher_in_class

    if for_update:
        item = await db.scalar(
            select(SubmissionItem)
            .where(SubmissionItem.id == submission_item_id)
            .with_for_update()
        )
    else:
        item = await db.get(SubmissionItem, submission_item_id)
    if item is None or item.deleted_at is not None:
        raise_http(ERR_NOT_FOUND, 404, "not_found", recoverable=False)
    sub = await db.get(Submission, item.submission_id)
    if sub is None or sub.deleted_at is not None:
        raise_http(ERR_NOT_FOUND, 404, "not_found", recoverable=False)
    a = (
        await db.get(Assignment, sub.assignment_id)
        if sub.assignment_id is not None
        else None
    )
    if a is None or a.deleted_at is not None:
        raise_http(ERR_NOT_FOUND, 404, "not_found", recoverable=False)
    await assert_teacher_in_class(db, teacher_id, a.class_id)
    return item, sub, a


async def _load_persisted_quiz_item(
    db: AsyncSession, sub: Submission, item: SubmissionItem
) -> tuple[QuizItem | None, str | None]:
    """Return one trusted persisted context, or a reason why none is trustworthy."""
    if sub.quiz_id is None:
        return None, "missing"
    rows = (
        await db.execute(
            select(QuizItem).where(
                QuizItem.quiz_id == sub.quiz_id,
                QuizItem.item_no == item.item_no,
                QuizItem.deleted_at.is_(None),
            ).limit(2)
        )
    ).scalars().all()
    if len(rows) != 1:
        return None, "missing" if not rows else "ambiguous"
    quiz_item = rows[0]
    if quiz_item.q_type != item.q_type:
        return None, "q_type_mismatch"
    return quiz_item, None


def _normalize_objective_answer(value: str | None) -> str:
    return (value or "").strip().casefold()


def _effective_question_max_score(quiz_item: QuizItem) -> float | None:
    """Return the only score ceiling supported by persisted question evidence."""
    if quiz_item.max_score is not None and float(quiz_item.max_score) > 0:
        return float(quiz_item.max_score)
    if quiz_item.q_type in ("choice", "judge"):
        return 1.0
    return None


def _objective_score(
    item: SubmissionItem, quiz_item: QuizItem | None, context_reason: str | None
) -> tuple[float, float, bool, str]:
    """Score objective answers only when a persisted standard answer is available."""
    if context_reason == "ambiguous":
        return 0.0, 0.0, True, "存在重复的已持久化题目上下文，无法依据标准答案判定"
    if context_reason == "q_type_mismatch":
        return 0.0, 0.0, True, "作答题型与已持久化题目题型不一致，无法依据标准答案判定"
    if quiz_item is None:
        return 0.0, 0.0, True, "缺少已持久化题目上下文，无法依据标准答案判定"
    standard_answer = _normalize_objective_answer(quiz_item.answer)
    if not standard_answer:
        return 0.0, 0.0, True, "缺少标准答案证据，无法依据标准答案判定"
    is_correct = _normalize_objective_answer(item.answer_text) == standard_answer
    full_mark = _effective_question_max_score(quiz_item) or 1.0
    return (
        full_mark if is_correct else 0.0,
        1.0,
        False,
        "已依据已持久化标准答案判定：答案一致"
        if is_correct
        else "已依据已持久化标准答案判定：答案不一致",
    )


def _untrusted_context_suggestion(
    item: SubmissionItem,
    context_reason: str | None,
    *,
    suggestion_id: str | None = None,
    version: int = 1,
) -> dict:
    """Do not present a stale suggestion as trustworthy after its context changes."""
    _, confidence, _, evidence = _objective_score(item, None, context_reason)
    suggestion = _serialize_suggestion(
        item, suggestion_id=suggestion_id, version=version
    )
    suggestion.update(
        suggestion_score=None,
        confidence=confidence,
        evidence=evidence,
        review_needed=True,
    )
    return suggestion


async def suggest_grade(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID | None,
    submission_item_id: uuid.UUID,
    *,
    client_request_id: str,
    use_ai: bool = True,
) -> dict:
    """批改建议（draft，绝不直接写正式分——confirm 才是唯一写分入口）。

    - choice/judge：本地规则判定（无 AI）；
    - solution：tel D9——教师显式触发（POST suggest，use_ai=True 默认）时同步调用
      共享 AI 初批管线 app.services.solution_pregrade（失败/超时/输出非法/空作答
      → 降级 0.0/0.3/manual_review，即原状路径）；needs_review 恒置 True，AI 永不
      静默写分；GET 详情的自动建议路径传 use_ai=False（不隐式调 AI）；
    - blank：本地 0.0/0.3/manual_review（共享管线 prompt 为解答题步骤分语义，
      不拉伸用于填空——评审意见采纳：收窄为仅 solution 调 AI）。
    """
    item, sub, assignment = await _load_item_in_class(db, teacher_id, submission_item_id)
    if class_id is not None and class_id != assignment.class_id:
        raise_http(ERR_NOT_FOUND, 404, "not_found", recoverable=False)

    # 客观题规则；否则本地建议 + 人工复核（星辰不可用时降级）
    if item.q_type in ("choice", "judge"):
        quiz_item, context_reason = await _load_persisted_quiz_item(db, sub, item)
        suggested, conf, needs_review, detail = _objective_score(
            item, quiz_item, context_reason
        )
        quiz_evidence = _quiz_evidence(quiz_item)
        rationale = {"type": "rule", "detail": detail}
        feedback = None
    elif item.q_type == "solution" and use_ai:
        # tel D9/A6：显式 AI 建议分——同步调用共享初批管线（monkeypatch
        # solution_pregrade.pregrade_solution 即可 stub，测试禁真实 LLM）。
        quiz_item, _context_reason = await _load_persisted_quiz_item(db, sub, item)
        try:
            _verdict, ai_score, extra = await solution_pregrade.pregrade_solution(
                quiz_item,
                item.answer_text,
                user_id=str(teacher_id),
                db=db,
                expected_answer=quiz_item.answer if quiz_item and quiz_item.answer else "",
                max_score=_effective_question_max_score(quiz_item) if quiz_item else None,
            )
        except Exception:  # noqa: BLE001 —— 管线意外异常与内部降级同待遇（不 500）
            _verdict, ai_score, extra = "pending_review", None, {"degraded": "ai_pregrade_unavailable"}
        quiz_evidence = None
        if extra.get("ai_pregraded") and ai_score is not None:
            suggested = float(ai_score)
            conf = float(extra.get("confidence") or 0.7)
            detail = str(extra.get("comment") or "").strip() or "AI 初批建议分，请人工确认"
            rationale = {"type": "ai_pregrade", "detail": detail}
            needs_review = True  # 恒置：AI 建议仅供参考，终分必须教师 confirm
            feedback = detail
        else:
            # AI 失败/超时/输出非法/无参考 → 降级现路径（0.0/0.3/manual_review）
            suggested, conf = 0.0, 0.3
            rationale = {"type": "manual_review", "detail": "主观题转人工复核"}
            needs_review = True
            feedback = "建议人工复核"
    else:
        suggested, conf = 0.0, 0.3
        quiz_evidence = None
        rationale = {"type": "manual_review", "detail": "主观题转人工复核"}
        needs_review = True
        feedback = "建议人工复核"

    item.suggested_score = Decimal(str(suggested))
    item.suggestion_rationale = rationale
    item.suggestion_feedback = feedback
    item.suggestion_confidence = Decimal(str(conf))
    item.needs_review = needs_review
    item.suggestion_status = "draft"
    item.ai_pregraded = True

    artifact = await create_artifact(
        db,
        owner_id=teacher_id,
        artifact_type="grading_suggestion",
        scene="teacher.grading",
        class_id=assignment.class_id,
        payload={
            **_serialize_suggestion(item),
            "quiz_evidence": quiz_evidence,
            "suggestion_fingerprint": _suggestion_fingerprint(item, quiz_evidence),
        },
        engine="local",
        degraded=needs_review,
        warnings=["建议转人工复核"] if needs_review else [],
    )
    db.add(artifact)
    await db.flush()
    # 对齐前端 GradingSuggestion：建议对象直接位于 data 顶层
    return _serialize_suggestion(
        item, suggestion_id=str(artifact.id), version=artifact.version
    )


async def grading_detail(
    db: AsyncSession, teacher_id: uuid.UUID, submission_item_id: uuid.UUID
) -> dict:
    """批改详情（对齐前端 GradingDetail）：尚未建议时先自动生成本地建议。"""
    item, _sub, assignment = await _load_item_in_class(db, teacher_id, submission_item_id)
    quiz_item, _context_reason = await _load_persisted_quiz_item(db, _sub, item)
    has_untrusted_objective_context = (
        item.q_type in ("choice", "judge") and _context_reason is not None
    )
    suggestion: dict | None = None
    if item.suggested_score is None:
        # 尚无建议：自动生成本地建议（tel D9：use_ai=False——GET 自动路径不隐式调 AI，
        # AI 建议只能由教师显式 POST /suggest 触发）
        suggestion = await suggest_grade(
            db, teacher_id, assignment.class_id if assignment else None, submission_item_id,
            client_request_id=f"auto:{submission_item_id}",
            use_ai=False,
        )
    elif has_untrusted_objective_context:
        artifact = (
            await db.execute(
                select(TeachingArtifact)
                .where(
                    TeachingArtifact.owner_id == teacher_id,
                    TeachingArtifact.artifact_type == "grading_suggestion",
                    TeachingArtifact.deleted_at.is_(None),
                    TeachingArtifact.payload["submission_item_id"].as_string()
                    == str(submission_item_id),
                )
                .order_by(TeachingArtifact.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        suggestion = _untrusted_context_suggestion(
            item,
            _context_reason,
            suggestion_id=str(artifact.id) if artifact is not None else None,
            version=artifact.version if artifact is not None else 1,
        )
    else:
        artifact = (
            await db.execute(
                select(TeachingArtifact)
                .where(
                    TeachingArtifact.owner_id == teacher_id,
                    TeachingArtifact.artifact_type == "grading_suggestion",
                    TeachingArtifact.deleted_at.is_(None),
                    TeachingArtifact.payload["submission_item_id"].as_string()
                    == str(submission_item_id),
                )
                .order_by(TeachingArtifact.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if artifact is not None:
            suggestion = _serialize_suggestion(
                item, suggestion_id=str(artifact.id), version=artifact.version
            )
        elif item.suggestion_status not in ("accepted", "overridden", "applied"):
            suggestion = await suggest_grade(
                db,
                teacher_id,
                assignment.class_id if assignment else None,
                submission_item_id,
                client_request_id=f"repair:{submission_item_id}",
                use_ai=False,
            )
    queue_item = _serialize_queue_item(item)
    if has_untrusted_objective_context:
        queue_item.update(status="low_confidence", confidence=0.0, suggestion_score=None)
    return {
        **queue_item,
        "original_answer": item.answer_text or "",
        "file_id": str(item.file_id) if item.file_id else None,
        "attachments": item.attachments or [],
        "scoring_standard": "按题目评分点逐项给分（客观题规则判定，主观题人工复核）",
        "assignment_title": assignment.title if assignment and assignment.deleted_at is None else None,
        "question_text": quiz_item.question_text if quiz_item else None,
        "question_type": quiz_item.q_type if quiz_item else None,
        "options": quiz_item.options if quiz_item else None,
        "standard_answer": quiz_item.answer if quiz_item and quiz_item.answer else None,
        "answer_analysis": quiz_item.answer_analysis if quiz_item else None,
        "suggestion": suggestion
        or (
            _untrusted_context_suggestion(item, _context_reason)
            if has_untrusted_objective_context
            else _serialize_suggestion(item)
        ),
    }


async def grading_workspace(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    *,
    class_id: uuid.UUID | None,
    assignment_id: uuid.UUID | None,
    item_no: int | None,
    status: str,
    submission_item_id: uuid.UUID | None,
) -> dict:
    """Build one authorized, question-focused V2 workstation projection."""
    from app.domains.teacher.scope import assert_teacher_in_class
    from app.domains.teacher.today import teacher_class_ids

    if status not in {"all", "ungraded", "review", "confirmed", "auto_graded"}:
        raise_http(ERR_VALIDATION, 422, "invalid_status", recoverable=True)
    if class_id is not None:
        await assert_teacher_in_class(db, teacher_id, class_id)
        class_ids = [class_id]
    else:
        class_ids = await teacher_class_ids(db, teacher_id)

    def _empty_workspace() -> dict:
        return {
            "context": {
                "class": None,
                "assignment": None,
                "question": None,
                "filters": {"status": status},
                "progress": {"total": 0, "confirmed": 0, "remaining": 0},
            },
            "available_context": {"assignments": [], "questions": []},
            "queue": [],
            "selected": None,
            "navigation": {"previous_id": None, "next_ungraded_id": None},
        }

    if not class_ids:
        return _empty_workspace()
    assignments = (
        await db.execute(
            select(Assignment)
            .where(
                Assignment.class_id.in_(class_ids),
                Assignment.deleted_at.is_(None),
                Assignment.status.in_(("published", "closed")),
            )
            .order_by(Assignment.created_at.desc(), Assignment.id.desc())
        )
    ).scalars().all()
    if assignment_id is not None:
        assignments = [assignment for assignment in assignments if assignment.id == assignment_id]
        if not assignments:
            raise_http(ERR_NOT_FOUND, 404, "not_found", recoverable=False)
    if not assignments:
        return _empty_workspace()

    async def _load_rows(assignment: Assignment) -> list:
        return (
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
        ).all()

    # 默认（未指定作业）时：优先选择最近一份有实际作答的作业进入工作台，
    # 避免教师看到空队列、与今日待批数量互相矛盾。
    assignment = assignments[0]
    clazz = await db.get(Class, assignment.class_id)
    if assignment_id is None:
        for candidate in assignments:
            rows = await _load_rows(candidate)
            if rows:
                assignment = candidate
                clazz = await db.get(Class, assignment.class_id)
                break
    all_rows = await _load_rows(assignment)
    if not all_rows:
        empty = _empty_workspace()
        empty["context"]["class"] = {
            "class_id": str(assignment.class_id),
            "label": clazz.name if clazz is not None else None,
        }
        empty["context"]["assignment"] = {
            "assignment_id": str(assignment.id),
            "title": assignment.title,
        }
        empty["available_context"]["assignments"] = [
            {"assignment_id": str(row.id), "title": row.title}
            for row in assignments
        ]
        return empty

    question_numbers = sorted({item.item_no for item, _sub in all_rows})
    selected_item_no = item_no if item_no is not None else question_numbers[0]
    if submission_item_id is not None and item_no is None:
        # tel E2E 修复：锚定项应自行决定题号——confirm 后前端按 submission_item_id 重载
        # （不带 item_no），此前落到第 1 题的队列 → 锚点不在其中 → 404，工作台断链。
        anchor_no = next(
            (it.item_no for it, _s in all_rows if str(it.id) == str(submission_item_id)),
            None,
        )
        if anchor_no is not None:
            selected_item_no = anchor_no
    if selected_item_no not in question_numbers:
        raise_http(ERR_NOT_FOUND, 404, "not_found", recoverable=False)
    question_rows = [
        (item, sub)
        for item, sub in all_rows
        if item.item_no == selected_item_no
    ]
    manual_review = await _manual_review_state_by_item(
        db, teacher_id, [item.id for item, _sub in question_rows]
    )
    queue_entries = [
        {
            "submission_item_id": str(item.id),
            "anonymous_label": f"第 {position} 份作答",
            "state": _workspace_queue_state(item, manual_review.get(item.id, False)),
            "manual_review": manual_review.get(item.id, False),
        }
        for position, (item, _sub) in enumerate(question_rows, start=1)
    ]
    filtered_queue = [
        entry for entry in queue_entries if status == "all" or entry["state"] == status
    ]
    if submission_item_id is not None:
        selected_index = next(
            (
                index
                for index, entry in enumerate(filtered_queue)
                if entry["submission_item_id"] == str(submission_item_id)
            ),
            None,
        )
        if selected_index is None:
            raise_http(ERR_NOT_FOUND, 404, "not_found", recoverable=False)
    elif filtered_queue:
        selected_index = next(
            (
                index
                for index, entry in enumerate(filtered_queue)
                if entry["state"] not in ("confirmed", "auto_graded")
            ),
            0,
        )
    else:
        selected_index = None

    selected_entry = (
        filtered_queue[selected_index] if selected_index is not None else None
    )
    selected_item: SubmissionItem | None = None
    selected_sub: Submission | None = None
    selected_detail: dict | None = None
    selected_quiz_item: QuizItem | None = None
    if selected_entry is not None:
        selected_id = uuid.UUID(selected_entry["submission_item_id"])
        selected_item, selected_sub = next(
            (item, sub) for item, sub in question_rows if item.id == selected_id
        )
        selected_detail = await grading_detail(db, teacher_id, selected_id)
        selected_quiz_item, _reason = await _load_persisted_quiz_item(
            db, selected_sub, selected_item
        )

    context_quiz_item = selected_quiz_item
    if context_quiz_item is None:
        first_item, first_sub = question_rows[0]
        context_quiz_item, _reason = await _load_persisted_quiz_item(
            db, first_sub, first_item
        )
    rubric_status, rubric_items, max_score = _workspace_rubric(context_quiz_item)
    question_options: list[dict] = []
    for number in question_numbers:
        row_item, row_sub = next(
            (item, sub) for item, sub in all_rows if item.item_no == number
        )
        quiz_item, _reason = await _load_persisted_quiz_item(db, row_sub, row_item)
        question_options.append(
            {
                "item_no": number,
                "label": f"第 {number} 题",
                "question_text": quiz_item.question_text if quiz_item else None,
            }
        )

    confirmed = sum(entry["state"] == "confirmed" for entry in queue_entries)
    auto_graded = sum(entry["state"] == "auto_graded" for entry in queue_entries)
    navigation = {"previous_id": None, "next_ungraded_id": None}
    if selected_index is not None:
        if selected_index > 0:
            navigation["previous_id"] = filtered_queue[selected_index - 1][
                "submission_item_id"
            ]
        following = filtered_queue[selected_index + 1 :] + filtered_queue[:selected_index]
        next_ungraded = next(
            (
                entry
                for entry in following
                if entry["state"] not in ("confirmed", "auto_graded")
            ),
            None,
        )
        navigation["next_ungraded_id"] = (
            next_ungraded["submission_item_id"] if next_ungraded else None
        )

    selected_payload = None
    if selected_detail is not None and selected_item is not None:
        selected_payload = {
            "submission_item_id": str(selected_item.id),
            "work": {
                "original_answer": selected_detail["original_answer"],
                "file_id": selected_detail["file_id"],
                # tel E2E 修复：拍照作答文件在 attachments（file_id 列恒空），随投影下发供原图兜底
                "attachments": selected_detail.get("attachments") or [],
            },
            "scoring": {
                "max_score": max_score,
                "rubric_status": rubric_status,
                "rubric_items": rubric_items,
                "standard_answer": selected_detail["standard_answer"],
                "answer_analysis": selected_detail["answer_analysis"],
                "fallback_standard": selected_detail["scoring_standard"],
            },
            "suggestion": _workspace_suggestion(selected_detail["suggestion"]),
            "confirmed_decision": (
                {
                    "final_score": selected_detail["teacher_final_score"],
                    "feedback": selected_detail["suggestion"].get("teacher_feedback"),
                    "decision": selected_detail["suggestion"].get("decision"),
                }
                if selected_detail["teacher_final_score"] is not None
                else None
            ),
        }
    return {
        "context": {
            "class": {
                "class_id": str(assignment.class_id),
                "label": clazz.name if clazz is not None else None,
            },
            "assignment": {
                "assignment_id": str(assignment.id),
                "title": assignment.title,
            },
            "question": {
                "item_no": selected_item_no,
                "question_text": context_quiz_item.question_text if context_quiz_item else None,
                "q_type": context_quiz_item.q_type if context_quiz_item else None,
                "options": context_quiz_item.options if context_quiz_item else None,
                "image": context_quiz_item.image if context_quiz_item else [],  # 题目配图（data URI/URL 列表）
                "max_score": max_score,
            },
            "filters": {"status": status},
            "progress": {
                # tel F3：已批 = 教师确认 + 系统自动批阅（选择/判断题无需逐题人审）
                "total": len(queue_entries),
                "confirmed": confirmed + auto_graded,
                "remaining": len(queue_entries) - confirmed - auto_graded,
            },
        },
        "available_context": {
            "assignments": [
                {"assignment_id": str(row.id), "title": row.title}
                for row in assignments
            ],
            "questions": question_options,
        },
        "queue": filtered_queue,
        "selected": selected_payload,
        "navigation": navigation,
    }


async def set_grading_review(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    submission_item_id: uuid.UUID,
    *,
    state: str,
    note: str | None,
    client_request_id: str,
    idempotency_key: str | None,
    request_id: str | None,
) -> dict:
    """Persist an audited teacher review marker without writing formal grade data."""
    if state not in {"pending", "cleared"}:
        raise_http(ERR_VALIDATION, 422, "invalid_review_state", recoverable=True)
    item, _sub, assignment = await _load_item_in_class(
        db, teacher_id, submission_item_id
    )
    review_note_digest = (
        hashlib.sha256(note.encode("utf-8")).hexdigest() if note else None
    )
    binding = {
        "submission_item_id": str(item.id),
        "review_state": state,
        "review_note_digest": review_note_digest,
    }
    if idempotency_key:
        await _lock_idempotency_key(db, idempotency_key)
        existing = await db.scalar(
            select(TeacherAction).where(
                TeacherAction.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if (
                existing.action_type != "grading.review.set"
                or (existing.details or {}).get("binding") != binding
            ):
                raise_http(
                    ERR_DUPLICATE, 409, "idempotency_conflict", recoverable=False
                )
            return {
                "submission_item_id": str(item.id),
                "state": state,
                "replayed": True,
            }
    db.add(
        TeacherAction(
            teacher_id=teacher_id,
            class_id=assignment.class_id,
            artifact_id=None,
            action_type="grading.review.set",
            client_request_id=client_request_id,
            idempotency_key=idempotency_key,
            before_digest=None,
            after_digest=None,
            request_id=request_id,
            details={**binding, "binding": binding, "replayed": False},
        )
    )
    await db.flush()
    return {
        "submission_item_id": str(item.id),
        "state": state,
        "replayed": False,
    }


async def grading_file(
    db: AsyncSession, teacher_id: uuid.UUID, submission_item_id: uuid.UUID
) -> tuple[bytes, str, str]:
    """读取教师班级范围内的学生原始照片，不生成公开 URL。"""
    item, _sub, _assignment = await _load_item_in_class(db, teacher_id, submission_item_id)
    # tel E2E 修复：拍照作答经 practice/submit 把文件放 attachments（JSONB），file_id 列恒空——
    # 回退取第一张作答图，否则批改工作台原图恒 404，教师看不到学生手写（愿景4 口径）。
    file_id = item.file_id
    if file_id is None:
        for att in item.attachments or []:
            if isinstance(att, dict) and att.get("file_id"):
                file_id = att["file_id"]
                break
    if file_id is None:
        raise_http(ERR_NOT_FOUND, 404, "file_not_found", recoverable=False)
    if isinstance(file_id, str):
        file_id = uuid.UUID(file_id)
    file_obj = await db.get(File, file_id)
    if file_obj is None or file_obj.deleted_at is not None:
        raise_http(ERR_NOT_FOUND, 404, "file_not_found", recoverable=False)
    try:
        from app.domains.files.router import _read_file_bytes

        content = _read_file_bytes(file_obj)
    except Exception:
        raise_http(ERR_NOT_FOUND, 404, "file_content_not_found", recoverable=False)
    return content, file_obj.mime or "application/octet-stream", file_obj.filename


async def _mastery_update_from_confirmed(
    db: AsyncSession, item: SubmissionItem, sub: Submission
) -> None:
    """学情只消费已确认终值：按该题知识点更新掌握度（best-effort，失败不阻断正式写）。"""
    try:
        if item.teacher_final_score is None:
            return
        quiz_item, context_reason = await _load_persisted_quiz_item(db, sub, item)
        if quiz_item is None or context_reason is not None:
            return
        kp_code = quiz_item.kp_code
        if not kp_code:
            return
        from app.models.coursework import MasteryRecord
        from app.models.knowledge_point import KnowledgePoint

        kp = (await db.execute(select(KnowledgePoint).where(KnowledgePoint.code == kp_code).limit(1))).scalar_one_or_none()
        if kp is None:
            return
        rec = (
            await db.execute(
                select(MasteryRecord).where(
                    MasteryRecord.user_id == sub.user_id,
                    MasteryRecord.kp_id == kp.id,
                )
            )
        ).scalar_one_or_none()
        if rec is None:
            rec = MasteryRecord(user_id=sub.user_id, kp_id=kp.id, mastery=0.5)
            db.add(rec)
        rec.practice_count = (rec.practice_count or 0) + 1
        # tel D8/A7 口径：以持久化题目满分（quiz_items.max_score）为参考，按比值计对
        # （终分 ≥ 60% 满分算答对一次）；满分缺失时维持旧口径（>=1.0）兼容存量，
        # 修复 10 分制卷型下硬编码 1.0 造成的掌握度失真。
        persisted_max = quiz_item.max_score
        if persisted_max is not None and float(persisted_max) > 0:
            correct_ref = 0.6 * float(persisted_max)
        else:
            correct_ref = 1.0
        if float(item.teacher_final_score or 0) >= correct_ref:
            rec.correct_count = (rec.correct_count or 0) + 1
    except Exception:  # noqa: BLE001 —— 学情更新不阻断正式计分/审计
        pass


async def confirm_grade(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID,
    submission_item_id: uuid.UUID,
    *,
    suggestion_id: uuid.UUID,
    decision: str,
    final_score: float | None,
    teacher_feedback: str | None,
    version: int,
    client_request_id: str,
    idempotency_key: str | None,
    request_id: str | None = None,
) -> dict:
    """唯一写正式分入口：校验教师/班级/建议/版本，幂等，确认成功才同步 score/学情。"""
    item, sub, assignment = await _load_item_in_class(
        db, teacher_id, submission_item_id, for_update=True
    )
    if class_id != assignment.class_id:
        raise_http(ERR_NOT_FOUND, 404, "not_found", recoverable=False)

    if decision not in ("accept", "override"):
        raise_http(ERR_VALIDATION, 422, "invalid_decision", recoverable=False)
    if decision == "override" and final_score is None:
        raise_http(ERR_VALIDATION, 422, "final_score_required", recoverable=True)
    requested_final = float(final_score) if final_score is not None else None

    binding = _confirmation_binding(
        teacher_id=teacher_id,
        class_id=class_id,
        submission_item_id=submission_item_id,
        suggestion_id=suggestion_id,
        decision=decision,
        final_score=requested_final,
        teacher_feedback=teacher_feedback,
        version=version,
    )
    if idempotency_key:
        await _lock_idempotency_key(db, idempotency_key)
        existing = await db.scalar(
            select(TeacherAction).where(TeacherAction.idempotency_key == idempotency_key)
        )
        if existing is not None:
            if not _action_matches_binding(existing, binding):
                raise_http(ERR_DUPLICATE, 409, "idempotency_conflict", recoverable=False)
            return {
                **_serialize_suggestion(
                    item, suggestion_id=str(suggestion_id), version=version
                ),
                "replayed": True,
            }

    if item.confirmed_at is not None:
        actions = (
            await db.execute(
                select(TeacherAction).where(
                    TeacherAction.teacher_id == teacher_id,
                    TeacherAction.action_type == "grade.confirm",
                )
            )
        ).scalars()
        if any(_action_matches_binding(action, binding) for action in actions):
            _add_confirmation_action(
                db,
                teacher_id=teacher_id,
                class_id=class_id,
                suggestion_id=suggestion_id,
                client_request_id=client_request_id,
                idempotency_key=idempotency_key,
                request_id=request_id,
                binding=binding,
                final_score=float(item.teacher_final_score or 0),
                replayed=True,
            )
            await db.flush()
            return {
                **_serialize_suggestion(
                    item, suggestion_id=str(suggestion_id), version=version
                ),
                "replayed": True,
            }
        raise_http(ERR_DUPLICATE, 409, "confirmation_conflict", recoverable=False)

    artifact = await get_owned_artifact(db, teacher_id, suggestion_id)
    artifact_payload = artifact.payload or {}
    if (
        artifact.artifact_type != "grading_suggestion"
        or artifact.class_id != assignment.class_id
        or artifact_payload.get("submission_item_id") != str(item.id)
    ):
        raise_http(ERR_VALIDATION, 422, "invalid_suggestion", recoverable=False)
    if artifact.version != version:
        raise_http(40901, 409, "version_conflict", recoverable=True)

    if decision == "override":
        quiz_item, context_reason = await _load_persisted_quiz_item(db, sub, item)
        if (
            quiz_item is not None
            and context_reason is None
            and requested_final is not None
            and (question_max := _effective_question_max_score(quiz_item)) is not None
            and requested_final > question_max
        ):
            raise_http(
                ERR_VALIDATION,
                422,
                "final_score_exceeds_question_maximum",
                recoverable=True,
            )
        final = requested_final
        suggestion_status = "overridden"
    else:
        quiz_item, context_reason = await _load_persisted_quiz_item(db, sub, item)
        if quiz_item is None or context_reason is not None:
            raise_http(
                ERR_CONFIRMATION_REQUIRED,
                422,
                "trusted_context_required",
                recoverable=True,
            )
        current_evidence = _quiz_evidence(quiz_item)
        recomputed, _confidence, needs_review, _detail = _objective_score(
            item, quiz_item, context_reason
        )
        artifact_score = artifact_payload.get("suggestion_score")
        evidence_matches = artifact_payload.get("quiz_evidence") == current_evidence
        fingerprint_matches = artifact_payload.get("suggestion_fingerprint") == _suggestion_fingerprint(
            item, current_evidence
        )
        if (
            item.q_type not in ("choice", "judge")
            or needs_review
            or item.needs_review
            or artifact_payload.get("review_needed") is not False
            or item.suggested_score is None
            or artifact_score is None
            or float(item.suggested_score) != recomputed
            or float(artifact_score) != recomputed
            or not evidence_matches
            or not fingerprint_matches
        ):
            raise_http(
                ERR_CONFIRMATION_REQUIRED,
                422,
                "suggestion_evidence_changed",
                recoverable=True,
            )
        final = recomputed
        suggestion_status = "accepted"

    # 确认成功 → 才写正式 score 与教师终值
    item.teacher_final_score = Decimal(str(final))
    item.teacher_feedback = teacher_feedback
    item.score = Decimal(str(final))  # score 保持旧兼容语义 = 正式终分
    item.suggestion_status = suggestion_status
    item.confirmed_by = teacher_id
    item.confirmed_at = _now()
    item.needs_review = False
    await db.flush()

    # 汇总 Submission.total_score 重算（best-effort）
    total = await db.scalar(
        select(func.sum(SubmissionItem.score)).where(SubmissionItem.submission_id == sub.id)
    )
    if total is not None:
        sub.total_score = total
    remaining_items = (
        await db.execute(
            select(SubmissionItem).where(
                SubmissionItem.submission_id == sub.id,
                SubmissionItem.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    # 自动判分的选择/判断题是「即时终局」，不应把整份提交永久卡在 pending_review。
    remaining = sum(
        1
        for it in remaining_items
        if it.confirmed_at is None and not _auto_graded(it)
    )
    sub.status = "graded" if remaining == 0 else "pending_review"

    # 学情只消费已确认终值（幂等：仅首次执行）
    await _mastery_update_from_confirmed(db, item, sub)

    _add_confirmation_action(
        db,
        teacher_id=teacher_id,
        class_id=class_id,
        suggestion_id=suggestion_id,
        client_request_id=client_request_id,
        idempotency_key=idempotency_key,
        request_id=request_id,
        binding=binding,
        final_score=final,
        replayed=False,
    )
    await db.flush()
    # 对齐前端 GradingSuggestion：确认后返回最新建议快照（decision=accepted/overridden）
    return _serialize_suggestion(
        item, suggestion_id=str(suggestion_id), version=artifact.version
    )


async def grading_queue(
    db: AsyncSession, teacher_id: uuid.UUID, class_id: uuid.UUID | None
) -> list[dict]:
    """批改队列（对齐前端 GradingQueueItem[]）。

    覆盖三类：unprocessed（未建议）、low_confidence（待复核）、confirmed（已确认）。
    """
    from app.domains.teacher.today import teacher_class_ids

    if class_id is not None:
        from app.domains.teacher.scope import assert_teacher_in_class

        await assert_teacher_in_class(db, teacher_id, class_id)
        class_ids = [class_id]
    else:
        class_ids = await teacher_class_ids(db, teacher_id)
    if not class_ids:
        return []
    rows = (
        await db.execute(
            select(SubmissionItem)
            .join(Submission, SubmissionItem.submission_id == Submission.id)
            .join(Assignment, Submission.assignment_id == Assignment.id)
            .where(
                Assignment.class_id.in_(class_ids),
                Assignment.deleted_at.is_(None),
                Submission.deleted_at.is_(None),
                SubmissionItem.deleted_at.is_(None),
            )
            .order_by(SubmissionItem.created_at.desc())
            .limit(100)
        )
    ).scalars().all()
    return [_serialize_queue_item(item) for item in rows]


async def grading_review_insights(
    db: AsyncSession, teacher_id: uuid.UUID, class_id: uuid.UUID
) -> dict:
    """批后讲评建议（调研版「批完→最值得讲的 3 题」）。

    确定性 SQL 聚合：取该班最近一份已批作业，按题统计判错人数与代表性作答；
    无已决数据 → 返回 empty（不生成虚构讲评）。
    输出元素：question_text / wrong_count / corrected_ratio / typical_submission_ids / kp / actions。
    """
    from app.domains.teacher.scope import assert_teacher_in_class

    await assert_teacher_in_class(db, teacher_id, class_id)

    latest = (
        await db.execute(
            select(Assignment)
            .where(
                Assignment.class_id == class_id,
                Assignment.status.in_(("published", "closed")),
                Assignment.deleted_at.is_(None),
            )
            .order_by(Assignment.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None or latest.quiz_id is None:
        return {"assignment_id": None, "title": "", "review_rate": 0, "top_questions": []}

    items = (
        await db.execute(
            select(QuizItem).where(
                QuizItem.quiz_id == latest.quiz_id,
                QuizItem.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    if not items:
        return {"assignment_id": str(latest.id), "title": latest.title, "review_rate": 0, "top_questions": []}

    sub_ids = select(Submission.id).where(
        Submission.assignment_id == latest.id,
        Submission.deleted_at.is_(None),
    )
    per_item: dict[int, dict] = {
        q.item_no: {
            "question_text": q.question_text,
            "kp_code": q.kp_code,
            "wrong_count": 0,
            "graded_count": 0,
            "typical": [],
        }
        for q in items
    }

    # 判错口径：verdict==wrong（教师已确认的错），典型作答只取 wrong 提交
    verdicts = (
        await db.execute(
            select(
                Submission.user_id,
                SubmissionItem.item_no,
                SubmissionItem.verdict,
                SubmissionItem.answer_text,
                SubmissionItem.file_id,
                SubmissionItem.attachments,
            )
            .join(Submission, Submission.id == SubmissionItem.submission_id)
            .where(
                SubmissionItem.submission_id.in_(sub_ids),
                SubmissionItem.deleted_at.is_(None),
            )
        )
    ).all()
    wrong_verdicts: dict[int, bool] = {}
    for user_id, item_no, verdict, answer_text, file_id, attachments in verdicts:
        slot = per_item.get(item_no)
        if slot is None:
            continue
        if verdict in ("correct", "wrong"):
            slot["graded_count"] += 1
        if verdict == "wrong":
            slot["wrong_count"] += 1
            wrong_verdicts[item_no] = True
            if len(slot["typical"]) < 2:
                slot["typical"].append(
                    {
                        "user_id": str(user_id),
                        "submission_item_id": None,
                        "file_id": str(file_id) if file_id else None,
                        "answer_text": (answer_text or "")[:200],
                        "attachment_count": len(attachments) if isinstance(attachments, list) else 0,
                    }
                )

    if not any(v.get("wrong_count") for v in per_item.values()):
        return {"assignment_id": str(latest.id), "title": latest.title, "review_rate": 100, "top_questions": []}

    ranked = [
        {
            "item_no": item_no,
            "question_text": slot["question_text"],
            "kp_code": slot["kp_code"],
            "wrong_count": slot["wrong_count"],
            "graded_count": slot["graded_count"],
            "correct_ratio": round((slot["graded_count"] - slot["wrong_count"]) / slot["graded_count"] * 100) if slot["graded_count"] else 0,
            "typical_answers": slot["typical"],
            "actions": [
                {"action": "open_lesson_review", "label": "加入明天讲评"},
                {"action": "generate_variant", "label": "生成一个变式"},
                {"action": "open_evidence", "label": "看典型作答"},
            ],
        }
        for item_no, slot in sorted(per_item.items(), key=lambda kv: kv[1]["wrong_count"], reverse=True)
        if slot["wrong_count"] > 0
    ][:3]

    decided = sum(1 for v in per_item.values() if v["graded_count"])
    return {
        "assignment_id": str(latest.id),
        "title": latest.title,
        "review_rate": round(decided / len(items) * 100) if items else 0,
        "top_questions": ranked,
    }
