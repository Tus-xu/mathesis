"""M3 教师端：题集与作业（§12）。

- 生成结果先成为 quiz_set draft Artifact（复用 M2 题库供给 + 规范化去重 + 数量护栏）；
- 题库不足时保留严格命中的题库题，并明确提示教师调整范围或题量；
- 教师确认 quiz_set 后才能据此创建 Assignment draft（默认 draft，不直接发布）；
- Assignment publish 单独确认、幂等、记录 teacher_action；不破坏 M2 published 兼容。
"""

from __future__ import annotations

import math
import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.teacher.artifacts import (
    _serialize_artifact,
    create_artifact,
    get_owned_artifact,
)
from app.domains.teacher.scope import assert_teacher_in_class, raise_http
from app.models.coursework import Assignment, AssignmentTarget, Quiz, QuizItem
from app.models.question_bank import QuestionBank
from app.models.teacher import TeacherAction, TeachingArtifact
from app.skills.question_supply import expand_kp_subtree, supply_questions

ERR_NOT_FOUND = 40400
ERR_VALIDATION = 40001
ERR_CONFIRMATION_REQUIRED = 42210
ERR_DUPLICATE = 40902

TYPE_MAP = {"choice": "choice", "blank": "blank", "text": "solution"}
OUTPUT_TYPE_MAP = {"choice": "choice", "blank": "blank", "solution": "solution"}
QUESTION_TYPES = ("choice", "blank", "text")
DIFFICULTIES = ("easy", "medium", "hard")


def _normalize_type_quotas(question_types: dict[str, int], count: int) -> tuple[dict[str, int], dict[str, int], bool]:
    """Keep request intent auditable without silently losing a requested type.

    Under-specified requests are extended round-robin among the explicitly
    requested types. Over-specified requests are rejected because shrinking a
    quota can erase a type the teacher explicitly asked for.
    """
    requested = {qtype: max(0, int(question_types.get(qtype) or 0)) for qtype in QUESTION_TYPES}
    total = sum(requested.values())
    if total > count:
        raise_http(ERR_VALIDATION, status.HTTP_422_UNPROCESSABLE_CONTENT, "question_type_quota_exceeds_count", recoverable=True)
    effective = dict(requested)
    if total == count:
        return requested, effective, False
    active = [qtype for qtype in QUESTION_TYPES if requested[qtype] > 0]
    if not active:
        effective["text"] = count
        return requested, effective, True
    for index in range(count - total):
        effective[active[index % len(active)]] += 1
    return requested, effective, True


def _slot_counts(total: int, difficulty: dict[str, float] | None) -> list[tuple[str | None, int]]:
    values = {name: float((difficulty or {}).get(name) or 0) for name in DIFFICULTIES}
    weight_sum = sum(values.values())
    if weight_sum <= 0:
        return [(None, total)]
    raw = {name: total * values[name] / weight_sum for name in DIFFICULTIES}
    counts = {name: math.floor(raw[name]) for name in DIFFICULTIES}
    remaining = total - sum(counts.values())
    order = sorted(DIFFICULTIES, key=lambda name: (-(raw[name] - counts[name]), DIFFICULTIES.index(name)))
    for name in order[:remaining]:
        counts[name] += 1
    return [(name, counts[name]) for name in DIFFICULTIES if counts[name] > 0]


