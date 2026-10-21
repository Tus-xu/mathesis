"""科研端（research-vnext / 8300）反向代理网关 —— RV16 / GATE80。

## 为什么存在

8300（research-vnext）是**只信任网关注入**的服务：它的每个业务端点都要求
`X-Research-Access-Context` + `X-Research-Access-Sig`（HMAC-SHA256，密钥
`RESEARCH_GATEWAY_HMAC_SECRET`），由 8000 网关注入（见
`services/research-vnext/research_vnext/core/api/deps.py:load_access_context`）。
P00 契约：**8000 是唯一权威，8300 不自签令牌、不从本地派生 capability 真值**。

历史上 8000 只提供 `POST /api/research/derivations/verify`（`research_router.py`），
**不含** 8300 转发与 AccessContext 签发；前端只能靠 `vite dev` 里的"开发签名桥"
自读 8300 的 `.env` 密钥注入 —— 于是 **dev 能跑、prod 必 401**。本模块补齐生产链路。

## 职责（严格加法式）

1. 校验 8000 的 JWT / 会话（**复用** `app.gateway.auth.get_current_user`，不另起鉴权）；
2. 由 8000 权威身份派生 `AccessContext`（`tenant_id` / `actor_id` / `workspace=research`
   / `capabilities` / `resource_scopes` / `security_version`）；
3. 用 `RESEARCH_GATEWAY_HMAC_SECRET` 做 HMAC-SHA256 签名，注入两个头；
4. 转发到 8300；**SSE 必须 streaming 透传**（不缓冲、不聚合），否则首 token 会被
   代理层吃掉，"流式"名存实亡；
5. 统一错误码（`ACCESS_CONTEXT_MISSING` / `UPSTREAM_UNAVAILABLE` / `LLM_TIMEOUT`
   / `LLM_UNAVAILABLE` / `DEGRADED` / `REVISION_CONFLICT`）——8300 的错误信封
   原样透传（其 `error.code` 已机器可读），仅在上游不可达/超时时合成网关侧错误码。

## 安全

- 客户端自带的 `X-Research-Access-Context` / `X-Research-Access-Sig` **一律剥除**
  （防伪造），只信任本网关重新签发的值；
- 密钥只从环境变量读取，**不进日志、不进响应**；缺失即 fail closed（503）。
- 跨租户隔离：`tenant_id` 由 8000 权威派生，客户端不可指定。

## 挂载

同一组路由同时覆盖 `/api/research-vnext/*` 与 `/api/research-v1/*`（对齐 8300 双前缀），
`app/main.py` 仅新增**一行** `include_router`。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import uuid
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.domains.identity.types import CurrentIdentity
from app.gateway.auth import require_role

#: 8300 期望的注入头（**大小写不敏感**，此处用规范写法）。
ACCESS_CONTEXT_HEADER = "X-Research-Access-Context"
ACCESS_SIG_HEADER = "X-Research-Access-Sig"

#: 需要覆盖/剥除的客户端头：任何客户端自带的 AccessContext 一律不可信。
_STRIPPED_REQUEST_HEADERS = {
    "x-research-access-context",
    "x-research-access-sig",
    "host",
    "content-length",
}

#: hop-by-hop 头：不得原样转发（RFC 7230 §6.1）。
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}

#: 允许透传给 8300 的请求头白名单（其余一律丢弃，最小暴露面）。
_FORWARD_REQUEST_HEADERS = {
    "accept",
    "accept-language",
    "content-type",
    "authorization",
    "traceparent",
    "tracestate",
    "last-event-id",
    "x-request-id",
    "x-client-request-id",
}

#: 允许回传给浏览器的响应头（SSE 相关头必须保留）。
_FORWARD_RESPONSE_HEADERS = {
    "content-type",
    "cache-control",
    "connection",
    "x-accel-buffering",
    "x-trace-id",
    "x-idempotency-status",
    "retry-after",
    "last-event-id",
}

#: 需要原样透传的 HTTP 方法。
_ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]

router = APIRouter(tags=["research-gateway"])

#: 双前缀（对齐 8300 `api_prefixes()`）。客户端与前端实际调用 vnext。
_PREFIXES = ("/api/research-vnext", "/api/research-v1")

#: 已批准研究者拥有的能力集（派生自 8000 权威角色，不采信客户端自述）。
_RESEARCH_CAPABILITIES = ("workspace.research.enter", "research.session.write")


class _GatewayError(Exception):
    """网关侧错误（机读码 + 人类可读原因 + HTTP 状态）。"""

    def __init__(self, http_status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.http_status = http_status
        self.code = code
        self.message = message

    def to_response(self) -> JSONResponse:
        return JSONResponse(
            status_code=self.http_status,
            content={"error": {"code": self.code, "message": self.message}},
        )


def _platform_tenant_id() -> str:
    """8000 权威平台租户 ID。

    8000 现有数据模型没有独立 tenant 表；科研端需要一个**稳定**的租户标识来满足
    8300 的 tenant 隔离。这里优先取显式配置；未配置时用 `uuid5` 从固定命名空间
    派生（**确定性**，因此跨进程/跨重启一致，不会让已建会话"换租户"）。
    """
    explicit = settings.research_gateway_tenant_id.strip()
    if explicit:
        return explicit
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "math-arena:research:tenant"))


def _derive_access_context(identity: CurrentIdentity, *, trace_id: str) -> dict[str, Any]:
    """由 8000 权威身份派生 AccessContext。

    **不采信任何客户端字段**：`actor_id` 来自已校验 JWT 的 `sub`，`tenant_id`
    来自平台派生，`capabilities` 来自角色（已批准的 researcher），
    `security_version` 来自数据库当前值（撤权后旧令牌立即失效）。
    """
    return {
        "actor_id": str(identity.user_id),
        "tenant_id": _platform_tenant_id(),
        "workspace": "research",
        "domain": "research-vnext",
        "capabilities": list(_RESEARCH_CAPABILITIES),
        "resource_scopes": [],
        "security_version": int(identity.security_version),
        "policy_version": "p1",
        "trace_id": trace_id,
        "authority": "api",
    }


def _b64url_no_pad(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def _build_signed_headers(identity: CurrentIdentity, *, trace_id: str) -> dict[str, str]:
    secret = settings.research_gateway_hmac_secret.strip()
    if not secret:
        # fail closed：没有密钥就**不能**伪造一个无签名的 AccessContext。
        raise _GatewayError(
            503,
            "ACCESS_CONTEXT_MISSING",
            "网关注入密钥未配置（RESEARCH_GATEWAY_HMAC_SECRET），科研端不可用",
        )
    ctx = _derive_access_context(identity, trace_id=trace_id)
    # 紧凑 JSON（separators）以保证 payload 稳定、可审计；签名覆盖的是这**同一串**。
    raw = _b64url_no_pad(json.dumps(ctx, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return {ACCESS_CONTEXT_HEADER: raw, ACCESS_SIG_HEADER: _sign(secret, raw)}


def _trace_id(request: Request) -> str:
    tp = request.headers.get("traceparent")
    if tp:
        parts = tp.split("-")
        if len(parts) >= 2 and len(parts[1]) == 32:
            return parts[1]
    rid = request.headers.get("x-request-id") or ""
    return rid[:64] or uuid.uuid4().hex


def _upstream_base_url() -> str:
    return settings.research_vnext_base_url.rstrip("/")


def _build_upstream_headers(request: Request, injected: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in request.headers.items():
        lk = k.lower()
        if lk in _STRIPPED_REQUEST_HEADERS or lk in _HOP_BY_HOP:
            continue
        if lk in _FORWARD_REQUEST_HEADERS:
            out[k] = v
    out.update(injected)
    return out


def _filter_response_headers(resp: httpx.Response) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in resp.headers.items():
        if k.lower() in _HOP_BY_HOP:
            continue
        if k.lower() in _FORWARD_RESPONSE_HEADERS:
            out[k] = v
    return out


def _is_event_stream(resp: httpx.Response) -> bool:
    return "text/event-stream" in (resp.headers.get("content-type") or "").lower()


# ==================== A 方案：瞬时下游故障短重试（迭代20 门禁补齐） ====================
#
# 两条硬边界（tests/test_research_gateway_retry.py 守卫）：
# 1. **非幂等写请求绝不重试**——没有 §46 幂等键（X-Client-Request-Id 头或 JSON body
#    的 client_request_id）的 POST/PUT/PATCH/DELETE 一律不重试，否则重试会凭空多建数据；
# 2. **确定性 500 不重试**——只有响应体命中**具体瞬时特征串**（连接被掐/死锁/
#    recovery 措辞）的 5xx 才值得再试一次；代码 bug 的 500 重试三遍只是把同一错误犯三遍。
#
# 幂等安全请求（GET/HEAD/OPTIONS）+ 带幂等键的写请求：最多 attempts 次尝试，
# 指数退避由 research_gateway_retry_backoff 控制（生产 0.25s，测试可置 0）。

#: 幂等安全方法（RFC 7231 §4.2.1）：语义上可安全重发。
_SAFE_RETRY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

#: 幂等键请求头（大小写不敏感，httpx/Starlette 头查找已做 ni 处理）。
_IDEMPOTENCY_HEADER = "x-client-request-id"

#: 上游 5xx 响应体的**具体**瞬时故障特征（统一 lower 后子串匹配）。
#: 刻意不含裸 "error"/"database" 这类宽泛词——代码 bug 的 500 不配重试。
#: 证据：ASGI/asyncpg/Postgres 真实瞬时措辞（InsufficientPrivilege 在 pgbouncer
#: 抖动时冒充连接故障、server closed the connection、deadlock detected）。
_TRANSIENT_MARKERS = (
    b"could not connect to server",
    b"connection refused",
    b"connection reset",
    b"server closed the connection",
    b"terminating connection",
    b"operationalerror",
    b"interfaceerror",
    b"deadlock detected",
    b"lock wait timeout",
    b"too many connections",
    b"server_not_available",
    b"connection pool timeout",
    b"temporarily unavailable",
)


def _is_transient_body(status_code: int, content: bytes) -> bool:
    """5xx 响应体是否命中具体瞬时特征（lower 后子串匹配）。"""
    if not (500 <= status_code < 600):
        return False
    lowered = content.lower()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)


def _is_retryable_request(request: Request, body: bytes) -> bool:
    """该请求是否允许重试（幂等性判定，与上游响应无关）。

    - 安全方法（GET/HEAD/OPTIONS）：恒可重试；
    - 写方法：必须携带 §46 幂等键——`X-Client-Request-Id` 头，或 JSON body 里的
      非空 `client_request_id` 字段；非 JSON body 视为不可重试（宁可少试不多建）。
    """
    if request.method.upper() in _SAFE_RETRY_METHODS:
        return True
    if (request.headers.get(_IDEMPOTENCY_HEADER) or "").strip():
        return True
    if not body:
        return False
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    key = payload.get("client_request_id")
    return isinstance(key, str) and bool(key.strip())


async def _send_upstream(
    method: str, url: str, *, headers: dict[str, str], body: bytes, timeout: httpx.Timeout
) -> tuple[httpx.Response, httpx.AsyncClient]:
    """向上游 8300 发一次请求；返回 (streaming response, client)——调用方负责关闭。

    连接级失败统一收敛为 `_GatewayError`（可重试判定后由 _forward 决定再试或抛出）。
    """
    client = httpx.AsyncClient(timeout=timeout)
    try:
        upstream_req = client.build_request(
            method, url, headers=headers, content=body if body else None
        )
        response = await client.send(upstream_req, stream=True)
        return response, client
    except httpx.ConnectError as e:
        await client.aclose()
        raise _GatewayError(
            502,
            "UPSTREAM_UNAVAILABLE",
            f"科研端不可达：{type(e).__name__}",
        ) from e
    except httpx.TimeoutException as e:
        await client.aclose()
        raise _GatewayError(
            504,
            "LLM_TIMEOUT",
            f"科研端连接超时：{type(e).__name__}",
        ) from e
    except httpx.HTTPError as e:  # noqa: BLE001 - 统一收敛成可读的网关错误
        await client.aclose()
        raise _GatewayError(
            502,
            "UPSTREAM_UNAVAILABLE",
            f"科研端转发失败：{type(e).__name__}",
        ) from e


async def _forward(request: Request, full_path: str) -> Any:
    """核心转发：注入签名 → 转发 8300（瞬时故障短重试）→ SSE 流式透传 / 普通响应。"""
    identity: CurrentIdentity = request.state.research_identity
    trace_id = _trace_id(request)
    injected = _build_signed_headers(identity, trace_id=trace_id)

    url = f"{_upstream_base_url()}{request.url.path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    body = await request.body()
    headers = _build_upstream_headers(request, injected)
    retryable = _is_retryable_request(request, body)

    timeout = httpx.Timeout(
        connect=settings.research_gateway_connect_timeout,
        read=settings.research_gateway_read_timeout,
        write=settings.research_gateway_read_timeout,
        pool=settings.research_gateway_connect_timeout,
    )

    attempts = max(1, int(getattr(settings, "research_gateway_retry_attempts", 1) or 1))
    backoff = float(getattr(settings, "research_gateway_retry_backoff", 0.0) or 0.0)

    for attempt in range(1, attempts + 1):
        last_chance = attempt >= attempts
        try:
            upstream, client = await _send_upstream(
                request.method, url, headers=headers, body=body, timeout=timeout
            )
        except _GatewayError:
            # 连接级失败：仅幂等安全请求可重试（非幂等写必须把第一次失败如实抛出）
            if not last_chance and retryable:
                await asyncio.sleep(backoff)
                continue
            raise

        resp_headers = _filter_response_headers(upstream)
        resp_headers.setdefault("X-Trace-Id", trace_id)

        if _is_event_stream(upstream):
            # **SSE 必须 streaming 透传**：逐块 yield 原始字节，不缓冲、不聚合，
            # 否则首 token 会被代理层攒到整段结束才发出（"假流式"）。
            # （默认参数绑定当次迭代的 upstream/client，规避 B023 闭包晚绑定）
            async def _sse_gen(_upstream=upstream, _client=client):
                try:
                    async for chunk in _upstream.aiter_raw():
                        yield chunk
                finally:
                    await _upstream.aclose()
                    await _client.aclose()

            return StreamingResponse(
                _sse_gen(),
                status_code=upstream.status_code,
                headers=resp_headers,
                media_type="text/event-stream",
            )

        try:
            content = await upstream.aread()
        finally:
            await upstream.aclose()
            await client.aclose()

        # 5xx 且命中具体瞬时特征且请求幂等 → 短重试；否则如实透传（含 4xx/确定性 500）
        if not last_chance and retryable and _is_transient_body(upstream.status_code, content):
            await asyncio.sleep(backoff)
            continue

        return StreamingResponse(
            iter([content]),
            status_code=upstream.status_code,
            headers=resp_headers,
            media_type=upstream.headers.get("content-type"),
        )

    raise _GatewayError(
        502, "UPSTREAM_UNAVAILABLE", "科研端转发失败：重试预算耗尽"
    )  # pragma: no cover


async def _guard(
    request: Request,
    full_path: str,
    identity: Annotated[CurrentIdentity, Depends(require_role("researcher"))],
) -> Any:
    """统一入口：先鉴权（FastAPI 依赖），再转发。"""
    request.state.research_identity = identity
    try:
        return await _forward(request, full_path)
    except _GatewayError as e:
        return e.to_response()


# 双前缀：同一处理器，各自声明完整路径（避免多 prefix 的 include_router 需求）。
for _prefix in _PREFIXES:

    async def _handler(  # noqa: ANN202
        request: Request,
        full_path: str,
        identity: Annotated[CurrentIdentity, Depends(require_role("researcher"))],
    ) -> Any:
        return await _guard(request, full_path, identity)

    _handler.__name__ = f"research_gateway_{_prefix.strip('/').replace('/', '_')}"
    router.add_api_route(
        f"{_prefix}/{{full_path:path}}",
        _handler,
        methods=_ALL_METHODS,
        name=_handler.__name__,
    )
