"""Teacher Prep 确定性领域服务（R14 骨架）。

职责（DECISIONS.md §4/§5/§6）：
- 唯一状态转换入口：所有迁移都过 `state_machines.assert_transition` 纯函数闸门；
- LLM 只产生 proposal 载荷；`confirm/reject` 必须 actor_role ∈ TEACHING_ROLES
  （经 8000 ClassMember 裁决的权威角色，每次高风险命令执行瞬间重新核验）；
- 所有命令：幂等（reliability_idempotency 五元键 + request_hash，principal=actor_id，
  同 key 不同载荷 → 409）、审计（tp_session_commands 逐条）、事务事件
  （tp_session_events + lineage 链）；
- expected_revision 乐观并发：任何对既有实体的变更都校验 revision，冲突 → 409；
- 同事务原子性：业务行 + 命令行 + 事件行 + 幂等响应在调用方 session 内一次提交。

错误语义：
- 40900 CONFLICT：revision 冲突 / 非法状态迁移 / 幂等键在途；
- 40301 ROLE_DENIED：跨班/成员关系/非教学角色动作（R13 adapter 403/404 归一）；
- 40400 NOT_FOUND：session/blueprint 不存在；
- 50300 DEPENDENCY_UNAVAILABLE：8000 权威不可达（fail closed）。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, ErrorCode
from app.db.models.platform import Classes
from app.ppt_v2.persistence.models import PptV2DeckSpecRecord
from app.reliability.idempotency import ReliabilityIdempotency, request_hash_of
from app.reliability.trace import get_current
from app.teacher_prep.blueprint.projector import LlmGenerate
from app.teacher_prep.blueprint.revision_projector import project_blueprint_revision
from app.teacher_prep.context.models import TextbookTeachingContext
from app.teacher_prep.profile.builder import (
    build_evidence_rows,
    build_profile_body,
    evidence_refs_of,
)
from app.teacher_prep.profile.evidence import TeachingEvidenceRow
from app.teacher_prep.profile.facts import fetch_facts_bundle
from app.teacher_prep.profile.interpreter import (
    InterpreterUnavailableError,
    ProfileInterpreter,
)
from app.teacher_prep.profile.metrics import MetricsInput, compute_profile_metrics
from app.teacher_prep.profile.models import (
    ClassLearningProfileRevisionRow,
    ClassLearningProfileRow,
)
from app.teacher_prep.profile.schema import (
    AttemptPolicyMetrics,
    TaskScopeInfo,
    VersionedClassLearningProfile,
)
from app.teacher_prep.specs.durations import normalize_flow_durations
from app.teacher_prep.specs.models import LessonPlanSpecRow, SpecProjectionRow
from app.teacher_prep.specs.projector import (
    ProjectionInputs,
    ProjectionRunLineage,
    compute_projection_hash,
    project_deck_spec,
    project_lesson_plan_spec,
)
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter
from app.teacher_prep_v2.contracts.errors import (
    ContractAuthorityUnreachable,
    ContractError,
    ContractMembershipDenied,
)

from . import state_machines as sm
from .models import (
    BlueprintRevisionRow,
    SessionCommandRow,
    SessionEventRow,
    TeachingBlueprintRow,
    TeachingSessionRow,
)

OWNER_SERVICE = "teacher_prep"
PROPOSAL_SOURCES = ("ai_suggestion", "teacher", "system")

_EMPTY_UUID = uuid.UUID(int=0)


def _class_ref(row: Any) -> str | None:
    """会话班级引用：None 表示未绑定班级（E-3 可选班级）；禁止编造 "None" 字符串。"""
    return str(row.class_id) if row.class_id is not None else None


class SessionNotFoundError(ApiError):
    def __init__(self, session_id: str):
        super().__init__(ErrorCode.NOT_FOUND, f"备课会话不存在（session_id={session_id}）")


class BlueprintNotFoundError(ApiError):
    def __init__(self, blueprint_id: str):
        super().__init__(ErrorCode.NOT_FOUND, f"教学蓝图不存在（blueprint_id={blueprint_id}）")


class ProfileNotFoundError(ApiError):
    def __init__(self, profile_id: str):
        super().__init__(ErrorCode.NOT_FOUND, f"班级学习画像不存在（profile_id={profile_id}）")


class RevisionConflictError(ApiError):
    def __init__(self, kind: str, entity: str, expected: int, actual: int):
        super().__init__(
            ErrorCode.CONFLICT,
            f"{kind} revision 冲突（{entity} expected={expected} actual={actual}）——请刷新后重试",
        )


class InvalidTransitionError(ApiError):
    def __init__(self, kind: str, current: str, target: str):
        super().__init__(
            ErrorCode.CONFLICT, f"{kind} 非法状态迁移 {current!r} → {target!r}"
        )


class MembershipDeniedError(ApiError):
    def __init__(self, message: str):
        super().__init__(ErrorCode.ROLE_DENIED, message)


class AuthorityUnreachableError(ApiError):
    def __init__(self, message: str):
        super().__init__(ErrorCode.DEPENDENCY_UNAVAILABLE, message)


class IdempotencyInFlightError(ApiError):
    def __init__(self, message: str):
        super().__init__(ErrorCode.CONFLICT, message)


def _validate_idem(key: str) -> None:
    try:
        sm.validate_idempotency_key(key)
    except ValueError as exc:
        raise ApiError(ErrorCode.VALIDATION, str(exc)) from exc


def _require_teaching_role(role: str) -> None:
    try:
        sm.assert_confirm_actor(role)
    except ValueError as exc:
        raise MembershipDeniedError(str(exc)) from exc


def _transition(kind: str, current: str, target: str) -> None:
    """状态机闸门 → 领域错误（409 语义）：把纯函数 ValueError 归一为 InvalidTransitionError。"""
    try:
        sm.assert_transition(kind, current, target)
    except sm.PrepStateTransitionInvalid as exc:
        raise InvalidTransitionError(kind, current, target) from exc


def _now() -> datetime:
    return datetime.now(UTC)


def _jsonable(value: Any) -> Any:
    """JsonB/JSON 列安全序列化（UUID→str 等）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _coverage_snapshot(profile: VersionedClassLearningProfile) -> dict[str, Any]:
    """画像证据质量冗余快照（coverage 列；与 body.evidence 确定性一致）。"""
    ev = profile.evidence
    return {
        "coverage_ratio": ev.coverage_ratio,
        "submission_count": ev.submission_count,
        "evidence_count": ev.evidence_count,
        "expected_evidence": ev.expected_evidence,
        "low_coverage": ev.low_coverage,
        "conflict_count": len(ev.conflicts),
    }


# ── 序列化 DTO ───────────────────────────────────────────────────────────────

def session_dto(row: TeachingSessionRow, *, class_name: str | None = None) -> dict[str, Any]:
    """会话投影。`class_name` 来自本库 `classes.name`（前端班级 chip 唯一合法来源）。"""
    return {
        "session_id": str(row.id),
        "teacher_id": str(row.teacher_id),
        "class_id": _class_ref(row),
        "class_name": class_name or None,
        "course_id": str(row.course_id) if row.course_id else None,
        "schedule_id": str(row.schedule_id) if row.schedule_id else None,
        "status": row.status,
        "revision": row.revision,
        "title": row.title,
        "intent": row.intent,
        "context_snapshot_id": str(row.context_snapshot_id) if row.context_snapshot_id else None,
        "prompt_release_id": str(row.prompt_release_id) if row.prompt_release_id else None,
        "ai_run_id": str(row.ai_run_id) if row.ai_run_id else None,
        "pending_action_id": str(row.pending_action_id) if row.pending_action_id else None,
        "opened_at": row.opened_at.isoformat() if row.opened_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "last_event_seq": row.last_event_seq,
    }


async def load_class_names(
    session: AsyncSession, class_ids: set[uuid.UUID] | list[uuid.UUID]
) -> dict[str, str]:
    """批量读 classes.name；查不到的 class_id 不进结果（诚实缺失，不伪造）。"""
    ids = {i for i in class_ids if i is not None}
    if not ids:
        return {}
    rows = (
        await session.execute(select(Classes.id, Classes.name).where(Classes.id.in_(ids)))
    ).all()
    return {str(cid): name for cid, name in rows if name}


def blueprint_dto(row: TeachingBlueprintRow) -> dict[str, Any]:
    return {
        "blueprint_id": str(row.id),
        "session_id": str(row.session_id),
        "status": row.status,
        "revision": row.revision,
        "title": row.title,
    }


