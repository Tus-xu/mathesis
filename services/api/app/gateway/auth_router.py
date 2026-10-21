"""认证路由

实现 /api/auth/* 系列端点（API 文档 §2）。
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domains.identity.sessions import SessionService, set_session_cookies
from app.domains.identity.types import CurrentIdentity
from app.gateway import redis as redis_util
from app.gateway.auth import get_current_user
from app.gateway.jwt import create_token_with_role
from app.gateway.schemas import (
    ApiResponse,
    LoginData,
    LoginRequest,
    RoleInfo,
    RoleSwitchRequest,
    SmsCodeData,
    SmsCodeRequest,
    UserData,
)
from app.models.database import get_db
from app.models.identity import AuthSession
from app.models.role_binding import RoleBinding
from app.models.user import User

logger = structlog.get_logger()

router = APIRouter()


# ========== POST /sms-code ==========


@router.post("/sms-code", response_model=ApiResponse)
async def send_sms_code(body: SmsCodeRequest, response: Response):
    """发送短信验证码（非生产环境固定 123456）"""
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "Sat, 05 Sep 2026 00:00:00 GMT"
    logger.warning("auth.legacy_endpoint", endpoint="sms-code")
    # 生产环境未接入真实短信通道前拒绝发送，防止 mock 验证码流入线上
    if settings.app_env == "production":
        return ApiResponse(code=50301, message="短信服务未配置，暂不可用")

    # 检查发送频率限制
    can_send = await redis_util.check_sms_rate_limit(body.phone)
    if not can_send:
        return ApiResponse(code=42901, message="发送频率过快，请 60 秒后重试")

    # 非生产环境使用固定验证码
    code = settings.dev_sms_code
    await redis_util.set_sms_code(body.phone, code)

    return ApiResponse(code=0, message="sent", data=SmsCodeData(ttl=redis_util.SMS_CODE_TTL))


# ========== POST /login ==========


@router.post("/login", response_model=ApiResponse)
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """验证码登录（新用户自动注册）"""
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = "Sat, 05 Sep 2026 00:00:00 GMT"
    logger.warning("auth.legacy_endpoint", endpoint="login")
    # 验证验证码
    stored_code = await redis_util.get_sms_code(body.phone)
    if stored_code is None:
        return ApiResponse(code=40002, message="验证码已过期，请重新获取")
    if stored_code != body.code:
        return ApiResponse(code=40002, message="验证码错误")

    # 验证码正确，删除已用验证码
    await redis_util.delete_sms_code(body.phone)

    # 查找或创建用户
    result = await db.execute(
        select(User).where(User.phone == body.phone, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    is_new = False
    if user is None:
        # 新用户自动注册
        is_new = True
        user = User(phone=body.phone, nickname="")
        db.add(user)
        await db.flush()  # 获取 user.id

        # 创建默认角色绑定 student
        role_binding = RoleBinding(user_id=user.id, role="student", verified=True)
        db.add(role_binding)
        await db.flush()

    # admin 引导：手机号命中 ADMIN_PHONES 白名单且无 admin 绑定时自动创建，
    # 使本次签发 JWT 的 roles 含 admin（/api/auth/role/apply 仍禁止自助申请 admin，
    # 白名单是唯一的 admin 运维通道）
    if user.phone in settings.admin_phone_list:
        admin_result = await db.execute(
            select(RoleBinding).where(
                RoleBinding.user_id == user.id,
                RoleBinding.role == "admin",
                RoleBinding.deleted_at.is_(None),
            )
        )
        if admin_result.scalar_one_or_none() is None:
            # 白名单引导即视为已批准：pending 会让 role/switch 拒绝激活，
            # 而 admin 又无法自审，白名单通道会被死锁。
            db.add(RoleBinding(user_id=user.id, role="admin", verified=True, status="approved"))
            await db.flush()
            logger.info("auth.admin_bootstrapped", user_id=str(user.id))

    # 查询用户所有角色
    roles_result = await db.execute(
        select(RoleBinding).where(
            RoleBinding.user_id == user.id,
            RoleBinding.status == "approved",
            RoleBinding.deleted_at.is_(None),
        )
    )
    role_bindings = roles_result.scalars().all()
    roles_list = [
        RoleInfo(role=rb.role, verified=rb.verified, org_name=rb.org_name) for rb in role_bindings
    ]
    role_names = [rb.role for rb in role_bindings]

    # 默认激活角色与新登录（identity/router.login_password）保持一致：
    # last_active_role → student → 首个已批准角色。不能取 role_names[0]，
    # 该查询无 ORDER BY，active_role 会随执行计划在 student/admin 间漂移。
    if not role_names:
        active_role = "student"
    elif user.last_active_role in role_names:
        active_role = user.last_active_role
    elif "student" in role_names:
        active_role = "student"
    else:
        active_role = role_names[0]

    # 兼容响应仍返回 data.token，但底层必须使用可撤销的新会话模型。
    issued = await SessionService(refresh_pepper=settings.auth_refresh_token_pepper).issue(
        db,
        user,
        active_role,
        remember=False,
    )
    set_session_cookies(
        response,
        issued,
        secure=settings.app_env == "production",
    )

    user_data = UserData(
        id=str(user.id),
        nickname=user.nickname or "",
        active_role=active_role,
        roles=roles_list,
        is_new=is_new,
    )

    return ApiResponse(
        code=0,
        message="ok",
        data=LoginData(token=issued.access_token, user=user_data),
    )


# ========== GET /me ==========


@router.get("/me", response_model=ApiResponse)
async def get_me(
    current_user: CurrentIdentity = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """获取当前用户信息"""
    user_id = current_user.user_id

    # 查询用户
    result = await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    user = result.scalar_one_or_none()
    if user is None:
        return ApiResponse(code=40401, message="用户不存在")

    # 查询角色绑定
    roles_result = await db.execute(
        select(RoleBinding).where(RoleBinding.user_id == user.id, RoleBinding.deleted_at.is_(None))
    )
    role_bindings = roles_result.scalars().all()
    active_role = current_user.active_role
    pending_role = None
    if current_user.session_id is not None:
        auth_session = await db.get(AuthSession, current_user.session_id)
        pending_role = auth_session.pending_role if auth_session is not None else None
    pending_status = next(
        (binding.status for binding in role_bindings if binding.role == pending_role), None
    )
    identity_status = {
        "pending": "pending_review",
        "needs_more_info": "needs_more_info",
        "rejected": "rejected",
        "approved": "authenticated",
    }.get(pending_status, "not_available" if pending_role else "authenticated")

    phone = user.phone or ""
    data = {
        "id": str(user.id),
        "nickname": user.nickname or "",
        "avatar_url": user.avatar_url,
        # 个人中心只展示脱敏手机号；完整号码不随 /auth/me 下发（避免日志/前端缓存泄露）
        "phone_masked": f"{phone[:3]}****{phone[-4:]}" if len(phone) == 11 else "",
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "status": user.status,
        "onboarding_status": user.onboarding_status,
        "active_role": active_role,
        "identity_status": identity_status,
        "roles": [
            {
                "role": rb.role,
                "status": rb.status,
                "verified": rb.verified,
                "org_name": rb.org_name,
            }
            for rb in role_bindings
        ],
    }
    if pending_role is not None:
        data["pending_role"] = pending_role

    return ApiResponse(
        code=0,
        message="ok",
        data=data,
    )


# ========== POST /role/switch ==========


@router.post("/role/switch", response_model=ApiResponse)
async def switch_role(
    body: RoleSwitchRequest,
    current_user: CurrentIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """切换角色（换发 JWT）"""
    user_id = current_user.user_id
    target_role = body.role

    # 查询用户在该角色上的绑定
    result = await db.execute(
        select(RoleBinding).where(
            RoleBinding.user_id == user_id,
            RoleBinding.role == target_role,
            RoleBinding.status == "approved",
            RoleBinding.deleted_at.is_(None),
        )
    )
    role_binding = result.scalar_one_or_none()

    if role_binding is None:
        raise HTTPException(
            status_code=403,
            detail={
                "code": 40301,
                "error_key": "AUTH_ROLE_NOT_APPROVED",
                "message": f"{target_role} 身份尚未获批或已停用",
            },
        )
    if current_user.session_id is None:
        raise HTTPException(
            status_code=401,
            detail={
                "code": 40100,
                "error_key": "AUTH_SESSION_REQUIRED",
                "message": "请重新登录后切换身份",
            },
        )
    auth_session = await db.scalar(
        select(AuthSession).where(AuthSession.id == current_user.session_id).with_for_update()
    )
    if auth_session is None or auth_session.revoked_at is not None:
        raise HTTPException(
            status_code=401,
            detail={"code": 40100, "error_key": "AUTH_SESSION_REVOKED", "message": "会话已失效"},
        )
    user = await db.get(User, user_id)
    auth_session.active_role = target_role
    auth_session.pending_role = None
    user.last_active_role = target_role
    token = create_token_with_role(
        user_id=str(user_id),
        role=target_role,
        session_id=str(auth_session.id),
        security_version=user.security_version,
    )
    return ApiResponse(
        code=0,
        message="ok",
        data={"access_token": token, "token": token, "expires_in": 900},
    )


# ========== POST /login-by-code — 班级码免密首登 ==========


class LoginByCodeRequest(BaseModel):
    invite_code: str = Field(..., min_length=6, max_length=8)
    nickname: str = Field(default="", max_length=64)


@router.post("/login-by-code", response_model=ApiResponse)
async def login_by_code(body: LoginByCodeRequest, db: AsyncSession = Depends(get_db)):
    """班级码免密首登（学生通过班级码直接注册+登录）"""
    raise HTTPException(
        status_code=410,
        detail={
            "code": 41001,
            "error_key": "AUTH_CLASS_CODE_LOGIN_DEPRECATED",
            "message": "班级码免密登录已停用，请先通过手机号登录后再加入班级",
        },
        headers={"Deprecation": "true", "Sunset": "Sat, 05 Sep 2026 00:00:00 GMT"},
    )

    from app.models.class_ import Class
    from app.models.class_member import ClassMember

    # 班级码尝试限流（防撞库）
    if not await redis_util.check_class_code_rate(body.invite_code):
        return ApiResponse(code=42901, message="尝试过于频繁，请稍后再试")

    # 验证班级码有效
    result = await db.execute(
        select(Class).where(
            Class.invite_code == body.invite_code,
            Class.deleted_at.is_(None),
            Class.status == "active",
        )
    )
    target_class = result.scalar_one_or_none()
    if target_class is None:
        return ApiResponse(code=40003, message="班级码不存在或已失效")

    # 创建新用户（免密）
    import uuid as uuid_mod

    random_phone = f"class_{uuid_mod.uuid4().hex[:12]}"
    user = User(phone=random_phone, nickname=body.nickname or "学生")
    db.add(user)
    await db.flush()

    # 创建默认角色绑定 student
    role_binding = RoleBinding(user_id=user.id, role="student", verified=True)
    db.add(role_binding)

    # 自动加入班级（confirmed=false，待教师确认）
    member = ClassMember(
        class_id=target_class.id,
        user_id=user.id,
        member_role="student",
        confirmed=False,
        join_via="code_first_login",
        nickname_in_class=body.nickname or "",
    )
    db.add(member)
    await db.flush()

    # 签发 JWT
    token = create_token_with_role(
        user_id=str(user.id),
        role="student",
        roles=["student"],
        verified=True,
    )

    user_data = UserData(
        id=str(user.id),
        nickname=user.nickname or "",
        active_role="student",
        roles=[RoleInfo(role="student", verified=True, org_name=None)],
        is_new=True,
    )

    return ApiResponse(code=0, message="ok", data=LoginData(token=token, user=user_data))


# ========== POST /register/teacher — retired unsafe registration flow ==========


@router.post("/register/teacher")
async def register_teacher():
    raise HTTPException(
        status_code=410,
        detail={
            "code": 41001,
            "error_key": "AUTH_LEGACY_TEACHER_REGISTRATION_RETIRED",
            "message": "教师注册入口已停用，请使用统一身份注册流程",
        },
        headers={"Deprecation": "true", "Sunset": "Sat, 05 Sep 2026 00:00:00 GMT"},
    )


# ========== POST /role/apply — 已登录用户申请角色绑定 ==========


class RoleApplyRequest(BaseModel):
    role: str = Field(..., pattern="^(teacher|researcher)$")
    org_name: str = Field(default="", max_length=128)


@router.post("/role/apply", response_model=ApiResponse)
async def apply_role(
    body: RoleApplyRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """已登录用户申请角色绑定

    researcher / teacher：均创建待审核绑定，由管理员审核或邀请码批准
    重复绑定/重复申请 → 40901
    """
    user_id = current_user["sub"]

    # 重复绑定检查
    result = await db.execute(
        select(RoleBinding).where(
            RoleBinding.user_id == user_id,
            RoleBinding.role == body.role,
            RoleBinding.deleted_at.is_(None),
        )
    )
    if result.scalar_one_or_none() is not None:
        return ApiResponse(code=40901, message=f"已绑定或已申请 {body.role} 角色")

    verified = False
    binding = RoleBinding(
        user_id=user_id,
        role=body.role,
        verified=verified,
        org_name=body.org_name or None,
    )
    db.add(binding)
    await db.flush()

    logger.info("auth.role_applied", user_id=user_id, role=body.role, verified=verified)
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "role": body.role,
            "verified": verified,
            "status": "pending",
        },
    )
