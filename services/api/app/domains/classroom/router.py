"""班级域路由（domains/classroom/router.py）

6 个 API 端点 + 权限控制（§5.2）。
越权规则：非本班教师→40302；学生confirm→40301；跨班读→404。
"""

import random
import uuid

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.gateway.auth import get_current_user
from app.gateway.schemas import ApiResponse
from app.models.class_ import Class
from app.models.class_member import ClassMember
from app.models.database import get_db
from app.models.role_binding import RoleBinding

logger = structlog.get_logger()
router = APIRouter()

# 班级码字符集（排除 0/O/1/I，ADR-001-6）
CODE_CHARSET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6


def _generate_invite_code() -> str:
    """生成 6 位班级码"""
    return "".join(random.choices(CODE_CHARSET, k=CODE_LENGTH))


# ========== Schemas ==========


class CreateClassRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    grade: str | None = None


class JoinClassRequest(BaseModel):
    invite_code: str = Field(..., min_length=6, max_length=8)
    nickname: str | None = None


class ConfirmMemberRequest(BaseModel):
    confirmed: bool = True


# ========== POST / — 教师建班 ==========


@router.post("")
@router.post("/", include_in_schema=False)
async def create_class(
    body: CreateClassRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """教师建班，自动生成班级码"""
    user_id = current_user["sub"]

    # 验证教师角色
    role_result = await db.execute(
        select(RoleBinding).where(
            RoleBinding.user_id == user_id,
            RoleBinding.role == "teacher",
            RoleBinding.deleted_at.is_(None),
        )
    )
    if role_result.scalar_one_or_none() is None:
        return ApiResponse(code=40301, message="仅教师可以建班")

    # 生成唯一班级码（撞库重试）
    for _ in range(10):
        code = _generate_invite_code()
        existing = await db.execute(select(Class).where(Class.invite_code == code))
        if existing.scalar_one_or_none() is None:
            break
    else:
        return ApiResponse(code=50001, message="班级码生成失败，请重试")

    new_class = Class(
        name=body.name,
        invite_code=code,
        owner_id=user_id,
        grade=body.grade or "",
        subject="math",
    )
    db.add(new_class)
    await db.flush()

    # 创建者自动加入为教师成员
    member = ClassMember(
        class_id=new_class.id,
        user_id=user_id,
        member_role="teacher",
        confirmed=True,
        join_via="create",
    )
    db.add(member)
    await db.flush()

    logger.info("class.created", class_id=str(new_class.id), owner=user_id)
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "id": str(new_class.id),
            "name": new_class.name,
            "inviteCode": code,
            "grade": new_class.grade,
        },
    )


# ========== POST /join — 学生申请加入 ==========


