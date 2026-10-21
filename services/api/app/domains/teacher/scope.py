"""M3 教师端：teacher role + class_scope（§8 鉴权与班级范围）。

- 只认 active_role == teacher（JWT 的 active_role），并校验 roles 列表含 teacher；
- 班级访问：教师必须是班级 owner，或在 class_members 中拥有已确认的 teacher 权限
  （member_role in {teacher, admin} 且 confirmed=True）；
- 所有按 ID 读取的 Artifact/作业/提交/资源，服务层必须反查 class scope；
- 不存在与不可见统一按契约处理（40400），避免枚举他人资源；
- 本模块是数据授权，不是 Registry allowed_roles 的替代品。
"""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.auth import get_current_user
from app.models.class_ import Class
from app.models.class_member import ClassMember
from app.models.database import get_db
from app.models.role_binding import RoleBinding

# 稳定业务错误码（对外契约）
ERR_ROLE_DENIED = 40301
ERR_CLASS_SCOPE_DENIED = 40302
ERR_NOT_FOUND = 40400


def raise_http(code: int, http_status: int, message: str, **data) -> None:
    """抛统一信封错误（main.http_exception_handler 透传 dict detail）。"""
    raise HTTPException(
        status_code=http_status,
        detail={"code": code, "message": message, "data": data or {}},
    )


def require_teacher_role(user: dict) -> uuid.UUID:
    """严格校验 active_role == teacher，返回 teacher_id。

    SSOT/审计 C-01：roles 列表包含 teacher 但 active_role != teacher 的令牌
    必须拒绝（激活角色是唯一准入事实，角色声明不构成授权）。
    """
    active = user.get("active_role", "student")
    if active != "teacher":
        raise_http(
            ERR_ROLE_DENIED,
            status.HTTP_403_FORBIDDEN,
            "role_denied",
            recoverable=False,
        )
    return uuid.UUID(user["sub"])


async def require_verified_teacher(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> uuid.UUID:
    """以当前数据库内未删除且已审核的教师绑定作为路由准入事实。"""
    teacher_id = require_teacher_role(user)
    binding = await db.scalar(
        select(RoleBinding.id).where(
            RoleBinding.user_id == teacher_id,
            RoleBinding.role == "teacher",
            # 统一认证后 verified 是 status == "approved" 的派生属性，查询须走 SSOT 列
            RoleBinding.status == "approved",
            RoleBinding.deleted_at.is_(None),
        )
    )
    if binding is None:
        raise_http(
            ERR_ROLE_DENIED,
            status.HTTP_403_FORBIDDEN,
            "role_denied",
            recoverable=False,
        )
    return teacher_id


async def get_teacher_class(
    db: AsyncSession,
    teacher_id: uuid.UUID,
    class_id: uuid.UUID,
) -> Class:
    """校验教师对该班级有权访问，返回班级实体；无权/不存在统一 404/403。"""
    # 先确认班级存在且未软删，再反查授权（避免对不存在资源给 403 泄露范围）
    clazz = await db.get(Class, class_id)
    if clazz is None or clazz.deleted_at is not None:
        raise_http(ERR_NOT_FOUND, status.HTTP_404_NOT_FOUND, "not_found", recoverable=False)

    if clazz.owner_id == teacher_id:
        return clazz

    rs = await db.execute(
        select(ClassMember).where(
            ClassMember.class_id == class_id,
            ClassMember.user_id == teacher_id,
            ClassMember.member_role.in_(("teacher", "admin")),
            ClassMember.confirmed.is_(True),
            ClassMember.deleted_at.is_(None),
        )
    )
    member = rs.scalar_one_or_none()
    if member is None:
        # 诊断信息只描述“为什么被拒”，不泄露其他成员；前端按 code=40302 渲染中文文案。
        probe = await db.scalar(
            select(ClassMember).where(
                ClassMember.class_id == class_id,
                ClassMember.user_id == teacher_id,
                ClassMember.deleted_at.is_(None),
            )
        )
        raise_http(
            ERR_CLASS_SCOPE_DENIED,
            status.HTTP_403_FORBIDDEN,
            "class_scope_denied",
            recoverable=False,
            class_id=str(class_id),
            member_role=getattr(probe, "member_role", None),
            confirmed=bool(getattr(probe, "confirmed", False)),
            reason="not_teaching_member",
        )
    return clazz


async def assert_teacher_in_class(
    db: AsyncSession, teacher_id: uuid.UUID, class_id: uuid.UUID
) -> None:
    """只校验访问权，不返回班级（供已持有类名的服务复用）。"""
    await get_teacher_class(db, teacher_id, class_id)
