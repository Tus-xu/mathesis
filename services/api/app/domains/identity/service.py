"""Identity application services."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.identity.security import PasswordHasher
from app.models.identity import (
    AccountDeletionRequest,
    AuthSession,
    IdentityAuditLog,
    OrganizationInvite,
    RoleApplication,
    UserConsent,
    UserCredential,
)
from app.models.role_binding import RoleBinding
from app.models.student_profile import StudentProfile
from app.models.user import User


class PasswordAuthenticationError(Exception):
    def __init__(self, error_key: str = "AUTH_PASSWORD_INVALID"):
        super().__init__("手机号或密码错误")
        self.error_key = error_key
        self.message = "手机号或密码错误"


class IdentityError(Exception):
    def __init__(self, error_key: str, message: str, http_status: int = 400):
        super().__init__(message)
        self.error_key = error_key
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class LoginRoleResolution:
    active_role: str
    pending_role: str | None
    identity_status: str


async def resolve_login_role(
    db: AsyncSession,
    user: User,
    preferred_role: str | None,
    *,
    review_enabled: bool = True,
) -> LoginRoleResolution:
    """Resolve an active role without allowing an unapproved professional session."""
    bindings = (
        await db.execute(
            select(RoleBinding).where(
                RoleBinding.user_id == user.id,
                RoleBinding.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    bindings_by_role = {binding.role: binding for binding in bindings}
    approved_roles = {
        binding.role for binding in bindings if binding.status == "approved"
    }

    if preferred_role is None:
        if not approved_roles:
            raise IdentityError("AUTH_ROLE_NOT_APPROVED", "账号没有已批准角色", 403)
        active_role = next(
            (
                role
                for role in (user.last_active_role, "student", "teacher", "researcher", "admin")
                if role in approved_roles
            ),
            None,
        )
        if active_role is None:
            raise IdentityError("AUTH_ROLE_NOT_APPROVED", "账号没有已批准角色", 403)
        return LoginRoleResolution(active_role, None, "authenticated")

    binding = bindings_by_role.get(preferred_role)
    if (
        not review_enabled
        and preferred_role in {"teacher", "researcher"}
        and binding is not None
        and binding.status in {"pending", "needs_more_info"}
    ):
        now = datetime.now(UTC)
        binding.status = "approved"
        binding._legacy_verified = True
        binding.approved_at = now
        db.add(
            IdentityAuditLog(
                event_type="role_review.bypassed",
                actor_user_id=user.id,
                subject_user_id=user.id,
                result="success",
                details={"role": preferred_role, "source": "login"},
            )
        )
        await db.flush()
        return LoginRoleResolution(preferred_role, None, "authenticated")
    application = None
    if preferred_role in {"teacher", "researcher"}:
        application = await db.scalar(
            select(RoleApplication)
            .where(
                RoleApplication.user_id == user.id,
                RoleApplication.role == preferred_role,
            )
            .order_by(RoleApplication.submitted_at.desc())
        )
    if binding is not None and binding.status == "approved":
        return LoginRoleResolution(preferred_role, None, "authenticated")
    if preferred_role not in {"teacher", "researcher"} or (binding is None and application is None):
        raise IdentityError("AUTH_ROLE_NOT_AVAILABLE", "所选身份不可用", 403)

    target_status = application.status if application is not None else binding.status
    identity_status = {
        "pending": "pending_review",
        "needs_more_info": "needs_more_info",
        "rejected": "rejected",
    }.get(target_status)
    if identity_status is None or "student" not in approved_roles:
        raise IdentityError("AUTH_ROLE_NOT_AVAILABLE", "所选身份不可用", 403)
    return LoginRoleResolution("student", preferred_role, identity_status)


class PasswordService:
    lock_after_failures = 5
    lock_duration = timedelta(minutes=15)

    def __init__(self, hasher: PasswordHasher | None = None):
        self.hasher = hasher or PasswordHasher()

    async def set_password(self, db: AsyncSession, user_id: uuid.UUID, password: str) -> None:
        secret_hash = self.hasher.hash(password)
        credential = (
            await db.execute(
                select(UserCredential).where(
                    UserCredential.user_id == user_id,
                    UserCredential.credential_type == "password",
                )
            )
        ).scalar_one_or_none()
        now = datetime.now(UTC)
        if credential is None:
            credential = UserCredential(
                user_id=user_id,
                credential_type="password",
                secret_hash=secret_hash,
                password_changed_at=now,
            )
            db.add(credential)
        else:
            credential.secret_hash = secret_hash
            credential.password_changed_at = now
            credential.failed_attempts = 0
            credential.locked_until = None
        await db.flush()

    async def authenticate(self, db: AsyncSession, phone: str | None, password: str) -> User:
        now = datetime.now(UTC)
        row = (
            await db.execute(
                select(User, UserCredential)
                .join(UserCredential, UserCredential.user_id == User.id)
                .where(
                    User.phone == phone,
                    User.deleted_at.is_(None),
                    UserCredential.credential_type == "password",
                )
            )
        ).one_or_none()
        if row is None:
            raise PasswordAuthenticationError()
        user, credential = row
        if user.status != "active":
            raise PasswordAuthenticationError("AUTH_ACCOUNT_RESTRICTED")
        if credential.locked_until is not None and credential.locked_until > now:
            raise PasswordAuthenticationError("AUTH_PASSWORD_LOCKED")
        check = self.hasher.verify_and_rehash(password, credential.secret_hash)
        if not check.valid:
            credential.failed_attempts += 1
            if credential.failed_attempts >= self.lock_after_failures:
                credential.locked_until = now + self.lock_duration
            await db.flush()
            raise PasswordAuthenticationError()
        credential.failed_attempts = 0
        credential.locked_until = None
        if check.replacement_hash:
            credential.secret_hash = check.replacement_hash
        await db.flush()
        return user

    async def reset_password(self, db: AsyncSession, user_id: uuid.UUID, password: str) -> None:
        user = (
            await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
        ).scalar_one_or_none()
        if user is None:
            raise PasswordAuthenticationError()
        await self.set_password(db, user_id, password)
        user.security_version += 1
        now = datetime.now(UTC)
        await db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now, revoke_reason="password_reset")
        )
        await db.flush()


class IdentityService:
    """Transaction-safe identity creation and student onboarding."""

    async def register_sms(
        self,
        db: AsyncSession,
        phone: str,
        *,
        role: str,
        consent_version: str,
        organization_name: str | None = None,
        department: str | None = None,
        staff_or_student_id: str | None = None,
        teaching_stage: str | None = None,
        subject: str | None = None,
        research_direction: str | None = None,
        evidence_file_id: uuid.UUID | None = None,
        review_enabled: bool = True,
    ) -> tuple[User, RoleApplication | None]:
        now = datetime.now(UTC)
        await db.execute(
            insert(User)
            .values(
                phone=phone,
                nickname="",
                status="active",
                onboarding_status="required",
                security_version=1,
                phone_verified_at=now,
            )
            .on_conflict_do_nothing(index_elements=[User.phone])
        )
        user = await db.scalar(
            select(User).where(User.phone == phone, User.deleted_at.is_(None)).with_for_update()
        )
        if user is None or user.status != "active":
            raise PasswordAuthenticationError("AUTH_ACCOUNT_RESTRICTED")
        binding = None
        if role != "student":
            binding = await db.scalar(
                select(RoleBinding).where(
                    RoleBinding.user_id == user.id,
                    RoleBinding.role == role,
                    RoleBinding.deleted_at.is_(None),
                )
            )
            if binding is not None and binding.status == "approved":
                raise IdentityError("IDENTITY_ROLE_ALREADY_APPROVED", "身份已获批准", 409)
        user.phone_verified_at = now
        await db.execute(
            insert(RoleBinding)
            .values(
                user_id=user.id,
                role="student",
                status="approved",
                _legacy_verified=True,
                approved_at=now,
            )
            .on_conflict_do_nothing(index_elements=[RoleBinding.user_id, RoleBinding.role])
        )
        await db.execute(
            insert(UserConsent)
            .values(
                user_id=user.id,
                consent_type="platform_terms",
                consent_version=consent_version,
                consented_at=now,
                source="sms_registration",
            )
            .on_conflict_do_nothing(
                index_elements=[
                    UserConsent.user_id,
                    UserConsent.consent_type,
                    UserConsent.consent_version,
                ]
            )
        )
        if role == "student":
            await db.flush()
            return user, None

        if not review_enabled:
            if binding is None:
                db.add(
                    RoleBinding(
                        user_id=user.id,
                        role=role,
                        org_name=organization_name,
                        status="approved",
                        verified=True,
                        approved_at=now,
                    )
                )
            else:
                binding.status = "approved"
                binding._legacy_verified = True
                binding.approved_at = now
                binding.org_name = organization_name
            db.add(
                IdentityAuditLog(
                    event_type="role_review.bypassed",
                    actor_user_id=user.id,
                    subject_user_id=user.id,
                    result="success",
                    details={"role": role, "source": "registration"},
                )
            )
            await db.flush()
            return user, None

        application = await db.scalar(
            select(RoleApplication).where(
                RoleApplication.user_id == user.id,
                RoleApplication.role == role,
                RoleApplication.status.in_(["pending", "needs_more_info"]),
            )
        )
        if application is None:
            application = RoleApplication(
                user_id=user.id,
                role=role,
                status="pending",
                organization_name_snapshot=organization_name,
                department=department,
                staff_or_student_id=staff_or_student_id,
                teaching_stage=teaching_stage,
                subject=subject,
                research_direction=research_direction,
                evidence_file_id=evidence_file_id,
                submitted_at=now,
            )
            db.add(application)
            db.add(
                IdentityAuditLog(
                    event_type="role_application.submitted",
                    actor_user_id=user.id,
                    subject_user_id=user.id,
                    result="success",
                    details={"role": role},
                )
            )
        if binding is None:
            db.add(RoleBinding(user_id=user.id, role=role, status="pending", verified=False))
        elif binding.status != "approved":
            binding.status = "pending"
            binding._legacy_verified = False
        await db.flush()
        return user, application

    async def login_sms(
        self, db: AsyncSession, phone: str, *, role: str = "student", review_enabled: bool = True
    ) -> tuple[User, bool]:
        """验证码登录即开通：不存在的手机号自动建号并绑定学生身份（幂等）。

        演示模式（review_enabled=False）下请求教师/科研身份时一并自动开通该专业身份
        （与 register_sms 的 bypass 分支同语义，写审计日志）；生产模式（review_enabled=True）
        下专业身份必须走申请审核，不在此放行。
        """
        now = datetime.now(UTC)
        created_id = await db.scalar(
            insert(User)
            .values(
                phone=phone,
                nickname="",
                status="active",
                onboarding_status="required",
                security_version=1,
                phone_verified_at=now,
            )
            .on_conflict_do_nothing(index_elements=[User.phone])
            .returning(User.id)
        )
        user = (
            await db.execute(select(User).where(User.phone == phone, User.deleted_at.is_(None)))
        ).scalar_one_or_none()
        if user is None or user.status != "active":
            raise PasswordAuthenticationError("AUTH_ACCOUNT_RESTRICTED")
        user.phone_verified_at = now
        await db.execute(
            insert(RoleBinding)
            .values(
                user_id=user.id,
                role="student",
                status="approved",
                _legacy_verified=True,
                approved_at=now,
            )
            .on_conflict_do_nothing(index_elements=[RoleBinding.user_id, RoleBinding.role])
        )
        if role in {"teacher", "researcher"} and not review_enabled:
            binding = await db.scalar(
                select(RoleBinding).where(
                    RoleBinding.user_id == user.id,
                    RoleBinding.role == role,
                    RoleBinding.deleted_at.is_(None),
                )
            )
            if binding is None:
                db.add(
                    RoleBinding(
                        user_id=user.id,
                        role=role,
                        status="approved",
                        verified=True,
                        approved_at=now,
                    )
                )
            else:
                binding.status = "approved"
                binding._legacy_verified = True
                binding.approved_at = now
            db.add(
                IdentityAuditLog(
                    event_type="role_review.bypassed",
                    actor_user_id=user.id,
                    subject_user_id=user.id,
                    result="success",
                    details={"role": role, "source": "login_auto_provision"},
                )
            )
        await db.flush()
        return user, created_id is not None

    async def onboard_student(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        *,
        nickname: str,
        stage: str,
        grade: str,
        school: str | None,
        consent_version: str,
    ) -> User:
        user = (
            await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
        ).scalar_one()
        binding = (
            await db.execute(
                select(RoleBinding).where(
                    RoleBinding.user_id == user_id,
                    RoleBinding.role == "student",
                    RoleBinding.status == "approved",
                    RoleBinding.deleted_at.is_(None),
                )
            )
        ).scalar_one()
        profile = (
            await db.execute(select(StudentProfile).where(StudentProfile.user_id == user_id))
        ).scalar_one_or_none()
        if profile is None:
            profile = StudentProfile(user_id=user_id)
            db.add(profile)
        profile.school_stage = stage
        profile.grade = grade
        binding.org_name = school or None
        user.nickname = nickname.strip()
        user.onboarding_status = "completed"
        await db.execute(
            insert(UserConsent)
            .values(
                user_id=user_id,
                consent_type="platform_terms",
                consent_version=consent_version,
                consented_at=datetime.now(UTC),
                source="student_onboarding",
            )
            .on_conflict_do_nothing(
                index_elements=[
                    UserConsent.user_id,
                    UserConsent.consent_type,
                    UserConsent.consent_version,
                ]
            )
        )
        await db.flush()
        return user

    async def submit_role_application(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        *,
        role: str,
        organization_name: str,
        department: str | None = None,
        staff_or_student_id: str | None = None,
        teaching_stage: str | None = None,
        subject: str | None = None,
        research_direction: str | None = None,
        evidence_file_id: uuid.UUID | None = None,
    ) -> RoleApplication:
        if role not in {"teacher", "researcher"}:
            raise IdentityError("IDENTITY_ROLE_INVALID", "只能申请教师或科研人员身份")
        pending = await db.scalar(
            select(RoleApplication).where(
                RoleApplication.user_id == user_id,
                RoleApplication.role == role,
                RoleApplication.status.in_(["pending", "needs_more_info"]),
            )
        )
        if pending is not None:
            raise IdentityError("IDENTITY_APPLICATION_ALREADY_PENDING", "该身份已有待处理申请", 409)
        now = datetime.now(UTC)
        application = RoleApplication(
            user_id=user_id,
            role=role,
            status="pending",
            organization_name_snapshot=organization_name,
            department=department,
            staff_or_student_id=staff_or_student_id,
            teaching_stage=teaching_stage,
            subject=subject,
            research_direction=research_direction,
            evidence_file_id=evidence_file_id,
            submitted_at=now,
        )
        db.add(application)
        binding = await db.scalar(
            select(RoleBinding).where(
                RoleBinding.user_id == user_id,
                RoleBinding.role == role,
                RoleBinding.deleted_at.is_(None),
            )
        )
        if binding is None:
            db.add(RoleBinding(user_id=user_id, role=role, status="pending", verified=False))
        elif binding.status != "approved":
            binding.status = "pending"
            binding._legacy_verified = False
        db.add(
            IdentityAuditLog(
                event_type="role_application.submitted",
                actor_user_id=user_id,
                subject_user_id=user_id,
                result="success",
                details={"role": role},
            )
        )
        await db.flush()
        return application

    async def review_role_application(
        self,
        db: AsyncSession,
        application_id: uuid.UUID,
        reviewer_id: uuid.UUID,
        *,
        decision: str,
        note: str | None,
    ) -> RoleApplication:
        application = await db.scalar(
            select(RoleApplication)
            .where(RoleApplication.id == application_id)
            .with_for_update()
        )
        if application is None:
            raise IdentityError("IDENTITY_APPLICATION_NOT_FOUND", "申请不存在", 404)
        if application.user_id == reviewer_id:
            raise IdentityError("IDENTITY_SELF_REVIEW_DENIED", "管理员不能审核自己的申请", 403)
        target = {
            "approved": "approved",
            "rejected": "rejected",
            "needs_more_info": "needs_more_info",
        }.get(decision)
        if target is None:
            raise IdentityError("IDENTITY_REVIEW_DECISION_INVALID", "审核决定无效")
        if application.status == target:
            return application
        if application.status not in {"pending", "needs_more_info"}:
            raise IdentityError("IDENTITY_REVIEW_TRANSITION_INVALID", "当前状态不能执行该审核", 409)
        now = datetime.now(UTC)
        application.status = target
        application.reviewed_at = now
        application.reviewed_by = reviewer_id
        application.review_note = note
        binding = await db.scalar(
            select(RoleBinding).where(
                RoleBinding.user_id == application.user_id,
                RoleBinding.role == application.role,
                RoleBinding.deleted_at.is_(None),
            )
        )
        if binding is None:
            binding = RoleBinding(user_id=application.user_id, role=application.role)
            db.add(binding)
        if target == "approved":
            binding.status = "approved"
            binding._legacy_verified = True
            binding.approved_at = now
            binding.approved_by = reviewer_id
        elif target == "rejected":
            binding.status = "rejected"
            binding._legacy_verified = False
            binding.status_reason = note
        else:
            binding.status = "pending"
            binding._legacy_verified = False
        await db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == application.user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now, revoke_reason=f"role_application_{target}")
        )
        db.add(
            IdentityAuditLog(
                event_type=f"role_application.{target}",
                actor_user_id=reviewer_id,
                subject_user_id=application.user_id,
                result="success",
                details={"application_id": str(application.id), "role": application.role},
            )
        )
        await db.flush()
        return application


class InvitationService:
    def __init__(self, pepper: str):
        self.pepper = pepper.encode()

    def _digest(self, token: str) -> str:
        return hmac.new(self.pepper, token.encode(), hashlib.sha256).hexdigest()

    async def create(
        self,
        db: AsyncSession,
        creator_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        allowed_role: str,
        max_uses: int,
        expires_at: datetime,
    ) -> tuple[str, OrganizationInvite]:
        if allowed_role not in {"teacher", "researcher"} or max_uses < 1:
            raise IdentityError("IDENTITY_INVITE_INVALID", "邀请码参数无效")
        token = secrets.token_urlsafe(24)
        invitation = OrganizationInvite(
            organization_id=organization_id,
            invite_digest=self._digest(token),
            allowed_role=allowed_role,
            max_uses=max_uses,
            expires_at=expires_at,
            status="active",
            created_by=creator_id,
        )
        db.add(invitation)
        await db.flush()
        return token, invitation

    async def redeem(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        token: str,
        *,
        role: str,
    ) -> RoleBinding:
        invitation = await db.scalar(
            select(OrganizationInvite)
            .where(OrganizationInvite.invite_digest == self._digest(token))
            .with_for_update()
        )
        now = datetime.now(UTC)
        if invitation is None:
            raise IdentityError("IDENTITY_INVITE_INVALID", "邀请码无效", 404)
        if (
            invitation.status != "active"
            or invitation.expires_at <= now
            or invitation.used_count >= invitation.max_uses
        ):
            raise IdentityError("IDENTITY_INVITE_EXHAUSTED", "邀请码已失效或用尽", 409)
        if role != invitation.allowed_role:
            raise IdentityError("IDENTITY_INVITE_ROLE_MISMATCH", "邀请码不适用于该身份")
        existing = await db.scalar(
            select(RoleBinding).where(
                RoleBinding.user_id == user_id,
                RoleBinding.role == role,
                RoleBinding.deleted_at.is_(None),
            )
        )
        if existing is not None and existing.status == "approved":
            raise IdentityError("IDENTITY_ROLE_ALREADY_APPROVED", "身份已获批准", 409)
        invitation.used_count += 1
        if invitation.used_count >= invitation.max_uses:
            invitation.status = "exhausted"
        binding = existing or RoleBinding(user_id=user_id, role=role)
        if existing is None:
            db.add(binding)
        binding.status = "approved"
        binding._legacy_verified = True
        binding.approved_at = now
        application = RoleApplication(
            user_id=user_id,
            role=role,
            status="approved",
            organization_id=invitation.organization_id,
            invite_id=invitation.id,
            submitted_at=now,
            reviewed_at=now,
            review_note="system_invite",
        )
        db.add(application)
        db.add(
            IdentityAuditLog(
                event_type="role_application.approved_by_invite",
                actor_user_id=None,
                subject_user_id=user_id,
                result="success",
                details={"invite_id": str(invitation.id), "role": role},
            )
        )
        await db.flush()
        return binding


class AccountLifecycleService:
    def __init__(self, now=None):
        self.now = now or (lambda: datetime.now(UTC))

    async def change_phone(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        expected_old_phone: str | None,
        new_phone: str,
    ) -> User:
        user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or user.deleted_at is not None:
            raise IdentityError("IDENTITY_USER_NOT_FOUND", "用户不存在", 404)
        if user.phone != expected_old_phone:
            raise IdentityError("IDENTITY_OLD_PHONE_MISMATCH", "旧手机号验证与当前账号不一致", 409)
        owner = await db.scalar(
            select(User).where(
                User.phone == new_phone,
                User.id != user_id,
                User.deleted_at.is_(None),
            )
        )
        if owner is not None:
            raise IdentityError("IDENTITY_PHONE_ALREADY_BOUND", "新手机号已绑定其他账号", 409)
        now = self.now()
        user.phone = new_phone
        user.phone_verified_at = now
        user.security_version += 1
        await db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now, revoke_reason="phone_changed")
        )
        db.add(
            IdentityAuditLog(
                event_type="account.phone_changed",
                actor_user_id=user_id,
                subject_user_id=user_id,
                masked_phone=f"{new_phone[:3]}****{new_phone[-4:]}",
                result="success",
                details={},
            )
        )
        await db.flush()
        return user

    async def request_deletion(
        self, db: AsyncSession, user_id: uuid.UUID
    ) -> AccountDeletionRequest:
        user = await db.scalar(select(User).where(User.id == user_id).with_for_update())
        if user is None or user.deleted_at is not None:
            raise IdentityError("IDENTITY_USER_NOT_FOUND", "用户不存在", 404)
        existing = await db.scalar(
            select(AccountDeletionRequest).where(
                AccountDeletionRequest.user_id == user_id,
                AccountDeletionRequest.status == "cooling_off",
            )
        )
        if existing is not None:
            return existing
        now = self.now()
        request = AccountDeletionRequest(
            user_id=user_id,
            status="cooling_off",
            requested_at=now,
            execute_after=now + timedelta(days=7),
        )
        db.add(request)
        user.status = "deletion_pending"
        user.security_version += 1
        await db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now, revoke_reason="deletion_requested")
        )
        db.add(
            IdentityAuditLog(
                event_type="account.deletion_requested",
                actor_user_id=user_id,
                subject_user_id=user_id,
                result="success",
                details={"execute_after": request.execute_after.isoformat()},
            )
        )
        await db.flush()
        return request

    async def cancel_deletion(
        self, db: AsyncSession, user_id: uuid.UUID
    ) -> AccountDeletionRequest:
        request = await db.scalar(
            select(AccountDeletionRequest)
            .where(
                AccountDeletionRequest.user_id == user_id,
                AccountDeletionRequest.status == "cooling_off",
            )
            .with_for_update()
        )
        if request is None:
            raise IdentityError("IDENTITY_DELETION_NOT_PENDING", "账号没有待执行注销", 409)
        now = self.now()
        request.status = "cancelled"
        request.cancelled_at = now
        user = await db.get(User, user_id)
        user.status = "active"
        user.security_version += 1
        db.add(
            IdentityAuditLog(
                event_type="account.deletion_cancelled",
                actor_user_id=user_id,
                subject_user_id=user_id,
                result="success",
                details={},
            )
        )
        await db.flush()
        return request

    async def execute_due_deletions(self, db: AsyncSession) -> int:
        now = self.now()
        requests = (
            await db.execute(
                select(AccountDeletionRequest)
                .where(
                    AccountDeletionRequest.status == "cooling_off",
                    AccountDeletionRequest.execute_after <= now,
                )
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        for request in requests:
            user = await db.get(User, request.user_id, with_for_update=True)
            if user is None:
                continue
            result_digest = hashlib.sha256(f"{user.id}:{now.isoformat()}".encode()).hexdigest()
            await db.execute(delete(UserCredential).where(UserCredential.user_id == user.id))
            await db.execute(delete(RoleApplication).where(RoleApplication.user_id == user.id))
            await db.execute(
                update(RoleBinding)
                .where(RoleBinding.user_id == user.id, RoleBinding.deleted_at.is_(None))
                .values(deleted_at=now, status="suspended", _legacy_verified=False)
            )
            await db.execute(
                update(AuthSession)
                .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
                .values(revoked_at=now, revoke_reason="deletion_completed")
            )
            user.phone = None
            user.email = None
            user.password_hash = None
            user.nickname = ""
            user.avatar_url = None
            user.status = "disabled"
            user.deleted_at = now
            request.status = "completed"
            request.completed_at = now
            request.result_digest = result_digest
            db.add(
                IdentityAuditLog(
                    event_type="account.deletion_completed",
                    actor_user_id=None,
                    subject_user_id=user.id,
                    result="success",
                    details={"result_digest": result_digest},
                )
            )
        await db.flush()
        return len(requests)
