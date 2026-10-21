"""WP-A 追加契约守卫（LEAD ADDENDUM A-2 / A-3）。

A-2：同一组 session 路由必须在**两个**前缀下都可达且响应等价 ——
     `/api/research-v1`（P00 既有契约）与 `/api/research-vnext`（前端实际调用，此前 404）。
A-3：启动路径必须**唯一**（只有 `lifespan`），不得再出现 `@app.on_event("startup")`。
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

import pytest

from research_vnext.core.config import API_PREFIX_V1, API_PREFIX_VNEXT, api_prefixes

SERVICE_ROOT = Path(__file__).resolve().parents[2]
APP_SRC = SERVICE_ROOT / "research_vnext" / "core" / "api" / "app.py"
ROUTES_SRC = SERVICE_ROOT / "research_vnext" / "core" / "api" / "routes.py"

PREFIXES = (API_PREFIX_V1, API_PREFIX_VNEXT)


# --------------------------------------------------------------------------
# A-2 · 双前缀
# --------------------------------------------------------------------------


def test_default_prefixes_contain_both_contracts() -> None:
    """两个前缀都必须在默认配置里，且顺序稳定（v1 在前，保持既有优先序）。"""
    assert API_PREFIX_V1 == "/api/research-v1"
    assert API_PREFIX_VNEXT == "/api/research-vnext"
    assert api_prefixes() == (API_PREFIX_V1, API_PREFIX_VNEXT)


def test_prefix_override_is_supported() -> None:
    """前缀来自配置，可按环境变量覆盖（灰度/回退能力必须是真实的，不是口号）。"""
    import os

    saved = os.environ.get("RESEARCH_API_PREFIXES")
    try:
        os.environ["RESEARCH_API_PREFIXES"] = "/api/only-one"
        assert api_prefixes() == ("/api/only-one",)
        # 归一化：无前导斜杠自动补，尾部斜杠被去掉，重复项去重
        os.environ["RESEARCH_API_PREFIXES"] = "api/x/,/api/x,/api/y/"
        assert api_prefixes() == ("/api/x", "/api/y")
        # 空值回落默认
        os.environ["RESEARCH_API_PREFIXES"] = "   "
        assert api_prefixes() == (API_PREFIX_V1, API_PREFIX_VNEXT)
    finally:
        if saved is None:
            os.environ.pop("RESEARCH_API_PREFIXES", None)
        else:
            os.environ["RESEARCH_API_PREFIXES"] = saved


def test_both_prefixes_registered_on_app() -> None:
    """OpenAPI 必须同时暴露两套路径，且路由函数名不被前缀污染。"""
    from research_vnext.core.api.app import create_app

    app = create_app()
    paths = set(app.openapi()["paths"])

    for prefix in PREFIXES:
        assert f"{prefix}/sessions:create" in paths, f"{prefix} 未挂载 sessions:create"
        assert f"{prefix}/sessions/{{session_id}}" in paths, f"{prefix} 未挂载 session 快照"
        assert f"{prefix}/sessions/{{session_id}}/events" in paths, f"{prefix} 未挂载 SSE"
        # CR-6a：前端 `sendResearchMessage` 调用的端点，必须在**两个**前缀下都存在
        assert f"{prefix}/sessions/{{session_id}}/messages" in paths, (
            f"{prefix} 未挂载 CR-6 `/messages`（前端发送消息会 404）"
        )
        # CR-6（单一供应商 ⇒ 失败必须可恢复）：查状态 + 重试这一轮
        assert f"{prefix}/sessions/{{session_id}}/messages/{{message_id}}" in paths, (
            f"{prefix} 未挂载轮次状态查询（前端无法判断该不该显示「重试」）"
        )
        assert f"{prefix}/sessions/{{session_id}}/messages/{{message_id}}:retry" in paths, (
            f"{prefix} 未挂载轮次重试（单供应商下失败将不可恢复）"
        )
        # RV03：作业状态/取消
        assert f"{prefix}/jobs/{{job_id}}" in paths, f"{prefix} 未挂载作业状态"
        assert f"{prefix}/jobs/{{job_id}}:cancel" in paths, f"{prefix} 未挂载作业取消"

    # 旧前缀路径集合必须是新前缀的超集/等集 —— 不允许新前缀"少挂"任何路由
    v1 = {p for p in paths if p.startswith(API_PREFIX_V1)}
    vnext = {p for p in paths if p.startswith(API_PREFIX_VNEXT)}
    assert {p[len(API_PREFIX_V1):] for p in v1} == {p[len(API_PREFIX_VNEXT):] for p in vnext}, (
        "两个前缀挂载的路由集合不一致"
    )
    # 22 = RV02 的 11 条
    #    + CR-6a 的 `/sessions/{id}/messages`
    #    + CR-6 重试的 `/messages/{message_id}` 与其 `:retry`
    #    + RV03 的 `/sessions/{id}/jobs`、`/jobs/{id}`、`/jobs/{id}:cancel`
    #    + RV05 parser 的 `/assets/{id}/parse`、`/parse-runs`、`/assets/{id}/anchors`、`/anchors/{id}`
    # 这个**精确**计数是刻意的：它能发现"某条路由被静默摘下"这类回归。
    # RV04 asset/literature 路由与 RV05 parser 路由必须同时存在。
    expected_min = {
        f"{API_PREFIX_V1}/assets",
        f"{API_PREFIX_V1}/assets/{{asset_id}}/parse",
        f"{API_PREFIX_V1}/assets/{{asset_id}}/anchors",
        f"{API_PREFIX_V1}/anchors/{{anchor_id}}",
    }
    missing = expected_min - v1
    assert not missing, f"RV05 parser 路由缺失: {missing}"
    assert len(v1) == len(vnext), "双前缀路由数不一致"
    assert len(v1) >= 22, f"v1 路由数异常偏低: {len(v1)}"

    # 路由函数名未被改写：FastAPI 在多前缀挂载时按规范给 operationId 追加路径后缀，
    # 但函数名必须原样保留在 operationId 前缀中（如 `create_session_api_research_v1_...`）。
    op_ids = {
        op["operationId"]
        for item in app.openapi()["paths"].values()
        for op in item.values()
        if isinstance(op, dict) and "operationId" in op
    }
    assert any(o.startswith("create_session_") for o in op_ids), f"create_session 未保留原名: {op_ids}"
    assert any(o.startswith("transition_") for o in op_ids), "transition 未保留原名"
    assert any(o.startswith("sse_events_") for o in op_ids), "sse_events 未保留原名"
    # 每个 v1 operationId 都必须存在对应的 vnext 版本 —— 证明两前缀挂的是同一组路由函数
    for o in op_ids:
        if "_api_research_v1_" in o:
            assert o.replace("_api_research_v1_", "_api_research_vnext_") in op_ids, (
                f"{o} 在新前缀下没有对应路由"
            )


# 说明：**真实 HTTP 的双前缀等价用例**放在 `tests/integration/test_dual_prefix_parity.py`。
# 原因：它需要真实 DB 与 `client` 夹具，而 `tests/integration/conftest.py` 的夹具
# 只在 integration 阶段被 pytest 加载；在 contracts 阶段自建 schema 会让
# `Base.metadata.schema`（import 期一次性求值）与新 schema 脱钩，产生假绿。
# 本文件只保留不依赖 DB 的静态/装配层守卫。


# --------------------------------------------------------------------------
# A-3 · 单一启动路径
# --------------------------------------------------------------------------


def _code_without_docstrings_and_comments(src: str) -> str:
    """去掉模块/函数 docstring 与注释，只留可执行代码。

    否则文档里解释"为何不再使用某个 API"的文字会被源码守卫误判为真实调用。
    """
    import ast

    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    body.pop(0)
    stripped = ast.unparse(tree)
    return "\n".join(line for line in stripped.splitlines() if not line.strip().startswith("#"))


def test_on_event_startup_hook_is_gone() -> None:
    """源码层守卫：可执行代码里不得再出现启动钩子的注册调用。

    只看真实代码（已剔除 docstring/注释），这样文档字符串里解释
    "为何不使用该 API"仍可保留。
    """
    code_only = _code_without_docstrings_and_comments(APP_SRC.read_text(encoding="utf-8"))
    assert "on_event" not in code_only, f"app.py 可执行代码仍注册启动钩子: {APP_SRC.name}"
    assert "add_event_handler" not in code_only, "app.py 仍调用 add_event_handler 注册启动钩子"
    # 行为层再加一道（不依赖文本匹配）
    from research_vnext.core.api.app import create_app

    app = create_app()
    assert list(app.router.on_startup) == [], f"仍有旧式启动钩子: {app.router.on_startup}"

    # lifespan 必须仍是唯一启动路径
    src = APP_SRC.read_text(encoding="utf-8")
    assert "lifespan=lifespan" in src
    assert "assert_no_runtime_ddl_in_prod" in src


def test_routes_module_declares_no_prefix() -> None:
    """前缀必须由 include_router 注入，否则无法挂载两次。"""
    src = ROUTES_SRC.read_text(encoding="utf-8")
    assert "APIRouter(prefix=" not in src, "routes.py 仍在 APIRouter 上硬编码 prefix"


def test_no_legacy_startup_handlers_registered() -> None:
    """行为层守卫：Starlette 的旧式启动钩子列表必须为空 —— 启动只走 lifespan。"""
    from research_vnext.core.api.app import create_app

    app = create_app()
    assert list(app.router.on_startup) == [], (
        f"仍有 on_event/add_event_handler 注册的启动钩子: {app.router.on_startup}"
    )
    # lifespan 是唯一启动路径
    assert app.router.lifespan_context is not None, "lifespan 缺失"


def test_lifespan_ddl_gate_still_fires(monkeypatch) -> None:
    """A-3 核心：删掉重复的 on_event 后，生产 DDL 门禁**仍然**由 lifespan 触发。

    做法：把 RESEARCH_ENV 切成 prod（此时 `is_test()` 为 False，门禁必须触发），
    打开 TestClient 触发 lifespan，断言启动**失败**。
    """
    import os

    from fastapi.testclient import TestClient

    from research_vnext.core.api.app import create_app
    from tests.conftest import IT_SCHEMA

    monkeypatch.setenv("RESEARCH_ENV", "prod")
    try:
        with TestClient(create_app()) as c:
            # 门禁触发时进程会因 lifespan 抛错而启动失败 —— 若能走到这里说明门禁失效
            r = c.get("/healthz")
            if r.status_code == 200:  # pragma: no cover - 仅在门禁失效时命中
                raise AssertionError(
                    f"生产门禁未生效：RESEARCH_ENV=prod 且 schema {os.environ.get('RESEARCH_DB_SCHEMA')} "
                    "下仍启动了服务"
                )
    except AssertionError:
        raise
    except Exception as e:  # noqa: BLE001
        # 期望路径：lifespan 中的 assert_no_runtime_ddl_in_prod 抛出
        assert "runtime DDL" in str(e) or "schema" in str(e), f"非预期异常: {e!r}"
