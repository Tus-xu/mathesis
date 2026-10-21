"""JWT 签发/校验（HS256）+ 开发种子账号。

claim `{sub, name, role, school_id}`（02-ARCHITECTURE §10）；dev seed `zhanglaoshi/teacher123`。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import jwt

from .config import get_settings
from .errors import ApiError, ErrorCode


def _claims(sub: str, name: str, role: str, school_id: str | None = None) -> dict[str, Any]:
    now = int(time.time())
    settings = get_settings()
    return {
        "sub": sub,
        "name": name,
        "role": role,
        "school_id": school_id,
        "iat": now,
        "exp": now + settings.jwt_ttl_days * 86400,
    }


def create_access_token(*, user_id: str, name: str, role: str, school_id: str | None = None) -> str:
    settings = get_settings()
    return jwt.encode(_claims(user_id, name, role, school_id), settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(ErrorCode.TOKEN_EXPIRED, "登录已过期") from exc
    except jwt.InvalidTokenError:
        pass  # 非 teacher-v3 签发 → 尝试统一身份平台签发方（双签发方接受，见下）
    else:
        # 统一平台 token 与本平台共用开发密钥时，上面会直接验签成功；
        # 但其 claims 是 {sub, active_role, roles[]}，没有 `role` 字段
        # → 必须走同一映射，否则 role="" 触发 40301 role_denied（集成断点）。
        if "role" not in claims and ("active_role" in claims or "roles" in claims):
            return _map_unified_claims(claims)
        return claims
    return _decode_unified_platform_token(token)


def _map_unified_claims(claims: dict[str, Any]) -> dict[str, Any]:
    """统一身份平台 claims（{sub, active_role, roles[], sid}）→ 本平台 claims。"""
    bindings = claims.get("roles") or []
    approved: set[str] = set()
    for b in bindings:
        if isinstance(b, dict):
            if b.get("status") == "approved" or b.get("verified") is True:
                approved.add(str(b.get("role") or ""))
        elif isinstance(b, str):
            approved.add(b)  # role/switch 签发的 token：roles 为字符串数组
    role = str(claims.get("active_role") or "")
    if role not in approved:
        role = "teacher" if "teacher" in approved else ("student" if "student" in approved else "")
    return {
        "sub": str(claims.get("sub", "")),
        "name": str(claims.get("nickname") or claims.get("name") or ""),
        "role": role,
        "school_id": claims.get("school_id"),
        "issuer": "unified-platform",
    }


def _decode_unified_platform_token(token: str) -> dict[str, Any]:
    """rescue 整合（2026-09-07）：接受统一身份平台（services/api）签发的 JWT。

    背景：前端统一登录走旧平台 /api/auth/*，其 token claims 为
    {sub, active_role, roles:[{role,status,verified}], sid, ...}，签名密钥与本服务不同。
    此前 teacher-v3 只认自己签发的 token——统一登录后调教师端点必 401（集成断点）。
    修复：本服务额外接受统一平台密钥签发的 token 并映射 claims；密钥经
    环境变量 LEGACY_JWT_SECRET 注入（未配置则维持原行为——无效凭证照旧拒绝）。
    """
    secret = get_settings().legacy_jwt_secret
    if not secret:
        raise ApiError(ErrorCode.UNAUTHORIZED, "无效的登录凭证")
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise ApiError(ErrorCode.TOKEN_EXPIRED, "登录已过期") from exc
    except jwt.InvalidTokenError as exc:
        raise ApiError(ErrorCode.UNAUTHORIZED, "无效的登录凭证") from exc

    return _map_unified_claims(claims)


class UserToken:
    """从 JWT claim 还原的当前用户（sub/name/role/school_id）。"""

    def __init__(self, user_id: str, name: str, role: str, school_id: str | None):
        self.id = user_id
        self.name = name
        self.role = role
        self.school_id = school_id

    @property
    def is_teacher(self) -> bool:
        return self.role == "teacher"


# ---- 开发种子账号（02-ARCHITECTURE §10：zhanglaoshi / teacher123） ----

SEED_TEACHER = {
    "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "teacher-platform/seed/zhanglaoshi")),
    "name": "张老师",
    "role": "teacher",
    "school_id": "seed-school-001",
    "password": "teacher123",  # dev seed 仅用于本地开发；生产口令由 B4/B5 接管
}


def verify_seed_login(username: str, password: str) -> UserToken | None:
    if username == "zhanglaoshi" and password == SEED_TEACHER["password"]:
        return UserToken(SEED_TEACHER["id"], SEED_TEACHER["name"], SEED_TEACHER["role"], SEED_TEACHER["school_id"])
    return None
