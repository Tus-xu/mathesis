"""服务鉴权（8200 自持，不依赖 8100 core.security）。

模式：
1. Bearer 教师 JWT（HS256，jwt_secret 校验；claims sub/name/role，失效时间校验）——生产主通道；
2. Bearer 内部服务令牌（settings.internal_token 常量时间比较）——局点/测试唯一入口；
3. 两者均缺/无效 → 40100。
"""
from __future__ import annotations

import hmac
from datetime import UTC, datetime

import jwt
from fastapi import Request

from app.config import get_settings
from app.errors import ApiError, ErrorCode


class ServiceUser:
    def __init__(self, principal: str, source: str, claims: dict | None = None):
        self.principal = principal
        self.source = source
        self.claims = claims or {}


def _const_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def authenticate(request: Request) -> ServiceUser:
    settings = get_settings()
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise ApiError(ErrorCode.UNAUTHORIZED, "缺少 Bearer 令牌", error_key="unauthorized")
    token = header[7:].strip()

    # 1) 内部服务令牌（等权比较；仅环境显式配置时可用）
    if settings.internal_token:
        if _const_eq(token, settings.internal_token):
            return ServiceUser(principal="internal:ppt-studio", source="internal-token")

    # 2) 教师 JWT
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["sub", "exp"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(ErrorCode.UNAUTHORIZED, "令牌已过期", error_key="expired") from exc
    except jwt.InvalidTokenError as exc:
        raise ApiError(ErrorCode.UNAUTHORIZED, "令牌无效", error_key="invalid_token") from exc

    exp = payload.get("exp")
    if exp is not None and datetime.fromtimestamp(exp, tz=UTC) < datetime.now(tz=UTC):
        raise ApiError(ErrorCode.UNAUTHORIZED, "令牌已过期", error_key="expired")
    return ServiceUser(
        principal=str(payload.get("sub", "unknown")),
        source="teacher-jwt",
        claims=payload,
    )