def _explicit_grading_spec(annotate_meta: object) -> dict[str, Any]:
    """Pass through only a reviewed, internally consistent source score spec.

    The teacher workflow must not invent a generic rubric for a question whose
    source has none. Source-import metadata may provide one, and this helper
    keeps it intact only when its points add up to the declared full mark.
    """
    if not isinstance(annotate_meta, dict):
        return {}
    max_score = annotate_meta.get("max_score")
    raw_rubric = annotate_meta.get("grading_rubric")
    if (
        not isinstance(max_score, (int, float))
        or isinstance(max_score, bool)
        or max_score <= 0
        or not isinstance(raw_rubric, list)
        or not raw_rubric
    ):
        return {}

    rubric: list[dict[str, Any]] = []
    for raw_item in raw_rubric:
        if not isinstance(raw_item, dict):
            return {}
        rubric_id = raw_item.get("id")
        criterion = raw_item.get("criterion")
        points = raw_item.get("points")
        evidence_hint = raw_item.get("evidence_hint")
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
            return {}
        rubric.append(
            {
                "id": rubric_id,
                "criterion": criterion,
                "points": float(points),
                "evidence_hint": evidence_hint,
            }
        )
    if abs(sum(item["points"] for item in rubric) - float(max_score)) > 1e-6:
        return {}
    return {"max_score": float(max_score), "grading_rubric": rubric}


# 题库真题常自带原卷序号（"1.（5 分）已知…"、"18. 命题…"），与 item_no 叠加成
# "3. 1.（5 分）"双序号；剥掉题干行首序号与括注分值（序号后跟数字如 3.14 不剥）。
_LEADING_NUMBERING = re.compile(r"^\s*\d{1,3}\s*[.、．]\s*(?:（\s*\d+\s*分?\s*）\s*)?(?!\d)")


def _item_from_row(row: QuestionBank, item_no: int, allowed_kps: set[str]) -> dict[str, Any]:
    matching_kps = sorted({str(code) for code in row.kp_codes if str(code) in allowed_kps})
    return {
        "item_no": item_no,
        "q_type": OUTPUT_TYPE_MAP.get(row.q_type, row.q_type),
        "question_text": _LEADING_NUMBERING.sub("", row.stem or "", count=1),
        "options": row.options if isinstance(row.options, dict) else None,
        "answer": row.answer,
        "analysis": row.analysis.strip() if isinstance(row.analysis, str) and row.analysis.strip() else "题库未提供解析，请教师确认后补充。",
        # A multi-KP row may carry an unrelated first code; audit the in-scope match instead.
        "kp_code": matching_kps[0] if matching_kps else None,
        "difficulty": row.difficulty,
        "hash": row.hash,
        "source": row.source,
        "source_ref": f"qb:{row.id}",
        **_explicit_grading_spec(row.annotate_meta),
    }


