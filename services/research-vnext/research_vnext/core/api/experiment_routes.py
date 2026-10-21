"""RV11 · ExperimentRun API —— dispatch / 查询 / cancel / reproduce。

端点（prefix 由 `app.py` 注入，与其它 router 同规则）
--------------------------------------------------------
| 方法 | 路径 | 用途 |
|---|---|---|
| POST | `/sessions/{sid}/experiment-runs:dispatch` | 人工确认过的计划 → 发实验 job（策略门内） |
| GET | `/sessions/{sid}/experiment-runs` | 运行列表（含进度/状态） |
| GET | `/sessions/{sid}/experiment-runs/{rid}` | 单次运行详情（manifest/指标/artifacts/对比） |
| POST | `/sessions/{sid}/experiment-runs/{rid}:cancel` | 请求取消（QUEUED 立即；RUNNING 安全点） |
| POST | `/sessions/{sid}/experiment-runs/{rid}:reproduce` | 一键复现：新 run 引用同一 immutable manifest |

边界：只读写 Research 自己的表；执行永远不在本进程——dispatch 只入队。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ..application import experiment_run_service as svc
from ..db.engine import session_scope
from .deps import READ_CAPABILITY, WRITE_CAPABILITY, authorize_session, load_access_context
from .routes import _err, _json, _trace

router = APIRouter(tags=["research-vnext-rv11"])


@router.post("/sessions/{session_id}/experiment-runs:dispatch")
def dispatch_experiment_run(
    request: Request,
    session_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """人工确认的计划 → ExperimentRun + job。

    请求体（全部可选）：`{planRevision, limits, network, environment}`——
    `limits`/`network`/`environment` 是**请求**，策略门会拒绝任何提权。
    """
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    raw = body_in or {}
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            run = svc.dispatch(
                db,
                session_id=session_id,
                tenant_id=ctx.tenant_id,
                actor_id=ctx.actor_id,
                plan_revision=raw.get("planRevision"),
                requested_limits=raw.get("limits"),
                network=raw.get("network"),
                environment=raw.get("environment"),
                client_request_id=str(raw.get("client_request_id") or ""),
                trace_id=trace.trace_id,
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"run": run}, 200, None, trace)


@router.get("/sessions/{session_id}/experiment-runs")
def list_experiment_runs(
    request: Request, session_id: uuid.UUID, limit: int = 50
) -> JSONResponse:
    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            items = svc.list_runs(db, session_id=session_id, limit=limit)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"items": items}, 200, None, None)


@router.get("/sessions/{session_id}/experiment-runs/{run_id}")
def get_experiment_run(
    request: Request, session_id: uuid.UUID, run_id: uuid.UUID
) -> JSONResponse:
    ctx = load_access_context(request)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=READ_CAPABILITY)
            run = svc.get_run(db, session_id=session_id, run_id=run_id)
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"run": run}, 200, None, None)


@router.post("/sessions/{session_id}/experiment-runs/{run_id}:cancel")
def cancel_experiment_run(
    request: Request,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    raw = body_in or {}
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            run = svc.cancel(
                db,
                session_id=session_id,
                run_id=run_id,
                reason=str(raw.get("reason") or "cancel_requested"),
                actor_id=ctx.actor_id,
                trace_id=trace.trace_id,
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"run": run}, 200, None, trace)


@router.post("/sessions/{session_id}/experiment-runs/{run_id}:reproduce")
def reproduce_experiment_run(
    request: Request,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """一键复现：新 ExperimentRun 引用同一 immutable manifest（不重新走确认门）。"""
    ctx = load_access_context(request)
    trace = _trace(request, ctx)
    with session_scope() as db:
        try:
            authorize_session(db, ctx, session_id, capability=WRITE_CAPABILITY)
            run = svc.dispatch_reproduce(
                db,
                session_id=session_id,
                run_id=run_id,
                actor_id=ctx.actor_id,
                client_request_id=str((body_in or {}).get("client_request_id") or ""),
                trace_id=trace.trace_id,
            )
        except Exception as e:  # noqa: BLE001
            return _err(e)
    return _json({"run": run}, 200, None, trace)
