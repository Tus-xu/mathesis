"""M3 教师端：teacher-only ToolRegistry（§15）。

复用 ToolDefinition/ToolRegistry/PolicyGate/ButlerExecutor/ButlerRuntime；
每个工具 allowed_roles={teacher}、显式 scenes、Pydantic extra="forbid"、
handler 内再次校验 class scope、正确 ToolRisk；
EXTERNAL 有业务降级；WRITE 要求 confirmation + idempotency；
Ledger 只保存 digest/脱敏摘要。不清空 M2 student registry，工具名与角色隔离。
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.butler.contracts import ActorRole, ToolRisk
from app.butler.executor import ToolExecutionContext
from app.butler.registry import ToolDefinition, ToolRegistry
from app.domains.teacher import (
    assessment,
    classroom,
    grading,
    insights,
    lessons,
    resources,
    today,
)
from app.domains.teacher.artifacts import confirm_artifact as _confirm_artifact
from app.domains.teacher.scope import ERR_NOT_FOUND, raise_http


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ==================== 输入/输出模型 ====================


class TOnly(_Strict):
    class_id: uuid.UUID


class ReadAny(_Strict):
    """teacher 全局读取：class_id 可选（today 或未指定班级的资源搜索）。"""

    class_id: uuid.UUID | None = None


class TopicInput(TOnly):
    class_id: uuid.UUID
    topic: str = Field(min_length=1)
    requirements: str | None = None


class LessonRef(TOnly):
    lesson_id: uuid.UUID
    source_refs: list[str] = Field(default_factory=list)


class QuizIn(TOnly):
    knowledge_points: list[str] = Field(default_factory=list)
    count: int = Field(default=8, ge=1, le=100)
    exclude_hashes: list[str] = Field(default_factory=list)


class GradeSuggestIn(TOnly):
    submission_item_id: uuid.UUID


class GradeConfirmIn(TOnly):
    submission_item_id: uuid.UUID
    suggestion_id: uuid.UUID
    decision: str = Field(pattern="^(accept|override)$")
    final_score: float | None = None
    teacher_feedback: str | None = None
    version: int = Field(default=1, ge=1)


class ResourceIn(TOnly):
    resource_id: str = Field(min_length=1)


class ClassroomIn(TOnly):
    enabled: bool
    lesson_id: uuid.UUID | None = None


class ArtifactConfirmIn(TOnly):
    artifact_id: uuid.UUID
    version: int = Field(ge=1)


class AssignmentPublishIn(TOnly):
    assignment_id: uuid.UUID


class InsightApplyIn(_Strict):
    lesson_id: uuid.UUID
    insight_id: uuid.UUID
    version: int = Field(ge=1)


class Anything(_Strict):
    data: dict[str, Any] = Field(default_factory=dict)


class ReadOut(_Strict):
    data: dict[str, Any] = Field(default_factory=dict)


class WriteOut(_Strict):
    data: dict[str, Any] = Field(default_factory=dict)


def _uid(context: ToolExecutionContext) -> uuid.UUID:
    return context.request.actor.user_id


async def _h_today_read(context: ToolExecutionContext, inp: dict) -> dict:
    if context.db is None:
        raise_http(ERR_NOT_FOUND, 500, "db_unavailable")
    return {"data": await today.build_today(context.db, _uid(context))}


async def _h_class_insights_read(context: ToolExecutionContext, inp: dict) -> dict:
    return {"data": {"insights": await insights.compute_class_insights(
        context.db, _uid(context), inp["class_id"])}}


async def _h_lesson_sources_read(context: ToolExecutionContext, inp: dict) -> dict:
    return {"data": {"lessons": await lessons.list_lessons(context.db, _uid(context), inp["class_id"])}}


async def _h_assignment_queue_read(context: ToolExecutionContext, inp: dict) -> dict:
    return {"data": {"assignments": await assessment.list_assignments(
        context.db, _uid(context), inp["class_id"], None)}}


async def _h_grading_queue_read(context: ToolExecutionContext, inp: dict) -> dict:
    return {"data": {"queue": await grading.grading_queue(context.db, _uid(context), inp["class_id"])}}


async def _h_classroom_state_read(context: ToolExecutionContext, inp: dict) -> dict:
    return {"data": await classroom.classroom_state(context.db, _uid(context), inp["class_id"])}


async def _h_resources_search(context: ToolExecutionContext, inp: dict) -> dict:
    return {"data": {"resources": await resources.list_resources(context.db, _uid(context), inp["class_id"])}}


async def _h_lesson_adapt(context: ToolExecutionContext, inp: dict) -> dict:
    r = await lessons.adapt_lesson(
        context.db, _uid(context), inp["class_id"],
        topic=inp["topic"], source_artifact_id=None, source_refs=inp.get("source_refs", []),
        requirements=inp.get("requirements"), duration_minutes=inp.get("duration_minutes"),
        client_request_id=context.request.client_request_id,
    )
    return {"data": r}


async def _h_slides_create(context: ToolExecutionContext, inp: dict) -> dict:
    r = await lessons.create_slides(context.db, _uid(context), inp["lesson_id"],
                                    version=1, style=None, requirements=None)
    return {"data": r}


async def _h_quiz_create(context: ToolExecutionContext, inp: dict) -> dict:
    r = await assessment.generate_quiz(
        context.db, _uid(context), inp["class_id"],
        knowledge_points=inp.get("knowledge_points", []), count=inp["count"],
        question_types={"choice": inp["count"]}, difficulty=None,
        exclude_hashes=inp.get("exclude_hashes", []), client_request_id=context.request.client_request_id,
    )
    return {"data": r}


async def _h_grade_suggest(context: ToolExecutionContext, inp: dict) -> dict:
    r = await grading.suggest_grade(
        context.db, _uid(context), inp["class_id"], inp["submission_item_id"],
        client_request_id=context.request.client_request_id,
    )
    return {"data": r}


async def _h_problem_explain(context: ToolExecutionContext, inp: dict) -> dict:
    r = await lessons.create_explainer(
        context.db, _uid(context), inp["class_id"],
        question=inp["topic"], reference_solution=inp.get("requirements"), target_minutes=None,
    )
    return {"data": r}


async def _h_course_preprocess(context: ToolExecutionContext, inp: dict) -> dict:
    r = await resources.resource_preprocess(
        context.db, _uid(context), inp["class_id"], inp["resource_id"],
        client_request_id=context.request.client_request_id,
    )
    return {"data": r}


async def _h_document_understand(context: ToolExecutionContext, inp: dict) -> dict:
    r = await resources.resource_understand(
        context.db, _uid(context), inp["class_id"], inp["resource_id"],
        question=None, output_type=None, client_request_id=context.request.client_request_id,
    )
    return {"data": r}


async def _h_artifact_confirm(context: ToolExecutionContext, inp: dict) -> dict:
    r = await _confirm_artifact(
        context.db, _uid(context), inp["artifact_id"],
        client_request_id=context.request.client_request_id,
        idempotency_key=context.idempotency_key,
    )
    return {"data": r}


async def _h_assignment_publish(context: ToolExecutionContext, inp: dict) -> dict:
    r = await assessment.publish_assignment(
        context.db, _uid(context), inp["class_id"], inp["assignment_id"],
        client_request_id=context.request.client_request_id,
        idempotency_key=context.idempotency_key, request_id=None,
    )
    return {"data": r}


async def _h_grade_confirm(context: ToolExecutionContext, inp: dict) -> dict:
    r = await grading.confirm_grade(
        context.db, _uid(context), inp["class_id"], inp["submission_item_id"],
        suggestion_id=inp["suggestion_id"], decision=inp["decision"],
        final_score=inp.get("final_score"), teacher_feedback=inp.get("teacher_feedback"),
        version=inp.get("version", 1), client_request_id=context.request.client_request_id,
        idempotency_key=context.idempotency_key,
    )
    return {"data": r}


async def _h_classroom_mode_set(context: ToolExecutionContext, inp: dict) -> dict:
    r = await classroom.set_classroom_mode(
        context.db, _uid(context), inp["class_id"],
        enabled=inp["enabled"], lesson_id=inp.get("lesson_id"), duration_minutes=None,
        client_request_id=context.request.client_request_id,
        idempotency_key=context.idempotency_key, request_id=None,
    )
    return {"data": r}


async def _h_insight_apply(context: ToolExecutionContext, inp: dict) -> dict:
    r = await lessons.apply_insight_to_lesson(
        context.db, _uid(context), inp["lesson_id"],
        insight_id=inp["insight_id"], version=inp["version"], instruction=None,
    )
    return {"data": r}


# ==================== 定义与工厂 ====================

_STUDENT_SCENES = frozenset()
TEACHER_SCENES = frozenset(
    {
        "teacher.today", "teacher.prep", "teacher.assessment",
        "teacher.grading", "teacher.classroom", "teacher.class.insights",
        "teacher.resources",
    }
)
_READ_SCENES = frozenset({"teacher.today", "teacher.prep", "teacher.assessment",
                          "teacher.grading", "teacher.classroom", "teacher.class.insights",
                          "teacher.resources"})


def _read_def(
    name: str, desc: str, handler, scenes: frozenset[str], input_model=None
) -> ToolDefinition:
    return ToolDefinition(
        name=name, version="1.0.0", description=desc,
        input_model=input_model or TOnly, output_model=ReadOut, risk=ToolRisk.READ,
        allowed_roles=frozenset({ActorRole.TEACHER}), allowed_scenes=scenes,
        timeout_s=20.0, idempotency_required=False, handler=handler,
    )


def _gen_def(name: str, desc: str, handler, input_model, scenes: frozenset[str]) -> ToolDefinition:
    return ToolDefinition(
        name=name, version="1.0.0", description=desc,
        input_model=input_model, output_model=Anything, risk=ToolRisk.LEARNING_ACTION,
        allowed_roles=frozenset({ActorRole.TEACHER}), allowed_scenes=scenes,
        timeout_s=20.0, idempotency_required=False, handler=handler,
    )


def _write_def(name: str, desc: str, handler, input_model, scenes: frozenset[str]) -> ToolDefinition:
    return ToolDefinition(
        name=name, version="1.0.0", description=desc,
        input_model=input_model, output_model=WriteOut, risk=ToolRisk.WRITE,
        allowed_roles=frozenset({ActorRole.TEACHER}), allowed_scenes=scenes,
        timeout_s=20.0, idempotency_required=True, handler=handler,
    )


def build_teacher_registry() -> ToolRegistry:
    """M3 teacher-only 注册表工厂（读 7 + 生成 7 + 写 5 = 19 工具，角色与 M2 隔离）。"""
    reg = ToolRegistry()
    scene = TEACHER_SCENES

    # 读取工具
    reg.register(_read_def("teacher.today.read", "今日工作台聚合", _h_today_read, frozenset({"teacher.today"}), input_model=ReadAny))
    reg.register(_read_def("teacher.class.insights.read", "班级可行动洞察", _h_class_insights_read, frozenset({"teacher.today", "teacher.class.insights"})))
    reg.register(_read_def("teacher.lesson.sources.read", "教案来源读取", _h_lesson_sources_read, frozenset({"teacher.prep", "teacher.resources"})))
    reg.register(_read_def("teacher.assignment.queue.read", "作业队列读取", _h_assignment_queue_read, frozenset({"teacher.today", "teacher.assessment"})))
    reg.register(_read_def("teacher.grading.queue.read", "批改队列读取", _h_grading_queue_read, frozenset({"teacher.today", "teacher.grading"})))
    reg.register(_read_def("teacher.classroom.state.read", "课堂状态读取", _h_classroom_state_read, frozenset({"teacher.classroom"})))
    reg.register(_read_def("teacher.resources.search", "资源检索", _h_resources_search, frozenset({"teacher.prep", "teacher.resources"}), input_model=ReadAny))

    # 生成工具（仅草稿）
    reg.register(_gen_def("teacher.lesson.adapt", "改编教案", _h_lesson_adapt, TopicInput, scene))
    reg.register(_gen_def("teacher.slides.create", "生成课件", _h_slides_create, LessonRef, scene))
    reg.register(_gen_def("teacher.quiz.create", "智能出题", _h_quiz_create, QuizIn, scene))
    reg.register(_gen_def("teacher.grade.suggest", "预批改建议", _h_grade_suggest, GradeSuggestIn, scene))
    reg.register(_gen_def("teacher.problem.explain", "讲解题目", _h_problem_explain, TopicInput, scene))
    reg.register(_gen_def("teacher.course.preprocess", "课程预处理", _h_course_preprocess, ResourceIn, scene))
    reg.register(_gen_def("teacher.document.understand", "文档理解", _h_document_understand, ResourceIn, scene))

    # 正式写工具（WRITE，需确认 + 幂等）
    reg.register(_write_def("teacher.artifact.confirm", "确认产物", _h_artifact_confirm, ArtifactConfirmIn, scene))
    reg.register(_write_def("teacher.assignment.publish", "发布作业", _h_assignment_publish, AssignmentPublishIn, scene))
    reg.register(_write_def("teacher.grade.confirm", "确认正式分", _h_grade_confirm, GradeConfirmIn, scene))
    reg.register(_write_def("teacher.classroom.mode.set", "设置课堂模式", _h_classroom_mode_set, ClassroomIn, scene))
    reg.register(_write_def("teacher.insight.apply", "应用洞察", _h_insight_apply, InsightApplyIn, scene))
    return reg