def event_dto(row: SessionEventRow) -> dict[str, Any]:
    return {
        "event_id": str(row.id),
        "seq": row.seq,
        "event_type": row.event_type,
        "command_id": str(row.command_id) if row.command_id else None,
        "blueprint_id": str(row.blueprint_id) if row.blueprint_id else None,
        "payload": _jsonable(row.payload),
        "parent_event_id": str(row.parent_event_id) if row.parent_event_id else None,
        "revision": row.revision,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ── 领域服务 ─────────────────────────────────────────────────────────────────

_RunFn = Callable[[], Awaitable[dict[str, Any]]]


class TeacherPrepService:
    """确定性领域服务：命令 → 状态机闸门 + 成员重验 + 事件/审计，同事务落库。"""

    @staticmethod
    async def _verify_membership(
        adapter: Api8000Adapter, *, token: str, class_id: str | None
    ) -> str:
        """执行瞬间重验 8000 成员关系；返回权威 member_role；403/404→403，5xx→503。

        E-3 可选班级：class_id=None（未绑定班级）→ 仅教师身份裁决，不产生班级域授权；
        绑定班级后的所有路径恢复现场 8000 成员裁决（fail closed）。
        """
        if not class_id:
            return "teacher"
        try:
            row = await adapter.require_class_membership(token=token, class_id=class_id)
        except ContractMembershipDenied as exc:
            raise MembershipDeniedError(str(exc)) from exc
        except ContractAuthorityUnreachable as exc:
            raise AuthorityUnreachableError(str(exc)) from exc
        return str(row.member_role)

    # ── create_session ──────────────────────────────────────────────────────

    async def create_session(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        class_id: str | None,
        idempotency_key: str,
        request_hash: str,
        course_id: str | None = None,
        schedule_id: str | None = None,
        title: str | None = None,
        intent: dict[str, Any] | None = None,
        context_snapshot_id: str | None = None,
        prompt_release_id: str | None = None,
        ai_run_id: str | None = None,
        pending_action_id: str | None = None,
    ) -> dict[str, Any]:
        _validate_idem(idempotency_key)

        async def _exec() -> dict[str, Any]:
            member_role = await self._verify_membership(adapter, token=token, class_id=class_id)
            sid = uuid.uuid4()
            now = _now()
            row = TeachingSessionRow(
                id=sid,
                teacher_id=uuid.UUID(actor_id),
                class_id=uuid.UUID(class_id) if class_id else None,
                course_id=uuid.UUID(course_id) if course_id else None,
                schedule_id=uuid.UUID(schedule_id) if schedule_id else None,
                status="draft",
                revision=1,
                last_event_seq=0,
                title=title,
                intent=_jsonable(intent) if intent else None,
                context_snapshot_id=uuid.UUID(context_snapshot_id) if context_snapshot_id else None,
                prompt_release_id=uuid.UUID(prompt_release_id) if prompt_release_id else None,
                ai_run_id=uuid.UUID(ai_run_id) if ai_run_id else None,
                pending_action_id=uuid.UUID(pending_action_id) if pending_action_id else None,
                last_command_at=now,
            )
            session.add(row)
            cmd_id = await self._log_command(
                session,
                session_id=sid,
                command_type="create_session",
                actor_id=actor_id,
                actor_role=member_role,
                class_id=class_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                expected_revision=None,
                result_status="applied",
                payload={"title": title, "intent": intent},
            )
            await self._log_events(
                session,
                session_id=sid,
                command_id=cmd_id,
                blueprint_id=None,
                events=[("session.created", {"actor_id": actor_id, "class_id": class_id})],
                row_seq=0,
                revision=1,
            )
            return {
                "session_id": str(sid),
                "status": "draft",
                "revision": 1,
                "command_id": str(cmd_id),
            }

        return await self._dispatch(
            session,
            operation="create_session",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            principal=actor_id,
            session_id_hint=None,  # 失败时无会话可挂审计行
            run=_exec,
        )

    # ── open / resume（继续历史；跨日续接 = 对 active 再次 open）────────────

    async def open_session(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        session_id: str,
        expected_revision: int,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, Any]:
        _validate_idem(idempotency_key)

        async def _exec() -> dict[str, Any]:
            row = await self._lock_session(session, session_id)
            member_role = await self._verify_membership(
                adapter, token=token, class_id=_class_ref(row)
            )
            self._expect(session_id, "会话", expected_revision, row.revision)

            if row.status == sm.SESSION_DRAFT:
                _transition("session", row.status, sm.SESSION_ACTIVE)
                row.status = sm.SESSION_ACTIVE
                event_type, resumed = "session.opened", False
            elif row.status == sm.SESSION_ACTIVE:
                event_type, resumed = "session.resumed", True  # 跨日/后续续接：状态不变
            else:
                raise InvalidTransitionError("会话", row.status, "active")

            row.revision += 1
            row.opened_at = _now()
            row.last_command_at = _now()
            cmd_id = await self._log_command(
                session,
                session_id=row.id,
                command_type="open_session",
                actor_id=actor_id,
                actor_role=member_role,
                class_id=_class_ref(row),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                expected_revision=expected_revision,
                result_status="applied",
                payload={"resumed": resumed},
            )
            await self._log_events(
                session,
                session_id=row.id,
                command_id=cmd_id,
                blueprint_id=None,
                events=[(event_type, {"actor_id": actor_id, "resumed": resumed})],
                row_seq=row.last_event_seq,
                revision=row.revision,
            )
            return {
                "session_id": str(row.id),
                "status": row.status,
                "revision": row.revision,
                "resumed": resumed,
            }

        return await self._dispatch(
            session,
            operation="open_session",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            principal=actor_id,
            session_id_hint=uuid.UUID(session_id),
            run=_exec,
        )

    # ── cancel / archive ────────────────────────────────────────────────────

    async def cancel_session(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        session_id: str,
        expected_revision: int,
        idempotency_key: str,
        request_hash: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        _validate_idem(idempotency_key)

        async def _exec() -> dict[str, Any]:
            row = await self._lock_session(session, session_id)
            member_role = await self._verify_membership(
                adapter, token=token, class_id=_class_ref(row)
            )
            self._expect(session_id, "会话", expected_revision, row.revision)
            if row.status == sm.SESSION_ARCHIVED:
                raise InvalidTransitionError("会话", row.status, "cancelled")
            _transition("session", row.status, sm.SESSION_CANCELLED)
            row.status = sm.SESSION_CANCELLED
            row.revision += 1
            row.closed_reason = reason
            row.last_command_at = _now()
            cmd_id = await self._log_command(
                session,
                session_id=row.id,
                command_type="cancel_session",
                actor_id=actor_id,
                actor_role=member_role,
                class_id=_class_ref(row),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                expected_revision=expected_revision,
                result_status="applied",
                payload={"reason": reason},
            )
            await self._log_events(
                session,
                session_id=row.id,
                command_id=cmd_id,
                blueprint_id=None,
                events=[("session.cancelled", {"actor_id": actor_id, "reason": reason})],
                row_seq=row.last_event_seq,
                revision=row.revision,
            )
            return {"session_id": str(row.id), "status": row.status, "revision": row.revision}

        return await self._dispatch(
            session,
            operation="cancel_session",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            principal=actor_id,
            session_id_hint=uuid.UUID(session_id),
            run=_exec,
        )

    async def archive_session(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        session_id: str,
        expected_revision: int,
        idempotency_key: str,
        request_hash: str,
    ) -> dict[str, Any]:
        _validate_idem(idempotency_key)

        async def _exec() -> dict[str, Any]:
            row = await self._lock_session(session, session_id)
            member_role = await self._verify_membership(
                adapter, token=token, class_id=_class_ref(row)
            )
            self._expect(session_id, "会话", expected_revision, row.revision)
            if row.status == sm.SESSION_ARCHIVED:
                raise InvalidTransitionError("会话", row.status, "archived")
            _transition("session", row.status, sm.SESSION_ARCHIVED)
            row.status = sm.SESSION_ARCHIVED
            row.revision += 1
            row.last_command_at = _now()
            cmd_id = await self._log_command(
                session,
                session_id=row.id,
                command_type="archive_session",
                actor_id=actor_id,
                actor_role=member_role,
                class_id=_class_ref(row),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                expected_revision=expected_revision,
                result_status="applied",
                payload={},
            )
            await self._log_events(
                session,
                session_id=row.id,
                command_id=cmd_id,
                blueprint_id=None,
                events=[("session.archived", {"actor_id": actor_id})],
                row_seq=row.last_event_seq,
                revision=row.revision,
            )
            return {"session_id": str(row.id), "status": row.status, "revision": row.revision}

        return await self._dispatch(
            session,
            operation="archive_session",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            principal=actor_id,
            session_id_hint=uuid.UUID(session_id),
            run=_exec,
        )

    # ── propose / confirm / reject blueprint ────────────────────────────────

    async def propose_blueprint(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        session_id: str,
        idempotency_key: str,
        request_hash: str,
        body: dict[str, Any],
        title: str | None = None,
        blueprint_id: str | None = None,
        expected_revision: int | None = None,
        source: str = "ai_suggestion",
        note: str | None = None,
    ) -> dict[str, Any]:
        _validate_idem(idempotency_key)
        if source not in PROPOSAL_SOURCES:
            raise ApiError(ErrorCode.VALIDATION, f"proposal source 必须 ∈ {PROPOSAL_SOURCES}")

        async def _exec() -> dict[str, Any]:
            srow = await self._lock_session(session, session_id)
            if srow.status == sm.SESSION_ARCHIVED:
                raise InvalidTransitionError("会话", srow.status, "提案")
            bid = uuid.UUID(blueprint_id) if blueprint_id else None

            if bid is None:
                member_role = await self._verify_membership(
                    adapter, token=token, class_id=_class_ref(srow)
                )
                brow = TeachingBlueprintRow(
                    id=uuid.uuid4(),
                    session_id=srow.id,
                    status=sm.BLUEPRINT_PROPOSAL,
                    revision=1,
                    title=title,
                    body=_jsonable(body),
                )
                session.add(brow)
                prev_no = None
            else:
                brow = await self._lock_blueprint(session, bid)
                if srow.id != brow.session_id:
                    raise MembershipDeniedError("蓝图不属于该会话（跨会话访问拒绝）")
                member_role = await self._verify_membership(
                    adapter, token=token, class_id=_class_ref(srow)
                )
                if expected_revision is None or expected_revision != brow.revision:
                    raise RevisionConflictError(
                        "蓝图", str(bid), expected_revision or -1, brow.revision
                    )
                _transition("blueprint", brow.status, sm.BLUEPRINT_PROPOSAL)
                prev_no = brow.revision
                brow.status = sm.BLUEPRINT_PROPOSAL
                brow.revision += 1
                brow.title = title
                brow.body = _jsonable(body)
                brow.confirmed_at = None
                brow.confirmed_by = None
                brow.rejected_at = None
                brow.rejected_by = None
                brow.rejected_reason = None

            # 蓝图行与修订行之间同样没有 relationship：新建蓝图时必须先落父行，
            # 否则 tp_blueprint_revisions_blueprint_id_fkey 会在子行插入时违例。
            await session.flush()
            rev = BlueprintRevisionRow(
                blueprint_id=brow.id,
                revision_no=brow.revision,
                body=_jsonable(body),
                verdict=sm.BLUEPRINT_PROPOSAL,
                source=source,
                actor_id=uuid.UUID(actor_id),
                actor_role=member_role,
                parent_revision_no=prev_no,
                note=note,
            )
            session.add(rev)
            cmd_id = await self._log_command(
                session,
                session_id=srow.id,
                blueprint_id=brow.id,
                command_type="propose_blueprint",
                actor_id=actor_id,
                actor_role=member_role,
                class_id=_class_ref(srow),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                expected_revision=expected_revision,
                result_status="applied",
                payload={"source": source, "revision_no": brow.revision, "note": note},
            )
            await self._log_events(
                session,
                session_id=srow.id,
                command_id=cmd_id,
                blueprint_id=brow.id,
                events=[
                    (
                        "blueprint.proposed",
                        {"actor_id": actor_id, "source": source, "revision_no": brow.revision},
                    )
                ],
                row_seq=srow.last_event_seq,
                revision=brow.revision,
            )
            return {
                "blueprint_id": str(brow.id),
                "status": brow.status,
                "revision": brow.revision,
                "session_id": str(srow.id),
            }

        return await self._dispatch(
            session,
            operation="propose_blueprint",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            principal=actor_id,
            session_id_hint=uuid.UUID(session_id),
            run=_exec,
        )

    async def confirm_blueprint(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        blueprint_id: str,
        expected_revision: int,
        idempotency_key: str,
        request_hash: str,
        note: str | None = None,
    ) -> dict[str, Any]:
        return await self._adjudicate(
            session=session,
            adapter=adapter,
            token=token,
            actor_id=actor_id,
            actor_role_hint=actor_role_hint,
            blueprint_id=blueprint_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            command="confirm_blueprint",
            target=sm.BLUEPRINT_CONFIRMED,
            event="blueprint.confirmed",
            detail={"note": note},
        )

    async def reject_blueprint(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        blueprint_id: str,
        expected_revision: int,
        idempotency_key: str,
        request_hash: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return await self._adjudicate(
            session=session,
            adapter=adapter,
            token=token,
            actor_id=actor_id,
            actor_role_hint=actor_role_hint,
            blueprint_id=blueprint_id,
            expected_revision=expected_revision,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            command="reject_blueprint",
            target=sm.BLUEPRINT_REJECTED,
            event="blueprint.rejected",
            detail={"reason": reason},
        )

    async def _adjudicate(
        self,
        *,
        session: AsyncSession,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        blueprint_id: str,
        expected_revision: int,
        idempotency_key: str,
        request_hash: str,
        command: str,
        target: str,
        event: str,
        detail: dict[str, Any],
    ) -> dict[str, Any]:
        _validate_idem(idempotency_key)
        # 前置硬闸门：LLM/系统角色无权落定确认状态（不等成员裁决，直接 403）
        _require_teaching_role(actor_role_hint)

        async def _exec() -> dict[str, Any]:
            brow = await self._lock_blueprint(session, uuid.UUID(blueprint_id))
            srow = await self._lock_session(session, str(brow.session_id))
            member_role = await self._verify_membership(
                adapter, token=token, class_id=_class_ref(srow)
            )
            _require_teaching_role(member_role)  # 执行瞬间权威角色仍须为教学角色
            self._expect(blueprint_id, "蓝图", expected_revision, brow.revision)
            _transition("blueprint", brow.status, target)

            prev_no = brow.revision
            brow.status = target
            brow.revision += 1
            if target == sm.BLUEPRINT_CONFIRMED:
                brow.confirmed_at, brow.confirmed_by = _now(), uuid.UUID(actor_id)
                brow.rejected_at, brow.rejected_by, brow.rejected_reason = None, None, None
                # R6-n1fix（N6 治本）：蓝图确认 = 章节权威落定 → 会话标题/章节主题与
                # **会话绑定章节**同源同步（与 orchestrator 改选同步同一
                # resolve_section_title）。s4 画布页眉（DeckPane 优先读 session.title）、
                # 首页/导航读 intent.knowledge_points——NL 原始知识点易过期
                # （本演示会话实测「椭圆的标准方程」残留），确认时点收敛到绑定章节。
                from .conversation.options import resolve_section_title

                _section_id = str(((srow.intent or {}).get("section_id")) or "")
                if _section_id:
                    _section_title, _ = await resolve_section_title(session, _section_id)
                    if _section_title:
                        srow.title = _section_title
                        _intent = dict(srow.intent or {})
                        _topic = re.sub(r"[0-9]+(?:\.[0-9]+)+", " ", _section_title)
                        _topic = re.sub(r"第[一二三四五六七八九十百\d]+课时", " ", _topic)
                        _topic = re.sub(r"\s+", " ", _topic).strip(" -·、（）()")
                        if _topic:
                            _intent["knowledge_points"] = [_topic]
                            srow.intent = _intent
            else:
                brow.rejected_at, brow.rejected_by = _now(), uuid.UUID(actor_id)
                brow.rejected_reason = detail.get("reason")
                brow.confirmed_at, brow.confirmed_by = None, None

            rev = BlueprintRevisionRow(
                blueprint_id=brow.id,
                revision_no=brow.revision,
                body=_jsonable(brow.body),
                verdict=target,
                source="teacher",
                actor_id=uuid.UUID(actor_id),
                actor_role=member_role,
                parent_revision_no=prev_no,
            )
            session.add(rev)
            cmd_id = await self._log_command(
                session,
                session_id=srow.id,
                blueprint_id=brow.id,
                command_type=command,
                actor_id=actor_id,
                actor_role=member_role,
                class_id=_class_ref(srow),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                expected_revision=expected_revision,
                result_status="applied",
                payload=_jsonable(detail),
            )
            await self._log_events(
                session,
                session_id=srow.id,
                command_id=cmd_id,
                blueprint_id=brow.id,
                events=[(event, {"actor_id": actor_id, **_jsonable(detail)})],
                row_seq=srow.last_event_seq,
                revision=brow.revision,
            )
            return {
                "blueprint_id": str(brow.id),
                "status": brow.status,
                "revision": brow.revision,
                "session_id": str(srow.id),
            }

        return await self._dispatch(
            session,
            operation=command,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            principal=actor_id,
            session_id_hint=None,  # run 内锁定后才知道真实 session_id
            run=_exec,
        )

    # ── R20 publish_profile：版本化学情证据发布/更新 ──────────────────────────

    async def publish_profile(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        session_id: str,
        class_id: str,
        idempotency_key: str,
        request_hash: str,
        profile: VersionedClassLearningProfile,
        profile_id: str | None = None,
        expected_revision: int | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """发布/更新版本化 ClassLearningProfile（每版本不可变快照；published 才可作证据）。

        - 新建：revision 必须为 1；更新：expected_revision 乐观并发 → 冲突 409；
        - archived 终态不可再发布；状态迁移只走 state_machines；
        - 时效/低覆盖/证据冲突 warning 随版本不可变（body.warnings + 列快照一致）。
        """
        _validate_idem(idempotency_key)
        _require_teaching_role(actor_role_hint)  # 学情证据发布 = 高风险写（先于成员裁决硬闸门）

        async def _exec() -> dict[str, Any]:
            member_role, srow = await self._verify_profile_context(
                session, adapter=adapter, token=token, class_id=class_id, session_id=session_id
            )
            if profile.class_id != class_id:
                raise MembershipDeniedError("画像 body.class_id 与目标班级不一致（跨班访问拒绝）")

            write = await self._upsert_profile_revision(
                session,
                srow=srow,
                member_role=member_role,
                actor_id=actor_id,
                class_id=class_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                expected_revision=expected_revision,
                profile=profile,
                profile_id=profile_id,
                note=note,
                command_type="publish_profile",
            )
            return write

        return await self._dispatch(
            session,
            operation="publish_profile",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            principal=actor_id,
            session_id_hint=uuid.UUID(session_id),
            run=_exec,
        )

    async def _upsert_profile_revision(
        self,
        session: AsyncSession,
        *,
        srow: TeachingSessionRow,
        member_role: str,
        actor_id: str,
        class_id: str,
        idempotency_key: str,
        request_hash: str,
        expected_revision: int | None,
        profile: VersionedClassLearningProfile,
        profile_id: str | None,
        note: str | None,
        command_type: str,
        extra_payload: dict[str, Any] | None = None,
        new_profile_id: str | None = None,
    ) -> dict[str, Any]:
        """画像发布/更新写入（publish_profile 与 recalculate_profile 共用；同事务）。

        `new_profile_id`：新建时调用方预生成的 id（保证 body.profile_id 与行 id 一致）。
        """
        if profile_id is None:
            if profile.revision != 1:
                raise ApiError(
                    ErrorCode.VALIDATION,
                    f"新建画像 revision 必须为 1（实际 {profile.revision}）",
                )
            pid = uuid.UUID(new_profile_id) if new_profile_id else uuid.uuid4()
            body = profile.model_dump(mode="json")
            body["status"] = sm.PROFILE_PUBLISHED
            body["revision"] = 1
            prow = ClassLearningProfileRow(
                id=pid,
                class_id=uuid.UUID(class_id),
                session_id=srow.id,
                owner_id=uuid.UUID(actor_id),
                status=sm.PROFILE_PUBLISHED,
                revision=1,
                body=_jsonable(body),
                computed_at=profile.freshness.computed_at,
                expires_at=profile.freshness.expires_at,
                coverage=_jsonable(_coverage_snapshot(profile)),
                warnings=list(profile.warnings),
            )
            session.add(prow)
            # tel E2E 修复：先 flush 父行，否则同批 flush 时 revisions 可能先于父行插入，
            # 触发 tp_class_learning_profile_revisions_profile_id_fkey 外键违约（实测 500）。
            await session.flush()
            prev_no = None
            event_type = "profile.published"
        else:
            prow = await self._lock_profile(session, uuid.UUID(profile_id))
            if prow.class_id != uuid.UUID(class_id):
                raise MembershipDeniedError("画像不属于该班级（跨班访问拒绝）")
            if prow.session_id != srow.id:
                raise MembershipDeniedError("画像不属于该会话（跨会话访问拒绝）")
            self._expect("画像", "画像", expected_revision, prow.revision)
            if prow.status == sm.PROFILE_ARCHIVED:
                raise InvalidTransitionError("画像", prow.status, sm.PROFILE_PUBLISHED)
            _transition("profile", prow.status, sm.PROFILE_PUBLISHED)
            prev_no = prow.revision
            new_rev = prow.revision + 1
            body = profile.model_copy(
                update={"status": sm.PROFILE_PUBLISHED, "revision": new_rev}
            ).model_dump(mode="json")
            prow.status = sm.PROFILE_PUBLISHED
            prow.revision = new_rev
            prow.body = _jsonable(body)
            prow.computed_at = profile.freshness.computed_at
            prow.expires_at = profile.freshness.expires_at
            prow.coverage = _jsonable(_coverage_snapshot(profile))
            prow.warnings = list(profile.warnings)
            event_type = (
                "profile.recalculated"
                if command_type == "recalculate_profile"
                else "profile.updated"
            )

        rev = ClassLearningProfileRevisionRow(
            profile_id=prow.id,
            revision_no=prow.revision,
            body=_jsonable(prow.body),
            verdict=prow.status,
            source="system",
            actor_id=uuid.UUID(actor_id),
            actor_role=member_role,
            parent_revision_no=prev_no,
            note=note,
        )
        session.add(rev)
        cmd_id = await self._log_command(
            session,
            session_id=srow.id,
            blueprint_id=None,
            command_type=command_type,
            actor_id=actor_id,
            actor_role=member_role,
            class_id=class_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            expected_revision=expected_revision,
            result_status="applied",
            payload={
                "profile_id": str(prow.id),
                "revision": prow.revision,
                "stale": profile.freshness.stale,
                "warnings": list(profile.warnings),
                **(extra_payload or {}),
            },
        )
        await self._log_events(
            session,
            session_id=srow.id,
            command_id=cmd_id,
            blueprint_id=None,
            events=[
                (
                    event_type,
                    {
                        "actor_id": actor_id,
                        "profile_id": str(prow.id),
                        "revision": prow.revision,
                        "warnings": list(profile.warnings),
                    },
                )
            ],
            row_seq=srow.last_event_seq,
            revision=prow.revision,
        )
        return {
            "profile_id": str(prow.id),
            "class_id": class_id,
            "session_id": str(srow.id),
            "status": prow.status,
            "revision": prow.revision,
        }

    @staticmethod
    async def _verify_profile_context(
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        class_id: str,
        session_id: str,
    ) -> tuple[str, TeachingSessionRow]:
        """画像命令公共前置：执行瞬间角色/成员裁决 + 会话锁 + 跨班一致性。"""
        member_role = await TeacherPrepService._verify_membership(adapter, token=token, class_id=class_id)
        _require_teaching_role(member_role)  # 执行瞬间权威角色仍须为教学角色
        srow = await TeacherPrepService._lock_session(session, session_id)
        if srow.class_id != uuid.UUID(class_id):
            raise MembershipDeniedError("会话与画像班级不一致（跨班访问拒绝）")
        return member_role, srow

    # ── R19 recalculate_profile：Adapter 拉取最小素材 → 确定性重算（幂等）──────

    async def recalculate_profile(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        session_id: str,
        class_id: str,
        idempotency_key: str,
        request_hash: str,
        assignment_id: str,
        profile_id: str | None = None,
        expected_revision: int | None = None,
        task_scope: TaskScopeInfo | None = None,
        attempt_policy: AttemptPolicyMetrics | None = None,
        kp_by_item: dict[str, list[int]] | None = None,
        expires_at: datetime | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """经 Adapter 拉取 PII 最小素材 → 确定性统计 → 版本化画像（重算幂等）。

        - 统计全部由 metrics.compute_profile_metrics 纯函数计算（无 LLM）；
        - 指纹与当前 revision 一致 → no-op（replayed，不新增版本/不重复落证据）；
        - 成员守卫每次现场裁决；8000 不可达 fail closed；跨班 403；
        - 证据行（typed refs）与画像同事务落库，可追溯。
        """
        _validate_idem(idempotency_key)
        _require_teaching_role(actor_role_hint)

        async def _exec() -> dict[str, Any]:
            member_role, srow = await self._verify_profile_context(
                session, adapter=adapter, token=token, class_id=class_id, session_id=session_id
            )
            # Adapter 只读拉取（403/404→成员拒绝；5xx→权威不可达，均 fail closed）
            try:
                bundle = await fetch_facts_bundle(adapter, token=token, assignment_id=assignment_id)
            except ContractMembershipDenied as exc:
                raise MembershipDeniedError(str(exc)) from exc
            except ContractAuthorityUnreachable as exc:
                raise AuthorityUnreachableError(str(exc)) from exc
            except ContractError as exc:  # 其余契约错误（对账冲突等）→ 409 语义
                raise ApiError(ErrorCode.CONFLICT, str(exc)) from exc

            prow: ClassLearningProfileRow | None = None
            prior_fingerprint: str | None = None
            prior_eligible: int | None = None
            if profile_id is not None:
                prow = await self._lock_profile(session, uuid.UUID(profile_id))
            else:
                # 未指定画像 → 自动解析该（班，会话）最新 published 画像：
                # 指纹一致 → 幂等重放；不一致 → 在其上 revision++（重算幂等语义）
                prow = await self._latest_profile(session, uuid.UUID(class_id), srow.id)
            if prow is not None:
                if prow.class_id != uuid.UUID(class_id):
                    raise MembershipDeniedError("画像不属于该班级（跨班访问拒绝）")
                if prow.session_id != srow.id:
                    raise MembershipDeniedError("画像不属于该会话（跨会话访问拒绝）")
                prior_metrics = (prow.body or {}).get("metrics") or {}
                if isinstance(prior_metrics, dict):
                    prior_fingerprint = prior_metrics.get("metrics_fingerprint")
                    cov = prior_metrics.get("coverage") or {}
                    prior_eligible = cov.get("eligible") if isinstance(cov, dict) else None

            now = _now()
            # run_id 须符合 OpaqueRef 语法（字母数字._/-）；幂等键可能含 ':'，统一消毒
            safe_idem = "".join(c if c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-/" else "-" for c in idempotency_key)
            result = compute_profile_metrics(
                MetricsInput(
                    bundle=bundle,
                    # tel E2E 修复：请求未带蓝图映射时回退 8000 facts 的 item_kp_map（题库 KP）
                    kp_by_item=kp_by_item or bundle.item_kp_map,
                    attempt_policy=attempt_policy,
                    prior_eligible_count=prior_eligible,
                    now=now,
                    run_id=f"recalc-{safe_idem}"[:128],
                    expires_at=expires_at,
                )
            )

            # 重算幂等：同指纹 → no-op（保留审计，不新增版本/证据）
            if prow is not None and prior_fingerprint == result.metrics_fingerprint:
                cmd_id = await self._log_command(
                    session,
                    session_id=srow.id,
                    blueprint_id=None,
                    command_type="recalculate_profile",
                    actor_id=actor_id,
                    actor_role=member_role,
                    class_id=class_id,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    expected_revision=expected_revision,
                    result_status="replayed",
                    payload={
                        "profile_id": str(prow.id),
                        "revision": prow.revision,
                        "result": "replayed",
                        "metrics_fingerprint": result.metrics_fingerprint,
                    },
                )
                await self._log_events(
                    session,
                    session_id=srow.id,
                    command_id=cmd_id,
                    blueprint_id=None,
                    events=[
                        (
                            "profile.recalculated",
                            {
                                "actor_id": actor_id,
                                "profile_id": str(prow.id),
                                "revision": prow.revision,
                                "result": "replayed",
                            },
                        )
                    ],
                    row_seq=srow.last_event_seq,
                    revision=prow.revision,
                )
                return {
                    "profile_id": str(prow.id),
                    "class_id": class_id,
                    "session_id": str(srow.id),
                    "status": prow.status,
                    "revision": prow.revision,
                    "result": "replayed",
                    "metrics_fingerprint": result.metrics_fingerprint,
                    "warnings": list(prow.warnings or []),
                }

            # 证据行组（typed refs；先建行拿 id，写入时回填 profile_id）
            target_profile_id = str(prow.id) if prow is not None else str(uuid.uuid4())
            effective_expected = (
                expected_revision if (expected_revision is not None or prow is None) else prow.revision
            )
            evidence_rows = build_evidence_rows(
                result=result,
                class_id=class_id,
                session_id=str(srow.id),
                profile_id=None,
                assignment_id=assignment_id,
                actor_id=actor_id,
            )
            profile = build_profile_body(
                profile_id=target_profile_id,
                class_id=class_id,
                session_id=str(srow.id),
                owner_id=actor_id,
                result=result,
                assignment_id=assignment_id,
                assignment_title=bundle.assignment_title,
                due_at=bundle.deadline,
                task_scope=task_scope,
                expires_at=expires_at,
                now=now,
                revision=(prow.revision + 1) if prow is not None else 1,
                evidence_count=len(evidence_rows),
                evidence_refs=evidence_refs_of(evidence_rows),
            )
            write = await self._upsert_profile_revision(
                session,
                srow=srow,
                member_role=member_role,
                actor_id=actor_id,
                class_id=class_id,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                expected_revision=effective_expected,
                profile=profile,
                profile_id=target_profile_id if prow is not None else None,
                note=note,
                command_type="recalculate_profile",
                extra_payload={
                    "result": "updated",
                    "metrics_fingerprint": result.metrics_fingerprint,
                    "evidence_rows": len(evidence_rows),
                },
                new_profile_id=target_profile_id if prow is None else None,
            )
            for row in evidence_rows:
                row.profile_id = uuid.UUID(write["profile_id"])
                session.add(row)
            # 证据失效：旧指纹的证据行（含 AI 解释）随新版本失效——保留审计
            #（value/refs 原样），但从当前 evidence_refs 引用集中退出。
            stale_rows = (
                (
                    await session.execute(
                        select(TeachingEvidenceRow).where(
                            TeachingEvidenceRow.profile_id == uuid.UUID(write["profile_id"]),
                            TeachingEvidenceRow.metrics_fingerprint != result.metrics_fingerprint,
                        )
                    )
                )
                .scalars()
                .all()
            )
            for stale in stale_rows:
                stale.status = "invalid"
            return {
                **write,
                "result": "updated",
                "metrics_fingerprint": result.metrics_fingerprint,
                "warnings": list(profile.warnings),
                "teacher_actions": [a.model_dump() for a in profile.teacher_actions],
            }

        return await self._dispatch(
            session,
            operation="recalculate_profile",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            principal=actor_id,
            session_id_hint=uuid.UUID(session_id),
            run=_exec,
        )

    # ── R19 get_profile：读（成员裁决 + 跨班 403）────────────────────────────

    async def get_profile(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        session_id: str,
        profile_id: str,
    ) -> dict[str, Any]:
        """读取画像事实 + valid 证据行（每次现场成员裁决；跨班/跨会话 403）。"""
        member_role, srow = await self._verify_profile_context(
            session, adapter=adapter, token=token, class_id=await self._profile_class_id(session, profile_id), session_id=session_id
        )
        prow = await self._lock_profile(session, uuid.UUID(profile_id))
        if prow.class_id != srow.class_id:
            raise MembershipDeniedError("画像不属于该班级（跨班访问拒绝）")
        if prow.session_id != srow.id:
            raise MembershipDeniedError("画像不属于该会话（跨会话访问拒绝）")
        rows = (
            (
                await session.execute(
                    select(TeachingEvidenceRow)
                    .where(
                        TeachingEvidenceRow.profile_id == prow.id,
                        TeachingEvidenceRow.status == "valid",
                    )
                    .order_by(TeachingEvidenceRow.metric_key, TeachingEvidenceRow.created_at)
                )
            )
            .scalars()
            .all()
        )
        return {
            "profile_id": str(prow.id),
            "class_id": str(prow.class_id),
            "session_id": str(prow.session_id),
            "status": prow.status,
            "revision": prow.revision,
            "body": _jsonable(prow.body),
            "computed_at": prow.computed_at.isoformat() if prow.computed_at else None,
            "expires_at": prow.expires_at.isoformat() if prow.expires_at else None,
            "coverage": _jsonable(prow.coverage),
            "warnings": list(prow.warnings or []),
            "evidence": [
                {
                    "evidence_id": str(r.id),
                    "ev_kind": r.ev_kind,
                    "metric_key": r.metric_key,
                    "value": _jsonable(r.value),
                    "refs": _jsonable(r.refs),
                    "sample_size": r.sample_size,
                    "calc_version": r.calc_version,
                    "metrics_fingerprint": r.metrics_fingerprint,
                    "source": r.source,
                    "computed_at": r.computed_at.isoformat() if r.computed_at else None,
                }
                for r in rows
            ],
        }

    @staticmethod
    async def _latest_profile(
        session: AsyncSession, class_id: uuid.UUID, session_id: uuid.UUID
    ) -> ClassLearningProfileRow | None:
        """该（班，会话）最新 published 画像（自动重算目标；无 → None）。"""
        return (
            (
                await session.execute(
                    select(ClassLearningProfileRow)
                    .where(
                        ClassLearningProfileRow.class_id == class_id,
                        ClassLearningProfileRow.session_id == session_id,
                        ClassLearningProfileRow.status == sm.PROFILE_PUBLISHED,
                    )
                    .order_by(ClassLearningProfileRow.revision.desc())
                    .limit(1)
                )
            )
            .scalar_one_or_none()
        )

    @staticmethod
    async def _profile_class_id(session: AsyncSession, profile_id: str) -> str:
        """读路径两段式成员裁决：先取画像所属班级，再对该班做成员裁决。"""
        try:
            pid = uuid.UUID(profile_id)
        except ValueError as exc:
            raise ProfileNotFoundError(profile_id) from exc
        row = (
            (await session.execute(select(ClassLearningProfileRow).where(ClassLearningProfileRow.id == pid)))
            .scalar_one_or_none()
        )
        if row is None:
            raise ProfileNotFoundError(profile_id)
        return str(row.class_id)

    # ── R19 interpret_profile：AI 解释 proposal（失败不影响基础统计）──────────

    async def interpret_profile(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        actor_role_hint: str,
        session_id: str,
        profile_id: str,
        idempotency_key: str,
        request_hash: str,
        interpreter: ProfileInterpreter | None = None,
    ) -> dict[str, Any]:
        """AI 解释 proposal：只读画像聚合统计 → proposal 证据行（ai_suggestion）。

        - 解释输入只含聚合统计块（metrics/aggregation/warnings），零 PII；
        - 解释器缺失/失败 → basic_mode（画像与统计零改动，不冒充成功）；
        - proposal 是 ai_suggestion 证据行：教师确认前不进入正式 evidence_refs。
        """
        _validate_idem(idempotency_key)
        _require_teaching_role(actor_role_hint)

        async def _exec() -> dict[str, Any]:
            class_id = await self._profile_class_id(session, profile_id)
            member_role, srow = await self._verify_profile_context(
                session, adapter=adapter, token=token, class_id=class_id, session_id=session_id
            )
            prow = await self._lock_profile(session, uuid.UUID(profile_id))
            if prow.class_id != srow.class_id:
                raise MembershipDeniedError("画像不属于该班级（跨班访问拒绝）")
            if prow.session_id != srow.id:
                raise MembershipDeniedError("画像不属于该会话（跨会话访问拒绝）")
            if prow.status != sm.PROFILE_PUBLISHED:
                raise InvalidTransitionError("画像", prow.status, "interpret")

            body = prow.body or {}
            payload = {
                "profile_id": str(prow.id),
                "revision": prow.revision,
                "metrics": body.get("metrics"),
                "aggregation": body.get("aggregation"),
                "warnings": list(body.get("warnings") or []),
                "teacher_actions": list(body.get("teacher_actions") or []),
            }

            def _fail(reason: str) -> dict[str, Any]:
                return {"basic_mode": True, "reason": reason, "proposal": None, "evidence_id": None}

            if interpreter is None:
                cmd_id = await self._log_command(
                    session, session_id=srow.id, blueprint_id=None,
                    command_type="interpret_profile", actor_id=actor_id, actor_role=member_role,
                    class_id=str(prow.class_id), idempotency_key=idempotency_key,
                    request_hash=request_hash, expected_revision=None,
                    result_status="applied",
                    payload={"profile_id": str(prow.id), "basic_mode": True, "reason": "interpreter_not_configured"},
                )
                await self._log_events(
                    session, session_id=srow.id, command_id=cmd_id, blueprint_id=None,
                    events=[("profile.interpretation.failed", {"profile_id": str(prow.id), "reason": "interpreter_not_configured"})],
                    row_seq=srow.last_event_seq, revision=prow.revision,
                )
                return _fail("interpreter_not_configured")

            try:
                proposal = await interpreter.interpret(payload)
                if not isinstance(proposal, dict):
                    raise InterpreterUnavailableError("proposal 必须为 dict")
            except Exception as exc:  # noqa: BLE001 —— AI 失败绝不能影响基础统计
                reason = f"interpreter_error:{type(exc).__name__}"
                cmd_id = await self._log_command(
                    session, session_id=srow.id, blueprint_id=None,
                    command_type="interpret_profile", actor_id=actor_id, actor_role=member_role,
                    class_id=str(prow.class_id), idempotency_key=idempotency_key,
                    request_hash=request_hash, expected_revision=None,
                    result_status="applied",
                    payload={"profile_id": str(prow.id), "basic_mode": True, "reason": reason},
                )
                await self._log_events(
                    session, session_id=srow.id, command_id=cmd_id, blueprint_id=None,
                    events=[("profile.interpretation.failed", {"profile_id": str(prow.id), "reason": reason})],
                    row_seq=srow.last_event_seq, revision=prow.revision,
                )
                return _fail(reason)

            ev = TeachingEvidenceRow(
                class_id=prow.class_id,
                session_id=srow.id,
                profile_id=prow.id,
                ev_kind="interpretation",
                metric_key="interpretation:proposal",
                status="valid",
                source="ai_suggestion",
                value=_jsonable({"proposal": proposal}),
                refs=[],
                sample_size=0,
                calc_version=str((body.get("metrics") or {}).get("calc_version") or "unknown"),
                metrics_fingerprint=(body.get("metrics") or {}).get("metrics_fingerprint"),
                computed_at=_now(),
                actor_id=uuid.UUID(actor_id),
            )
            session.add(ev)
            await session.flush()
            cmd_id = await self._log_command(
                session, session_id=srow.id, blueprint_id=None,
                command_type="interpret_profile", actor_id=actor_id, actor_role=member_role,
                class_id=str(prow.class_id), idempotency_key=idempotency_key,
                request_hash=request_hash, expected_revision=None,
                result_status="applied",
                payload={"profile_id": str(prow.id), "basic_mode": False, "evidence_id": str(ev.id)},
            )
            await self._log_events(
                session, session_id=srow.id, command_id=cmd_id, blueprint_id=None,
                events=[("profile.interpretation.proposed", {"profile_id": str(prow.id), "evidence_id": str(ev.id)})],
                row_seq=srow.last_event_seq, revision=prow.revision,
            )
            return {
                "basic_mode": False,
                "reason": None,
                "proposal": proposal,
                "evidence_id": str(ev.id),
            }

        return await self._dispatch(
            session,
            operation="interpret_profile",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            principal=actor_id,
            session_id_hint=uuid.UUID(session_id),
            run=_exec,
        )

    # ── R20 propose_blueprint_revision：已确认蓝图 + 画像 → 修订提案 + diff ────

    async def propose_blueprint_revision(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        session_id: str,
        blueprint_id: str,
        expected_revision: int,
        idempotency_key: str,
        request_hash: str,
        context: TextbookTeachingContext,
        profile: VersionedClassLearningProfile,
        llm_generate: LlmGenerate,
        max_context_tokens: int = 8000,
    ) -> dict[str, Any]:
        """已确认蓝图 + published 画像 → 修订提案（schema 校验 + grounding + 8 维 diff）。

        铁律：
        - 只允许修订已确认蓝图（confirmed → proposal）；旧 revision 不可变保留；
        - 提案写入 verdict=proposal——未经教师 confirm_blueprint 不得成为 approved；
        - 低覆盖/旧 Profile/证据冲突 warning 随提案保留（diff.warnings）；
        - 画像必须 published（未发布/已归档不可作证据）。
        """
        _validate_idem(idempotency_key)
        if profile.status != sm.PROFILE_PUBLISHED:
            raise ApiError(
                ErrorCode.VALIDATION,
                f"学情证据画像状态必须为 published 才能生成修订提案（当前 {profile.status}）",
            )

        async def _exec() -> dict[str, Any]:
            srow = await self._lock_session(session, session_id)
            brow = await self._lock_blueprint(session, uuid.UUID(blueprint_id))
            if brow.session_id != srow.id:
                raise MembershipDeniedError("蓝图不属于该会话（跨会话访问拒绝）")
            member_role = await self._verify_membership(
                adapter, token=token, class_id=_class_ref(srow)
            )
            self._expect("蓝图", "蓝图", expected_revision, brow.revision)
            if brow.status != sm.BLUEPRINT_CONFIRMED:
                raise InvalidTransitionError("蓝图", brow.status, sm.BLUEPRINT_PROPOSAL)

            profile_ref = self._profile_evidence_ref(profile)
            base_body = dict(_jsonable(brow.body))
            base_body["blueprint_id"] = str(brow.id)
            base_body["revision"] = brow.revision
            proposal, diff = await project_blueprint_revision(
                context,
                base_blueprint_body=base_body,
                profile_ref=profile_ref,
                llm_generate=llm_generate,
                max_context_tokens=max_context_tokens,
            )

            prev_no = brow.revision
            brow.status = sm.BLUEPRINT_PROPOSAL
            brow.revision += 1
            brow.body = _jsonable(proposal.model_dump(mode="json"))
            brow.confirmed_at = None
            brow.confirmed_by = None
            session.add(
                BlueprintRevisionRow(
                    blueprint_id=brow.id,
                    revision_no=brow.revision,
                    body=_jsonable(brow.body),
                    verdict=sm.BLUEPRINT_PROPOSAL,
                    source="ai_suggestion",
                    actor_id=uuid.UUID(actor_id),
                    actor_role=member_role,
                    parent_revision_no=prev_no,
                    note="blueprint revision proposal (R20)",
                )
            )
            cmd_id = await self._log_command(
                session,
                session_id=srow.id,
                blueprint_id=brow.id,
                command_type="propose_blueprint_revision",
                actor_id=actor_id,
                actor_role=member_role,
                class_id=_class_ref(srow),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                expected_revision=expected_revision,
                result_status="applied",
                payload={
                    "revision_no": brow.revision,
                    "profile_id": str(profile.profile_id),
                    "profile_revision": profile.revision,
                    "diff_summary": diff.summary.model_dump(mode="json"),
                    "warnings": list(diff.warnings),
                },
            )
            await self._log_events(
                session,
                session_id=srow.id,
                command_id=cmd_id,
                blueprint_id=brow.id,
                events=[
                    (
                        "blueprint.revision_proposed",
                        {
                            "actor_id": actor_id,
                            "revision_no": brow.revision,
                            "profile_id": str(profile.profile_id),
                            "profile_revision": profile.revision,
                            "diff_summary": diff.summary.model_dump(mode="json"),
                            "warnings": list(diff.warnings),
                        },
                    )
                ],
                row_seq=srow.last_event_seq,
                revision=brow.revision,
            )
            return {
                "blueprint_id": str(brow.id),
                "session_id": str(srow.id),
                "status": brow.status,
                "revision": brow.revision,
                "proposal": proposal.model_dump(mode="json"),
                "diff": diff.model_dump(mode="json"),
            }

        return await self._dispatch(
            session,
            operation="propose_blueprint_revision",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            principal=actor_id,
            session_id_hint=uuid.UUID(session_id),
            run=_exec,
        )

    # ── R20 project_specs：approved blueprint + immutable context → 双 Spec ────

    async def project_specs(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        actor_id: str,
        session_id: str,
        blueprint_id: str,
        expected_revision: int,
        idempotency_key: str,
        request_hash: str,
        context: TextbookTeachingContext,
        profile_ref: dict[str, Any],
        lineage: ProjectionRunLineage,
        kinds: tuple[str, ...] = ("lesson_plan", "deck"),
        manage_idempotency: bool = True,
    ) -> dict[str, Any]:
        """从同一 approved blueprint revision + 不可变 context snapshot 投影双 Spec。

        - 未确认蓝图 → 409（未确认不能下发）；
        - 投影确定性 + 幂等：projection_hash（输入规范化）UNIQUE(blueprint_id, kind, hash)，
          同输入重试 → 重放既有 spec_target_id；
        - 双 Spec 共享 approved blueprint/context hash 锚点（projection 块 + 列冗余）；
        - LessonPlanSpec → tp_lesson_plan_specs；TeachingDeckSpec → ppt_v2_deck_specs
          （新写入零 page_refs，SourceLocator 引用）。
        """
        _validate_idem(idempotency_key)
        unknown = set(kinds) - {"lesson_plan", "deck"}
        if unknown:
            raise ApiError(
                ErrorCode.VALIDATION,
                f"不支持的投影类型：{sorted(unknown)}（允许 lesson_plan/deck）",
            )

        async def _exec() -> dict[str, Any]:
            srow = await self._lock_session(session, session_id)
            brow = await self._lock_blueprint(session, uuid.UUID(blueprint_id))
            if brow.session_id != srow.id:
                raise MembershipDeniedError("蓝图不属于该会话（跨会话访问拒绝）")
            member_role = await self._verify_membership(
                adapter, token=token, class_id=_class_ref(srow)
            )
            self._expect("蓝图", "蓝图", expected_revision, brow.revision)
            if brow.status != sm.BLUEPRINT_CONFIRMED:
                raise InvalidTransitionError("蓝图", brow.status, "确认后投影")  # 未确认不能下发

            body = _jsonable(brow.body)
            # 时长一致性（数据红线）：LLM 推导的环节时长合计可能 ≠ 课时预算（45×课时数）。
            # 投影前确定性归一化到预算（largest-remainder 整数分配；Σ 恒等于预算、
            # 每环节 ≥1 分钟、不改变相对比例）；deck 与教案共享同一归一化 body → 两产物一致。
            from .conversation.node_ops import lesson_budget_minutes

            body = normalize_flow_durations(body, lesson_budget_minutes(srow))
            results: dict[str, Any] = {}
            for kind in kinds:
                inputs = ProjectionInputs(
                    blueprint_id=str(brow.id),
                    blueprint_revision=brow.revision,
                    blueprint_body=body,
                    context_snapshot_hash=context.snapshot_hash,
                    profile_ref=_jsonable(profile_ref),
                )
                projection_hash = compute_projection_hash(inputs)
                existing = await self._find_projection(session, brow.id, kind, projection_hash)
                if existing is not None:
                    results[kind] = {
                        "projection_hash": projection_hash,
                        "spec_id": str(existing.spec_target_id) if existing.spec_target_id else None,
                        "replayed": True,
                    }
                    continue

                run_hash = projection_hash
                spec_id = str(uuid.uuid4())
                if kind == "lesson_plan":
                    lp = project_lesson_plan_spec(
                        spec_id=spec_id,
                        blueprint_id=str(brow.id),
                        blueprint_revision=brow.revision,
                        blueprint_body=body,
                        context=context,
                        profile_ref=_jsonable(profile_ref),
                        lineage=lineage,
                        run_hash=run_hash,
                        spec_revision=await self._next_lesson_plan_revision(session, brow.id),
                    )
                    session.add(
                        LessonPlanSpecRow(
                            id=uuid.UUID(spec_id),
                            session_id=srow.id,
                            blueprint_id=brow.id,
                            schema_version=lp.schema_version,
                            purpose="lesson_plan",
                            revision=lp.revision,
                            body=_jsonable(lp.model_dump(mode="json")),
                            approved_blueprint_revision=brow.revision,
                            context_snapshot_hash=context.snapshot_hash,
                            lineage=_jsonable(asdict(lineage)),
                        )
                    )
                    results[kind] = {
                        "projection_hash": projection_hash,
                        "spec_id": spec_id,
                        "replayed": False,
                        "schema_version": lp.schema_version,
                        "revision": lp.revision,
                        "spec": lp.model_dump(mode="json"),
                    }
                else:
                    deck = project_deck_spec(
                        spec_id=spec_id,
                        blueprint_id=str(brow.id),
                        blueprint_revision=brow.revision,
                        blueprint_body=body,
                        context=context,
                        profile_ref=_jsonable(profile_ref),
                        lineage=lineage,
                        run_hash=run_hash,
                    )
                    # Phase E（Golden B Gate §6）：发布前确定性整形（封面/去重/薄页/公式/
                    # 目标补全/附图）。只整形不改教学事实；失败回滚原 spec 并记 warning；
                    # 报告由 deck_polish 内部 logging 记录（不改 API 契约）。
                    from .specs.deck_polish import polish_deck_spec

                    deck, _polish_report = polish_deck_spec(deck, max_figures=3)
                    # R3e：MATH_RENDER 图形资产字节落对象存储（导出链 _inline_assets 读字节
                    # 下发引擎；内容寻址幂等）。失败不阻断投影（导出侧按缺资产诚实降级）。
                    from .specs.math_figures import ensure_asset_stored

                    for deck_asset in deck.assets:
                        ensure_asset_stored(deck_asset)
                    session.add(
                        PptV2DeckSpecRecord(
                            id=uuid.UUID(spec_id),
                            school_id=srow.class_id,
                            schema_version=deck.schema_version,
                            revision=deck.revision,
                            spec=_jsonable(deck.model_dump(mode="json")),
                        )
                    )
                    results[kind] = {
                        "projection_hash": projection_hash,
                        "spec_id": spec_id,
                        "replayed": False,
                        "schema_version": deck.schema_version,
                        "revision": deck.revision,
                        "spec": deck.model_dump(mode="json"),
                    }

                session.add(
                    SpecProjectionRow(
                        session_id=srow.id,
                        blueprint_id=brow.id,
                        kind=kind,
                        spec_target_id=uuid.UUID(spec_id),
                        projection_hash=projection_hash,
                        approved_blueprint_revision=brow.revision,
                        context_snapshot_hash=context.snapshot_hash,
                        lineage=_jsonable(asdict(lineage)),
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                    )
                )

            cmd_id = await self._log_command(
                session,
                session_id=srow.id,
                blueprint_id=brow.id,
                command_type="project_specs",
                actor_id=actor_id,
                actor_role=member_role,
                class_id=_class_ref(srow),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                expected_revision=expected_revision,
                result_status="applied",
                payload={
                    "kinds": sorted(kinds),
                    "approved_blueprint_revision": brow.revision,
                    "context_snapshot_hash": context.snapshot_hash,
                    "hashes": {k: r["projection_hash"] for k, r in results.items()},
                },
            )
            await self._log_events(
                session,
                session_id=srow.id,
                command_id=cmd_id,
                blueprint_id=brow.id,
                events=[
                    (
                        "specs.projected",
                        {
                            "actor_id": actor_id,
                            "blueprint_id": str(brow.id),
                            "approved_blueprint_revision": brow.revision,
                            "context_snapshot_hash": context.snapshot_hash,
                            "kinds": sorted(kinds),
                            "hashes": {k: r["projection_hash"] for k, r in results.items()},
                        },
                    )
                ],
                row_seq=srow.last_event_seq,
                revision=brow.revision,
            )
            return {
                "session_id": str(srow.id),
                "blueprint_id": str(brow.id),
                "approved_blueprint_revision": brow.revision,
                "context_snapshot_hash": context.snapshot_hash,
                "results": results,
            }

        # ConversationService already owns an outer idempotency/transaction
        # envelope for action dispatch.  Re-entering this service's commit-
        # based envelope there would close the outer savepoint before the
        # workspace can be assembled.  Direct service callers retain the
        # original envelope by default; nested callers opt into the pure run.
        if not manage_idempotency:
            return await _exec()
        return await self._dispatch(
            session,
            operation="project_specs",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            principal=actor_id,
            session_id_hint=uuid.UUID(session_id),
            run=_exec,
        )

    # ── 查询 ────────────────────────────────────────────────────────────────

    async def list_authorized_sessions(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        teacher_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """只返回当前授权班级的会话：逐会话向 8000 重验成员关系，被拒绝的静默过滤。"""
        rows = (
            (
                await session.execute(
                    select(TeachingSessionRow)
                    .where(TeachingSessionRow.teacher_id == uuid.UUID(teacher_id))
                    .order_by(TeachingSessionRow.updated_at.desc())
                    .limit(min(limit, 200))
                )
            )
            .scalars()
            .all()
        )
        items: list[dict[str, Any]] = []
        names = await load_class_names(session, {row.class_id for row in rows})
        for row in rows:
            try:
                await self._verify_membership(adapter, token=token, class_id=_class_ref(row))
            except MembershipDeniedError:
                continue  # 班已移除 → 不返回（数据最小化）
            items.append(session_dto(row, class_name=names.get(_class_ref(row))))
        return items

    async def get_session_detail(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        teacher_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        row = await self._get_session(session, session_id)
        if str(row.teacher_id) != teacher_id:
            raise MembershipDeniedError("非本人会话（跨教师访问拒绝）")
        await self._verify_membership(adapter, token=token, class_id=_class_ref(row))
        blueprints = (
            (
                await session.execute(
                    select(TeachingBlueprintRow)
                    .where(TeachingBlueprintRow.session_id == row.id)
                    .order_by(TeachingBlueprintRow.created_at)
                )
            )
            .scalars()
            .all()
        )
        names = await load_class_names(session, {row.class_id})
        return {
            **session_dto(row, class_name=names.get(_class_ref(row))),
            "blueprints": [blueprint_dto(b) for b in blueprints],
        }

    async def list_events(
        self,
        session: AsyncSession,
        *,
        adapter: Api8000Adapter,
        token: str,
        teacher_id: str,
        session_id: str,
        limit: int = 500,
    ) -> dict[str, Any]:
        row = await self._get_session(session, session_id)
        if str(row.teacher_id) != teacher_id:
            raise MembershipDeniedError("非本人会话（跨教师访问拒绝）")
        await self._verify_membership(adapter, token=token, class_id=_class_ref(row))
        events = (
            (
                await session.execute(
                    select(SessionEventRow)
                    .where(SessionEventRow.session_id == row.id)
                    .order_by(SessionEventRow.seq)
                    .limit(min(limit, 1000))
                )
            )
            .scalars()
            .all()
        )
        return {
            "session_id": str(row.id),
            "count": len(events),
            "items": [event_dto(e) for e in events],
        }

    # ── 内部工具 ────────────────────────────────────────────────────────────

    @staticmethod
    async def _session_exists(session: AsyncSession, session_id: uuid.UUID) -> bool:
        return (
            (
                await session.execute(
                    select(TeachingSessionRow.id).where(TeachingSessionRow.id == session_id)
                )
            ).first()
            is not None
        )

    @staticmethod
    async def _get_session(session: AsyncSession, session_id: str) -> TeachingSessionRow:
        try:
            sid = uuid.UUID(session_id)
        except ValueError as exc:
            raise SessionNotFoundError(session_id) from exc
        row = (
            (
                await session.execute(
                    select(TeachingSessionRow).where(TeachingSessionRow.id == sid)
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            raise SessionNotFoundError(session_id)
        return row

    @staticmethod
    async def _lock_session(session: AsyncSession, session_id: str) -> TeachingSessionRow:
        try:
            sid = uuid.UUID(session_id)
        except ValueError as exc:
            raise SessionNotFoundError(session_id) from exc
        row = (
            (
                await session.execute(
                    select(TeachingSessionRow)
                    .where(TeachingSessionRow.id == sid)
                    .with_for_update()
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            raise SessionNotFoundError(session_id)
        return row

    @staticmethod
    async def _lock_blueprint(session: AsyncSession, blueprint_id: uuid.UUID) -> TeachingBlueprintRow:
        row = (
            (
                await session.execute(
                    select(TeachingBlueprintRow)
                    .where(TeachingBlueprintRow.id == blueprint_id)
                    .with_for_update()
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            raise BlueprintNotFoundError(str(blueprint_id))
        return row

    @staticmethod
    async def _lock_profile(session: AsyncSession, profile_id: uuid.UUID) -> ClassLearningProfileRow:
        row = (
            (
                await session.execute(
                    select(ClassLearningProfileRow)
                    .where(ClassLearningProfileRow.id == profile_id)
                    .with_for_update()
                )
            )
            .scalar_one_or_none()
        )
        if row is None:
            raise ProfileNotFoundError(str(profile_id))
        return row

    @staticmethod
    def _profile_evidence_ref(profile: VersionedClassLearningProfile) -> dict[str, Any]:
        """版本化画像 → 修订提案证据引用（时效/覆盖/warnings 随提案保留）。"""
        return {
            "profile_id": str(profile.profile_id),
            "profile_revision": profile.revision,
            "computed_at": profile.freshness.computed_at.isoformat(),
            "expires_at": (
                profile.freshness.expires_at.isoformat() if profile.freshness.expires_at else None
            ),
            "stale": profile.freshness.stale,
            "coverage_ratio": profile.evidence.coverage_ratio,
            "warnings": list(profile.warnings),
        }

    @staticmethod
    async def _find_projection(
        session: AsyncSession,
        blueprint_id: uuid.UUID,
        kind: str,
        projection_hash: str,
    ) -> SpecProjectionRow | None:
        """投影幂等查询：同 (blueprint, kind, projection_hash) → 重放既有投影。"""
        return (
            (
                await session.execute(
                    select(SpecProjectionRow).where(
                        SpecProjectionRow.blueprint_id == blueprint_id,
                        SpecProjectionRow.kind == kind,
                        SpecProjectionRow.projection_hash == projection_hash,
                    )
                )
            )
            .scalar_one_or_none()
        )

    @staticmethod
    async def _next_lesson_plan_revision(session: AsyncSession, blueprint_id: uuid.UUID) -> int:
        """教案 spec 的下一版本号（同一蓝图下 revision 单调递增）。

        教案是「活的产物」：UNIQUE(blueprint_id, revision) 决定同蓝图不能有两份同版本
        spec。教师改动课堂设计后再点同步是**新版本**（而非新文档），故取既有 max+1；
        若不递增，第二次同步会撞唯一约束 → 500（2026-09-21 实测事故）。
        """
        current = (
            await session.execute(
                select(func.max(LessonPlanSpecRow.revision)).where(
                    LessonPlanSpecRow.blueprint_id == blueprint_id
                )
            )
        ).scalar()
        return int(current or 0) + 1

    @staticmethod
    def _expect(entity: str, kind: str, expected: int | None, actual: int) -> None:
        if expected is not None and expected != actual:
            raise RevisionConflictError(kind, entity, expected, actual)

    @staticmethod
    async def _log_command(
        session: AsyncSession,
        *,
        session_id: uuid.UUID,
        command_type: str,
        actor_id: str,
        actor_role: str,
        class_id: str | None,
        idempotency_key: str,
        request_hash: str,
        result_status: str,
        expected_revision: int | None,
        payload: dict[str, Any],
        blueprint_id: uuid.UUID | None = None,
        error_code: int | None = None,
        error_message: str | None = None,
    ) -> uuid.UUID:
        trace = get_current()
        # 子表与父表之间没有 relationship，UoW 无法自行排序：先把挂起的父行
        # （新建的 TeachingSession / Blueprint）落库，再插命令行，否则会撞
        # tp_session_commands_session_id_fkey / tp_blueprint_revisions_blueprint_id_fkey。
        await session.flush()
        cmd = SessionCommandRow(
            session_id=session_id,
            blueprint_id=blueprint_id,
            command_type=command_type,
            actor_id=uuid.UUID(actor_id),
            actor_role=actor_role,
            class_id=uuid.UUID(class_id) if class_id else None,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            expected_revision=expected_revision,
            result_status=result_status,
            error_code=error_code,
            error_message=error_message,
            payload=_jsonable(payload),
            trace_id=trace.trace_id if trace else None,
            correlation_id=trace.correlation_id if trace else None,
        )
        session.add(cmd)
        await session.flush()
        return cmd.id

    @staticmethod
    async def _log_events(
        session: AsyncSession,
        *,
        session_id: uuid.UUID,
        command_id: uuid.UUID,
        events: list[tuple[str, dict[str, Any]]],
        row_seq: int,
        revision: int,
        blueprint_id: uuid.UUID | None = None,
    ) -> None:
        """追加事件：command.applied 为链头 → 业务事件以它为 parent；推进会话 last_event_seq。"""
        parent: uuid.UUID | None = None
        seq = row_seq
        for event_type, payload in [("command.applied", {})] + events:
            seq += 1
            ev_id = uuid.uuid4()
            ev = SessionEventRow(
                id=ev_id,
                session_id=session_id,
                command_id=command_id,
                blueprint_id=blueprint_id,
                seq=seq,
                event_type=event_type,
                payload=_jsonable(payload),
                parent_event_id=parent,
                revision=revision,
            )
            session.add(ev)
            if parent is None:
                parent = ev_id  # 链头成为后续事件的 parent
        srow = (
            (
                await session.execute(
                    select(TeachingSessionRow)
                    .where(TeachingSessionRow.id == session_id)
                    .with_for_update()
                )
            )
            .scalar_one_or_none()
        )
        if srow is not None:
            srow.last_event_seq = seq
            # Keep the timestamp materialized after the flush.  The model's
            # server-side on-update otherwise expires this scalar, and an
            # async workspace projection would attempt forbidden implicit IO
            # while serializing the same session row.
            srow.updated_at = _now()

    # ── 幂等分发壳 ──────────────────────────────────────────────────────────

    async def _dispatch(
        self,
        session: AsyncSession,
        *,
        operation: str,
        idempotency_key: str,
        request_hash: str,
        principal: str,
        session_id_hint: uuid.UUID | None,
        run: _RunFn,
    ) -> dict[str, Any]:
        """幂等壳：reserve → run（同事务）→ complete；失败 → fail() 释放 + 尽力审计。

        - principal = actor_id（用户维度隔离幂等键，跨用户同 key 不串）；
        - 失败 attempt 审计仅在已存在真实会话时落命令行（避免悬空 FK）；
        - 同 key 同载荷 → 重放首响应；同 key 不同载荷 → 409（reserve 内部裁决）。
        """
        reserved, existing = await ReliabilityIdempotency.reserve(
            session,
            owner_service=OWNER_SERVICE,
            operation=operation,
            principal=principal,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if not reserved:
            if existing is not None and existing.status == "COMPLETED":
                # 同键同载荷 → 窗口内重放首响应（不重复副作用）；尽量补一条重放审计
                if session_id_hint is not None:
                    await self._log_command(
                        session,
                        session_id=session_id_hint,
                        command_type=operation,
                        actor_id="00000000-0000-0000-0000-000000000000",
                        actor_role="system",
                        class_id="00000000-0000-0000-0000-000000000000",
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        expected_revision=None,
                        result_status="replayed",
                        payload={},
                    )
                await session.commit()
                return dict(existing.response or {})
            raise IdempotencyInFlightError(
                f"幂等键在途（{operation} key={idempotency_key}）——并发请求或首者未落定"
            )

        try:
            # 嵌套事务包裹 run：任何异常 → savepoint 回滚，杜绝部分业务行残留
            async with session.begin_nested():
                response = await run()
        except (ApiError, ValueError) as exc:
            code = ErrorCode.CONFLICT
            err = str(exc)
            if isinstance(exc, ApiError):
                code = exc.code
            if session_id_hint is not None and await self._session_exists(session, session_id_hint):
                await self._log_command(
                    session,
                    session_id=session_id_hint,
                    command_type=operation,
                    actor_id="00000000-0000-0000-0000-000000000000",
                    actor_role="unknown",
                    class_id="00000000-0000-0000-0000-000000000000",
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    expected_revision=None,
                    result_status=(
                        "denied"
                        if code == ErrorCode.ROLE_DENIED
                        else "invalid_transition"
                        if code == ErrorCode.CONFLICT
                        else "conflict"
                    ),
                    payload={"error": err[:500]},
                    error_code=int(code),
                    error_message=err[:500],
                )
            await ReliabilityIdempotency.fail(
                session,
                owner_service=OWNER_SERVICE,
                operation=operation,
                principal=principal,
                idempotency_key=idempotency_key,
            )
            await session.commit()
            if isinstance(exc, ApiError):
                raise exc
            raise ApiError(ErrorCode.CONFLICT, err) from exc

        await ReliabilityIdempotency.complete(
            session,
            owner_service=OWNER_SERVICE,
            operation=operation,
            principal=principal,
            idempotency_key=idempotency_key,
            response=response,
        )
        await session.commit()
        return response


__all__ = [
    "TeacherPrepService",
    "SessionNotFoundError",
    "BlueprintNotFoundError",
    "ProfileNotFoundError",
    "RevisionConflictError",
    "InvalidTransitionError",
    "MembershipDeniedError",
    "AuthorityUnreachableError",
    "IdempotencyInFlightError",
    "session_dto",
    "load_class_names",
    "blueprint_dto",
    "event_dto",
    "request_hash_of",
]
