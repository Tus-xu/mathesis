"""RV-WB · 论文工作台「自研内核」REST 端点（挂在双前缀下）。

由 `core/api/routes.py` 末尾以 `router.include_router(wb_router)` 挂载，
因此会随父路由一起被 `app.py` 在**两个**前缀（`/api/research-v1` 与
`/api/research-vnext`）下注册。`APIRouter` **不**声明 `prefix=`（契约测试
`tests/contracts/test_dual_prefix_and_startup.py` 守门）。

端点
----
| 方法 | 路径 | 能力要求 | 内核 |
|---|---|---|---|
| GET  | `/wb/capabilities` | `workspace.research.enter` | — |
| POST | `/wb/latex:edit`   | `research.session.write` | LLM 网关 + TeX 校验 |
| POST | `/wb/method:search`| `workspace.research.enter` | 文献内核（Crossref）+ 可选 LLM |
| POST | `/wb/translate`    | `research.session.write` | LLM 网关 |

授权（与既有端点**同形**）
--------------------------
四个端点都执行 `load_access_context`（网关注入 + HMAC 校验）→ `_check_workspace`
（workspace 必须为 research）→ `require_capability`。缺少这一步会让"签名合法但
空能力 / 越权 workspace"被放行（XF 阶段 QA 已证伪，此处直接沿用同一防线）。

响应信封与错误转换复用 `core/api/routes.py` 的 helper（`_err` / `_json` / `_trace`）
—— 为规避模块级循环导入（`routes.py` 末尾 `from .wb_routes import router`），
这些 helper 采用**惰性引入**（见 `_helpers`），与 `xf_routes.py` 同一手法。

错误体形状
----------
- `latex:edit` 的错误体在普通 `{error:{...}}` 之上**加法式**带上前端渲染契约键
  `{success:false, security_msg, changes:[], final_updated_code:""}`（逐键对齐 `/xf`）。
- 其余端点错误体为 `{error: {...}}`。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from ..writing import service as wb_service
from ..writing.models import (
    WB_LLM_BAD_OUTPUT,
    WB_TEX_SOURCE_INVALID,
    WbError,
)
from .deps import (
    READ_CAPABILITY,
    WRITE_CAPABILITY,
    _check_workspace,
    load_access_context,
    require_capability,
)

# 前缀由 include_router(prefix=...) 注入（见模块 docstring），此处**不要**写 prefix。
router = APIRouter(tags=["research-vnext-wb"])


def _helpers():
    """惰性获取 `routes.py` 的响应 helper（`_err` / `_json` / `_trace`）。

    必须惰性：`routes.py` 末尾会 `from .wb_routes import router`。若本模块在
    顶层 `from .routes import ...`，则当调用方**先**导入 `wb_routes`（例如单测
    直接 `from research_vnext.core.api import wb_routes`）时会形成循环导入。
    """
    from .routes import _err, _json, _trace  # noqa: E402

    return _err, _json, _trace


def _authorize_or_error(request: Request, capability: str):
    """加载 AccessContext 并执行**与既有端点同形**的授权校验。

    顺序（复用 `core/api/deps.py` 既有原语）：`load_access_context` →
    `_check_workspace`（非 research → 403）→ `require_capability`（缺能力 → 403）。
    返回 `(ctx, None)` 或 `(None, JSONResponse)`。
    """
    _err, _json, _trace = _helpers()  # noqa: F841 - _err 下方使用
    try:
        ctx = load_access_context(request)
        _check_workspace(ctx)
        require_capability(ctx, capability)
        return ctx, None
    except Exception as e:  # noqa: BLE001 - 交回既有 DomainError → HTTP 映射
        return None, _err(e)


def _retry_headers(e: WbError) -> dict[str, str] | None:
    if e.http_status in (503, 504) and e.retry_after:
        return {"Retry-After": str(e.retry_after)}
    return None


def _wb_error_response(e: WbError) -> JSONResponse:
    """通用 `WbError` → HTTP（`{error: {...}}`）。"""
    return JSONResponse(
        status_code=e.http_status,
        content={"error": e.to_dict()},
        headers=_retry_headers(e),
    )


def _wb_latex_error_response(e: WbError) -> JSONResponse:
    """`latex:edit` 的错误体：加法式带上前端渲染契约键（逐键对齐 `/xf`）。

    `security_msg` 仅在安全类错误（入参守卫 / 模型输出守卫）时非空。
    """
    security_msg = e.message if e.code in (WB_TEX_SOURCE_INVALID, WB_LLM_BAD_OUTPUT) else ""
    body: dict[str, Any] = {
        "success": False,
        "changes": [],
        "final_updated_code": "",
        "security_msg": security_msg,
        "error": e.to_dict(),
    }
    return JSONResponse(status_code=e.http_status, content=body, headers=_retry_headers(e))


def _as_dict(body_in: dict[str, Any] | None) -> dict[str, Any]:
    return body_in if isinstance(body_in, dict) else {}


def _first_str(raw: dict[str, Any], *keys: str) -> str | None:
    """按顺序返回**第一个字符串值**的字段（跳过缺失/非字符串）。

    用于「规范名 + 兼容别名」：如 `file_content`（规范）与 `source`（别名），
    二者任一提供即可；非字符串值一律忽略（视作未提供）。
    """
    for k in keys:
        v = raw.get(k)
        if isinstance(v, str):
            return v
    return None


@router.get("/wb/capabilities")
def wb_capabilities(request: Request) -> JSONResponse:
    """论文工作台能力清单（引擎归属 / 可用性 / 端点 / 入参 schema / 响应键）。

    **不含任何凭证**；`requestSchema` / `responseKeys` 供前端数据驱动。
    """
    _err, _json, _trace = _helpers()
    ctx, err = _authorize_or_error(request, READ_CAPABILITY)
    if err is not None:
        return err
    trace = _trace(request, ctx)
    return _json(wb_service.capabilities(), 200, None, trace)


@router.post("/wb/latex:edit")
async def wb_latex_edit(
    request: Request,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """用 LLM 网关编辑 LaTeX 片段。

    body：`{"file_content": "<tex>", "instruction": "...", "target_file": "main.tex"}`。
    **规范名 `file_content`/`target_file`；`source`/`filename` 为兼容别名**（任一可）。
    成功 → `{success:true, changes[], final_updated_code, security_msg:"", ...}`。
    **任何自由文本入参**（源码 / 指令 / 目标文件名）命中安全守卫 →
    400 `WB_TEX_SOURCE_INVALID`（**不调用模型**、<1s 返回）；
    模型输出命中守卫 → 502 `WB_LLM_BAD_OUTPUT`；未配置 → 503；超时 → 504；缺参 → 400。
    """
    _err, _json, _trace = _helpers()
    ctx, err = _authorize_or_error(request, WRITE_CAPABILITY)
    if err is not None:
        return err
    trace = _trace(request, ctx)

    raw = _as_dict(body_in)
    source = _first_str(raw, "file_content", "source")
    if source is None:
        return _wb_latex_error_response(
            WbError("缺少 file_content（.tex 源码，兼容别名 source）", http_status=400)
        )
    instruction = _first_str(raw, "instruction") or ""
    target_file = _first_str(raw, "target_file", "filename") or "main.tex"

    try:
        result = await wb_service.edit_latex(source, instruction, filename=target_file)
    except WbError as e:
        return _wb_latex_error_response(e)
    return _json(result, 200, None, trace)


@router.post("/wb/method:search")
async def wb_method_search(
    request: Request,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """经文献内核检索方法素材（**真实**检索）+ 可选 LLM 生成方法说明。

    body：`{"method_name": "...", "query": "...", "limit": 5}`。
    **规范名 `method_name`；`query` 为兼容别名**（任一作为检索式）。
    `method_name` 原样回显；LLM 生成字段不可用时留空 + `warning`（**整体仍 200**，
    真实 `sources` 照常返回）。两者皆缺 → 400；文献内核不可用 → 503。
    """
    _err, _json, _trace = _helpers()
    ctx, err = _authorize_or_error(request, READ_CAPABILITY)
    if err is not None:
        return err
    trace = _trace(request, ctx)

    raw = _as_dict(body_in)
    # 规范名 method_name，别名 query —— 任一作为检索式；两者都接受。
    method_name = _first_str(raw, "method_name")
    query_alias = _first_str(raw, "query")
    term = (method_name or query_alias or "").strip()
    if not term:
        return _wb_error_response(
            WbError("缺少 method_name（检索式，兼容别名 query）", http_status=400)
        )
    limit = raw.get("limit", 5)

    try:
        result = await wb_service.method_search(
            term, limit=limit, method_name=(method_name or term)
        )
    except WbError as e:
        return _wb_error_response(e)
    return _json(result, 200, None, trace)


@router.post("/wb/translate")
async def wb_translate(
    request: Request,
    body_in: dict[str, Any] | None = Body(default=None),
) -> JSONResponse:
    """学术翻译（LLM 网关；**同一次调用**产出段级对照；公式真实比对计数）。

    body：`{"text": "...", "target_lang": "en", "source_lang": "zh"}`。
    **规范名 `target_lang`/`source_lang`；`targetLang`/`sourceLang` 为兼容别名**。
    缺参 → 400；未配置 → 503；超时 → 504；输出不可用 → 502。
    """
    _err, _json, _trace = _helpers()
    ctx, err = _authorize_or_error(request, WRITE_CAPABILITY)
    if err is not None:
        return err
    trace = _trace(request, ctx)

    raw = _as_dict(body_in)
    text = _first_str(raw, "text")
    if text is None:
        return _wb_error_response(WbError("缺少 text（待翻译文本）", http_status=400))
    source_lang = _first_str(raw, "source_lang", "sourceLang") or "auto"
    target_lang = _first_str(raw, "target_lang", "targetLang") or ""

    try:
        result = await wb_service.translate(
            text, source_lang=source_lang, target_lang=target_lang
        )
    except WbError as e:
        return _wb_error_response(e)
    return _json(result, 200, None, trace)


__all__ = ["router"]
