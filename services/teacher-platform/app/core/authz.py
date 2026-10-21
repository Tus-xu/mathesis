"""资源级授权（G3 防 BOLA/IDOR）。

teacher 角色只挡门外，不挡横向越权——一切按 id 取资源的端点必须过
`assert_resource_owner`：`user.school_id == resource.school_id AND user.id in allowed_teachers`。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .errors import ApiError, ErrorCode
from .security import UserToken


@dataclass
class ResourceOwner:
    school_id: str | None
    allowed_teacher_ids: set[str] | None = None  # None = 该资源不限定具体教师


def assert_resource_owner(user: UserToken, resource: ResourceOwner | dict[str, Any] | None) -> None:
    """校验当前用户是否拥有该资源。resource 为空（如新建）时放行。

    resource 字典支持 {school_id, owner_id} 键（与前端 teacherV3.ts 对齐）。
    """
    if resource is None:
        return
    if isinstance(resource, dict):
        resource = ResourceOwner(
            school_id=resource.get("school_id"),
            allowed_teacher_ids={resource["owner_id"]} if resource.get("owner_id") else None,
        )
    # 教师（非 admin）必须同校
    if user.role == "admin":
        return
    if resource.school_id and resource.school_id != user.school_id:
        raise ApiError(ErrorCode.ROLE_DENIED, "无权访问该资源")
    if resource.allowed_teacher_ids is not None and user.id not in resource.allowed_teacher_ids:
        raise ApiError(ErrorCode.ROLE_DENIED, "无权访问该资源")
