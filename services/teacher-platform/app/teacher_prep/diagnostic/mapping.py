"""R18 契约映射：8100 诊断草稿 ⇄ R13 PreClassDiagnosticTask / TaskAssignment。

范围（DECISIONS.md §6：教师侧先闭环；学生端零页面改造）：
- `tp_diagnostic_tasks.body`（DiagnosticDraft，教师可编辑权威）是唯一内容源；
- 发布时映射为跨端契约 PreClassDiagnosticTask（学生消费视图：题干 prompt +
  SourceLocator 证据 + 满分；答案/解析/难度/知识点为教师参考，留在 8100 草稿，
  不下发给学生端，避免答案泄露）；
- TaskAssignment 为 8000 下发契约；`id` 由诊断任务 id 稳定派生（重试永不重建）；
- 稳定映射键（8000 client_assignment_id）由 R13 mapping.assignment_mapping_key
  依据 TaskAssignment 语义指纹生成——同任务重复确认 → 同一 8000 assignment。
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime
from typing import Any

from app.teacher_prep_v2.adapters.mapping import derive_contract_id
from app.teacher_prep_v2.contracts.base import DataRetention
from app.teacher_prep_v2.contracts.models import (
    AssignmentTarget,
    LatePolicy,
    PreClassDiagnosticTask,
    TaskAssignment,
    TaskRef,
)

from .models import DiagnosticTaskRow


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _preview_of(ref: str | None, body: dict[str, Any]) -> str | None:
    if not ref:
        return None
    for it in body.get("items") or []:
        if it.get("source_ref") == ref:
            q = (it.get("question") or "").strip()
            return q[:200] or None
    return None


def diagnostic_draft_to_contract(
    task_row: DiagnosticTaskRow,
    *,
    tenant_id: str,
    class_id: str,
    session_id: str,
    owner_id: str,
    open_at: datetime | None = None,
    due_at: datetime,
    idempotency_key: str,
) -> PreClassDiagnosticTask:
    """8100 诊断草稿 → R13 PreClassDiagnosticTask 跨端契约（学生消费视图）。

    - items：prompt=题干、problem_locator=证据 ref（存在时）、max_score=满分；
      答案/解析/难度/kp 不下发（教师参考，防学生端答案泄露）；
    - status：published（本契约仅用于已发布任务）。
    """
    body = task_row.body or {}
    items: list[dict[str, Any]] = []
    for it in body.get("items") or []:
        item: dict[str, Any] = {
            "item_no": it["item_no"],
            "prompt": it["question"],
        }
        ref = it.get("source_ref")
        if ref:
            item["problem_locator"] = {
                "schema_version": "1.0.0",
                "ref": ref,
                "preview": _preview_of(ref, body),
            }
        if it.get("max_score") is not None:
            item["max_score"] = it["max_score"]
        items.append(item)

    open_at = open_at or _utc_now()
    return PreClassDiagnosticTask(
        id=str(task_row.id),
        tenant_id=tenant_id,
        class_id=class_id,
        session_id=session_id,
        owner_id=owner_id,
        title=task_row.title or body.get("title") or "学前诊断",
        description=body.get("description"),
        window={"open_at": open_at, "due_at": due_at},
        attempts_allowed=1,
        resubmit_allowed=False,
        items=items,
        status="published",
        revision=task_row.revision,
        provenance={
            "producer_service": "teacher-platform",
            "producer_version": "r18",
            "actor_id": owner_id,
            "idempotency_key": idempotency_key,
            "created_at": _utc_now(),
            "source": "teacher",
        },
        data_retention=DataRetention(policy="TERM").model_dump(),
    )


def build_task_assignment(
    task_row: DiagnosticTaskRow,
    *,
    tenant_id: str,
    class_id: str,
    session_id: str,
    owner_id: str,
    due_at: datetime,
    idempotency_key: str,
) -> TaskAssignment:
    """诊断任务 → TaskAssignment（下发契约；稳定 id 派生，重试永不重建）。"""
    return TaskAssignment(
        id=derive_contract_id(str(task_row.id), salt="prep:diagnostic:assignment"),
        tenant_id=tenant_id,
        class_id=class_id,
        session_id=session_id,
        owner_id=owner_id,
        task_ref=TaskRef(task_id=str(task_row.id), task_kind="PRE_CLASS_DIAGNOSTIC").model_dump(),
        target=AssignmentTarget().model_dump(),
        due_at=due_at,
        late_policy=LatePolicy().model_dump(),
        attempts_allowed=1,
        provision=None,
        status="pending",
        idempotency_key=idempotency_key,
        revision=0,
        provenance={
            "producer_service": "teacher-platform",
            "producer_version": "r18",
            "actor_id": owner_id,
            "idempotency_key": idempotency_key,
            "created_at": _utc_now(),
            "source": "teacher",
        },
        data_retention=DataRetention(policy="TERM").model_dump(),
    )


def default_due_at(*, open_at: datetime | None = None, hours: int = 24) -> datetime:
    """默认截止：开放后 24 小时（教师可在发布载荷覆盖 due_at）。"""
    return (open_at or _utc_now()) + __import__("datetime").timedelta(hours=hours)


# tel F1：诊断题型 → 8000 QuizItem.q_type（coursework.py 口径 choice/judge/blank/solution）。
# - single_choice → choice（题干内可解析出 A./B. 选项行时携带 options，学生端单选作答；
#   解析不出选项行 → 回落 blank（学生填写选项字母，8000 check_equivalence/_match_choice
#   对 "A"/"A."/"选项A" 宽容判分）——绝不发空 options 的 choice（学生端单选渲染为空、无法作答）；
# - fill_blank → blank（check_equivalence 自动判 + 待人工兜底）；
# - short_answer / confidence_rating → solution（主观题，教师批改台终审——
#   confidence 是学生自评，无客观标答，禁止伪自动判分）。
_ITEM_TYPE_TO_QTYPE: dict[str, str] = {
    "single_choice": "choice",
    "fill_blank": "blank",
    "short_answer": "solution",
    "confidence_rating": "solution",
}
_CHOICE_LINE_RE = re.compile(r"^\s*([A-Fa-f])[\.、．:：]\s*(\S.*)$")


def _parse_choice_options(question: str) -> dict[str, str] | None:
    """从题干逐行解析 `A. xxx` 选项行 → {A:…}；不足两个选项按无可选集合处理。"""
    options: dict[str, str] = {}
    for line in (question or "").splitlines():
        m = _CHOICE_LINE_RE.match(line)
        if m:
            options[m.group(1).upper()] = m.group(2).strip()
    return options if len(options) >= 2 else None


def diagnostic_items_to_quiz_items(body: dict[str, Any]) -> list[dict[str, Any]]:
    """诊断草稿 items → 8000 内联 quiz_items（tel F1：CreateAssignmentRequest.quiz_items）。

    答案/解析随题下发到 8000 QuizItem 作判分锚点（与题库题同口径；8000 学生端
    assignment 详情不回传 answer/analysis——student_router get_assignment 已核），
    8100 侧教师参考字段（objective/kp 文本）不下发。
    """
    out: list[dict[str, Any]] = []
    for it in body.get("items") or []:
        q_type = _ITEM_TYPE_TO_QTYPE.get(str(it.get("item_type") or ""), "solution")
        question = str(it.get("question") or "")
        options: dict[str, str] | None = None
        if q_type == "choice":
            options = _parse_choice_options(question)
            if options is None:
                # 无可选集合的「单选」→ 填写选项字母（保自动判分；不发空 options choice）
                q_type = "blank"
        item: dict[str, Any] = {
            "item_no": int(it.get("item_no") or len(out) + 1),
            "q_type": q_type,
            "question_text": question,
            "options": options,
            "answer": str(it.get("answer") or ""),
            "analysis": it.get("explanation"),
            "kp_code": it.get("kp"),
            "difficulty": it.get("difficulty") or "medium",
        }
        if it.get("max_score") is not None:
            item["max_score"] = it["max_score"]
        out.append(item)
    return out


def diagnostic_pending_action_id() -> str:
    return str(uuid.uuid4())


def diagnostic_card_props(task_row: DiagnosticTaskRow) -> dict[str, Any]:
    """diagnostic_card 组件 props（教师全量审阅视图；编排器与 worker 共用）。

    - 全量题目（含答案/解析/难度/kp）只面向教师端（R13 契约下发时会剥离）；
    - published_assignment_id 来自 provision 快照（发布成功回填）。
    """
    body = task_row.body or {}
    items = [
        {
            "item_no": it.get("item_no"),
            "objective": it.get("objective"),
            "item_type": it.get("item_type"),
            "question": it.get("question") or "",
            "answer": it.get("answer") or "",
            "explanation": it.get("explanation") or "",
            "difficulty": it.get("difficulty") or "easy",
            "kp": it.get("kp") or "",
            "source_ref": it.get("source_ref"),
            "max_score": it.get("max_score"),
        }
        for it in body.get("items") or []
    ]
    return {
        "diagnostic_id": str(task_row.id),
        "blueprint_id": str(task_row.blueprint_id),
        "revision": task_row.revision,
        "status": task_row.status,
        "title": task_row.title or body.get("title") or "学前诊断",
        "description": task_row.description,
        "estimated_duration_minutes": task_row.estimated_duration_minutes,
        "knowledge_points": body.get("knowledge_points") or [],
        "items": items,
        "pending_publish": False,
        "published_assignment_id": (task_row.provision or {}).get("assignment_id"),
        "copy_keys": {
            "edit": "prep.diagnostic.edit",
            "publish": "prep.diagnostic.publish",
            "skip": "prep.diagnostic.skip",
        },
    }


__all__ = [
    "diagnostic_draft_to_contract",
    "build_task_assignment",
    "diagnostic_items_to_quiz_items",
    "default_due_at",
    "diagnostic_pending_action_id",
    "diagnostic_card_props",
]