async def generate_quiz(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID,
    *,
    knowledge_points: list[str],
    count: int,
    question_types: dict[str, int],
    difficulty: dict[str, float] | None,
    exclude_hashes: list[str],
    client_request_id: str,
) -> dict:
    await assert_teacher_in_class(db, teacher_id, class_id)

    requested_types, effective_types, quota_normalized = _normalize_type_quotas(question_types, count)
    target = count
    allowed_kps = set(await expand_kp_subtree(db, knowledge_points))

    used_hashes: set[str] = set(exclude_hashes)
    items: list[dict[str, Any]] = []
    slot_fulfillment: list[dict[str, Any]] = []
    relaxed_slots: list[dict[str, Any]] = []
    missing_analysis = 0
    plans = [(qtype, requested_difficulty, slot_target) for qtype in QUESTION_TYPES for requested_difficulty, slot_target in _slot_counts(effective_types[qtype], difficulty) if slot_target > 0]
    # Phase 1: reserve every exact slot before any relaxed query can consume it.
    for qtype, requested_difficulty, slot_target in plans:
            rows = await supply_questions(
                db,
                kp_codes=knowledge_points,
                q_type=TYPE_MAP[qtype],
                difficulty=requested_difficulty,
                count=slot_target,
                exclude_hashes=used_hashes,
                scope="student",
                strict_kp_subtree=True,
                publishable_only=True, relax_difficulty=False,
                selection_seed=f"{client_request_id}:phase1:{qtype}:{requested_difficulty or 'any'}",
            )
            for row in rows:
                used_hashes.add(row.hash)
                if not isinstance(row.analysis, str) or not row.analysis.strip():
                    missing_analysis += 1
                items.append(_item_from_row(row, len(items) + 1, allowed_kps))
            slot_fulfillment.append({
                "question_type": qtype,
                "difficulty": requested_difficulty or "any",
                "requested": slot_target,
                "fulfilled": len(rows),
                "relaxed": 0,
            })
    # Phase 2: fill only the shortages in the same strict KP subtree and type.
    for slot in slot_fulfillment:
        shortage = slot["requested"] - slot["fulfilled"]
        if shortage <= 0:
            continue
        qtype = str(slot["question_type"])
        rows = await supply_questions(db, kp_codes=knowledge_points, q_type=TYPE_MAP[qtype], difficulty=None, count=shortage, exclude_hashes=used_hashes, scope="student", strict_kp_subtree=True, publishable_only=True, relax_difficulty=False, selection_seed=f"{client_request_id}:phase2:{qtype}:{slot['difficulty']}")
        for row in rows:
            used_hashes.add(row.hash)
            if not isinstance(row.analysis, str) or not row.analysis.strip():
                missing_analysis += 1
            items.append(_item_from_row(row, len(items) + 1, allowed_kps))
        slot["fulfilled"] += len(rows)
        requested_difficulty = slot["difficulty"]
        actual_relaxed = 0 if requested_difficulty == "any" else sum(
            row.difficulty != requested_difficulty for row in rows
        )
        slot["relaxed"] += actual_relaxed
        if actual_relaxed:
            relaxed_slots.append(slot)

    available_count = len(items)
    insufficient = available_count < target
    warnings: list[str] = []
    if insufficient:
        warnings.append(f"题库仅有 {available_count}/{target} 道严格命中题，请调整知识点范围、题型或题量后再发布。")
    if relaxed_slots:
        warnings.append("以下难度槽位在同一知识点与题型内放宽：" + "；".join(
            f"{slot['question_type']}/{slot['difficulty']} 放宽 {slot['relaxed']} 题" for slot in relaxed_slots
        ))
    if missing_analysis:
        warnings.append(f"{missing_analysis} 道题库题未提供解析，请教师确认后补充。")

    artifact = await create_artifact(
        db,
        owner_id=teacher_id,
        artifact_type="quiz_set",
        scene="teacher.assessment",
        class_id=class_id,
        payload={
            "knowledge_points": knowledge_points,
            "count": target,
            "difficulty": difficulty,
            "items": items,
            "duplicated": 0,
            "insufficient": insufficient,
            "question_type_distribution": {qtype: sum(item["q_type"] == qtype for item in items) for qtype in ("choice", "blank", "solution")},
        },
        source_refs=[i["source_ref"] for i in items if i.get("source_ref")],
        engine="local",
        degraded=insufficient,
        warnings=warnings,
        validation={
            "question_count": available_count,
            "dedup": True,
            "bank_count": available_count,
            "requested_count": target,
            "available_count": available_count,
            "requested_question_type_distribution": requested_types,
            "effective_question_type_distribution": effective_types,
            "quota_normalized": quota_normalized,
            "expanded_knowledge_points": sorted(allowed_kps),
            "requested_difficulty_distribution": difficulty or {},
            "slot_fulfillment": slot_fulfillment,
        },
    )
    db.add(artifact)
    await db.flush()
    # 对齐前端 TeacherArtifact：返回完整 Artifact（content 含题集）
    return _serialize_artifact(artifact)


async def _materialize_quiz(
    db: AsyncSession, teacher_id: uuid.UUID, artifact: TeachingArtifact
) -> uuid.UUID:
    """从已确认 quiz_set artifact 物化 Quiz + QuizItems，返回 quiz_id。"""
    payload = artifact.payload or {}
    return await _materialize_quiz_items(
        db,
        teacher_id,
        title=f"quiz_set-{artifact.id}",
        kp_codes=list(payload.get("knowledge_points") or []),
        items=payload.get("items") or [],
    )


