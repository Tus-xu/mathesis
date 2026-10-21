"""FastAPI 公共依赖（鉴权/DB/Redis）。

- `get_current_user`：解析 Bearer JWT；非 teacher 返回 40301 role_denied（契约测试断言）。
- `get_session`：DB 会话（复用 db.session）。
- `get_redis`：Valkey 客户端（按需懒建）。
"""
from __future__ import annotations


from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .errors import ApiError, ErrorCode
from .security import UserToken, decode_access_token
from ..db.session import get_session

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> UserToken:
    if credentials is None:
        raise ApiError(ErrorCode.UNAUTHORIZED, "请先登录")
    claims = decode_access_token(credentials.credentials)
    user = UserToken(
        user_id=str(claims["sub"]),
        name=claims.get("name", ""),
        role=claims.get("role", ""),
        school_id=claims.get("school_id"),
    )
    if not user.is_teacher:
        raise ApiError(ErrorCode.ROLE_DENIED, "role_denied")
    return user


async def get_current_user_any(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> UserToken:
    """不校验 role（课堂 H5 token 等场景；role 判定由端点自行处理）。"""
    if credentials is None:
        raise ApiError(ErrorCode.UNAUTHORIZED, "请先登录")
    claims = decode_access_token(credentials.credentials)
    return UserToken(
        user_id=str(claims["sub"]),
        name=claims.get("name", ""),
        role=claims.get("role", ""),
        school_id=claims.get("school_id"),
    )


async def get_redis() -> Redis:
    settings = get_settings()
    return Redis.from_url(settings.valkey_url, decode_responses=True)


async def get_db() -> AsyncSession:
    async for session in get_session():
        yield session


__all__ = [
    "get_current_user",
    "get_current_user_any",
    "get_redis",
    "get_db",
    "get_session",
]
