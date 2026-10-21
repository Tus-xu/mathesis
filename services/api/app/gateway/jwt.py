"""JWT 工具模块

提供 JWT token 的创建和解析功能。
"""

from datetime import UTC, datetime, timedelta

from jose import jwt

from app.config import settings


def create_access_token(
    data: dict,
    expires_delta: timedelta | None = None,
) -> str:
    """创建 JWT access token

    Args:
        data: 要编码到 token 中的数据
        expires_delta: 过期时间增量，默认使用认证 access token 分钟数

    Returns:
        编码后的 JWT token 字符串
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(minutes=settings.auth_access_token_minutes)

    to_encode.update({"iat": datetime.now(UTC), "exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded_jwt


def create_token_with_role(
    user_id: str,
    role: str,
    roles: list[str] | None = None,
    verified: bool = True,
    session_id: str | None = None,
    security_version: int = 1,
) -> str:
    """创建包含角色信息的 JWT token

    Args:
        user_id: 用户 ID
        role: 当前激活角色
        roles: 用户所有角色列表
        verified: 当前角色是否已验证

    Returns:
        JWT token 字符串
    """
    data: dict = {
        "sub": user_id,
        "active_role": role,
        "sv": security_version,
        # Compatibility-only display claims. Authorization never reads them.
        "roles": roles or [role],
        "verified": verified,
    }
    if session_id:
        data["sid"] = session_id
    return create_access_token(data)


def decode_token(token: str) -> dict:
    """解码 JWT token

    Args:
        token: JWT token 字符串

    Returns:
        解码后的数据字典

    Raises:
        JWTError: token 无效或已过期
    """
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
