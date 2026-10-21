"""Database-backed authentication and authorization dependencies."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domains.identity.types import CurrentIdentity
from app.gateway.jwt import decode_token
from app.models.database import get_db
from app.models.identity import AuthSession
from app.models.role_binding import RoleBinding
from app.models.user import User

security = HTTPBearer(auto_error=False)


def _deny(http_status: int, error_key: str, message: str) -> HTTPException:
    code = 40100 if http_status == status.HTTP_401_UNAUTHORIZED else 40300
    headers = {"WWW-Authenticate": "Bearer"} if http_status == 401 else None
    return HTTPException(
        status_code=http_status,
        detail={"code": code, "error_key": error_key, "message": message},
        headers=headers,
    )


def _uuid_claim(payload: dict, name: str, *, required: bool) -> uuid.UUID | None:
    value = payload.get(name)
    if value is None and not required:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise _deny(401, "AUTH_TOKEN_INVALID", "登录凭据无效") from None


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CurrentIdentity:
    """Validate immutable claims against current user, session, and role state."""

    if credentials is None:
        raise _deny(401, "AUTH_CREDENTIALS_REQUIRED", "未提供认证凭据")
    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise _deny(401, "AUTH_TOKEN_INVALID", "登录凭据已过期或无效") from None

    user_id = _uuid_claim(payload, "sub", required=True)
    session_id = _uuid_claim(payload, "sid", required=False)
    active_role = str(payload.get("active_role") or "")
    if not active_role:
        raise _deny(401, "AUTH_TOKEN_INVALID", "登录凭据缺少当前角色")

    user = (
        await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if user is None:
        raise _deny(401, "AUTH_USER_NOT_FOUND", "账号不存在")
    if user.status != "active":
        key = (
            "AUTH_ACCOUNT_SUSPENDED"
            if user.status in {"suspended", "disabled"}
            else "AUTH_ACCOUNT_RESTRICTED"
        )
        raise _deny(403, key, "账号当前不可访问业务功能")

    claim_security_version = payload.get("sv")
    legacy_token = session_id is None
    if legacy_token:
        if not settings.auth_allow_legacy_tokens or user.security_version != 1:
            raise _deny(401, "AUTH_LEGACY_TOKEN_REJECTED", "请重新登录")
        claim_security_version = 1
    try:
        parsed_security_version = int(claim_security_version)
    except (TypeError, ValueError):
        raise _deny(401, "AUTH_TOKEN_INVALID", "登录凭据缺少安全版本") from None
    if parsed_security_version != user.security_version:
        raise _deny(401, "AUTH_SECURITY_VERSION_MISMATCH", "账号安全状态已变化，请重新登录")

    if session_id is not None:
        auth_session = (
            await db.execute(
                select(AuthSession).where(
                    AuthSession.id == session_id,
                    AuthSession.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if (
            auth_session is None
            or auth_session.revoked_at is not None
            or auth_session.expires_at <= now
            or auth_session.idle_expires_at <= now
        ):
            raise _deny(401, "AUTH_SESSION_REVOKED", "会话已失效，请重新登录")
        if auth_session.security_version != user.security_version:
            raise _deny(401, "AUTH_SECURITY_VERSION_MISMATCH", "账号安全状态已变化，请重新登录")
        if auth_session.active_role != active_role:
            raise _deny(401, "AUTH_SESSION_ROLE_MISMATCH", "会话角色已变化，请重新登录")

    binding = (
        await db.execute(
            select(RoleBinding).where(
                RoleBinding.user_id == user_id,
                RoleBinding.role == active_role,
                RoleBinding.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if binding is None or binding.status != "approved":
        raise _deny(403, "AUTH_ROLE_NOT_APPROVED", "当前角色尚未获批或已停用")

    return CurrentIdentity(
        user_id=user_id,
        session_id=session_id,
        active_role=active_role,
        security_version=user.security_version,
        legacy_token=legacy_token,
    )


def require_role(*roles: str):
    """Require the already validated active role to match one allowed role."""

    async def _check(
        current_user: Annotated[CurrentIdentity, Depends(get_current_user)],
    ) -> CurrentIdentity:
        if current_user.active_role not in roles:
            raise _deny(403, "AUTH_ROLE_REQUIRED", "当前角色无权访问该功能")
        return current_user

    return _check


def require_class_scope():
    """Retain the existing class scope compatibility dependency."""

    async def _check(
        class_id: str,
        current_user: Annotated[CurrentIdentity, Depends(get_current_user)],
    ) -> CurrentIdentity:
        class_ids = current_user.get("class_ids", [])
        if class_id not in class_ids:
            raise HTTPException(status_code=403, detail="无权访问该班级数据")
        return current_user

    return _check


def require_verified():
    """Approved active bindings are verified by construction."""

    async def _check(
        current_user: Annotated[CurrentIdentity, Depends(get_current_user)],
    ) -> CurrentIdentity:
        return current_user

    return _check
