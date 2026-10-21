"""M3 教师端：Capability Gateway（§10.4 / §16）。

固定顺序：teacher/class scope → Context 脱敏 → Butler Policy → 本地/星辰执行 →
Schema 校验 → Artifact/Ledger。前端不得指定 workflow/Provider/模型/密钥。

7 个 Capability 均有本地确定性构建器；星辰不可用/失败时返回规范化降级结果，
本地 Provider 也失败才抛 50311/capability_unavailable。原始上游响应不直传。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

import app.domains.teacher.workflow_adapter as adapter
from app.domains.teacher.scope import raise_http
from app.models.question_bank import QuestionBank
from app.skills.question_supply import supply_questions

VALID_CAPABILITIES = frozenset(
    {
        "adapt_lesson",
        "create_slides",
        "create_quiz",
        "suggest_grade",
        "explain_problem",
        "preprocess_course",
        "understand_document",
    }
)


_LESSON_PHASES = (
    ("导入", 1, "教师呈现与{topic}相关的问题情境，学生写下已有理解和待解决的问题。"),
    ("概念建构", 2, "教师引导学生用图像、变化过程或符号表述梳理{topic}的核心含义。"),
    ("例题与辨析", 3, "学生先独立判断一个与{topic}相关的例子，再同伴互相说明判断依据。"),
    ("合作练习", 3, "小组完成一题{topic}练习，推选代表板演并由同伴补充或质疑。"),
    ("当堂检测", 2, "学生独立完成两道针对{topic}的即时检测题，教师据此收集需要回看的点。"),
    ("总结与作业", 1, "学生用一句话总结{topic}的关键认识，并记录课后订正或拓展任务。"),
)


def _allocate_lesson_minutes(duration: int) -> list[int]:
    """按稳定权重分配课时，任何合法请求的总时长均精确保持。"""
    total = max(1, duration)
    phase_count = min(total, len(_LESSON_PHASES))
    if total <= len(_LESSON_PHASES):
        return [1] * phase_count

    weights = [phase[1] for phase in _LESSON_PHASES]
    remaining = total - len(_LESSON_PHASES)
    weight_sum = sum(weights)
    extras = [remaining * weight // weight_sum for weight in weights]
    remainder = remaining - sum(extras)
    # 同余数时按教学主体活动优先的稳定顺序分配，保证输出可重现。
    priority = (2, 3, 1, 4, 0, 5)
    for index in priority[:remainder]:
        extras[index] += 1
    return [1 + extra for extra in extras]


def _local_lesson(topic: str, requirements: str | None, duration: int | None) -> dict:
    requested_duration = duration or 45
    minutes = _allocate_lesson_minutes(requested_duration)
    timeline = [
        {
            "phase": phase,
            "minutes": phase_minutes,
            "activities": [activity.format(topic=topic)],
        }
        for (phase, _weight, activity), phase_minutes in zip(
            _LESSON_PHASES[:len(minutes)], minutes, strict=True
        )
    ]
    if requirements and requirements.strip():
        # 这是教师明确输入的教学要求，不将其包装为任何班情事实或模型判断。
        timeline[-2]["activities"].append(
            f"教师要求：{requirements.strip()}；据此调整提问、例题与学生表达安排。"
        )
    return {
        "topic": topic,
        "duration_minutes": requested_duration,
        "objectives": [
            f"说出{topic}的核心概念，并用自己的语言解释其数学含义。",
            f"在图像、变化过程或简单例子中识别{topic}的适用情境。",
        ],
        "timeline": timeline,
        "requirements": requirements,
        "template": "local_deterministic_template_v1",
    }


def _lesson_request_values(payload: dict[str, Any]) -> tuple[str, int, str | None]:
    """从请求中取得教案的权威字段；外部 provider 不得覆盖它们。"""
    topic = str(payload.get("topic") or "").strip()
    requested_duration = payload.get("duration_minutes")
    duration = requested_duration if isinstance(requested_duration, int) else 45
    requirements = payload.get("requirements")
    return topic, duration, requirements if isinstance(requirements, str) else None


def _normalize_lesson_payload(candidate: Any, request_payload: dict[str, Any]) -> tuple[dict, bool]:
    """对所有教案输出建立可落库的最小契约，非法内容整体退回本地模板。"""
    topic, duration, requirements = _lesson_request_values(request_payload)
    fallback = _local_lesson(topic, requirements, duration)
    if not isinstance(candidate, dict):
        return fallback, False

    objectives = candidate.get("objectives")
    timeline = candidate.get("timeline")
    if not isinstance(objectives, list) or not objectives or not isinstance(timeline, list) or not timeline:
        return fallback, False
    cleaned_objectives = [objective.strip() for objective in objectives if isinstance(objective, str)]
    if (
        len(cleaned_objectives) != len(objectives)
        or not all(cleaned_objectives)
        or not any(topic in objective for objective in cleaned_objectives)
    ):
        return fallback, False

    cleaned_timeline: list[dict] = []
    for step in timeline:
        if not isinstance(step, dict):
            return fallback, False
        phase = step.get("phase")
        minutes = step.get("minutes")
        activities = step.get("activities")
        if (
            not isinstance(phase, str)
            or not phase.strip()
            or isinstance(minutes, bool)
            or not isinstance(minutes, int)
            or minutes <= 0
            or not isinstance(activities, list)
            or not activities
        ):
            return fallback, False
        cleaned_activities = [activity.strip() for activity in activities if isinstance(activity, str)]
        if len(cleaned_activities) != len(activities) or not all(cleaned_activities):
            return fallback, False
        cleaned_timeline.append(
            {"phase": phase.strip(), "minutes": minutes, "activities": cleaned_activities}
        )
    if sum(step["minutes"] for step in cleaned_timeline) != duration:
        return fallback, False
    if requirements and requirements.strip() and not any(
        requirements.strip() in activity
        for step in cleaned_timeline
        for activity in step["activities"]
    ):
        cleaned_timeline[-1]["activities"].append(
            f"教师要求：{requirements.strip()}；据此调整提问、例题与学生表达安排。"
        )

    return {
        "topic": topic,
        "duration_minutes": duration,
        "objectives": cleaned_objectives,
        "timeline": cleaned_timeline,
        "requirements": requirements,
        "template": str(candidate.get("template") or "xingchen_normalized_template_v1"),
    }, True


def _local_slides(lesson: dict | None) -> dict:
    lessons = [{"phase": p.get("phase"), "minutes": p.get("minutes")}
               for p in (lesson or {}).get("timeline", [])] or [{"phase": "课堂"}]
    return {"slides": [{"page": i + 1, "title": p["phase"], "bullets": [], "notes": ""}
                       for i, p in enumerate(lessons)]}


async def _local_quiz(
    db: AsyncSession, gc: dict, excluded: set[str], target: int
) -> list[dict]:
    items: list[dict] = []
    for qtype in ("choice", "blank", "solution"):
        rows = await supply_questions(
            db,
            kp_codes=gc.get("knowledge_points") or [],
            q_type=qtype,
            difficulty=None,
            count=target,
            exclude_hashes=excluded,
            scope="student",
        )
        for row in rows:
            if row.hash in excluded:
                continue
            excluded.add(row.hash)
            items.append(_item_from_bank(row, len(items) + 1))
    return items


def _item_from_bank(row: QuestionBank, item_no: int) -> dict:
    tmap = {"choice": "choice", "blank": "blank", "solution": "text"}
    return {
        "item_no": item_no,
        "q_type": tmap.get(row.q_type, row.q_type),
        "question_text": row.stem,
        "options": row.options if isinstance(row.options, dict) else None,
        "answer": row.answer,
        "analysis": row.analysis,
        "kp_code": row.kp_codes[0] if row.kp_codes else None,
        "difficulty": row.difficulty,
        "hash": row.hash,
        "source": row.source,
        "source_ref": f"qb:{row.id}",
    }


def _local_explainer(question: str) -> dict:
    return {
        "question": question,
        "steps": [
            {"step": 1, "instruction": "重述问题", "detail": "", "is_certain": True},
            {"step": 2, "instruction": "分步推导", "detail": "待教材/教师材料补充", "is_certain": False},
            {"step": 3, "instruction": "易错提醒", "detail": "待教师依据本班错因补充", "is_certain": False},
        ],
    }


def _local_suggestion() -> dict:
    return {"suggested_score": 0.0, "confidence": 0.3, "needs_review": True}


def _local_preprocess(resource_id: str, text: str | None) -> dict:
    clean = " ".join((text or "").split())
    slices = [
        {"slice_id": f"{resource_id}:{i // 500 + 1}", "text": clean[i : i + 500]}
        for i in range(0, len(clean), 500)
    ]
    return {
        "structure": {"title": resource_id or "教学材料", "section_count": len(slices)},
        "slices": slices,
        "kps": [],
        "warnings": ["使用本地文本切片，知识点需教师确认"],
    }


def _local_document(question: str | None, text: str | None) -> dict:
    clean = " ".join((text or "").split())
    return {
        "summary": clean[:240] if clean else "未提取到可理解的文本，请检查资源解析结果。",
        "concepts": [],
        "qna": [
            {
                "question": question or "",
                "answer": clean[:500] if clean else "当前无可引用文本，无法给出有依据的回答。",
            }
        ] if question else [],
        "source_locs": [],
        "uncertain": ["无来源结论需人工核验"],
    }


_LOCAL_BUILDERS: dict[str, Callable] = {
    "adapt_lesson": lambda gc, excluded, db: _local_lesson(
        (gc.get("payload") or {}).get("topic", ""),
        (gc.get("payload") or {}).get("requirements"),
        (gc.get("payload") or {}).get("duration_minutes"),
    ),
    "create_slides": lambda gc, excluded, db: _local_slides(gc.get("payload") or {}),
    "explain_problem": lambda gc, excluded, db: _local_explainer(
        (gc.get("payload") or {}).get("question", "")
    ),
    "suggest_grade": lambda gc, excluded, db: _local_suggestion(),
    "preprocess_course": lambda gc, excluded, db: _local_preprocess(
        (gc.get("payload") or {}).get("resource_id", ""),
        (gc.get("payload") or {}).get("text"),
    ),
    "understand_document": lambda gc, excluded, db: _local_document(
        (gc.get("payload") or {}).get("question"),
        (gc.get("payload") or {}).get("text"),
    ),
}


async def run_capability(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    *,
    scene: str,
    class_id: uuid.UUID | None,
    capability: str,
    payload: dict[str, Any],
    source_refs: list[str] | None = None,
    client_request_id: str | None = None,
) -> dict:
    """执行单个 Capability，返回 {payload, engine, degraded, warnings, validation}。

    顺序：本地构建器优先（确定性、无外部依赖）；仅当配置了星辰且能力适用时，才尝试
    星辰并可在失败时降级。原始上游响应不直传。本地失败才抛 capability_unavailable。
    """
    if capability not in VALID_CAPABILITIES:
        raise_http(40001, status.HTTP_400_BAD_REQUEST, "unknown_capability", recoverable=True)

    # 归一化输入上下文（脱敏后）
    gc = {
        "scene": scene,
        "class_id": str(class_id) if class_id else None,
        "payload": payload,
        "source_refs": source_refs or [],
        "client_request_id": client_request_id,
    }

    # 星辰优先（仅当该能力有映射且配置就绪）；失败 → 本地降级（审计 I-03：异步调用）
    xingchen_result = None
    workflow = adapter.WORKFLOWS.get(capability)
    if workflow is not None:
        gc["teacher_id"] = str(teacher_id)
        xingchen_result = await adapter.run(capability, gc, db=db)

    if xingchen_result is not None and xingchen_result.get("status") == "succeeded":
        if capability == "adapt_lesson":
            normalized_payload, valid = _normalize_lesson_payload(
                xingchen_result.get("content"), payload
            )
            if not valid:
                return {
                    "payload": normalized_payload,
                    "engine": "local",
                    "degraded": True,
                    "warnings": ["xingchen_lesson_payload_invalid"],
                    "validation": {
                        "kind": "local_deterministic",
                        "deterministic": True,
                        "lesson_payload": "local_fallback",
                        "remote_status": "succeeded",
                    },
                }
            return {
                "payload": normalized_payload,
                "engine": "xingchen",
                "degraded": False,
                "warnings": [],
                "validation": {
                    "source": "xingchen",
                    "status": "succeeded",
                    "lesson_payload": "normalized",
                },
            }
        return {
            "payload": xingchen_result["content"],
            "engine": "xingchen",
            "degraded": False,
            "warnings": [],
            "validation": {"source": "xingchen", "status": "succeeded"},
        }

    # 本地构建器（schema 校验后落 artifact）
    try:
        if capability == "create_quiz":
            excluded = set(payload.get("exclude_hashes") or [])
            target = int(payload.get("count", 8))
            items = await _local_quiz(db, gc, excluded, target)
            local_payload = {
                "items": items,
                "knowledge_points": payload.get("knowledge_points"),
                "requested_count": target,
                "available_count": len(items),
                "insufficient": len(items) < target,
            }
        else:
            local_payload = _LOCAL_BUILDERS[capability](gc, set(), db)
    except Exception:  # noqa: BLE001
        raise_http(50311, status.HTTP_503_SERVICE_UNAVAILABLE, "capability_unavailable",
                   capability=capability, recoverable=True)

    degraded = xingchen_result is not None and xingchen_result.get("status") == "degraded"
    warnings = []
    if xingchen_result is not None:
        warnings = list(xingchen_result.get("warnings") or [])
    engine = "local"
    if degraded:
        warnings = warnings or ["workflow degraded to local"]
    # Local lesson output is intentionally a basic editable scaffold, never an
    # equivalent to a source-grounded model result.  Keep this stable marker so
    # the teacher UI can block formal PPT/release actions instead of styling a
    # generic template as a finished lesson.
    if capability == "adapt_lesson":
        degraded = True
        if "lesson_basic_template" not in warnings:
            warnings.append("lesson_basic_template")
    if capability == "create_quiz" and local_payload["insufficient"]:
        warnings.append(
            f"题库仅有 {local_payload['available_count']}/{local_payload['requested_count']} 道严格命中题；"
            "请上传题源或调整知识点、题型、题量后再创建草稿。"
        )
    if capability == "adapt_lesson":
        local_payload, _ = _normalize_lesson_payload(local_payload, payload)
    return {
        "payload": local_payload,
        "engine": engine,
        "degraded": degraded,
        "warnings": warnings,
        "validation": {
            "kind": "local_deterministic",
            "deterministic": True,
            **({"lesson_payload": "local_template"} if capability == "adapt_lesson" else {}),
        },
    }
