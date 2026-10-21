"""Rotating refresh sessions and browser cookie boundaries."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.gateway.jwt import create_token_with_role
from app.models.identity import AuthRefreshToken, AuthSession
from app.models.role_binding import RoleBinding
from app.models.user import User


class SessionError(Exception):
    def __init__(self, error_key: str, message: str, *, persist_changes: bool = False):
        super().__init__(message)
        self.error_key = error_key
        self.message = message
        self.persist_changes = persist_changes


@dataclass(frozen=True)
class IssuedSession:
    session_id: uuid.UUID
    access_token: str
    refresh_token: str
    csrf_token: str
    access_expires_in: int
    refresh_expires_at: datetime
    active_role: str = "student"


# 同一浏览器可同时保有学生/教师/科研/管理四套 cookie；端口不隔离 cookie，
# 因此不能靠"双开端口"隔离账号，只能按角色分 cookie 名。
SESSION_SCOPES = ("student", "teacher", "researcher", "admin")
# 并发刷新宽限：两个标签页同时携带同一个 refresh token 时，允许在窗口内
# 各换发一个新 child，而不是把整条 family 判为盗用并吊销。
ROTATION_GRACE_SECONDS = 30


def validate_csrf(cookie_token: str | None, header_token: str | None) -> None:
    if (
        not cookie_token
        or not header_token
        or not secrets.compare_digest(cookie_token, header_token)
    ):
        raise SessionError("AUTH_CSRF_INVALID", "安全校验失败，请刷新页面后重试")


def _scoped_cookie_names(scope: str) -> tuple[str, str]:
    return f"ma_refresh_{scope}", f"ma_csrf_{scope}"


def set_session_cookies(
    response: Response, issued: IssuedSession, *, secure: bool, scope: str | None = None
) -> None:
    """写入会话 cookie。

    除 legacy `ma_refresh`/`ma_csrf`（兼容旧客户端）外，同时写**角色级** cookie：
    `ma_refresh_<role>` / `ma_csrf_<role>`。这样教师与学生可在同一浏览器/不同端口
    同时登录，互不覆盖；刷新时由前端 `X-Auth-Scope` 选择对应 cookie。
    """
    max_age = max(0, int((issued.refresh_expires_at - datetime.now(UTC)).total_seconds()))
    effective_scope = scope or issued.active_role
    if effective_scope not in SESSION_SCOPES:
        effective_scope = None
    if effective_scope:
        refresh_name, csrf_name = _scoped_cookie_names(effective_scope)
        response.set_cookie(
            refresh_name,
            issued.refresh_token,
            max_age=max_age,
            path="/api/auth",
            secure=secure,
            httponly=True,
            samesite="lax",
        )
        response.set_cookie(
            csrf_name,
            issued.csrf_token,
            max_age=max_age,
            path="/",
            secure=secure,
            httponly=False,
            samesite="lax",
        )
    # legacy：只作为旧客户端兜底；新前端优先使用角色级 cookie。
    response.set_cookie(
        "ma_refresh",
        issued.refresh_token,
        max_age=max_age,
        path="/api/auth",
        secure=secure,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        "ma_csrf",
        issued.csrf_token,
        max_age=max_age,
        path="/",
        secure=secure,
        httponly=False,
        samesite="lax",
    )


def clear_session_cookies(
    response: Response,
    *,
    secure: bool,
    scope: str | None = None,
    all_scopes: bool = False,
) -> None:
    """清理 cookie。

    - scope 指定：只清该角色 + legacy（不碰其它角色，避免教师登出踢掉学生）；
    - all_scopes：清所有角色 + legacy（logout-all）。
    """
    targets: list[tuple[str, bool, str]] = []
    scopes = SESSION_SCOPES if all_scopes else ((scope,) if scope in SESSION_SCOPES else ())
    for item in scopes:
        refresh_name, csrf_name = _scoped_cookie_names(item)
        targets.extend(((refresh_name, True, "/api/auth"), (csrf_name, False, "/")))
    if all_scopes or scope is None or scope in SESSION_SCOPES:
        targets.extend(
            (("ma_refresh", True, "/api/auth"), ("ma_csrf", False, "/"), ("ma_csrf", False, "/api/auth"))
        )
    for name, httponly, path in targets:
        response.delete_cookie(
            name,
            path=path,
            secure=secure,
            httponly=httponly,
            samesite="lax",
        )


class SessionService:
    access_expires_in = 15 * 60

    def __init__(
        self,
        *,
        refresh_pepper: str,
        now: Callable[[], datetime] | None = None,
        token_generator: Callable[[], str] | None = None,
        rotation_grace_seconds: int = ROTATION_GRACE_SECONDS,
    ):
        self.rotation_grace_seconds = max(0, int(rotation_grace_seconds))
        if not refresh_pepper:
            raise ValueError("refresh token pepper must not be empty")
        self.refresh_pepper = refresh_pepper.encode("utf-8")
        self.now = now or (lambda: datetime.now(UTC))
        self.token_generator = token_generator or (lambda: secrets.token_urlsafe(48))

    def _hash_refresh(self, raw_token: str) -> str:
        return hmac.new(
            self.refresh_pepper,
            raw_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _policy(role: str, remember: bool) -> tuple[timedelta, timedelta]:
        if role == "admin":
            return timedelta(hours=settings.auth_admin_absolute_hours), timedelta(
                minutes=settings.auth_admin_idle_minutes
            )
        if remember:
            return timedelta(hours=settings.auth_remember_absolute_hours), timedelta(
                minutes=settings.auth_remember_idle_minutes
            )
        return timedelta(hours=settings.auth_standard_absolute_hours), timedelta(
            minutes=settings.auth_standard_idle_minutes
        )

    def _issued(
        self,
        user: User,
        auth_session: AuthSession,
        refresh_token: str,
        *,
        csrf_token: str | None = None,
    ) -> IssuedSession:
        return IssuedSession(
            session_id=auth_session.id,
            access_token=create_token_with_role(
                str(user.id),
                auth_session.active_role,
                session_id=str(auth_session.id),
                security_version=user.security_version,
            ),
            refresh_token=refresh_token,
            # 刷新时沿用客户端已有的 CSRF cookie；多标签页并发刷新不会因为
            # 一个标签页换发了新 CSRF 而让另一个标签页的 cookie/header 失配。
            csrf_token=csrf_token or secrets.token_urlsafe(32),
            access_expires_in=self.access_expires_in,
            refresh_expires_at=auth_session.expires_at,
            active_role=auth_session.active_role,
        )

    async def issue(
        self,
        db: AsyncSession,
        user: User,
        active_role: str,
        *,
        remember: bool,
        pending_role: str | None = None,
        device_name: str | None = None,
        user_agent_digest: str | None = None,
        ip_prefix: str | None = None,
    ) -> IssuedSession:
        if pending_role not in {None, "student", "teacher", "researcher"}:
            raise ValueError("pending role must be student, teacher, researcher, or None")
        now = self.now()
        absolute, idle = self._policy(active_role, remember)
        family_id = uuid.uuid4()
        auth_session = AuthSession(
            id=uuid.uuid4(),
            user_id=user.id,
            token_family_id=family_id,
            security_version=user.security_version,
            active_role=active_role,
            pending_role=pending_role,
            remember=remember,
            device_name=device_name,
            user_agent_digest=user_agent_digest,
            ip_prefix=ip_prefix,
            last_seen_at=now,
            idle_expires_at=now + idle,
            expires_at=now + absolute,
        )
        db.add(auth_session)
        await db.flush()
        raw_refresh = self.token_generator()
        db.add(
            AuthRefreshToken(
                session_id=auth_session.id,
                token_family_id=family_id,
                token_hash=self._hash_refresh(raw_refresh),
                status="active",
                issued_at=now,
                expires_at=auth_session.expires_at,
            )
        )
        await db.flush()
        return self._issued(user, auth_session, raw_refresh)

    async def _revoke_family(
        self,
        db: AsyncSession,
        auth_session: AuthSession,
        reason: str,
    ) -> None:
        now = self.now()
        auth_session.revoked_at = now
        auth_session.revoke_reason = reason
        await db.execute(
            update(AuthRefreshToken)
            .where(
                AuthRefreshToken.token_family_id == auth_session.token_family_id,
                AuthRefreshToken.status == "active",
            )
            .values(status="revoked")
        )
        await db.flush()

    async def rotate(
        self, db: AsyncSession, raw_refresh: str, *, csrf_token: str | None = None
    ) -> IssuedSession:
        token_hash = self._hash_refresh(raw_refresh)
        token = (
            await db.execute(
                select(AuthRefreshToken)
                .where(AuthRefreshToken.token_hash == token_hash)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if token is None:
            raise SessionError("AUTH_REFRESH_INVALID", "刷新凭据无效")
        auth_session = (
            await db.execute(
                select(AuthSession)
                .where(AuthSession.id == token.session_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if auth_session is None:
            raise SessionError("AUTH_REFRESH_INVALID", "刷新凭据无效")
        now = self.now()
        if (
            auth_session.revoked_at is not None
            or auth_session.expires_at <= now
            or auth_session.idle_expires_at <= now
            or token.expires_at <= now
        ):
            await self._revoke_family(db, auth_session, "session_expired")
            raise SessionError("AUTH_SESSION_REVOKED", "会话已失效", persist_changes=True)
        user = (
            await db.execute(select(User).where(User.id == auth_session.user_id).with_for_update())
        ).scalar_one_or_none()
        if user is None or user.status != "active":
            await self._revoke_family(db, auth_session, "account_restricted")
            raise SessionError("AUTH_ACCOUNT_RESTRICTED", "账号当前不可用", persist_changes=True)
        binding = (
            await db.execute(
                select(RoleBinding).where(
                    RoleBinding.user_id == user.id,
                    RoleBinding.role == auth_session.active_role,
                    RoleBinding.status == "approved",
                    RoleBinding.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if binding is None or auth_session.security_version != user.security_version:
            await self._revoke_family(db, auth_session, "security_state_changed")
            raise SessionError("AUTH_SECURITY_VERSION_MISMATCH", "账号安全状态已变化", persist_changes=True)

        if token.status == "used":
            # 多标签页并发刷新：同一个 refresh token 在宽限窗口内被第二次使用，
            # 视为并发轮换（不是盗用），各发一个新 child，不吊销 family。
            used_at = token.used_at
            within_grace = (
                used_at is not None
                and (now - used_at).total_seconds() <= self.rotation_grace_seconds
            )
            if within_grace:
                return await self._issue_child(
                    db, auth_session, user, token.id, now, csrf_token
                )
            await self._revoke_family(db, auth_session, "refresh_replay")
            raise SessionError(
                "AUTH_REFRESH_REPLAYED",
                "检测到刷新凭据重放，请重新登录",
                persist_changes=True,
            )
        if token.status != "active":
            await self._revoke_family(db, auth_session, "refresh_replay")
            raise SessionError(
                "AUTH_REFRESH_REPLAYED",
                "检测到刷新凭据重放，请重新登录",
                persist_changes=True,
            )

        token.status = "used"
        token.used_at = now
        return await self._issue_child(db, auth_session, user, token.id, now, csrf_token)

    async def _issue_child(
        self,
        db: AsyncSession,
        auth_session: AuthSession,
        user: User,
        parent_token_id: uuid.UUID,
        now: datetime,
        csrf_token: str | None,
    ) -> IssuedSession:
        raw_child = self.token_generator()
        db.add(
            AuthRefreshToken(
                session_id=auth_session.id,
                token_family_id=auth_session.token_family_id,
                parent_token_id=parent_token_id,
                token_hash=self._hash_refresh(raw_child),
                status="active",
                issued_at=now,
                expires_at=auth_session.expires_at,
            )
        )
        _, idle = self._policy(auth_session.active_role, auth_session.remember)
        auth_session.last_seen_at = now
        auth_session.idle_expires_at = min(now + idle, auth_session.expires_at)
        await db.flush()
        return self._issued(user, auth_session, raw_child, csrf_token=csrf_token)

    async def revoke_by_refresh(
        self, db: AsyncSession, raw_refresh: str, reason: str = "logout"
    ) -> None:
        token = (
            await db.execute(
                select(AuthRefreshToken).where(
                    AuthRefreshToken.token_hash == self._hash_refresh(raw_refresh)
                )
            )
        ).scalar_one_or_none()
        if token is None:
            return
        auth_session = await db.get(AuthSession, token.session_id)
        if auth_session is not None and auth_session.revoked_at is None:
            await self._revoke_family(db, auth_session, reason)

    async def revoke_all(self, db: AsyncSession, user_id: uuid.UUID, reason: str) -> None:
        now = self.now()
        await db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now, revoke_reason=reason)
        )
        await db.flush()
