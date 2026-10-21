"""RV-XF · 论文工作台「讯飞线上工作流」REST 端点（挂在双前缀下）。

由 `core/api/routes.py` 末尾以 `router.include_router(xf_router)` 挂载，
因此会随父路由一起被 `app.py` 在**两个**前缀（`/api/research-v1` 与
`/api/research-vnext`）下注册。`APIRouter` **不**声明 `prefix=`（契约测试
`tests/contracts/test_dual_prefix_and_startup.py` 守门）。

授权（与既有端点**同形**）
--------------------------
四个端点都必须做**对象无关**的两项校验（复用 `core/api/deps.py` 的原语，不自创）：
`load_access_context`（网关注入 + HMAC 校验）→ `_check_workspace`（workspace 必须
为 research）→ `require_capability`（读端点要 `workspace.research.enter`，
`run`/`compile` 要 `research.session.write`）。缺少这一步会让「签名合法但空能力 /
越权 workspace」被放行（QA 已证伪）。

响应信封与错误转换**复用** `core/api/routes.py` 的 helper（`_json` / `_err` /
`_trace`）—— 为规避「`xf_routes` 被直接 import 时」的模块级循环导入，
这些 helper 采用**惰性引入**（见 `_helpers`）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, Response

from ..xf import service
from ..xf.latex import compile_latex_sync
from ..xf.models import (
    XF_TEX_KERNEL_UNAVAILABLE,
    XF_UNKNOWN_WORKFLOW,
    WORKFLOW_BY_KEY,
    XfError,
)
from .deps import (
    READ_CAPABILITY,
    WRITE_CAPABILITY,
    _check_workspace,
    load_access_context,
    require_capability,
)

# 前缀由 include_router(prefix=...) 注入（见模块 docstring），此处**不要**写 prefix。
router = APIRouter(tags=["research-vnext-xf"])


def _helpers():
    """惰性获取 `routes.py` 的响应 helper（`_err` / `_json` / `_trace`）。

    必须惰性：`routes.py` 末尾会 `from .xf_routes import router`。若本模块在
    顶层 `from .routes import ...`，则当调用方**先**导入 `xf_routes`（如 QA 的
    单测直接 `from research_vnext.core.api import xf_routes`）时会形成循环导入
    （`ImportError: cannot import name 'router' from partially initialized module`）。
    """
    from .routes import _err, _json, _trace  # noqa: E402

    return _err, _json, _trace


def _authorize_or_error(request: Request, capability: str):
    """加载 AccessContext 并执行**与既有端点同形**的授权校验。

    顺序（复用 `core/api/deps.py` 既有原语）：`load_access_context` →
    `_check_workspace`（非 research → 403）→ `require_capability`（缺能力 → 403）。
    返回 `(ctx, None)` 或 `(None, JSONResponse)`。

    说明：xf 端点是**无会话对象**的算力/额度入口，故不执行 `authorize_session`
    的 tenant/project 对象级校验（那需要 session_id）。
    """
    _err, _json, _trace = _helpers()  # noqa: F841 - _err 下方使用
    try:
        ctx = load_access_context(request)
        _check_workspace(ctx)
        require_capability(ctx, capability)
        return ctx, None
    except Exception as e:  # noqa: BLE001 - 交回既有 DomainError → HTTP 映射
        return None, _err(e)


def _xf_error_response(e: XfError) -> JSONResponse:
    """XfError → HTTP。429/503 带 `Retry-After`；latex 内核不可用附带 reason/hint。"""
    headers: dict[str, str] = {}
    if e.http_status in (429, 503) and e.retry_after:
        headers["Retry-After"] = str(e.retry_after)
    body: dict[str, Any] = {"error": e.to_dict()}
    if e.code == XF_TEX_KERNEL_UNAVAILABLE:
        # 诚实降级契约：前端可据此显示「内核不可用」而非「编译失败」。
        body["code"] = e.code
        body["available"] = False
        body.setdefault("reason", e.detail.get("reason", e.message))
        body.setdefault("hint", e.detail.get("hint", ""))
    return JSONResponse(status_code=e.http_status, content=body, headers=headers or None)


@router.get("/xf/workflows")
def list_xf_workflows(request: Request) -> JSONResponse:
    """列出四个线上工作流的元信息（key/标题/说明/必填参数/是否已配置）。"""
    _err, _json, _trace = _helpers()
    ctx, err = _authorize_or_error(request, READ_CAPABILITY)
    if err is not None:
        return err
    trace = _trace(request, ctx)
    return _json({"items": service.available_workflows()}, 200, None, trace)


@router.get("/xf/health")
def xf_health(request: Request) -> JSONResponse:
    """健康检查：开关 / base_url / appId（掩码）/ 各 flow 配置状态。不含凭证原文。"""
    _err, _json, _trace = _helpers()
    ctx, err = _authorize_or_error(request, READ_CAPABILITY)
    if err is not None:
        return err
    trace = _trace(request, ctx)
    return _json(service.health(), 200, None, trace)


@router.post("/xf/workflows/{workflow}/run")
async def run_xf_workflow(
    request: Request,
    workflow: str,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """运行一次工作流。body：`{"parameters": {...}, "uid": "..."}`。

    授权：需 `research.session.write`。错误映射：未知 workflow→400；
    未配置/禁用→503；鉴权→502（透传上游 message）；超时→504；
    限流/并发/额度→429；传输/上游→502。
    """
    _err, _json, _trace = _helpers()
    ctx, err = _authorize_or_error(request, WRITE_CAPABILITY)
    if err is not None:
        return err
    trace = _trace(request, ctx)

    if workflow not in WORKFLOW_BY_KEY:
        return _xf_error_response(
            XfError(
                f"未知工作流: {workflow}",
                code=XF_UNKNOWN_WORKFLOW,
                http_status=400,
            )
        )

    raw = body_in or {}
    parameters = raw.get("parameters")
    if not isinstance(parameters, dict):
        parameters = {}
    uid = raw.get("uid")
    uid = str(uid) if uid else None

    try:
        result = await service.run(workflow, parameters, uid)
    except XfError as e:
        return _xf_error_response(e)
    return _json(result.to_dict(), 200, None, trace)


@router.post("/xf/latex:compile")
def compile_latex(
    request: Request,
    body_in: dict[str, Any] | None = Body(default=None),
) -> Response:
    """单份 `.tex` 同步编译为 PDF（复用 export 层 xelatex 内核）。

    授权：需 `research.session.write`。body：`{"source": "<tex>", "filename": "main.tex"}`。
    成功→200 + `application/pdf`；源码非法→400；编译失败→422；编译超时→504；
    内核/镜像不可用→503（诚实降级，见 `XF_TEX_KERNEL_UNAVAILABLE`）。
    """
    _err, _json, _trace = _helpers()
    ctx, err = _authorize_or_error(request, WRITE_CAPABILITY)
    if err is not None:
        return err
    trace = _trace(request, ctx)

    raw = body_in or {}
    source = raw.get("source")
    filename = raw.get("filename") or "main.tex"
    if not isinstance(source, str):
        return _xf_error_response(
            XfError(
                "缺少 source（应为 .tex 源码字符串）",
                code="XF_TEX_SOURCE_INVALID",
                http_status=400,
            )
        )

    try:
        pdf_bytes, meta = compile_latex_sync(source, str(filename))
    except XfError as e:
        return _xf_error_response(e)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "X-Trace-Id": trace.trace_id,
            "X-XF-Pages": str(meta.get("pages", 0)),
            "X-XF-Filename": str(meta.get("filename", "main.tex")),
        },
    )
