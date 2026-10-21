"""认证域 Pydantic 模型

请求/响应 schema 定义，对齐 API 文档 §2。
"""

import uuid
from typing import Any

from pydantic import BaseModel, Field

# ========== 统一响应信封 ==========


class ApiResponse(BaseModel):
    """统一 API 响应信封"""

    code: int = 0
    message: str = "ok"
    data: Any = None
    requestId: str = Field(default_factory=lambda: str(uuid.uuid4()))


# ========== 请求体 ==========


class SmsCodeRequest(BaseModel):
    """发送验证码请求"""

    phone: str = Field(..., min_length=11, max_length=11, pattern=r"^1[3-9]\d{9}$")


class LoginRequest(BaseModel):
    """验证码登录请求"""

    phone: str = Field(..., min_length=11, max_length=11, pattern=r"^1[3-9]\d{9}$")
    code: str = Field(..., min_length=4, max_length=6)


class RoleSwitchRequest(BaseModel):
    """角色切换请求"""

    role: str = Field(..., min_length=1, max_length=16)


# ========== 响应数据 ==========


class SmsCodeData(BaseModel):
    """发送验证码响应数据"""

    ttl: int = 300


class RoleInfo(BaseModel):
    """角色信息"""

    role: str
    verified: bool = False
    org_name: str | None = None


class UserData(BaseModel):
    """用户信息（登录响应）"""

    id: str
    nickname: str = ""
    active_role: str = "student"
    roles: list[RoleInfo] = []
    is_new: bool = False


class LoginData(BaseModel):
    """登录响应数据"""

    token: str
    user: UserData


class RoleSwitchData(BaseModel):
    """角色切换响应数据"""

    token: str


class MeData(BaseModel):
    """当前用户信息响应数据"""

    id: str
    nickname: str = ""
    avatar_url: str | None = None
    active_role: str = "student"
    roles: list[RoleInfo] = []
