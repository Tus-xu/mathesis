"""RV09 · 地图 / 修订审计 / 记忆 / prompt 发布 的只读与编辑端点。

前缀由 `app.py` 的 `include_router(prefix=...)` 注入（与 `routes.py` 同规则），
本模块**不**自带 prefix。

端点一览（与 `RV09/E2E-REPORT.md` 的 E2E 步骤一一对应）
--------------------------------------------------------
| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/sessions/{id}/pending-actions/{pid}:revise` | 逐项 accept / reject / edit（**不改正式状态**） |
| GET | `/sessions/{id}/objects/{object_ref}` | 某对象族的修订历史 + 最新内容（刷新恢复 / 409 diff 素材） |
| GET | `/sessions/{id}/revisions/audit` | 修订因果审计（prompt / context / model / cost / trace） |
| GET | `/sessions/{id}/checkpoint` | 短期记忆 checkpoint（会话 / thread 级） |
| GET / PUT | `/sessions/{id}/preferences` | 长期偏好（按命名空间读写） |
| GET | `/sessions/{id}/ai-runs` | 本会话 AI run 列表（含上下文清单与成本依据） |
| GET | `/prompts` | prompt 发布台账（只读诊断；**不含**提示词正文） |

边界：本模块只读写 Research Core 自己的表；不 import Teacher 任何代码，
也不接受任何指向 Teacher 域的参数。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ..application import session_service as svc
from ..db.engine import session_scope
from ..domain.errors import DomainError
from ..llm.prompts import registry as prompts
from ..memory import long_term as LT
from ..memory import short_term as ST
from .deps import READ_CAPABILITY, WRITE_CAPABILITY, authorize_session, load_access_context
from .routes import _err, _json, _trace

router = APIRouter(tags=["research-vnext-rv09"])


# --------------------------------------------------------------------------
# 提案编辑（逐项 accept / reject / edit）
# --------------------------------------------------------------------------


@router.post("/sessions/{session_id}/pending-actions/{pending_id}:revise")
def revise_pending_action(
    request: Request,
    session_id: uuid.UUID,
    pending_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """对一个**尚未确认**的提案做逐项 accept / reject / edit。

    只改提案草稿 —— 正式状态仍然只在 `:accept` 里写。
    两类拒绝码一目了然：
    - `EDIT_NOT_SUPPORTED_FOR_OBJECT`（该对象族是整体结构，不支持逐项删改）；
    - `PROBLEM_MAP_EDIT_FORBIDDEN_FIELD`（试图改 `origin`：来源由服务端裁决）。
    """
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    raw = body_in or {}
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            body = svc.revise_pending(
                db,
                session_id=session_id,
                pending_id=pending_id,
                ctx_capabilities=ctx.capabilities,
                actor_id=ctx.actor_id,
                trace=trace,
                body=raw,
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body, 200, None, trace)


# --------------------------------------------------------------------------
# 对象族读取（刷新恢复 / diff 素材）
# --------------------------------------------------------------------------


@router.get("/sessions/{session_id}/objects/{object_ref}")
def get_object_family(
    request: Request, session_id: uuid.UUID, object_ref: str, revision: int | None = None
) -> JSONResponse:
    """某对象族的修订历史。`?revision=N` 时额外返回该修订的完整内容。

    刻意**不**把全部历史内容一次返回：长会话里 problem_map 可能已有十几版，
    一次性返回会让快照接口变重；前端要对比哪两版就显式取哪两版。
    """
    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            table = svc.revision_table(object_ref)
            if table is None:
                raise DomainError("OBJECT_REF_UNKNOWN", f"未知 object_ref: {object_ref!r}")
            rows = (
                db.query(table)
                .filter(table.session_id == session_id)
                .order_by(table.revision)
                .all()
            )
            items = [
                {
                    "id": str(r.id),
                    "revision": int(r.revision),
                    "contentHash": r.content_hash or "",
                    "confirmedAt": r.confirmed_at.isoformat() if r.confirmed_at else None,
                    "confirmedBy": str(r.confirmed_by) if r.confirmed_by else None,
                    "traceId": r.trace_id or "",
                    "clientRequestId": r.client_request_id or "",
                }
                for r in rows
            ]
            latest = rows[-1] if rows else None
            body: dict[str, Any] = {
                "objectRef": object_ref,
                "revision": int(latest.revision) if latest is not None else None,
                "revisions": items,
                "latest": (
                    {**items[-1], "content": dict(latest.content or {})} if latest is not None else None
                ),
            }
            if revision is not None:
                match = next((r for r in rows if int(r.revision) == revision), None)
                body["requested"] = (
                    {**next(i for i in items if i["revision"] == revision), "content": dict(match.content or {})}
                    if match is not None
                    else None
                )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json(body)


@router.get("/sessions/{session_id}/revisions/audit")
def revision_audit(request: Request, session_id: uuid.UUID) -> JSONResponse:
    """修订因果审计（只追加）。回答"这条修订是谁、依据什么确认的"。"""
    from ..db.models import ObjectRevisionAuditRow

    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            rows = (
                db.query(ObjectRevisionAuditRow)
                .filter(ObjectRevisionAuditRow.session_id == session_id)
                .order_by(ObjectRevisionAuditRow.created_at, ObjectRevisionAuditRow.id)
                .all()
            )
            items = [
                {
                    "id": r.id,
                    "objectRef": r.object_ref,
                    "objectId": str(r.object_id),
                    "revision": int(r.revision),
                    "previousRevision": r.previous_revision,
                    "contentHash": r.content_hash,
                    "diffCount": int((r.diff or {}).get("count", 0)),
                    "diff": r.diff or {},
                    "pendingActionId": str(r.pending_action_id) if r.pending_action_id else None,
                    "aiRunId": str(r.ai_run_id) if r.ai_run_id else None,
                    "promptRef": r.prompt_ref,
                    "promptVersion": int(r.prompt_version or 0),
                    "contextManifestRef": r.context_manifest_ref,
                    "contextTrimReasons": list(r.context_trim_reasons or []),
                    "model": r.model,
                    "provider": r.provider,
                    # 成本是 `null` 时表示"未知"，`0.0` 才表示"确实免费"。
                    "costUsd": float(r.cost_usd) if r.cost_usd is not None else None,
                    "costBasis": r.cost_basis,
                    "eventId": r.event_id,
                    "actorId": str(r.actor_id) if r.actor_id else None,
                    "traceId": r.trace_id,
                    "at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"items": items})


@router.get("/sessions/{session_id}/ai-runs")
def ai_runs(request: Request, session_id: uuid.UUID) -> JSONResponse:
    """本会话的 AI run 审计（含上下文清单与成本依据）。"""
    from ..db.models import ResearchAiRunRow

    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            rows = (
                db.query(ResearchAiRunRow)
                .filter(ResearchAiRunRow.session_id == session_id)
                .order_by(ResearchAiRunRow.created_at)
                .all()
            )
            items = [
                {
                    "id": str(r.id),
                    "messageId": str(r.message_id) if r.message_id else None,
                    "provider": r.provider,
                    "model": r.model,
                    "promptRef": r.prompt_ref,
                    "promptVersion": int(r.prompt_version or 0),
                    "promptPublished": bool(r.prompt_published),
                    "status": r.status,
                    "errorCode": r.error_code,
                    "inputTokens": r.input_tokens,
                    "outputTokens": r.output_tokens,
                    "latencyMs": r.latency_ms,
                    "costUsd": float(r.cost_usd) if r.cost_usd is not None else None,
                    "costBasis": r.cost_basis,
                    "contextManifest": r.context_manifest or {},
                    "traceId": r.trace_id,
                    "at": r.created_at.isoformat(),
                }
                for r in rows
            ]
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"items": items})


# --------------------------------------------------------------------------
# 记忆
# --------------------------------------------------------------------------


@router.get("/sessions/{session_id}/checkpoint")
def get_checkpoint(request: Request, session_id: uuid.UUID, thread_key: str = ST.DEFAULT_THREAD) -> JSONResponse:
    """短期记忆：本会话该 thread 的进度 checkpoint。

    跨会话/跨租户读取在 SQL 层就返回空（见 `memory/short_term.load_checkpoint`）。
    """
    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            cp = ST.load_checkpoint(
                db, session_id=session_id, tenant_id=ctx.tenant_id, thread_key=thread_key
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    if cp is None:
        return JSONResponse(status_code=404, content={"error": {"code": "CHECKPOINT_NOT_FOUND",
                                                               "message": "本会话该线程尚无 checkpoint"}})
    return _json(cp.as_dict())


def _require_project_id(request: Request, session_id: uuid.UUID, ctx) -> str:
    """取会话的 `project_id` 作为长期偏好的 `research_id`。

    口径与 `application/context_service.PURPOSE_FOR_PROMPT` 一致：
    会话会分叉，项目不会 —— 因此"研究"的稳定标识是项目。
    """
    from ..db.models import ResearchSessionRow

    with session_scope() as db:
        authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
        row = db.get(ResearchSessionRow, session_id)
        if row is None:
            raise DomainError("SESSION_NOT_FOUND", f"会话不存在: {session_id}")
        return str(row.project_id)


@router.get("/sessions/{session_id}/preferences")
def list_preferences(request: Request, session_id: uuid.UUID, purpose: str = "ASSISTANT_STYLE") -> JSONResponse:
    ctx = load_access_context(request)
    try:
        research_id = _require_project_id(request, session_id, ctx)
        with session_scope() as db:
            items = [
                p.as_dict()
                for p in LT.list_preferences(
                    db,
                    tenant_id=ctx.tenant_id,
                    user_id=ctx.actor_id,
                    research_id=research_id,
                    purpose=purpose,
                )
            ]
    except Exception as e:  # noqa: BLE001
        return _err(e)
    return _json({"purpose": purpose, "researchId": research_id, "items": items})


@router.put("/sessions/{session_id}/preferences")
def put_preference(
    request: Request,
    session_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """写一条**用户明确设定**的长期偏好。

    刻意只允许 `source="user"`：模型推断出来的偏好属于 `authorized=False`
    且不进提示词，不能从这个入口写入。
    """
    ctx = load_access_context(request)
    raw = body_in or {}
    try:
        research_id = _require_project_id(request, session_id, ctx)
        purpose = str(raw.get("purpose") or "ASSISTANT_STYLE")
        key = str(raw.get("key") or "")
        if not key:
            raise DomainError("PREFERENCE_KEY_REQUIRED", "缺少 key")
        with session_scope() as db:
            pref = LT.put_preference(
                db,
                tenant_id=ctx.tenant_id,
                user_id=ctx.actor_id,
                research_id=research_id,
                purpose=purpose,
                key=key,
                value=str(raw.get("value") or ""),
                authorized=bool(raw.get("authorized", True)),
                source="user",
            )
            body = pref.as_dict()
    except Exception as e:  # noqa: BLE001
        return _err(e)
    return _json(body)


# --------------------------------------------------------------------------
# prompt 发布台账（只读诊断）
# --------------------------------------------------------------------------


@router.get("/prompts")
def prompt_releases() -> JSONResponse:
    """prompt 发布台账：版本 / ref / 内容哈希 / 发布态 / note。

    **不返回提示词正文**：正文可能随版本演进，且它并不包含用户数据 ——
    但把它做成 API 输出会让"改提示词"看起来像一次 API 调用可完成的动作，
    而发布是一个需要 note 与人的判断的部署动作。
    """
    published = prompts.published_version(prompts.ASSISTANT_PROPOSE_ID)
    return _json(
        {
            "prompts": prompts.release_report(),
            "published": {
                "assistantPropose": published,
                "ref": prompts.require_published(prompts.ASSISTANT_PROPOSE_ID).ref
                if published is not None
                else None,
            },
        }
    )


__all__ = ["router"]
