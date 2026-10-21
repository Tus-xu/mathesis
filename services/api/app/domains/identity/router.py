"""Public identity endpoints introduced by the unified auth contract."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domains.identity.challenges import ChallengeError, ChallengeService, RedisChallengeStore
from app.domains.identity.security import PasswordHasher, PasswordPolicyError
from app.domains.identity.service import (
    AccountLifecycleService,
    IdentityError,
    IdentityService,
    InvitationService,
    PasswordAuthenticationError,
    PasswordService,
    resolve_login_role,
)
from app.domains.identity.sessions import (
    SESSION_SCOPES,
    SessionError,
    SessionService,
    clear_session_cookies,
    set_session_cookies,
    validate_csrf,
)
from app.domains.identity.sms import DemoSmsProvider, TencentSmsProvider
from app.domains.identity.types import CurrentIdentity
from app.gateway.auth import get_current_user
from app.gateway.redis import get_redis
from app.gateway.schemas import ApiResponse
from app.models.database import get_db
from app.models.identity import AccountDeletionRequest, AuthSession, RoleApplication
from app.models.role_binding import RoleBinding
from app.models.user import User

router = APIRouter()
profile_router = APIRouter()


class SmsChallengeRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    purpose: str


class PasswordSetRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class PasswordLoginRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    password: str = Field(min_length=1, max_length=256)
    remember: bool = False
    preferred_role: Literal["student", "teacher", "researcher"] | None = None


class PasswordResetRequest(PasswordSetRequest):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    challenge_id: str = Field(min_length=1, max_length=64)
    code: str = Field(pattern=r"^\d{6}$")


class ReauthenticateRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    challenge_id: str = Field(min_length=1, max_length=64)
    code: str = Field(pattern=r"^\d{6}$")


class SmsLoginRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    challenge_id: str = Field(min_length=1, max_length=64)
    code: str = Field(pattern=r"^\d{6}$")
    remember: bool = False
    preferred_role: Literal["student", "teacher", "researcher"] | None = None


class SmsRegistrationRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    challenge_id: str = Field(min_length=1, max_length=64)
    code: str = Field(pattern=r"^\d{6}$")
    role: str = Field(pattern=r"^(student|teacher|researcher)$")
    consent_version: str = Field(min_length=1, max_length=32)
    organization_name: str | None = Field(default=None, min_length=1, max_length=255)
    department: str | None = Field(default=None, max_length=128)
    staff_or_student_id: str | None = Field(default=None, max_length=64)
    teaching_stage: str | None = Field(default=None, max_length=64)
    subject: str | None = Field(default=None, max_length=64)
    research_direction: str | None = Field(default=None, min_length=1, max_length=255)
    evidence_file_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_role_fields(self) -> SmsRegistrationRequest:
        self.organization_name = (
            self.organization_name.strip() if self.organization_name is not None else None
        )
        self.research_direction = (
            self.research_direction.strip() if self.research_direction is not None else None
        )
        if self.role in {"teacher", "researcher"} and not self.organization_name:
            raise ValueError("organization_name is required for professional roles")
        if self.role == "researcher" and not self.research_direction:
            raise ValueError("research_direction is required for researchers")
        return self


class StudentOnboardingRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=64)
    stage: str = Field(min_length=1, max_length=32)
    grade: str = Field(min_length=1, max_length=32)
    school: str | None = Field(default=None, max_length=128)
    consent_version: str = Field(min_length=1, max_length=32)


class RoleApplicationRequest(BaseModel):
    role: str = Field(pattern=r"^(teacher|researcher)$")
    organization_name: str = Field(min_length=1, max_length=255)
    department: str | None = Field(default=None, max_length=128)
    staff_or_student_id: str | None = Field(default=None, max_length=64)
    teaching_stage: str | None = Field(default=None, max_length=64)
    subject: str | None = Field(default=None, max_length=64)
    research_direction: str | None = Field(default=None, max_length=255)
    evidence_file_id: uuid.UUID | None = None


class InviteRedeemRequest(BaseModel):
    token: str = Field(min_length=22, max_length=128)
    role: str = Field(pattern=r"^(teacher|researcher)$")


class PhoneChangeRequest(BaseModel):
    old_challenge_id: str = Field(min_length=1, max_length=64)
    old_code: str = Field(pattern=r"^\d{6}$")
    new_phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    new_challenge_id: str = Field(min_length=1, max_length=64)
    new_code: str = Field(pattern=r"^\d{6}$")


class DeletionCancelRequest(BaseModel):
    phone: str = Field(pattern=r"^1[3-9]\d{9}$")
    challenge_id: str = Field(min_length=1, max_length=64)
    code: str = Field(pattern=r"^\d{6}$")


def get_challenge_service() -> ChallengeService:
    if settings.auth_sms_provider == "demo":
        provider = DemoSmsProvider(
            environment=settings.app_env,
            allowlist=set(settings.auth_demo_sms_phone_list),
        )
    elif settings.auth_sms_provider == "tencent":
        provider = TencentSmsProvider(
            secret_id=settings.tencent_sms_secret_id,
            secret_key=settings.tencent_sms_secret_key,
            sdk_app_id=settings.tencent_sms_sdk_app_id,
            sign_name=settings.tencent_sms_sign_name,
            template_id=settings.tencent_sms_template_id,
            region=settings.tencent_sms_region,
            template_params=settings.tencent_sms_template_param_list,
        )
    else:
        raise RuntimeError("AUTH_SMS_PROVIDER 不受支持")
    return ChallengeService(
        store=RedisChallengeStore(get_redis()),
        provider=provider,
        pepper=settings.auth_otp_pepper,
    )


def get_password_service() -> PasswordService:
    return PasswordService(PasswordHasher())


def get_session_service() -> SessionService:
    return SessionService(refresh_pepper=settings.auth_refresh_token_pepper)


def get_identity_service() -> IdentityService:
    return IdentityService()


def get_invitation_service() -> InvitationService:
    return InvitationService(settings.auth_invite_pepper)


def get_lifecycle_service() -> AccountLifecycleService:
    return AccountLifecycleService()


async def require_recent_identity_reauth(
    current_user: CurrentIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    session = (
        await db.get(AuthSession, current_user.session_id)
        if current_user.session_id
        else None
    )
    now = datetime.now(UTC)
    if (
        session is None
        or session.reauthenticated_at is None
        or session.reauthenticated_at < now - timedelta(minutes=10)
        or session.reauthenticated_at > now
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": 40302,
                "error_key": "AUTH_RECENT_REAUTH_REQUIRED",
                "message": "该操作需要最近 10 分钟内重新认证",
            },
        )


def _identity_error(exc: IdentityError) -> HTTPException:
    return HTTPException(
        status_code=exc.http_status,
        detail={
            "code": exc.http_status * 100 + 1,
            "error_key": exc.error_key,
            "message": exc.message,
        },
    )


def _secure_cookies() -> bool:
    return settings.app_env in {"staging", "production"}


def _session_error(exc: SessionError) -> HTTPException:
    status_code = 403 if exc.error_key == "AUTH_CSRF_INVALID" else 401
    return HTTPException(
        status_code=status_code,
        detail={"code": 40301 if status_code == 403 else 40100, "error_key": exc.error_key, "message": exc.message},
    )


def _password_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PasswordPolicyError):
        return HTTPException(
            status_code=400,
            detail={"code": 40001, "error_key": exc.error_key, "message": exc.message},
        )
    if isinstance(exc, PasswordAuthenticationError):
        status_code = 423 if exc.error_key == "AUTH_PASSWORD_LOCKED" else 401
        return HTTPException(
            status_code=status_code,
            detail={"code": 40101, "error_key": exc.error_key, "message": exc.message},
        )
    raise exc


@router.post("/challenges/sms", response_model=ApiResponse)
async def create_sms_challenge(
    body: SmsChallengeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    service: ChallengeService = Depends(get_challenge_service),
):
    try:
        issued = await service.create(
            body.phone,
            body.purpose,
            ip_prefix=request.client.host if request.client else "unknown",
        )
    except ChallengeError as exc:
        detail = {
            "code": 42901 if exc.http_status == 429 else 50301 if exc.http_status == 503 else 40002,
            "error_key": exc.error_key,
            "message": exc.message,
        }
        if exc.retry_after is not None:
            detail["retry_after"] = exc.retry_after
        raise HTTPException(status_code=exc.http_status, detail=detail) from None
    # 登录防呆：告知客户端该号码是否已有账号。验证码登录是"即登即建号"，
    # 手机号输错一位就会被当成新账号并强制走引导，这里给前端一个提前提示的依据。
    existing_user_id = await db.scalar(
        select(User.id).where(User.phone == body.phone, User.deleted_at.is_(None)).limit(1)
    )
    return ApiResponse(
        code=0,
        message="sent",
        data={
            "challenge_id": issued.challenge_id,
            "expires_in": issued.expires_in,
            "retry_after": issued.retry_after,
            "account_exists": existing_user_id is not None,
            **({"demo_code": issued.demo_code} if issued.demo_code else {}),
        },
    )


@router.post("/password/set", response_model=ApiResponse)
async def set_password(
    body: PasswordSetRequest,
    current_user: CurrentIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: PasswordService = Depends(get_password_service),
):
    try:
        await service.set_password(db, current_user.user_id, body.password)
    except PasswordPolicyError as exc:
        raise _password_error(exc) from None
    return ApiResponse(code=0, message="ok", data={"password_set": True})


@router.post("/login/sms", response_model=ApiResponse)
async def login_sms(
    body: SmsLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    challenges: ChallengeService = Depends(get_challenge_service),
    sessions: SessionService = Depends(get_session_service),
):
    try:
        await challenges.consume(body.challenge_id, body.phone, "login", body.code)
    except ChallengeError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": 40002, "error_key": exc.error_key, "message": exc.message},
        ) from None
    user = await db.scalar(select(User).where(User.phone == body.phone, User.deleted_at.is_(None)))
    identity_service = get_identity_service()
    if user is None:
        # 验证码登录即开通（演示语义）：通过短信核验的任意手机号自动创建账号并绑定
        # 学生身份；演示模式（专业审核关闭）下选择教师/科研身份时一并自动开通该身份。
        # 此前该分支直接拒绝（AUTH_ROLE_NOT_AVAILABLE），service.login_sms
        # 的自动开通能力没有接线——未注册手机号一律被"该手机号尚未申请此身份"挡住。
        user, _created = await identity_service.login_sms(
            db,
            body.phone,
            role=body.preferred_role or "student",
            review_enabled=settings.auth_professional_review_enabled,
        )
    if user.status != "active":
        raise _password_error(PasswordAuthenticationError("AUTH_ACCOUNT_RESTRICTED"))
    try:
        resolution = await resolve_login_role(
            db,
            user,
            body.preferred_role,
            review_enabled=settings.auth_professional_review_enabled,
        )
    except IdentityError as exc:
        # 幂等补绑：学生身份无条件补；教师/科研身份仅在演示模式（审核关闭）下补，
        # 生产模式（审核开启）仍必须走申请审核
        demo_professional = (
            body.preferred_role in {"teacher", "researcher"}
            and not settings.auth_professional_review_enabled
        )
        if not (
            exc.error_key == "AUTH_ROLE_NOT_AVAILABLE"
            and (body.preferred_role in (None, "student") or demo_professional)
        ):
            raise _identity_error(exc) from None
        user, _ = await identity_service.login_sms(
            db,
            body.phone,
            role=body.preferred_role or "student",
            review_enabled=settings.auth_professional_review_enabled,
        )
        try:
            resolution = await resolve_login_role(
                db,
                user,
                body.preferred_role,
                review_enabled=settings.auth_professional_review_enabled,
            )
        except IdentityError as exc_retry:
            raise _identity_error(exc_retry) from None
    bindings = (
        await db.execute(
            select(RoleBinding).where(
                RoleBinding.user_id == user.id,
                RoleBinding.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    issued = await sessions.issue(
        db,
        user,
        resolution.active_role,
        remember=body.remember,
        pending_role=resolution.pending_role,
    )
    set_session_cookies(response, issued, secure=_secure_cookies())
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "access_token": issued.access_token,
            "expires_in": issued.access_expires_in,
            "onboarding_required": user.onboarding_status != "completed",
            "user": {
                "id": str(user.id),
                "nickname": user.nickname or "",
                "active_role": resolution.active_role,
                "roles": [
                    {"role": binding.role, "status": binding.status, "verified": binding.verified}
                    for binding in bindings
                ],
            },
            "identity_status": resolution.identity_status,
            **({"pending_role": resolution.pending_role} if resolution.pending_role else {}),
        },
    )


@router.post("/register/sms", response_model=ApiResponse)
async def register_sms(
    body: SmsRegistrationRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    challenges: ChallengeService = Depends(get_challenge_service),
    identities: IdentityService = Depends(get_identity_service),
    sessions: SessionService = Depends(get_session_service),
):
    try:
        await challenges.consume(body.challenge_id, body.phone, "registration", body.code)
    except ChallengeError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": 40002, "error_key": exc.error_key, "message": exc.message},
        ) from None
    try:
        user, application = await identities.register_sms(
            db,
            body.phone,
            review_enabled=settings.auth_professional_review_enabled,
            **body.model_dump(exclude={"phone", "challenge_id", "code"}),
        )
    except IdentityError as exc:
        raise _identity_error(exc) from None
    except PasswordAuthenticationError as exc:
        raise _password_error(exc) from None
    resolution = await resolve_login_role(
        db,
        user,
        body.role,
        review_enabled=settings.auth_professional_review_enabled,
    )
    issued = await sessions.issue(
        db,
        user,
        resolution.active_role,
        remember=False,
        pending_role=resolution.pending_role,
    )
    set_session_cookies(response, issued, secure=_secure_cookies())
    response.headers["Cache-Control"] = "no-store"
    bindings = (
        await db.execute(
            select(RoleBinding).where(
                RoleBinding.user_id == user.id,
                RoleBinding.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    roles = [
        {"role": binding.role, "status": binding.status, "verified": binding.verified}
        for binding in bindings
    ]
    data = {
        "access_token": issued.access_token,
        "expires_in": issued.access_expires_in,
        "onboarding_required": user.onboarding_status != "completed",
        "user": {
            "id": str(user.id),
            "nickname": user.nickname or "",
            "active_role": resolution.active_role,
            "roles": roles,
        },
        "identity_status": resolution.identity_status,
    }
    if application is not None:
        data.update(
            {
                "identity_status": (
                    "needs_more_info"
                    if application.status == "needs_more_info"
                    else "pending_review"
                ),
                "pending_role": application.role,
                "application": {
                    "id": str(application.id),
                    "role": application.role,
                    "status": application.status,
                    "organization_name": application.organization_name_snapshot,
                    "submitted_at": application.submitted_at.isoformat(),
                },
            }
        )
    return ApiResponse(code=0, message="ok", data=data)


@profile_router.post("/onboarding/student", response_model=ApiResponse)
async def onboard_student(
    body: StudentOnboardingRequest,
    current_user: CurrentIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    identities: IdentityService = Depends(get_identity_service),
):
    await identities.onboard_student(
        db,
        current_user.user_id,
        nickname=body.nickname,
        stage=body.stage,
        grade=body.grade,
        school=body.school,
        consent_version=body.consent_version,
    )
    return ApiResponse(code=0, message="ok", data={"onboarding_required": False})


@profile_router.post("/role-applications", response_model=ApiResponse)
async def submit_role_application(
    body: RoleApplicationRequest,
    current_user: CurrentIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: IdentityService = Depends(get_identity_service),
):
    try:
        application = await service.submit_role_application(
            db, current_user.user_id, **body.model_dump()
        )
    except IdentityError as exc:
        raise _identity_error(exc) from None
    return ApiResponse(
        data={"id": str(application.id), "role": application.role, "status": application.status}
    )


@profile_router.get("/role-applications/current", response_model=ApiResponse)
async def current_role_applications(
    current_user: CurrentIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(RoleApplication)
            .where(RoleApplication.user_id == current_user.user_id)
            .order_by(RoleApplication.submitted_at.desc())
        )
    ).scalars().all()
    return ApiResponse(
        data=[
            {
                "id": str(row.id),
                "role": row.role,
                "status": row.status,
                "organization_name": row.organization_name_snapshot,
                "review_note": row.review_note,
                "submitted_at": row.submitted_at.isoformat(),
            }
            for row in rows
        ]
    )


@profile_router.post("/organization-invites/redeem", response_model=ApiResponse)
async def redeem_organization_invite(
    body: InviteRedeemRequest,
    current_user: CurrentIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    service: InvitationService = Depends(get_invitation_service),
):
    try:
        binding = await service.redeem(db, current_user.user_id, body.token, role=body.role)
    except IdentityError as exc:
        raise _identity_error(exc) from None
    return ApiResponse(data={"role": binding.role, "status": binding.status})


@profile_router.post("/phone/change", response_model=ApiResponse)
async def change_phone(
    body: PhoneChangeRequest,
    current_user: CurrentIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    challenges: ChallengeService = Depends(get_challenge_service),
    lifecycle: AccountLifecycleService = Depends(get_lifecycle_service),
):
    user = await db.get(User, current_user.user_id)
    try:
        await challenges.consume(
            body.old_challenge_id, user.phone, "phone_change_old", body.old_code
        )
        await challenges.consume(
            body.new_challenge_id, body.new_phone, "phone_change_new", body.new_code
        )
        changed = await lifecycle.change_phone(
            db, current_user.user_id, user.phone, body.new_phone
        )
    except ChallengeError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": 40002, "error_key": exc.error_key, "message": exc.message},
        ) from None
    except IdentityError as exc:
        raise _identity_error(exc) from None
    return ApiResponse(data={"phone": changed.phone, "sessions_revoked": True})


@profile_router.post("/account/deletion", response_model=ApiResponse)
async def request_account_deletion(
    _reauth: None = Depends(require_recent_identity_reauth),
    current_user: CurrentIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    lifecycle: AccountLifecycleService = Depends(get_lifecycle_service),
):
    try:
        request = await lifecycle.request_deletion(db, current_user.user_id)
    except IdentityError as exc:
        raise _identity_error(exc) from None
    return ApiResponse(
        data={
            "status": request.status,
            "requested_at": request.requested_at.isoformat(),
            "execute_after": request.execute_after.isoformat(),
        }
    )


@profile_router.get("/account/deletion", response_model=ApiResponse)
async def get_account_deletion(
    current_user: CurrentIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    request = await db.scalar(
        select(AccountDeletionRequest)
        .where(AccountDeletionRequest.user_id == current_user.user_id)
        .order_by(AccountDeletionRequest.requested_at.desc())
    )
    return ApiResponse(
        data=None
        if request is None
        else {
            "status": request.status,
            "requested_at": request.requested_at.isoformat(),
            "execute_after": request.execute_after.isoformat(),
        }
    )


@profile_router.post("/account/deletion/cancel", response_model=ApiResponse)
async def cancel_account_deletion(
    body: DeletionCancelRequest,
    db: AsyncSession = Depends(get_db),
    challenges: ChallengeService = Depends(get_challenge_service),
    lifecycle: AccountLifecycleService = Depends(get_lifecycle_service),
):
    try:
        await challenges.consume(
            body.challenge_id, body.phone, "deletion_cancel", body.code
        )
    except ChallengeError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": 40002, "error_key": exc.error_key, "message": exc.message},
        ) from None
    user = await db.scalar(
        select(User).where(User.phone == body.phone, User.deleted_at.is_(None))
    )
    if user is None:
        return ApiResponse(data={"status": "cancelled"})
    try:
        request = await lifecycle.cancel_deletion(db, user.id)
    except IdentityError as exc:
        raise _identity_error(exc) from None
    return ApiResponse(data={"status": request.status})


@router.post("/login/password", response_model=ApiResponse)
async def login_password(
    body: PasswordLoginRequest,
    response: Response,
    db: AsyncSession = Depends(get_db),
    service: PasswordService = Depends(get_password_service),
    sessions: SessionService = Depends(get_session_service),
):
    try:
        user = await service.authenticate(db, body.phone, body.password)
    except PasswordAuthenticationError as exc:
        raise _password_error(exc) from None
    try:
        resolution = await resolve_login_role(
            db,
            user,
            body.preferred_role,
            review_enabled=settings.auth_professional_review_enabled,
        )
    except IdentityError as exc:
        raise _identity_error(exc) from None
    bindings = (
        await db.execute(
            select(RoleBinding).where(
                RoleBinding.user_id == user.id,
                RoleBinding.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    issued = await sessions.issue(
        db,
        user,
        resolution.active_role,
        remember=body.remember,
        pending_role=resolution.pending_role,
    )
    set_session_cookies(response, issued, secure=_secure_cookies())
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "access_token": issued.access_token,
            "token": issued.access_token,
            "expires_in": issued.access_expires_in,
            "onboarding_required": user.onboarding_status != "completed",
            "user": {
                "id": str(user.id),
                "nickname": user.nickname or "",
                "active_role": resolution.active_role,
                "roles": [
                    {
                        "role": binding.role,
                        "status": binding.status,
                        "verified": binding.verified,
                    }
                    for binding in bindings
                ],
            },
            "identity_status": resolution.identity_status,
            **({"pending_role": resolution.pending_role} if resolution.pending_role else {}),
        },
    )


@router.post("/password/reset", response_model=ApiResponse)
async def reset_password(
    body: PasswordResetRequest,
    db: AsyncSession = Depends(get_db),
    challenges: ChallengeService = Depends(get_challenge_service),
    passwords: PasswordService = Depends(get_password_service),
):
    try:
        await challenges.consume(
            body.challenge_id,
            body.phone,
            "password_reset",
            body.code,
        )
    except ChallengeError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": 40002, "error_key": exc.error_key, "message": exc.message},
        ) from None
    user = (
        await db.execute(select(User).where(User.phone == body.phone, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if user is not None:
        try:
            await passwords.reset_password(db, user.id, body.password)
        except PasswordPolicyError as exc:
            raise _password_error(exc) from None
    return ApiResponse(code=0, message="ok", data={"password_reset": True})


@router.post("/reauth", response_model=ApiResponse)
async def reauthenticate(
    body: ReauthenticateRequest,
    current_user: CurrentIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    challenges: ChallengeService = Depends(get_challenge_service),
    passwords: PasswordService = Depends(get_password_service),
):
    if current_user.session_id is None:
        raise HTTPException(
            status_code=401,
            detail={"code": 40100, "error_key": "AUTH_SESSION_REQUIRED", "message": "请重新登录"},
        )
    user = await db.get(User, current_user.user_id)
    try:
        await passwords.authenticate(db, user.phone, body.password)
        await challenges.consume(
            body.challenge_id, user.phone, "admin_reauth", body.code
        )
    except PasswordAuthenticationError as exc:
        raise _password_error(exc) from None
    except ChallengeError as exc:
        raise HTTPException(
            status_code=exc.http_status,
            detail={"code": 40002, "error_key": exc.error_key, "message": exc.message},
        ) from None
    session = await db.get(AuthSession, current_user.session_id, with_for_update=True)
    if session is None or session.revoked_at is not None:
        raise HTTPException(
            status_code=401,
            detail={"code": 40100, "error_key": "AUTH_SESSION_REVOKED", "message": "会话已失效"},
        )
    session.reauthenticated_at = datetime.now(UTC)
    return ApiResponse(data={"reauthenticated": True, "valid_for": 600})


def _resolve_session_scope(raw: str | None) -> str | None:
    if raw is None or raw == "":
        return None
    if raw not in SESSION_SCOPES:
        raise _session_error(SessionError("AUTH_SCOPE_INVALID", "会话作用域无效"))
    return raw


def _csrf_cookie(request: Request, scope: str | None, legacy: str | None) -> str | None:
    if scope:
        return request.cookies.get(f"ma_csrf_{scope}")
    return legacy


@router.post("/token/refresh", response_model=ApiResponse)
async def refresh_token(
    request: Request,
    response: Response,
    ma_refresh: str | None = Cookie(default=None),
    ma_csrf: str | None = Cookie(default=None),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    x_auth_scope: str | None = Header(default=None, alias="X-Auth-Scope"),
    db: AsyncSession = Depends(get_db),
    service: SessionService = Depends(get_session_service),
):
    scope: str | None = None
    try:
        scope = _resolve_session_scope(x_auth_scope)
        refresh_cookie = (
            request.cookies.get(f"ma_refresh_{scope}") if scope else ma_refresh
        )
        csrf_cookie = _csrf_cookie(request, scope, ma_csrf)
        validate_csrf(csrf_cookie, x_csrf_token)
        if not refresh_cookie:
            raise SessionError("AUTH_REFRESH_INVALID", "刷新凭据缺失")
        # 沿用同一 CSRF cookie 完成轮换：并发标签页不会因为另一个标签页
        # 换了新 CSRF 而失败。
        issued = await service.rotate(db, refresh_cookie, csrf_token=csrf_cookie)
    except SessionError as exc:
        if exc.persist_changes:
            await db.commit()
        if exc.error_key != "AUTH_CSRF_INVALID":
            clear_session_cookies(
                response, secure=_secure_cookies(), scope=scope
            )
        raise _session_error(exc) from None
    set_session_cookies(
        response, issued, secure=_secure_cookies(), scope=scope or issued.active_role
    )
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "access_token": issued.access_token,
            "expires_in": issued.access_expires_in,
            "active_role": issued.active_role,
        },
    )


@router.post("/logout", response_model=ApiResponse)
async def logout(
    request: Request,
    response: Response,
    ma_refresh: str | None = Cookie(default=None),
    ma_csrf: str | None = Cookie(default=None),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    x_auth_scope: str | None = Header(default=None, alias="X-Auth-Scope"),
    db: AsyncSession = Depends(get_db),
    service: SessionService = Depends(get_session_service),
):
    scope = _resolve_session_scope(x_auth_scope)
    try:
        validate_csrf(_csrf_cookie(request, scope, ma_csrf), x_csrf_token)
    except SessionError as exc:
        raise _session_error(exc) from None
    refresh_cookie = request.cookies.get(f"ma_refresh_{scope}") if scope else ma_refresh
    if refresh_cookie:
        await service.revoke_by_refresh(db, refresh_cookie)
    clear_session_cookies(response, secure=_secure_cookies(), scope=scope)
    response.headers["Cache-Control"] = "no-store"
    return ApiResponse(code=0, message="ok", data={"logged_out": True})


@router.post("/logout-all", response_model=ApiResponse)
async def logout_all(
    request: Request,
    response: Response,
    current_user: CurrentIdentity = Depends(get_current_user),
    ma_csrf: str | None = Cookie(default=None),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    x_auth_scope: str | None = Header(default=None, alias="X-Auth-Scope"),
    db: AsyncSession = Depends(get_db),
    service: SessionService = Depends(get_session_service),
):
    scope = _resolve_session_scope(x_auth_scope)
    try:
        validate_csrf(_csrf_cookie(request, scope, ma_csrf), x_csrf_token)
    except SessionError as exc:
        raise _session_error(exc) from None
    await service.revoke_all(db, current_user.user_id, "logout_all")
    clear_session_cookies(response, secure=_secure_cookies(), all_scopes=True)
    return ApiResponse(code=0, message="ok", data={"logged_out": True})


@router.get("/sessions", response_model=ApiResponse)
async def list_sessions(
    current_user: CurrentIdentity = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(AuthSession)
            .where(AuthSession.user_id == current_user.user_id)
            .order_by(AuthSession.last_seen_at.desc())
        )
    ).scalars().all()
    return ApiResponse(
        code=0,
        message="ok",
        data=[
            {
                "id": str(row.id),
                "active_role": row.active_role,
                "device_name": row.device_name,
                "ip_prefix": row.ip_prefix,
                "last_seen_at": row.last_seen_at.isoformat(),
                "expires_at": row.expires_at.isoformat(),
                "revoked": row.revoked_at is not None,
                "current": row.id == current_user.session_id,
            }
            for row in rows
        ],
    )


@router.delete("/sessions/{session_id}", response_model=ApiResponse)
async def revoke_session(
    request: Request,
    session_id: uuid.UUID,
    current_user: CurrentIdentity = Depends(get_current_user),
    ma_csrf: str | None = Cookie(default=None),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    x_auth_scope: str | None = Header(default=None, alias="X-Auth-Scope"),
    db: AsyncSession = Depends(get_db),
):
    scope = _resolve_session_scope(x_auth_scope)
    try:
        validate_csrf(_csrf_cookie(request, scope, ma_csrf), x_csrf_token)
    except SessionError as exc:
        raise _session_error(exc) from None
    target = (
        await db.execute(
            select(AuthSession).where(
                AuthSession.id == session_id,
                AuthSession.user_id == current_user.user_id,
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise HTTPException(
            status_code=404,
            detail={"code": 40401, "error_key": "AUTH_SESSION_NOT_FOUND", "message": "会话不存在"},
        )
    if target.revoked_at is None:
        target.revoked_at = datetime.now(UTC)
        target.revoke_reason = "device_revoked"
    return ApiResponse(code=0, message="ok", data={"revoked": True})