async def _materialize_quiz_items(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    *,
    title: str,
    kp_codes: list[str],
    items: list[dict],
) -> uuid.UUID:
    """从题组 payload 物化 Quiz + QuizItems（artifact 路径与内联 quiz_items 共用）。"""
    quiz = Quiz(
        user_id=teacher_id,
        source="assignment",
        title=title,
        kp_codes=kp_codes,
        status="active",
    )
    db.add(quiz)
    await db.flush()
    for it in items:
        db.add(
            QuizItem(
                quiz_id=quiz.id,
                item_no=it.get("item_no", 0),
                q_type=it.get("q_type", "choice"),
                question_text=it.get("question_text", ""),
                options=it.get("options"),
                answer=it.get("answer", ""),
                answer_analysis=it.get("analysis"),
                kp_code=it.get("kp_code"),
                difficulty=it.get("difficulty", "medium"),
                ai_generated=False,
                source=it.get("source"),
                max_score=it.get("max_score"),
                grading_rubric=it.get("grading_rubric"),
            )
        )
    await db.flush()
    return quiz.id


def _serialize_assignment(a: Assignment) -> dict:
    """对齐前端 Assignment 契约。"""
    return {
        "assignment_id": str(a.id),
        "client_assignment_id": a.client_assignment_id,
        "class_id": str(a.class_id),
        "title": a.title,
        "type": a.type,
        "status": a.status,
        "deadline": a.deadline.isoformat() if a.deadline else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


async def create_assignment(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID,
    *,
    title: str,
    artifact_id: uuid.UUID | None,
    client_assignment_id: str,
    deadline: str | None,
    assignment_type: str = "quiz",
    quiz_items: list[dict] | None = None,
) -> dict:
    await assert_teacher_in_class(db, teacher_id, class_id)

    # 幂等：同 client_assignment_id 已创建 → 返回既有 draft
    existing = (
        await db.execute(
            select(Assignment).where(
                Assignment.creator_id == teacher_id,
                Assignment.client_assignment_id == client_assignment_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        data = _serialize_assignment(existing)
        data["replayed"] = True
        return data

    if artifact_id is not None:
        artifact = await get_owned_artifact(db, teacher_id, artifact_id)
        if artifact.artifact_type != "quiz_set":
            raise_http(ERR_VALIDATION, 422, "artifact_not_quiz_set", recoverable=False)
        if artifact.status != "confirmed":
            raise_http(ERR_CONFIRMATION_REQUIRED, 422, "confirmation_required", recoverable=True)

        quiz_id = await _materialize_quiz(db, teacher_id, artifact)
    else:
        # tel F1：备课诊断无 8000 artifact 物化链——内联题组直发（与 artifact 路径
        # 同一 Quiz/QuizItem 落库事实；schema 层已校验与 artifact_id 二选一）
        if not quiz_items:
            raise_http(ERR_VALIDATION, 422, "artifact_or_quiz_items_required", recoverable=False)
        quiz_id = await _materialize_quiz_items(
            db,
            teacher_id,
            title=title,
            kp_codes=sorted({str(it["kp_code"]) for it in quiz_items if it.get("kp_code")}),
            items=quiz_items,
        )
    a = Assignment(
        class_id=class_id,
        creator_id=teacher_id,
        title=title,
        # tel D2/A3：type 透传（quiz/pre_class/homework；schema Literal 校验），
        # 替换原硬编码 "quiz"；存量语义不变。
        type=assignment_type,
        quiz_id=quiz_id,
        deadline=_parse_dt(deadline),
        status="draft",  # 新建教师作业默认 draft，不直接发布
        client_assignment_id=client_assignment_id,
        source_artifact_id=artifact_id,
    )
    db.add(a)
    await db.flush()
    # 班级定向：学生端 /api/student/assignments 依赖 assignment_targets
    # （target_type=class）；发布前为 draft，学生仍不可见（查询过滤 status=published）
    db.add(AssignmentTarget(assignment_id=a.id, target_type="class", target_id=class_id))
    await db.flush()
    data = _serialize_assignment(a)
    data["replayed"] = False
    return data


def _parse_dt(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


async def _get_assignment_in_class(
    db: AsyncSession, teacher_id: uuid.UUID, class_id: uuid.UUID, assignment_id: uuid.UUID
) -> Assignment:
    await assert_teacher_in_class(db, teacher_id, class_id)
    a = await db.get(Assignment, assignment_id)
    if a is None or a.deleted_at is not None or a.class_id != class_id:
        raise_http(ERR_NOT_FOUND, status.HTTP_404_NOT_FOUND, "not_found", recoverable=False)
    return a


async def _assignment_transition(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID,
    assignment_id: uuid.UUID,
    *,
    target: str,
    required: str,
    client_request_id: str,
    idempotency_key: str | None,
    request_id: str | None,
) -> dict:
    a = await _get_assignment_in_class(db, teacher_id, class_id, assignment_id)
    if idempotency_key:
        existing = (
            await db.execute(
                select(TeacherAction).where(TeacherAction.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            data = _serialize_assignment(a)
            data["replayed"] = True
            return data
    if a.status != required:
        # 已是目标状态 → 幂等成功
        if a.status == target:
            data = _serialize_assignment(a)
            data["replayed"] = True
            return data
        raise_http(ERR_VALIDATION, 422, "invalid_state", current=a.status, recoverable=True)
    a.status = target
    db.add(
        TeacherAction(
            teacher_id=teacher_id,
            class_id=class_id,
            artifact_id=a.source_artifact_id,
            action_type=f"assignment.{target}",
            client_request_id=client_request_id,
            idempotency_key=idempotency_key,
            before_digest=None,
            after_digest=None,
            request_id=request_id,
            details={"assignment_id": str(a.id)},
        )
    )
    await db.flush()
    data = _serialize_assignment(a)
    data["replayed"] = False
    return data


async def publish_assignment(
    db, teacher_id, class_id, assignment_id, *, client_request_id, idempotency_key, request_id
) -> dict:
    return await _assignment_transition(
        db, teacher_id, class_id, assignment_id,
        target="published", required="draft",
        client_request_id=client_request_id, idempotency_key=idempotency_key, request_id=request_id,
    )


async def close_assignment(
    db, teacher_id, class_id, assignment_id, *, client_request_id, idempotency_key, request_id
) -> dict:
    return await _assignment_transition(
        db, teacher_id, class_id, assignment_id,
        target="closed", required="published",
        client_request_id=client_request_id, idempotency_key=idempotency_key, request_id=request_id,
    )


async def archive_assignment(
    db, teacher_id, class_id, assignment_id, *, client_request_id, idempotency_key, request_id
) -> dict:
    a = await _get_assignment_in_class(db, teacher_id, class_id, assignment_id)
    if idempotency_key:
        existing = (
            await db.execute(
                select(TeacherAction).where(TeacherAction.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            data = _serialize_assignment(a)
            data["replayed"] = True
            return data
    a.status = "archived"
    await db.flush()
    data = _serialize_assignment(a)
    data["replayed"] = False
    return data


async def list_assignments(
    db, teacher_id: uuid.UUID, class_id: uuid.UUID | None, assignment_status: str | None, *,
    client_assignment_id: str | None = None,
) -> list[dict]:
    from app.domains.teacher.today import teacher_class_ids

    if class_id is not None:
        await assert_teacher_in_class(db, teacher_id, class_id)
        class_ids = [class_id]
    else:
        class_ids = await teacher_class_ids(db, teacher_id)
    if not class_ids:
        return []
    stmt = (
        select(Assignment)
        .where(Assignment.class_id.in_(class_ids), Assignment.deleted_at.is_(None))
        .order_by(Assignment.created_at.desc())
    )
    if assignment_status:
        stmt = stmt.where(Assignment.status == assignment_status)
    if client_assignment_id:
        # tel F1：幂等对账读回过滤（8100 Api8000Adapter._fetch_by_client_key 消费）
        stmt = stmt.where(Assignment.client_assignment_id == client_assignment_id)
    rows = (await db.execute(stmt)).scalars().all()
    return [_serialize_assignment(a) for a in rows]
