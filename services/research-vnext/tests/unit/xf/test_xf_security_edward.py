"""RV-XF 安全回归单测（QA/Edward 对抗性验证新增；纯函数 + MockTransport，不触网/不编译）。

覆盖任务书 A2/A3、B、C、D：
- A3 编译器 argv 恒含 `-no-shell-escape`；docker 隔离参数齐备；容器不继承宿主 env。
- A2 `_safe_filename` 消解一切路径语义；`main.tex` 由 argv 钉死。
- B  脱敏铁律：任意错误路径 / health / workflows 均不得出现真实 key/secret。
- C  工作流白名单；未知/畸形 workflow → 400。
- D  `code`→异常/HTTP 映射逐条核对（含 20201→503、22500→502、未知码→502）。
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import pytest

from research_vnext.core.export import compiler as export_compiler
from research_vnext.core.export.compiler import build_xelatex_argv
from research_vnext.core.xf import client as xf_client
from research_vnext.core.xf import service as xf_service
from research_vnext.core.xf.latex import _safe_filename, _validate_tex_source
from research_vnext.core.xf.models import (
    XF_CONCURRENCY,
    XF_NOT_CONFIGURED,
    XF_QUOTA,
    XF_RATE_LIMITED,
    XF_TEX_SOURCE_INVALID,
    XF_TIMEOUT,
    XF_TRANSPORT,
    XF_UNAUTHORIZED,
    XF_UNKNOWN_WORKFLOW,
    XF_UPSTREAM_ERROR,
    XfConcurrency,
    XfError,
    XfNotConfigured,
    XfQuotaExceeded,
    XfRateLimited,
    XfTimeout,
    XfTransportError,
    XfUnauthorized,
    XfUpstreamError,
    load_settings,
)

SECRET = "SUPERSECRET_SENTINEL_8f3a"
API_KEY = "KEY_SENTINEL_1122334455"
APP_ID = "appid_sentinel"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    xf_client._semaphores.clear()
    monkeypatch.setenv("RESEARCH_XF_ENABLED", "true")
    monkeypatch.setenv("RESEARCH_XF_BASE_URL", "https://xingchen-api.xf-yun.com")
    monkeypatch.setenv("RESEARCH_XF_APP_ID", APP_ID)
    monkeypatch.setenv("RESEARCH_XF_API_KEY", API_KEY)
    monkeypatch.setenv("RESEARCH_XF_API_SECRET", SECRET)
    monkeypatch.setenv("RESEARCH_XF_FLOW_WRITING_POLISH", "7495986277563379712")


def _run(handler, workflow="writing_polish"):
    transport = httpx.MockTransport(handler)
    return asyncio.run(
        xf_client.run_workflow(workflow, {"AGENT_USER_INPUT": "x"}, uid="u1", transport=transport)
    )


# ==========================================================================
# A3 · 编译器 argv / docker 隔离（静态 + 实证）
# ==========================================================================


@pytest.mark.parametrize("workdir_name", [".", "sub", "a b"])
@pytest.mark.parametrize("passes", [1, 2, 3])
def test_argv_always_has_no_shell_escape(workdir_name, passes) -> None:
    argv = build_xelatex_argv(workdir_name=workdir_name, passes=passes)
    assert "-no-shell-escape" in argv, "铁律：任何调用路径都必须带 -no-shell-escape"
    assert not any(a == "-shell-escape" or a.startswith("--shell-escape") for a in argv)
    assert argv[0] == "xelatex"
    assert "-interaction=nonstopmode" in argv
    assert "-halt-on-error" in argv
    assert argv[-1] == "main.tex", "输入文件名被钉死为 main.tex（无注入面）"


def test_docker_run_flags_are_hardened(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """捕获 docker run 的真实 argv，断言隔离参数齐备且容器不继承宿主 env。"""
    calls: list = []
    monkeypatch.setenv("RESEARCH_XF_API_SECRET", "SHOULD_NOT_ENTER_CONTAINER")

    class _Proc:
        returncode = 0
        stdout = b"fake log"
        stderr = b""

    def _fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _Proc()

    monkeypatch.setattr(export_compiler.subprocess, "run", _fake_run)
    runner = export_compiler._docker_runner("math-arena-tex:0.1.0", cpus="2", memory="2g", pids_limit="128")
    runner(["xelatex", "-no-shell-escape", "main.tex"], workdir=tmp_path, timeout_seconds=5, env=None)

    run_calls = [(c, k) for c, k in calls if c[:3] == ["docker", "run", "--rm"]]
    assert run_calls, f"未捕获到 docker run 调用：{[c for c, _ in calls]}"
    cmd, kwargs = run_calls[0]
    assert "--network" in cmd and cmd[cmd.index("--network") + 1] == "none"
    assert "--read-only" in cmd
    assert "--user" in cmd and cmd[cmd.index("--user") + 1] == "65534:65534"  # 非 root
    assert "--cpus" in cmd and "--memory" in cmd and "--pids-limit" in cmd
    assert "--tmpfs" in cmd
    # 容器环境只透传 HOME，不继承宿主（宿主有真实密钥）
    env_flags = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-e"]
    assert env_flags == ["HOME=/tmp"], f"容器 env 只允许 HOME，实际 {env_flags}"
    assert "--env-file" not in cmd
    assert "SHOULD_NOT_ENTER_CONTAINER" not in " ".join(cmd)
    assert kwargs.get("env") is None
    # 超时兜底：命名容器 + 显式 docker rm -f（G5 根因修复的回归锚点）
    assert "--name" in cmd, "容器必须唯一命名，否则超时后无法精确清理"
    cleanup = [c for c, _ in calls if c[:3] == ["docker", "rm", "-f"]]
    assert cleanup, "缺少超时/异常后的容器清理调用"


# ==========================================================================
# A2 · filename 注入消解
# ==========================================================================


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\evil.tex", "evil.tex"),
        ("/abs/path/main.tex", "main.tex"),
        ("main.tex -shell-escape", "main.tex-shell-escape"),
        ("a b c.tex", "abc.tex"),
        ("", "main.tex"),
        ("x" * 300 + ".tex", ("x" * 300 + ".tex")[:128]),
    ],
)
def test_safe_filename_neutralizes_path_semantics(raw, expected) -> None:
    out = _safe_filename(raw)
    assert "/" not in out and "\\" not in out and " " not in out
    assert out == expected


# ==========================================================================
# B · 脱敏铁律
# ==========================================================================


@pytest.mark.parametrize(
    "resp",
    [
        httpx.Response(401, json={"message": f"bad key={SECRET}"}),
        httpx.Response(403, json={"message": f"secret={API_KEY}"}),
        httpx.Response(500, text=f"trace {SECRET} {API_KEY}"),
        httpx.Response(200, json={"code": 20357, "message": f"conc {SECRET}"}),
        httpx.Response(200, json={"code": 99999, "message": f"weird {API_KEY}"}),
        httpx.Response(404, text=f"not found {SECRET}"),
    ],
)
def test_no_credential_leaks_anywhere(resp, caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return resp

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(Exception) as ei:  # noqa: B017
            _run(handler)
    blob = str(ei.value) + caplog.text
    assert SECRET not in blob
    assert API_KEY not in blob


def test_health_and_workflows_never_echo_credentials() -> None:
    import json as _json

    h = _json.dumps(xf_service.health(), ensure_ascii=False)
    w = _json.dumps(xf_service.available_workflows(), ensure_ascii=False)
    for blob in (h, w):
        assert SECRET not in blob
        assert API_KEY not in blob
    # appId 允许出现但必须掩码
    assert "appid_sentinel" not in h  # 前4位+***，原文不得出现
    assert "appi***" in h


def test_workflows_flowid_is_masked() -> None:
    items = xf_service.available_workflows()
    wp = next(i for i in items if i["key"] == "writing_polish")
    assert wp["configured"] is True
    # 允许掩码；此处实现为 a…b 形式，绝不含完整 flow_id
    assert wp["flowId"] != "7495986277563379712"
    assert "…" in wp["flowId"] or "***" in wp["flowId"]


# ==========================================================================
# C · 工作流白名单
# ==========================================================================


@pytest.mark.parametrize(
    "wf",
    ["does_not_exist", "../writing_polish", "writing_polish/../latex_editing",
     "%2e%2e%2fwriting_polish", "x" * 500, "writing_polish\x00", ""],
)
def test_workflow_whitelist_rejects_all_unknown(wf) -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("未知 workflow 绝不应对上游发起请求")

    with pytest.raises(XfError) as ei:
        _run(handler, workflow=wf)
    assert ei.value.http_status == 400
    assert ei.value.code in {XF_UNKNOWN_WORKFLOW, XF_NOT_CONFIGURED}


# ==========================================================================
# D · 错误码→异常/HTTP 映射（逐条）
# ==========================================================================


@pytest.mark.parametrize(
    "code,exc,exp_code,exp_http",
    [
        (20373, XfQuotaExceeded, XF_QUOTA, 429),
        (20201, XfNotConfigured, XF_NOT_CONFIGURED, 503),
        (20357, XfConcurrency, XF_CONCURRENCY, 429),
        (20375, XfRateLimited, XF_RATE_LIMITED, 429),
        (20376, XfRateLimited, XF_RATE_LIMITED, 429),
        (20902, XfRateLimited, XF_RATE_LIMITED, 429),
        (20903, XfRateLimited, XF_RATE_LIMITED, 429),
        (20804, XfTimeout, XF_TIMEOUT, 504),
        (22500, XfUpstreamError, XF_UPSTREAM_ERROR, 502),   # Start node protocol error
        (99999, XfUpstreamError, XF_UPSTREAM_ERROR, 502),   # 未知码
    ],
)
def test_upstream_code_to_http_mapping(code, exc, exp_code, exp_http) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": code, "message": "boom"})

    with pytest.raises(exc) as ei:
        _run(handler)
    assert ei.value.code == exp_code
    assert ei.value.http_status == exp_http


def test_http_401_is_unauthorized_502_and_passthrough() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "HMAC secret key does not match"})

    with pytest.raises(XfUnauthorized) as ei:
        _run(handler)
    assert ei.value.code == XF_UNAUTHORIZED
    assert ei.value.http_status == 502  # 上游鉴权失败 → 本地 502
    assert "HMAC secret key does not match" in str(ei.value)


def test_transport_error_is_502() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(XfTransportError) as ei:
        _run(handler)
    assert ei.value.code == XF_TRANSPORT
    assert ei.value.http_status == 502


def test_timeout_is_504(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_XF_TIMEOUT_SECONDS", "1")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(XfTimeout) as ei:
        _run(handler)
    assert ei.value.code == XF_TIMEOUT
    assert ei.value.http_status == 504


def test_retry_after_present_for_429() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 20375, "message": "limited"})

    with pytest.raises(XfRateLimited) as ei:
        _run(handler)
    assert ei.value.retry_after and ei.value.retry_after > 0


def test_not_configured_when_credentials_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("RESEARCH_XF_BASE_URL", "RESEARCH_XF_APP_ID",
                 "RESEARCH_XF_API_KEY", "RESEARCH_XF_API_SECRET"):
        monkeypatch.delenv(name, raising=False)
    assert load_settings().has_credentials is False

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("未配置不应触网")

    with pytest.raises(XfNotConfigured) as ei:
        _run(handler)
    assert ei.value.http_status == 503


# ==========================================================================
# D11 · 并发闸（RESEARCH_XF_MAX_CONCURRENCY）确实限制在途调用
# ==========================================================================


def test_concurrency_gate_limits_inflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_XF_MAX_CONCURRENCY", "2")
    xf_client._semaphores.clear()
    state = {"cur": 0, "max": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.05)
        state["cur"] -= 1
        return httpx.Response(200, json={"code": 0, "choices": [{"messages": [{"content": "{}"}]}]})

    async def main() -> None:
        transport = httpx.MockTransport(handler)
        await asyncio.gather(
            *[xf_client.run_workflow("writing_polish", {"AGENT_USER_INPUT": "x"},
                                     uid=f"u{i}", transport=transport) for i in range(6)]
        )

    asyncio.run(main())
    assert state["max"] <= 2, f"并发闸失效：观测到 {state['max']} 路同时打到上游"
    assert state["max"] >= 2, "并发闸过严（未达到配置的 2 路）"


# ==========================================================================
# C8 · AccessContext 解析健壮性（500 根因）
# ==========================================================================


def _signed_request(ctx: dict, secret: str):
    import base64
    import hashlib
    import hmac
    import json

    from starlette.requests import Request

    payload = base64.urlsafe_b64encode(
        json.dumps(ctx, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    scope = {
        "type": "http", "method": "GET", "path": "/x",
        "headers": [
            (b"x-research-access-context", payload.encode()),
            (b"x-research-access-sig", sig.encode()),
        ],
    }
    return Request(scope)


def _full_ctx() -> dict:
    return {
        "actor_id": "d1f1756e-6e5a-4a3f-9b0e-52f0a1b2c3d4",
        "tenant_id": "a1a2a3a4-b1b2-4c1c-8d1d-000000000001",
        "session_id": "00000000-0000-4000-8000-0000000000ff",
        "workspace": "research", "domain": "research-vnext",
        "capabilities": ["workspace.research.enter"],
        "resource_scopes": [], "security_version": 1, "policy_version": "p1",
        "trace_id": "0" * 32,
    }


def test_access_context_valid_signature_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    from research_vnext.core.api import deps

    monkeypatch.setattr(deps, "gateway_hmac_secret", lambda: SECRET)
    ctx = deps.load_access_context(_signed_request(_full_ctx(), SECRET))
    assert str(ctx.actor_id).startswith("d1f1756e")


@pytest.mark.xfail(strict=False, reason="BUG: 合法签名但缺字段 → KeyError 未捕获 → HTTP 500（应为 401）")
def test_access_context_missing_field_is_401_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    from research_vnext.core.api import deps
    from research_vnext.core.domain.errors import DomainError

    monkeypatch.setattr(deps, "gateway_hmac_secret", lambda: SECRET)
    with pytest.raises(DomainError):
        deps.load_access_context(_signed_request({"workspace": "research"}, SECRET))


@pytest.mark.xfail(strict=False, reason="BUG: 合法签名但非法 UUID → ValueError 未捕获 → HTTP 500（应为 401）")
def test_access_context_bad_uuid_is_401_not_500(monkeypatch: pytest.MonkeyPatch) -> None:
    from research_vnext.core.api import deps
    from research_vnext.core.domain.errors import DomainError

    monkeypatch.setattr(deps, "gateway_hmac_secret", lambda: SECRET)
    bad = _full_ctx()
    bad["actor_id"] = "not-a-uuid"
    with pytest.raises(DomainError):
        deps.load_access_context(_signed_request(bad, SECRET))


def _signed_headers(ctx: dict, secret: str) -> dict:
    import base64
    import hashlib
    import hmac
    import json

    payload = base64.urlsafe_b64encode(
        json.dumps(ctx, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return {"X-Research-Access-Context": payload, "X-Research-Access-Sig": sig,
            "Content-Type": "application/json"}


@pytest.mark.xfail(strict=False, reason="BUG: xf run 端点只验签名，不做 capability/workspace 授权（越权可调用）")
def test_xf_run_enforces_capability_and_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from research_vnext.core.api import deps, xf_routes
    from research_vnext.core.xf.models import XfResult

    monkeypatch.setattr(deps, "gateway_hmac_secret", lambda: SECRET)

    async def _stub_run(workflow, parameters, uid=None):
        return XfResult(workflow=workflow, flow_id="f", latency_ms=1,
                        result={"text": "ok"}, raw_text="ok")

    monkeypatch.setattr(xf_service, "run", _stub_run)

    app = FastAPI()
    app.include_router(xf_routes.router, prefix="/api/research-vnext")
    client = TestClient(app)

    # 合法签名，但既无 capability 又越权 workspace
    ctx = _full_ctx()
    ctx["capabilities"] = []
    ctx["workspace"] = "classroom"
    r = client.post(
        "/api/research-vnext/xf/workflows/writing_polish/run",
        headers=_signed_headers(ctx, SECRET),
        json={"parameters": {}},
    )
    assert r.status_code in (401, 403), f"越权请求竟被放行：HTTP {r.status_code} {r.text[:120]}"


# ==========================================================================
# Round-2 · 新前置校验 `_validate_tex_source`（实际执行层，QA 复验）
#   背景：export 层 `guard_latex` 逐字正则可被 `^^5c` 等绕过；工程师在 xf 层
#   新增 `_validate_tex_source` 作为进入容器前的强制层。以下锚定该层的实际行为。
# ==========================================================================


_BLOCKED_SOURCES = [
    r"\write18{rm -rf /}",
    r"\immediate\write18{id}",
    r"\input{/etc/passwd}",
    r"\include{/etc/passwd}",
    r"\openout1=/etc/x",
    r"\lstinputlisting{/etc/passwd}",
    r"\VerbatimInput{/etc/passwd}",
    r"\@@input{/etc/passwd}",
    r"\input{../../etc/passwd}",
    "^^5cinput{/etc/passwd}",
    "^^5cwrite18{id}",
    "^^5ccsname write^^5cendcsname",
    r"\includegraphics{/etc/passwd}",
    r"\InputIfFileExists{/etc/passwd}{}{}",
    r"\IfFileExists{/etc/passwd}{}{}",
    r"\@input{/etc/passwd}",
    r"\expandafter\input{/etc/passwd}",
    r"\usepackage{shellesc}\ShellEscape{id}",
    r"\usepackage{minted}",
    r"\usepackage{listings}",
    r"\usepackage{fancyvrb}",
]


@pytest.mark.parametrize("src", _BLOCKED_SOURCES)
def test_validate_tex_source_blocks_attack(src: str) -> None:
    with pytest.raises(XfError) as ei:
        _validate_tex_source(src)
    assert ei.value.code == XF_TEX_SOURCE_INVALID
    assert ei.value.http_status == 400


_LEGIT_SOURCES = [
    r"\documentclass{article}\begin{document}$x^2+1$\end{document}",
    r"\documentclass{article}\usepackage{amsmath}\begin{document}a\end{document}",
    r"\documentclass{article}\usepackage{hyperref}\begin{document}\href{https://example.com}{x}\end{document}",
    r"\documentclass{article}\usepackage[utf8]{inputenc}\begin{document}hi\end{document}",
    r"\documentclass{article}\begin{document}\includegraphics{fig.png}\end{document}",
]


@pytest.mark.parametrize("src", _LEGIT_SOURCES)
def test_validate_tex_source_allows_legit(src: str) -> None:
    _validate_tex_source(src)  # 不抛即通过（防过度拦截）


@pytest.mark.xfail(strict=False, reason="残余：绝对路径正则可被 \\string / \\char / 非花括号分隔器规避")
@pytest.mark.parametrize(
    "src",
    [
        r"\includegraphics{\string/etc/passwd}",
        r"\includegraphics{\char47etc/passwd}",
        r'\XeTeXpdffile"/etc/passwd"',
    ],
)
def test_validate_tex_source_residual_path_evasion(src: str) -> None:
    """这些目前能进容器（仅能‘尝试打开’文件，不能回读文本）；记录为残余缺口。"""
    with pytest.raises(XfError):
        _validate_tex_source(src)