@router.post("/join")
async def join_class(
    body: JoinClassRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """学生申请加入班级（confirmed=false，待确认）"""
    user_id = current_user["sub"]

    # 查找班级
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

    # 检查是否已加入/已申请
    existing = await db.execute(
        select(ClassMember).where(
            ClassMember.class_id == target_class.id,
            ClassMember.user_id == user_id,
            ClassMember.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none() is not None:
        return ApiResponse(code=40902, message="已申请或已在班级中")

    # 创建待确认成员
    member = ClassMember(
        class_id=target_class.id,
        user_id=user_id,
        member_role="student",
        confirmed=False,
        join_via="code",
        nickname_in_class=body.nickname or "",
    )
    db.add(member)
    await db.flush()

    logger.info("class.joined", class_id=str(target_class.id), user=user_id)
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "classId": str(target_class.id),
            "className": target_class.name,
            "status": "pending",
        },
    )


# ========== GET /mine — 我的班级列表 ==========


@router.get("/mine")
async def my_classes(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """我任教/加入的班级列表"""
    user_id = current_user["sub"]

    result = await db.execute(
        select(Class, ClassMember)
        .join(ClassMember, Class.id == ClassMember.class_id)
        .where(
            ClassMember.user_id == user_id,
            ClassMember.deleted_at.is_(None),
            Class.deleted_at.is_(None),
        )
        .order_by(Class.created_at.desc())
    )
    rows = result.all()

    # tel A1：每班 confirmed 学生成员数（子查询分组计数，纯加法字段）。
    # 8100 normalize_authorized_class_items 已兼容消费（catalog/service.py:110-115）。
    class_ids = [cls.id for cls, _member in rows]
    student_counts: dict[uuid.UUID, int] = {}
    if class_ids:
        cnt_rs = await db.execute(
            select(ClassMember.class_id, func.count(ClassMember.id))
            .where(
                ClassMember.class_id.in_(class_ids),
                ClassMember.member_role == "student",
                ClassMember.confirmed.is_(True),
                ClassMember.deleted_at.is_(None),
            )
            .group_by(ClassMember.class_id)
        )
        student_counts = {cid: int(n) for cid, n in cnt_rs.all()}

    items = []
    for cls, member in rows:
        # canTeach 与 teacher/scope.py:get_teacher_class 完全同口径：
        # owner 直接放行；否则必须已确认且 member_role ∈ {teacher, admin}。
        # 前端不再靠 myRole 字符串猜权限（assistant/owner 假阳性 → 点开就 40302）。
        can_teach = cls.owner_id == uuid.UUID(user_id) or (
            bool(member.confirmed) and member.member_role in ("teacher", "admin")
        )
        items.append(
            {
                "id": str(cls.id),
                "name": cls.name,
                "grade": cls.grade,
                "myRole": member.member_role,
                "confirmed": member.confirmed,
                "isOwner": cls.owner_id == uuid.UUID(user_id),
                "canTeach": can_teach,
                "inviteCode": cls.invite_code if can_teach else None,
                "createdAt": cls.created_at.isoformat() if cls.created_at else None,
                "students": student_counts.get(cls.id, 0),
            }
        )

    return ApiResponse(code=0, message="ok", data={"items": items, "total": len(items)})


# ========== GET /{id}/members — 成员列表 ==========


@router.get("/{class_id}/classroom-mode")
async def get_member_classroom_mode(
    class_id: uuid.UUID,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """学生/成员读取课堂模式；非成员与 M3 关闭统一按不存在处理。"""
    if not settings.m3_enable_teacher:
        return ApiResponse(code=40401, message="班级不存在")
    from app.domains.teacher.classroom import classroom_state_for_member

    state = await classroom_state_for_member(db, uuid.UUID(current_user["sub"]), class_id)
    if state is None:
        return ApiResponse(code=40401, message="班级不存在")
    return ApiResponse(code=0, message="ok", data=state)


@router.get("/{class_id}/members")
async def list_members(
    class_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """成员列表（教师见全量含待确认，学生只见已确认）"""
    user_id = current_user["sub"]

    # 验证班级存在
    cls_result = await db.execute(
        select(Class).where(Class.id == class_id, Class.deleted_at.is_(None))
    )
    target_class = cls_result.scalar_one_or_none()
    if target_class is None:
        return ApiResponse(code=40401, message="班级不存在")

    # 验证当前用户是该班成员
    my_member_result = await db.execute(
        select(ClassMember).where(
            ClassMember.class_id == class_id,
            ClassMember.user_id == user_id,
            ClassMember.deleted_at.is_(None),
        )
    )
    my_member = my_member_result.scalar_one_or_none()
    if my_member is None:
        # 非本班成员 → 404（不泄露存在性）
        return ApiResponse(code=40401, message="班级不存在")

    # 查询成员
    query = select(ClassMember).where(
        ClassMember.class_id == class_id, ClassMember.deleted_at.is_(None)
    )

    # 学生只见 confirmed=true
    if my_member.member_role == "student":
        query = query.where(ClassMember.confirmed.is_(True))

    result = await db.execute(query.order_by(ClassMember.joined_at.desc()))
    members = result.scalars().all()
    if not members:
        return ApiResponse(code=0, message="ok", data={"items": [], "total": 0})

    # 花名册必须带真实用户昵称：班级成员表只存 member_role/确认态，
    # 名称来自用户档案（nickname_in_class 为空时降级到用户昵称）。
    from app.models.user import User

    user_ids = [m.user_id for m in members]
    users_result = await db.execute(select(User).where(User.id.in_(user_ids)))
    user_map = {u.id: u for u in users_result.scalars().all()}

    items = []
    for m in members:
        user = user_map.get(m.user_id)
        item = {
            "id": str(m.id),
            "userId": str(m.user_id),
            "memberRole": m.member_role,
            "confirmed": m.confirmed,
            "joinVia": m.join_via,
            "nicknameInClass": m.nickname_in_class or "",
            "nickname": user.nickname if user is not None else "",
            "joinedAt": m.joined_at.isoformat() if m.joined_at else None,
        }
        items.append(item)

    return ApiResponse(code=0, message="ok", data={"items": items, "total": len(items)})


# ========== PATCH /{id}/members/{uid} — 教师确认入班 ==========


@router.patch("/{class_id}/members/{member_user_id}")
async def confirm_member(
    class_id: str,
    member_user_id: str,
    body: ConfirmMemberRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """教师确认/取消确认成员"""
    user_id = current_user["sub"]

    # 验证当前用户是该班教师
    my_result = await db.execute(
        select(ClassMember).where(
            ClassMember.class_id == class_id,
            ClassMember.user_id == user_id,
            ClassMember.member_role == "teacher",
            ClassMember.deleted_at.is_(None),
        )
    )
    if my_result.scalar_one_or_none() is None:
        # 学生尝试 confirm → 40301
        return ApiResponse(code=40301, message="仅教师可以确认成员")

    # 查找目标成员
    target_result = await db.execute(
        select(ClassMember).where(
            ClassMember.class_id == class_id,
            ClassMember.user_id == member_user_id,
            ClassMember.deleted_at.is_(None),
        )
    )
    target = target_result.scalar_one_or_none()
    if target is None:
        return ApiResponse(code=40401, message="成员不存在")

    target.confirmed = body.confirmed
    await db.flush()

    logger.info(
        "class.member_confirmed", class_id=class_id, member=member_user_id, confirmed=body.confirmed
    )
    return ApiResponse(code=0, message="ok", data={"confirmed": body.confirmed})


# ========== POST /{id}/invite-code/reset — 重置班级码 ==========


@router.post("/{class_id}/invite-code/reset")
async def reset_invite_code(
    class_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """重置班级码（旧码立即失效）"""
    user_id = current_user["sub"]

    # 验证是该班教师
    cls_result = await db.execute(
        select(Class).where(
            Class.id == class_id,
            Class.owner_id == user_id,
            Class.deleted_at.is_(None),
        )
    )
    target_class = cls_result.scalar_one_or_none()
    if target_class is None:
        return ApiResponse(code=40302, message="仅班级创建教师可以重置班级码")

    # 生成新码
    for _ in range(10):
        new_code = _generate_invite_code()
        existing = await db.execute(
            select(Class).where(Class.invite_code == new_code, Class.id != class_id)
        )
        if existing.scalar_one_or_none() is None:
            break

    target_class.invite_code = new_code
    await db.flush()

    logger.info("class.code_reset", class_id=class_id)
    return ApiResponse(code=0, message="ok", data={"inviteCode": new_code})
