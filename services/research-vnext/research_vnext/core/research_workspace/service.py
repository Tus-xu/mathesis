"""RV17 · 工作区共享服务：项目对象级授权 + 公共序列化 helper。

所有工作区端点都在 `authorize_session` 六层鉴权的**同一形状**下：
workspace → 撤权 floor → project（tenant + owner）→ capability。
本模块把"项目级"这一段抽出来，避免在每个 service 里重复。
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from ..api.deps import (
    READ_CAPABILITY,
    AccessContext,
    check_revocation,
    require_capability,
)
from ..domain.errors import CapabilityMissing
from ..project import service as project_svc
from ..project.models import ResearchProjectRow


def authorize_project(
    db: Session,
    ctx: AccessContext,
    project_id: uuid.UUID,
    *,
    capability: str = READ_CAPABILITY,
) -> ResearchProjectRow:
    """项目级对象授权：workspace → 撤权 → tenant+owner → capability。

    复用 `project_svc.get_project`（它已做 tenant+owner 检查并对"不存在"与
    "不属于你"给同一 404，避免跨租户探测）。
    """
    if ctx.workspace != "research":
        raise CapabilityMissing(f"workspace={ctx.workspace!r} 无权访问 research 域")
    check_revocation(db, ctx)
    row = project_svc.get_project(
        db, tenant_id=ctx.tenant_id, owner_id=ctx.actor_id, project_id=project_id
    )
    require_capability(ctx, capability)
    return row


__all__ = ["authorize_project"]
