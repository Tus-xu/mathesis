"""对象级鉴权依赖（RV02 要求 5）。

铁律：**不只依赖 JWT 有效**。
每个带 session / project / object ID 的 endpoint 都必须执行：
tenant → project → object scope → owner → capability → security_version 六层校验。

AccessContext 由**网关签名**注入（HMAC-SHA256），8300 **不自签令牌**、
**不从本地派生 capability 真值**（P00 契约 1：8000 是唯一权威）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass, field

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from ..config import gateway_hmac_secret
from ..db.engine import session_scope
from ..db.models import ResearchSessionRow
from ..domain.errors import (
    AccessContextInvalidSignature,
    AccessContextMissing,
    CapabilityMissing,
    ObjectScopeMissing,
    ProjectMismatch,
    SecurityVersionRevoked,
    SessionNotFound,
    TenantMismatch,
)
from ..observability.trace import TraceContext, parse_traceparent
from ..application import session_service as svc

WRITE_CAPABILITY = "research.session.write"
READ_CAPABILITY = "workspace.research.enter"


@dataclass
class AccessContext:
    actor_id: uuid.UUID
    tenant_id: uuid.UUID
    workspace: str
    domain: str
    capabilities: list[str]
    resource_scopes: list[dict] = field(default_factory=list)
    security_version: int = 1
    policy_version: str = "p1"
    trace_id: str = ""
    session_id: uuid.UUID | None = None

    @property
    def project_scope_ids(self) -> set[str]:
        return {s["id"] for s in self.resource_scopes if s.get("kind") == "project" and s.get("id")}

    @property
    def session_scope_ids(self) -> set[str]:
        return {s["id"] for s in self.resource_scopes if s.get("kind") == "session" and s.get("id")}


def _b64u_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def load_access_context(request: Request) -> AccessContext:
    raw = request.headers.get("X-Research-Access-Context")
    sig = request.headers.get("X-Research-Access-Sig")
    if not raw or not sig:
        raise AccessContextMissing("缺少网关注入的 AccessContext")

    secret = gateway_hmac_secret()
    if not secret:
        raise AccessContextMissing("网关签名密钥未配置（fail closed）")

    expected = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig.lower()):
        raise AccessContextInvalidSignature("AccessContext 签名校验失败")

    try:
        data = json.loads(_b64u_decode(raw).decode())
    except Exception as e:  # noqa: BLE001
        raise AccessContextInvalidSignature(f"AccessContext 解析失败: {e}") from e

    # 字段抽取**必须**一并纳入异常边界：合法签名 + 畸形字段（缺字段 / 非法 UUID /
    # 类型错）此前会抛 KeyError/ValueError 逃逸到 ASGI 层 → HTTP 500（QA 已证伪）。
    # 此处一律收窄为 401（AccessContextInvalidSignature）。针对**合法请求**是纯收窄，
    # 不改变任何合法 AccessContext 的解析结果。
    try:
        return AccessContext(
            actor_id=uuid.UUID(data["actor_id"]),
            tenant_id=uuid.UUID(data["tenant_id"]),
            workspace=data.get("workspace", ""),
            domain=data.get("domain", ""),
            capabilities=list(data.get("capabilities", [])),
            resource_scopes=list(data.get("resource_scopes", [])),
            security_version=int(data.get("security_version", 1)),
            policy_version=data.get("policy_version", "p1"),
            trace_id=data.get("trace_id", ""),
            session_id=uuid.UUID(data["session_id"]) if data.get("session_id") else None,
        )
    except AccessContextInvalidSignature:
        raise
    except (KeyError, ValueError, TypeError, AttributeError) as e:
        # 只回字段**类型**，不回显字段值（可能为攻击者可控内容）。
        raise AccessContextInvalidSignature(
            f"AccessContext 字段非法（{type(e).__name__}）"
        ) from e


def trace_from(request: Request, ctx: AccessContext) -> TraceContext:
    tp = request.headers.get("traceparent")
    parsed = parse_traceparent(tp)
    if parsed is None and ctx.trace_id:
        return TraceContext(trace_id=ctx.trace_id)
    return parsed or TraceContext()


def _check_workspace(ctx: AccessContext) -> None:
    if ctx.workspace != "research":
        raise CapabilityMissing(f"workspace={ctx.workspace!r} 无权访问 research 域")


def _check_revocation(db: Session, ctx: AccessContext) -> None:
    floor = svc.get_security_floor(db, ctx.actor_id)
    if ctx.security_version < floor:
        raise SecurityVersionRevoked(
            f"security_version {ctx.security_version} 低于服务端 floor {floor}（已撤权）",
            security_version=ctx.security_version,
            floor=floor,
        )


def require_capability(ctx: AccessContext, capability: str) -> None:
    if capability not in ctx.capabilities:
        raise CapabilityMissing(f"缺少 capability: {capability}")


def check_revocation(db: Session, ctx: AccessContext) -> None:
    """公开入口：对象级授权必须复检 security_version floor（撤权后失效）。"""
    _check_revocation(db, ctx)


def authorize_session(
    db: Session,
    ctx: AccessContext,
    session_id: uuid.UUID,
    *,
    capability: str = READ_CAPABILITY,
) -> ResearchSessionRow:
    """对象级鉴权：tenant → project → object scope → owner → capability。"""
    _check_workspace(ctx)
    _check_revocation(db, ctx)

    row = db.get(ResearchSessionRow, session_id)
    if row is None:
        raise SessionNotFound(f"会话不存在: {session_id}")

    if row.tenant_id != ctx.tenant_id:
        raise TenantMismatch(
            "会话不属于当前 tenant", session_tenant=str(row.tenant_id), actor_tenant=str(ctx.tenant_id)
        )

    # 对象级 scope
    if ctx.project_scope_ids:
        if str(row.project_id) not in ctx.project_scope_ids:
            raise ProjectMismatch(
                "scope 中的 project 与会话 project 不一致",
                session_project=str(row.project_id),
            )
    elif ctx.session_scope_ids:
        if str(row.id) not in ctx.session_scope_ids:
            raise ObjectScopeMissing("scope 中的 session 与目标 session 不一致")
    elif row.owner_id != ctx.actor_id:
        raise ObjectScopeMissing(
            "缺少对象级 scope（禁止仅凭 identity 放行）", session_id=str(row.id)
        )

    require_capability(ctx, capability)
    return row


def authorize_create(db: Session, ctx: AccessContext, tenant_id: uuid.UUID) -> None:
    _check_workspace(ctx)
    _check_revocation(db, ctx)
    if tenant_id != ctx.tenant_id:
        raise TenantMismatch("命令 tenant 与 AccessContext tenant 不一致")
    require_capability(ctx, WRITE_CAPABILITY)


def set_security_floor(actor_id: uuid.UUID, min_security_version: int) -> None:
    """撤权入口：抬高 actor 的 security_version floor。"""
    with session_scope() as db:
        svc.set_security_floor(db, actor_id, min_security_version)


# ----------------------------- 统一错误信封收敛 -----------------------------


def _validation_message(e: ValidationError) -> str:
    """把 pydantic 校验错误汇总成安全、可读的消息（只回字段名 + 原因，不回显原始 body）。"""
    parts: list[str] = []
    for err in e.errors()[:8]:
        loc = ".".join(str(x) for x in err.get("loc", ())) or "<body>"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts) or "请求体校验失败"


def input_error_response(e: Exception) -> JSONResponse | None:
    """把**可预期的输入错误**收敛为统一错误信封（`{error:{code,message}}`）。

    - pydantic `ValidationError`（手工构造请求模型时字段非法/缺失）→ **422** `VALIDATION_ERROR`；
    - 其它异常返回 `None`，**交由调用方 re-raise**（HTTP 500 + 栈日志）。

    设计取舍：只收敛**可预期**的输入错误，绝不把所有异常都吞成 4xx ——
    真正的程序缺陷必须保持 500 并留痕，否则排障会被掩盖。
    各路由的 `_err()` 应先调用本函数，再 `raise`，从而把这一映射收敛到**同一处**。
    """
    if isinstance(e, ValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": _validation_message(e)}},
        )
    return None
