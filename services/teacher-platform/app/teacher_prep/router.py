"""R14 Teacher Prep API（会话/蓝图骨架；不实现诊断/学情/PPT/前端大页面）。

挂载：`/api/teacher-v3/prep/...`（在 api/routes.py 聚合）。
鉴权：get_current_user（JWT role=teacher，40301）；
成员裁决：service 层每次高风险命令向 8000 重验（403/404→40301，5xx→503）。
幂等：请求体带 `idempotency_key`；同 key 同载荷重放首响应，不同载荷 409。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user, get_session
from app.core.response import ok
from app.core.security import UserToken
from app.teacher_prep.profile.schema import AttemptPolicyMetrics, TaskScopeInfo
from app.teacher_prep.service import (
    TeacherPrepService,
    request_hash_of,
)
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter, HttpxApiTransport

router = APIRouter()

CurrentUser = Annotated[UserToken, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_session)]

_IDEM_MIN = 8


# ── 请求模型 ─────────────────────────────────────────────────────────────────

class _Idem(BaseModel):
    idempotency_key: str = Field(min_length=_IDEM_MIN, max_length=128)


class CreateSessionRequest(_Idem):
    class_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    course_id: str | None = None
    schedule_id: str | None = None
    title: str | None = Field(default=None, max_length=200)
    intent: dict | None = None
    context_snapshot_id: str | None = None
    prompt_release_id: str | None = None
    ai_run_id: str | None = None
    pending_action_id: str | None = None


class SessionCommandRequest(_Idem):
    session_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    expected_revision: int = Field(ge=1)


class OpenSessionRequest(SessionCommandRequest):
    pass


class CancelSessionRequest(SessionCommandRequest):
    reason: str | None = Field(default=None, max_length=500)


class ArchiveSessionRequest(SessionCommandRequest):
    pass


class ProposeBlueprintRequest(_Idem):
    session_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    body: dict = Field(...)
    title: str | None = Field(default=None, max_length=200)
    blueprint_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    expected_revision: int | None = Field(default=None, ge=1)
    source: str = Field(default="ai_suggestion", pattern=r"^(ai_suggestion|teacher|system)$")
    note: str | None = Field(default=None, max_length=500)


class AdjudicateBlueprintRequest(_Idem):
    blueprint_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    expected_revision: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=500)
    reason: str | None = Field(default=None, max_length=500)


class RecalculateProfileRequest(_Idem):
    """R19 确定性重算画像（统计由 8100 纯函数计算；AI 不参与数字）。"""

    session_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    class_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    assignment_id: str = Field(min_length=8, max_length=64)  # 8000 assignments.id
    profile_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    expected_revision: int | None = Field(default=None, ge=1)
    expires_at: str | None = None  # ISO 8601；None=默认时效
    task_scope: dict | None = None  # TaskScopeInfo 载荷（task_id/task_kind/...）
    attempt_policy: dict | None = None  # AttemptPolicyMetrics 载荷（known/attempts_allowed/...）
    kp_by_item: dict[str, list[int]] | None = None  # kp_code → item_nos（前置知识映射）
    note: str | None = Field(default=None, max_length=500)


class InterpretProfileRequest(_Idem):
    session_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    profile_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")


def _request_hash(request: Request, body: dict | None) -> str:
    return request_hash_of(request.method, request.url.path, body)


def _adapter() -> Api8000Adapter:
    from app.core.config import get_settings
    from app.teacher_prep_v2.adapters.mapping import InMemoryMappingRegistry

    s = get_settings()
    return Api8000Adapter(HttpxApiTransport(s.api8000_base_url), InMemoryMappingRegistry())


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return ""


_service = TeacherPrepService()


# ── 会话 ─────────────────────────────────────────────────────────────────────

@router.get("/prep/sessions")
async def list_sessions(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=100, ge=1, le=200),
) -> dict:
    items = await _service.list_authorized_sessions(
        session, adapter=_adapter(), token=_bearer_token(request), teacher_id=user.id, limit=limit
    )
    return ok({"items": items}, request_id=request.state.request_id)


@router.post("/prep/sessions")
async def create_session(
    request: Request,
    body: CreateSessionRequest,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    result = await _service.create_session(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        actor_id=user.id,
        class_id=body.class_id,
        idempotency_key=body.idempotency_key,
        request_hash=_request_hash(request, body.model_dump()),
        course_id=body.course_id,
        schedule_id=body.schedule_id,
        title=body.title,
        intent=body.intent,
        context_snapshot_id=body.context_snapshot_id,
        prompt_release_id=body.prompt_release_id,
        ai_run_id=body.ai_run_id,
        pending_action_id=body.pending_action_id,
    )
    return ok(result, request_id=request.state.request_id)


@router.post("/prep/sessions/open")
async def open_session(
    request: Request,
    body: OpenSessionRequest,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    result = await _service.open_session(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        actor_id=user.id,
        session_id=body.session_id,
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
        request_hash=_request_hash(request, body.model_dump()),
    )
    return ok(result, request_id=request.state.request_id)


@router.post("/prep/sessions/cancel")
async def cancel_session(
    request: Request,
    body: CancelSessionRequest,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    result = await _service.cancel_session(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        actor_id=user.id,
        session_id=body.session_id,
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
        request_hash=_request_hash(request, body.model_dump()),
        reason=body.reason,
    )
    return ok(result, request_id=request.state.request_id)


@router.post("/prep/sessions/archive")
async def archive_session(
    request: Request,
    body: ArchiveSessionRequest,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    result = await _service.archive_session(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        actor_id=user.id,
        session_id=body.session_id,
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
        request_hash=_request_hash(request, body.model_dump()),
    )
    return ok(result, request_id=request.state.request_id)


@router.get("/prep/sessions/{session_id}")
async def get_session(
    session_id: str,
    request: Request,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    result = await _service.get_session_detail(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        teacher_id=user.id,
        session_id=session_id,
    )
    return ok(result, request_id=request.state.request_id)


@router.get("/prep/sessions/{session_id}/events")
async def list_session_events(
    session_id: str,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    limit: int = Query(default=500, ge=1, le=1000),
) -> dict:
    result = await _service.list_events(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        teacher_id=user.id,
        session_id=session_id,
        limit=limit,
    )
    return ok(result, request_id=request.state.request_id)


# ── 蓝图 ─────────────────────────────────────────────────────────────────────

@router.post("/prep/blueprints/propose")
async def propose_blueprint(
    request: Request,
    body: ProposeBlueprintRequest,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    result = await _service.propose_blueprint(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        actor_id=user.id,
        session_id=body.session_id,
        idempotency_key=body.idempotency_key,
        request_hash=_request_hash(request, body.model_dump()),
        body=body.body,
        title=body.title,
        blueprint_id=body.blueprint_id,
        expected_revision=body.expected_revision,
        source=body.source,
        note=body.note,
    )
    return ok(result, request_id=request.state.request_id)


@router.post("/prep/blueprints/confirm")
async def confirm_blueprint(
    request: Request,
    body: AdjudicateBlueprintRequest,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    result = await _service.confirm_blueprint(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        actor_id=user.id,
        actor_role_hint=user.role,
        blueprint_id=body.blueprint_id,
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
        request_hash=_request_hash(request, body.model_dump()),
        note=body.note,
    )
    return ok(result, request_id=request.state.request_id)


@router.post("/prep/blueprints/reject")
async def reject_blueprint(
    request: Request,
    body: AdjudicateBlueprintRequest,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    result = await _service.reject_blueprint(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        actor_id=user.id,
        actor_role_hint=user.role,
        blueprint_id=body.blueprint_id,
        expected_revision=body.expected_revision,
        idempotency_key=body.idempotency_key,
        request_hash=_request_hash(request, body.model_dump()),
        reason=body.reason,
    )
    return ok(result, request_id=request.state.request_id)


# ── R19 学情证据画像（确定性统计 + 版本化 + 教师侧证据）───────────────────────


@router.post("/prep/profiles/recalculate")
async def recalculate_profile(
    request: Request,
    body: RecalculateProfileRequest,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    expires_at = None
    if body.expires_at:
        from datetime import datetime

        expires_at = datetime.fromisoformat(body.expires_at.replace("Z", "+00:00"))
    result = await _service.recalculate_profile(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        actor_id=user.id,
        actor_role_hint=user.role,
        session_id=body.session_id,
        class_id=body.class_id,
        idempotency_key=body.idempotency_key,
        request_hash=_request_hash(request, body.model_dump()),
        assignment_id=body.assignment_id,
        profile_id=body.profile_id,
        expected_revision=body.expected_revision,
        task_scope=TaskScopeInfo(**body.task_scope) if body.task_scope else None,
        attempt_policy=(
            AttemptPolicyMetrics(**body.attempt_policy) if body.attempt_policy else None
        ),
        kp_by_item=body.kp_by_item,
        expires_at=expires_at,
        note=body.note,
    )
    return ok(result, request_id=request.state.request_id)


@router.get("/prep/profiles/{profile_id}")
async def get_profile(
    profile_id: str,
    request: Request,
    user: CurrentUser,
    session: DbSession,
    session_id: str = Query(..., pattern=r"^[0-9a-fA-F-]{36}$"),
) -> dict:
    result = await _service.get_profile(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        session_id=session_id,
        profile_id=profile_id,
    )
    return ok(result, request_id=request.state.request_id)


@router.post("/prep/profiles/{profile_id}/interpret")
async def interpret_profile(
    profile_id: str,
    request: Request,
    body: InterpretProfileRequest,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    result = await _service.interpret_profile(
        session,
        adapter=_adapter(),
        token=_bearer_token(request),
        actor_id=user.id,
        actor_role_hint=user.role,
        session_id=body.session_id,
        profile_id=profile_id,
        idempotency_key=body.idempotency_key,
        request_hash=_request_hash(request, body.model_dump()),
        interpreter=_profile_interpreter(),
    )
    return ok(result, request_id=request.state.request_id)


def _profile_interpreter():
    """生产解释器注入点（LLM gateway 接线留业务轮）；当前显式无解释器 → basic_mode。"""
    from app.teacher_prep.profile.interpreter import NullInterpreter

    return NullInterpreter()


# ── 课堂发布（Phase B）：session 的 TeachingDeckSpec → 8000 课堂课件快照 ──────
# 边界：8100 只做「归属裁决 + 确定性打包 + 转发」；课堂 artifact、讲稿/练习准备、
# 学生可见性全部由 8000 Owner A 端点负责（不在此处伪造 courseware_id/status）。

_PUBLISH_MAX_ASSET_BYTES = 4 * 1024 * 1024  # 设计 §4.1：资产总量 ≤4MB
_PUBLISH_MAX_SLIDES = 30  # 设计 §4.1：3–30 页
_PUBLISH_MIN_SLIDES = 3
_PUBLISH_MAX_ELEMENTS = 12  # 设计 §4.1：单页 elements ≤12
_COURSEWARE_SUBJECT = "math"  # 8000 课堂契约固定学科标记（高中备课范围）


class PublishClassroomRequest(BaseModel):
    """发布到班级课堂：目标班级 + 可选指定 deck/revision 之外的展示标题。"""

    class_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    deck_id: str | None = Field(default=None, pattern=r"^[0-9a-fA-F-]{36}$")
    title: str | None = Field(default=None, max_length=200)
    include_practice: bool = True


@router.post("/prep/sessions/{session_id}/publish-classroom")
async def publish_classroom(
    session_id: str,
    request: Request,
    body: PublishClassroomRequest,
    user: CurrentUser,
    session: DbSession,
) -> dict:
    """把当前备课 session 的课堂课件（TeachingDeckSpec）发布为班级课堂快照。

    链路：session 归属 → 目标班成员裁决（8000，fail closed）→ 解析 deck →
    spec 归属（artifact 反查）→ 打包 slides/资产 → 8000 `POST /api/teacher/classroom-coursewares`。

    错误语义（不返回假成功）：
    - 非本人 session → 403（service）；目标班非当前成员 → 403；deck 非本人 → 403；
    - 无 deck 投影 → 409；deck 0 页 / 超 30 页 → 422；
    - 8000 5xx / 网络 → 503（ContractAuthorityUnreachable → DEPENDENCY_UNAVAILABLE）；
    - 资产读取失败/超 4MB → 跳过该资产并写 warning，不阻断发布。
    """
    from app.core.errors import ApiError, ErrorCode
    from app.teacher_prep_v2.adapters.errors import raise_api_error
    from app.teacher_prep_v2.contracts.errors import ContractError

    adapter = _adapter()
    token = _bearer_token(request)
    try:
        # 1) session 归属（service 同时向 8000 重验 session 自身班级成员关系）
        await _service.get_session_detail(
            session,
            adapter=adapter,
            token=token,
            teacher_id=user.id,
            session_id=session_id,
        )
        # 2) 目标班成员裁决：跨班/非成员一律 fail closed（403），不能借发布越班
        await adapter.require_class_membership(token=token, class_id=body.class_id)
        # 3) deck 解析 + 归属（spec_access 无证据即拒）
        row, spec = await _load_publish_deck(
            session, session_id=session_id, deck_id=body.deck_id, actor_id=user.id, role=user.role
        )
        # Golden B Gate（存量兜底）：历史 deck 在投影时尚未经过 polish。发布快照前
        # 对副本再跑一次确定性整形（幂等），保证课堂拿到「同图不重复、无相邻
        # image-focus、封面/去重/薄页已修」的版本；不改源库、不生成新事实。
        from app.teacher_prep.specs.deck_polish import polish_deck_spec

        spec, _polish_report = polish_deck_spec(spec, classroom_compat=True)
        if not spec.slides:
            raise ApiError(
                ErrorCode.VALIDATION, "课件没有可发布的页面（0 页），请先生成 PPT 再发布课堂"
            )
        if len(spec.slides) > _PUBLISH_MAX_SLIDES:
            raise ApiError(
                ErrorCode.VALIDATION,
                f"课件 {len(spec.slides)} 页超过课堂发布上限 {_PUBLISH_MAX_SLIDES} 页，请精简后再发布",
            )

        warnings: list[str] = []
        if len(spec.slides) < _PUBLISH_MIN_SLIDES:
            warnings.append(
                f"课件仅 {len(spec.slides)} 页，少于课堂建议页数 {_PUBLISH_MIN_SLIDES} 页"
            )
        if not body.include_practice:
            # 8000 契约（当前版本）固定准备随堂练习；不静默忽略教师选择，如实告知。
            warnings.append("8000 课堂准备当前固定生成随堂练习，include_practice=false 暂不生效")

        slides_payload = _pack_classroom_slides(spec, warnings)
        payload = {
            "class_id": body.class_id,
            "deck_id": str(row.id),
            "deck_revision": int(row.revision),
            "title": _publish_title(body.title, spec),
            "textbook_line": await _textbook_line(session, spec),
            "subject": _COURSEWARE_SUBJECT,
            "slides": slides_payload,
            "source_refs": _publish_source_refs(spec),
            "warnings": warnings,
        }
        created = await adapter.create_classroom_courseware(token=token, payload=payload)
    except ContractError as exc:
        raise_api_error(exc)  # 契约错误 → 8100 平台错误码（403/409/503），不泄露 8000 细节
        raise  # pragma: no cover - raise_api_error 必然抛出；保证类型收窄

    return ok(
        {
            "courseware_id": created["courseware_id"],
            "status": created["status"],
            "title": payload["title"],
            "class_id": body.class_id,
            "warnings": warnings,
        },
        request_id=request.state.request_id,
    )


async def _load_publish_deck(
    session: AsyncSession,
    *,
    session_id: str,
    deck_id: str | None,
    actor_id: str,
    role: str,
):
    """解析待发布的 deck（body.deck_id 优先，否则本 session 最新 deck 投影）。

    `spec_access` 与 ppt_v2 路由守卫同口径：`ppt_v2_deck_specs` 无 owner 列，
    归属由 projection→session.teacher_id / artifact ACL 反查，无证据即拒（403）。
    """
    import uuid

    from sqlalchemy import select

    from app.core.errors import ApiError, ErrorCode
    from app.ppt_v2.service import spec_edit
    from app.storage import artifacts as artifact_service
    from app.teacher_prep.specs.models import SpecProjectionRow

    if deck_id:
        target_deck_id = deck_id
    else:
        try:
            sid = uuid.UUID(session_id)
        except (ValueError, AttributeError) as exc:
            raise ApiError(ErrorCode.NOT_FOUND, f"备课会话不存在（session_id={session_id}）") from exc
        projection = (
            await session.execute(
                select(SpecProjectionRow)
                .where(
                    SpecProjectionRow.session_id == sid,
                    SpecProjectionRow.kind == "deck",
                    SpecProjectionRow.spec_target_id.is_not(None),
                )
                .order_by(SpecProjectionRow.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if projection is None or projection.spec_target_id is None:
            raise ApiError(
                ErrorCode.CONFLICT,
                "当前备课会话还没有生成的课件（请先生成 PPT 再发布课堂）",
            )
        target_deck_id = str(projection.spec_target_id)

    allowed, reason = await artifact_service.spec_access(
        session,
        spec_id=str(target_deck_id),
        actor_id=actor_id,
        roles=[role] if role else None,
    )
    if not allowed and reason == "no_ownership_record":
        # SQLite 兼容兜底：`Uuid` 列在 SQLite 以 32 位 hex 存储，spec_access 的
        # 原生 SQL `cast(... as text)` 与 Postgres 规范 UUID 文本不一致 → 恒
        # no_ownership_record（仅测试/离线库现象）。ORM 绑定按驱动规范化 UUID，
        # 守卫语义与 spec_access 完全一致（projection→session owner 优先，其次
        # artifact ACL），无证据仍然拒绝。
        allowed, reason = await _spec_access_via_orm(
            session, deck_id=str(target_deck_id), actor_id=actor_id, role=role
        )
    if not allowed:
        raise ApiError(ErrorCode.FORBIDDEN, f"非本人课件或无权发布（{reason}）")

    row = await spec_edit.load_deck_row(session, str(target_deck_id))
    spec = spec_edit.load_spec(row)
    return row, spec


async def _spec_access_via_orm(
    session: AsyncSession, *, deck_id: str, actor_id: str, role: str | None
) -> tuple[bool, str]:
    """`artifact_service.spec_access` 的 ORM 等价实现（SQLite UUID 文本形态兜底）。

    证据链与 spec_access 一致：projection → tp_sessions.teacher_id == actor；
    否则 teacher_artifacts(spec_id) 的 ACL；两者皆无 → no_ownership_record。
    """
    import uuid

    from sqlalchemy import select

    from app.storage.artifacts import acl_allows
    from app.storage.models import TeacherArtifact
    from app.teacher_prep.models import TeachingSessionRow
    from app.teacher_prep.specs.models import SpecProjectionRow

    try:
        deck_uid = uuid.UUID(str(deck_id))
    except (ValueError, AttributeError, TypeError):
        return False, "spec_id_invalid"
    if role == "admin":
        return True, "admin"

    owner = (
        await session.execute(
            select(TeachingSessionRow.teacher_id)
            .join(SpecProjectionRow, SpecProjectionRow.session_id == TeachingSessionRow.id)
            .where(SpecProjectionRow.spec_target_id == deck_uid)
            .limit(1)
        )
    ).scalar_one_or_none()
    if owner is not None:
        if str(owner) == actor_id:
            return True, "session_owner"
        return False, "other_teacher"

    artifact = (
        await session.execute(
            select(TeacherArtifact).where(TeacherArtifact.spec_id == str(deck_uid)).limit(1)
        )
    ).scalar_one_or_none()
    if artifact is not None:
        return acl_allows(artifact, actor_id, [role] if role else None)

    return False, "no_ownership_record"


def _publish_title(explicit: str | None, spec) -> str:
    """展示标题：显式标题 > deck metadata.title > 兜底（去空白、限长）。"""
    title = (explicit or "").strip() or str(spec.metadata.title or "").strip() or "课堂课件"
    return title[:200]


def _publish_source_refs(spec) -> list[dict]:
    """教材溯源（设计 §4.1）：只带 3 个可读定位字段，避免把内部 chunk 形态外泄。"""
    refs: list[dict] = []
    for src in spec.textbook_sources:
        refs.append(
            {
                "textbook_id": str(src.textbook_id),
                "section_id": str(src.section_id),
                "locator": src.locator or (src.chunk_refs[0] if src.chunk_refs else None),
            }
        )
    return refs


async def _textbook_line(session: AsyncSession, spec) -> str:
    """可读教材行（出版社 + 册次 · 章节标题）；无教材行时诚实返回空串，不编造。"""
    import uuid

    from app.textbook_v2.models import TextbookSection, TextbookSource

    for src in spec.textbook_sources:
        try:
            source = await session.get(TextbookSource, uuid.UUID(str(src.textbook_id)))
        except (ValueError, AttributeError):
            source = None
        if source is None:
            continue
        publisher = str(source.publisher or "").strip()
        volume = str(source.volume or source.edition or "").strip()
        head = publisher if volume and volume == publisher else " ".join(
            p for p in (publisher, volume) if p
        )
        section_title = ""
        try:
            section = await session.get(TextbookSection, uuid.UUID(str(src.section_id)))
        except (ValueError, AttributeError):
            section = None
        if section is not None:
            section_title = str(section.title or "").strip()
        line = " · ".join(p for p in (head, section_title) if p)
        if line:
            return line[:200]
    return ""


def _pack_classroom_slides(spec, warnings: list[str]) -> list[dict]:
    """把 TeachingDeckSpec.slides 打包为 8000 课堂契约的 slides[]。

    讲稿来源：**`slide.speaker_note`**（`deck.speaker_notes` 是投影汇总，历史恒空，
    不能作为讲稿事实）。资产：按 `slide.assets` 引用本 deck 的 `AssetRef`，从
    `teacher-assets` 读字节 → base64；累计 ≤4MB，超限/读取失败/未授权 → 跳过 + warning，
    绝不阻断发布（设计 §4.1 / 风险表「资产过大」）。
    """
    import base64
    import hashlib

    from app.storage.minio import get_storage

    storage = None
    try:
        storage = get_storage()
    except Exception as exc:  # noqa: BLE001 - 存储不可用不得阻断发布（诚实降级 + warning）
        warnings.append(f"对象存储不可用，本次发布不携带图片资产（{type(exc).__name__}）")

    assets_by_id = {str(a.id): a for a in spec.assets}
    encoded: dict[str, dict | None] = {}
    budget = {"used": 0}

    def _encode_asset(asset_id: str, slide_id: str) -> dict | None:
        if asset_id in encoded:
            return encoded[asset_id]
        encoded[asset_id] = None  # 先占位：同一资产只判定/编码一次
        ref = assets_by_id.get(asset_id)
        if ref is None:
            warnings.append(f"第 {slide_id} 页引用的资产 {asset_id} 不在课件资产表中，已跳过")
            return None
        if storage is None:
            return None
        if str(getattr(ref, "rights_status", "QUARANTINED")) != "ACTIVE":
            warnings.append(
                f"资产 {asset_id} 未授权（rights_status={ref.rights_status}），已跳过"
            )
            return None
        hint = max(int(getattr(ref, "size_bytes", 0) or 0), 0)
        if budget["used"] + hint > _PUBLISH_MAX_ASSET_BYTES:
            warnings.append(f"图片资产累计超过 4MB 上限，已跳过资产 {asset_id}")
            return None
        try:
            data = storage.get_bytes("teacher-assets", str(ref.object_key))
        except Exception as exc:  # noqa: BLE001 - 单资产失败降级，不阻断发布
            warnings.append(f"资产 {asset_id} 读取失败，已跳过（{type(exc).__name__}）")
            return None
        if budget["used"] + len(data) > _PUBLISH_MAX_ASSET_BYTES:
            warnings.append(f"图片资产累计超过 4MB 上限，已跳过资产 {asset_id}")
            return None
        budget["used"] += len(data)
        item = {
            "id": asset_id,
            "mime_type": str(ref.mime_type),
            "data_base64": base64.b64encode(data).decode("ascii"),
            # sha256 以实际下发字节复算（不复制元数据，保证与 data_base64 同源）
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        encoded[asset_id] = item
        return item

    slides_payload: list[dict] = []
    for slide in spec.slides:
        elements = list(slide.elements)
        if len(elements) > _PUBLISH_MAX_ELEMENTS:
            warnings.append(
                f"第 {slide.id} 页元素 {len(elements)} 个超过 {_PUBLISH_MAX_ELEMENTS} 个上限，已截断"
            )
            elements = elements[:_PUBLISH_MAX_ELEMENTS]
        slide_assets = [
            item
            for item in (_encode_asset(str(aid), str(slide.id)) for aid in slide.assets)
            if item is not None
        ]
        slides_payload.append(
            {
                "id": str(slide.id),
                "type": slide.type,
                "title": slide.title,
                "content": slide.content or "",
                "layout_hint": str(slide.layout_hint or slide.type),
                "elements": [e.model_dump(mode="json") for e in elements],
                "math_object": (
                    slide.math_object.model_dump(mode="json") if slide.math_object else None
                ),
                "assets": slide_assets,
                "speaker_note": slide.speaker_note or "",
            }
        )
    return slides_payload
