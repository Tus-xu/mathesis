"""M3 教师端测试辅助：注册用户/班级/成员、mint 角色 token。

直接写测试库便于构造越权场景；token 用 create_token_with_role 控制 active_role。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.jwt import create_token_with_role
from app.models.class_ import Class
from app.models.class_member import ClassMember
from app.models.role_binding import RoleBinding
from app.models.user import User


async def make_user(
    db: AsyncSession,
    *,
    nickname: str | None = None,
    teacher_verified: bool | None = True,
) -> uuid.UUID:
    """创建 M3 教师测试用户，默认附带已审核教师绑定。

    ``teacher_verified=None`` 专门构造无绑定场景；``False`` 构造待审核绑定，
    以免授权拒绝测试被默认的有效绑定弱化。
    """
    u = User(phone=None, email=None, nickname=nickname or f"u{uuid.uuid4().hex[:6]}")
    db.add(u)
    await db.flush()
    bindings = [
        RoleBinding(user_id=u.id, role=role, status="approved", verified=True)
        for role in ("student", "researcher", "admin")
    ]
    if teacher_verified is not None:
        bindings.append(
            RoleBinding(user_id=u.id, role="teacher", verified=teacher_verified)
        )
    db.add_all(bindings)
    await db.flush()
    return u.id


def token(user_id: uuid.UUID, role: str) -> str:
    return create_token_with_role(str(user_id), role)


async def make_class(db: AsyncSession, owner_id: uuid.UUID, **kw) -> uuid.UUID:
    c = Class(
        owner_id=owner_id,
        invite_code=uuid.uuid4().hex[:8],
        name=kw.get("name", "测试班"),
        grade=kw.get("grade", "高二"),
        subject=kw.get("subject", "math"),
    )
    db.add(c)
    await db.flush()
    return c.id


async def add_member(
    db: AsyncSession,
    class_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    member_role: str = "student",
    confirmed: bool = True,
) -> ClassMember:
    m = ClassMember(
        class_id=class_id,
        user_id=user_id,
        member_role=member_role,
        confirmed=confirmed,
        join_via="code",
    )
    db.add(m)
    await db.flush()
    return m


async def class_of(db: AsyncSession, class_id: uuid.UUID) -> Class | None:
    return await db.get(Class, class_id)


async def member(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID) -> ClassMember | None:
    rs = await db.execute(
        select(ClassMember).where(
            ClassMember.class_id == class_id, ClassMember.user_id == user_id
        )
    )
    return rs.scalar_one_or_none()
