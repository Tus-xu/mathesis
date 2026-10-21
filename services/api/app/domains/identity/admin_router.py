"""Administrator identity review and invitation endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domains.identity.service import IdentityError, IdentityService, InvitationService
from app.domains.identity.types import CurrentIdentity
from app.gateway.auth import require_role
from app.gateway.schemas import ApiResponse
from app.models.database import get_db
from app.models.identity import AuthSession, IdentityAuditLog, OrganizationInvite, RoleApplication
from app.models.role_binding import RoleBinding
from app.models.user import User

router = APIRouter()


class ReviewRequest(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class InviteCreateRequest(BaseModel):
    organization_id: uuid.UUID
    allowed_role: str = Field(pattern=r"^(teacher|researcher)$")
    max_uses: int = Field(default=1, ge=1, le=1000)
    expires_at: datetime


async def require_recent_reauth(
    admin: CurrentIdentity = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
) -> None:
    session = await db.get(AuthSession, admin.session_id) if admin.session_id else None
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


def _identity_exception(exc: IdentityError) -> HTTPException:
    return HTTPException(
        status_code=exc.http_status,
        detail={"code": exc.http_status * 100 + 1, "error_key": exc.error_key, "message": exc.message},
    )


def _application_data(row: RoleApplication) -> dict:
    return {
        "id": str(row.id),
        "user_id": str(row.user_id),
        "role": row.role,
        "status": row.status,
        "organization_name": row.organization_name_snapshot,
        "department": row.department,
        "subject": row.subject,
        "research_direction": row.research_direction,
        "review_note": row.review_note,
        "submitted_at": row.submitted_at.isoformat(),
    }


@router.get("/applications", response_model=ApiResponse)
async def list_applications(
    status: str | None = Query(default=None),
    role: str | None = Query(default=None),
    _admin: CurrentIdentity = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    query = select(RoleApplication).order_by(RoleApplication.submitted_at.desc())
    if status:
        query = query.where(RoleApplication.status == status)
    if role:
        query = query.where(RoleApplication.role == role)
    rows = (await db.execute(query)).scalars().all()
    return ApiResponse(data=[_application_data(row) for row in rows])


@router.get("/applications/{application_id}", response_model=ApiResponse)
async def get_application(
    application_id: uuid.UUID,
    _admin: CurrentIdentity = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(RoleApplication, application_id)
    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"error_key": "IDENTITY_APPLICATION_NOT_FOUND", "message": "申请不存在"},
        )
    return ApiResponse(data=_application_data(row))


async def _review(
    application_id: uuid.UUID,
    decision: str,
    body: ReviewRequest,
    admin: CurrentIdentity,
    db: AsyncSession,
) -> ApiResponse:
    try:
        row = await IdentityService().review_role_application(
            db, application_id, admin.user_id, decision=decision, note=body.note
        )
    except IdentityError as exc:
        raise _identity_exception(exc) from None
    return ApiResponse(data={"id": str(row.id), "status": row.status})


@router.post("/applications/{application_id}/approve", response_model=ApiResponse)
async def approve_application(
    application_id: uuid.UUID,
    body: ReviewRequest,
    _reauth: None = Depends(require_recent_reauth),
    admin: CurrentIdentity = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await _review(application_id, "approved", body, admin, db)


@router.post("/applications/{application_id}/reject", response_model=ApiResponse)
async def reject_application(
    application_id: uuid.UUID,
    body: ReviewRequest,
    _reauth: None = Depends(require_recent_reauth),
    admin: CurrentIdentity = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await _review(application_id, "rejected", body, admin, db)


@router.post("/applications/{application_id}/request-more-info", response_model=ApiResponse)
async def request_more_info(
    application_id: uuid.UUID,
    body: ReviewRequest,
    _reauth: None = Depends(require_recent_reauth),
    admin: CurrentIdentity = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await _review(application_id, "needs_more_info", body, admin, db)


async def _change_binding(
    binding_id: uuid.UUID,
    target: str,
    note: str | None,
    admin: CurrentIdentity,
    db: AsyncSession,
) -> ApiResponse:
    binding = await db.get(RoleBinding, binding_id)
    if binding is None:
        raise HTTPException(status_code=404, detail={"error_key": "IDENTITY_ROLE_NOT_FOUND", "message": "角色绑定不存在"})
    if binding.user_id == admin.user_id:
        raise HTTPException(status_code=403, detail={"error_key": "IDENTITY_SELF_REVIEW_DENIED", "message": "管理员不能变更自己的角色"})
    now = datetime.now(UTC)
    binding.status = target
    binding._legacy_verified = target == "approved"
    binding.status_reason = note
    if target == "suspended":
        binding.suspended_at = now
        binding.suspended_by = admin.user_id
    else:
        binding.approved_at = now
        binding.approved_by = admin.user_id
        binding.suspended_at = None
        binding.suspended_by = None
    await db.execute(
        update(AuthSession)
        .where(AuthSession.user_id == binding.user_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now, revoke_reason=f"role_{target}")
    )
    db.add(
        IdentityAuditLog(
            event_type=f"role_binding.{target}",
            actor_user_id=admin.user_id,
            subject_user_id=binding.user_id,
            result="success",
            details={"binding_id": str(binding.id)},
        )
    )
    return ApiResponse(data={"id": str(binding.id), "status": binding.status})


@router.post("/roles/{binding_id}/suspend", response_model=ApiResponse)
async def suspend_role(
    binding_id: uuid.UUID,
    body: ReviewRequest,
    _reauth: None = Depends(require_recent_reauth),
    admin: CurrentIdentity = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await _change_binding(binding_id, "suspended", body.note, admin, db)


@router.post("/roles/{binding_id}/restore", response_model=ApiResponse)
async def restore_role(
    binding_id: uuid.UUID,
    body: ReviewRequest,
    _reauth: None = Depends(require_recent_reauth),
    admin: CurrentIdentity = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await _change_binding(binding_id, "approved", body.note, admin, db)


@router.get("/users", response_model=ApiResponse)
async def list_users(
    _admin: CurrentIdentity = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(User).where(User.deleted_at.is_(None)).order_by(User.created_at.desc()).limit(200)
        )
    ).scalars().all()
    return ApiResponse(data=[{"id": str(row.id), "phone": row.phone, "nickname": row.nickname, "status": row.status} for row in rows])


@router.get("/users/{user_id}", response_model=ApiResponse)
async def get_user(
    user_id: uuid.UUID,
    _admin: CurrentIdentity = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail={"error_key": "IDENTITY_USER_NOT_FOUND", "message": "用户不存在"})
    bindings = (
        await db.execute(
            select(RoleBinding).where(RoleBinding.user_id == user_id, RoleBinding.deleted_at.is_(None))
        )
    ).scalars().all()
    return ApiResponse(
        data={
            "id": str(user.id),
            "phone": user.phone,
            "nickname": user.nickname,
            "status": user.status,
            "roles": [{"id": str(row.id), "role": row.role, "status": row.status} for row in bindings],
        }
    )


@router.post("/invites", response_model=ApiResponse)
async def create_invite(
    body: InviteCreateRequest,
    _reauth: None = Depends(require_recent_reauth),
    admin: CurrentIdentity = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    try:
        token, row = await InvitationService(settings.auth_invite_pepper).create(
            db, admin.user_id, **body.model_dump()
        )
    except IdentityError as exc:
        raise _identity_exception(exc) from None
    return ApiResponse(data={"id": str(row.id), "token": token, "expires_at": row.expires_at.isoformat()})


@router.get("/invites", response_model=ApiResponse)
async def list_invites(
    _admin: CurrentIdentity = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(select(OrganizationInvite).order_by(OrganizationInvite.created_at.desc()))
    ).scalars().all()
    return ApiResponse(
        data=[
            {
                "id": str(row.id),
                "allowed_role": row.allowed_role,
                "used_count": row.used_count,
                "max_uses": row.max_uses,
                "status": row.status,
                "expires_at": row.expires_at.isoformat(),
            }
            for row in rows
        ]
    )


@router.delete("/invites/{invite_id}", response_model=ApiResponse)
async def revoke_invite(
    invite_id: uuid.UUID,
    _reauth: None = Depends(require_recent_reauth),
    _admin: CurrentIdentity = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(OrganizationInvite, invite_id)
    if row is None:
        raise HTTPException(status_code=404, detail={"error_key": "IDENTITY_INVITE_INVALID", "message": "邀请码不存在"})
    row.status = "revoked"
    row.revoked_at = datetime.now(UTC)
    return ApiResponse(data={"revoked": True})
