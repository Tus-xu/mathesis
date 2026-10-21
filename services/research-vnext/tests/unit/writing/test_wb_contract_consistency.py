"""RV-WB · 跨端点契约一致性测试。

理由（team-lead 定案）：`/xf/*` 保留做 A/B 与回退，所以 `/wb/latex:edit` 与
`/xf/workflows/latex_editing/run` 必须**同时挂载、双前缀可达**，且 wb 的成功响应
键集**包含**前端渲染契约 `{success, changes, final_updated_code, security_msg}`，
这样将来把某功能切回讯飞时前端一套渲染分支零改动。

注：两者的**请求体形状**不同（`/wb` 扁平字段；`/xf` 为讯飞 `{parameters:{...}}`）——
由 `GET /wb/capabilities[].requestSchema` 数据驱动；本测试断言二者**可达性**与
wb 的**响应键集**一致契约，而非字面同体。
"""

from __future__ import annotations

import pytest

from research_vnext.core.llm import gateway

VALID_TEX = "\\documentclass{article}\n\\begin{document}\nHello $x^2$\n\\end{document}\n"
EDITED_TEX = "\\documentclass{article}\n\\begin{document}\nHi $x^2$\n\\end{document}\n"
REQUIRED_KEYS = {"success", "changes", "final_updated_code", "security_msg"}


class FakeBackend:
    name = "fake"
    available = True

    def __init__(self, text: str) -> None:
        self._text = text

    @property
    def model(self) -> str:
        return "fake-model"

    async def acompletion(self, *, model=None, messages, stream=False, **kw):  # noqa: ANN001,ANN003
        return gateway.Completion(text=self._text, model="fake-model", provider="fake", raw={})


@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch):
    from fastapi import FastAPI

    from research_vnext.core.api import wb_routes, xf_routes
    from research_vnext.core.xf import service as xf_service
    from research_vnext.core.xf.models import XfResult

    async def _stub_run(workflow, parameters, uid=None):
        return XfResult(
            workflow=workflow, flow_id="f", latency_ms=1, result={"text": "ok"}, raw_text="ok"
        )

    monkeypatch.setattr(xf_service, "run", _stub_run)
    gateway.set_backend(FakeBackend(EDITED_TEX))
    from research_vnext.core.config import api_prefixes

    a = FastAPI()
    for prefix in api_prefixes():
        a.include_router(wb_routes.router, prefix=prefix)
        a.include_router(xf_routes.router, prefix=prefix)
    yield a
    gateway.set_backend(None)


def _paths(app) -> set[str]:
    """从 OpenAPI schema 取**已挂载**路径（跨 FastAPI 版本稳定）。

    注意：不能扫 `app.routes[].path` —— 本版本 `include_router` 产生的是
    `_IncludedRouter` 包装对象（无 `.path`），直接扫会得到空串。OpenAPI schema
    是官方稳定入口，且能反映 prefix 叠加后的真实路径。
    """
    return set(app.openapi().get("paths", {}).keys())


def test_both_endpoints_mounted_under_both_prefixes(app) -> None:
    from research_vnext.core.config import api_prefixes

    paths = _paths(app)
    assert paths, "OpenAPI schema 无路径，挂载失败"
    for prefix in api_prefixes():
        assert f"{prefix}/wb/latex:edit" in paths
        assert f"{prefix}/xf/workflows/{{workflow}}/run" in paths


def test_both_endpoints_reachable_and_wb_keys_align(app, ctx_factory, hdr_factory) -> None:
    from fastapi.testclient import TestClient

    from research_vnext.core.config import api_prefixes

    with TestClient(app) as c:
        for prefix in api_prefixes():
            headers = hdr_factory(ctx_factory())
            wb = c.post(
                f"{prefix}/wb/latex:edit",
                headers=headers,
                json={"file_content": VALID_TEX, "instruction": "tidy"},
            )
            assert wb.status_code == 200, f"{prefix} wb: {wb.text}"
            assert REQUIRED_KEYS <= set(wb.json()), f"wb 成功响应缺少契约键: {set(wb.json())}"

            xf = c.post(
                f"{prefix}/xf/workflows/latex_editing/run",
                headers=hdr_factory(ctx_factory()),
                json={"parameters": {"AGENT_USER_INPUT": "x"}},
            )
            assert xf.status_code == 200, f"{prefix} xf: {xf.text}"


def test_wb_guard_error_body_keeps_contract_keys(app, ctx_factory, hdr_factory) -> None:
    from fastapi.testclient import TestClient

    from research_vnext.core.config import api_prefixes

    with TestClient(app) as c:
        for prefix in api_prefixes():
            # 源码命中
            r = c.post(
                f"{prefix}/wb/latex:edit",
                headers=hdr_factory(ctx_factory()),
                json={"file_content": "\\write18{id}", "instruction": "x"},
            )
            assert r.status_code == 400, f"{prefix}: {r.text}"
            body = r.json()
            assert REQUIRED_KEYS <= set(body), f"guard 错误体缺少契约键: {set(body)}"
            assert body["success"] is False

            # 指令命中（源码干净）
            r2 = c.post(
                f"{prefix}/wb/latex:edit",
                headers=hdr_factory(ctx_factory()),
                json={"file_content": VALID_TEX, "instruction": "加入 \\write18{id}"},
            )
            assert r2.status_code == 400, f"{prefix}: {r2.text}"
            b2 = r2.json()
            assert REQUIRED_KEYS <= set(b2)
            assert b2["success"] is False and b2["security_msg"]
