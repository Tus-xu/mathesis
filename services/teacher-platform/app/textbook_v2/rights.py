"""教材权利状态机（R05）。

状态：QUARANTINED（隔离）→ STAGED（暂存）→ ACTIVE（可用）。
- 未获授权素材默认 QUARANTINED，任何生产检索 / 模板预览 / 导出都看不到；
- 只有 rights evidence + validation 通过才能发布 ACTIVE；
- ACTIVE 可回退 STAGED/QUARANTINED（发现权利问题时的降级路径）。

状态转换由本模块统一校验，禁止在业务层随手改 rights_status。
"""

from __future__ import annotations

import enum
import re

from sqlalchemy import ColumnElement


class RightsStatus(enum.StrEnum):
    """教材权利状态。取值与 DB CheckConstraint 保持一致（见 models.py / migration）。"""

    QUARANTINED = "QUARANTINED"
    STAGED = "STAGED"
    ACTIVE = "ACTIVE"

    def __str__(self) -> str:  # 便于直接写入 ORM 字符串列
        return self.value


# 教师用书批次命名（切片镜像实测/CORPUS-RECONCILIATION.json 5 批 MATCHED）：
# tbixi（教师用书·必修）/ txbixi（教师用书·选择性必修）——学生册（bixi/bbixi/xbixi/
# sbixi/sxbixi/kebiao/moban/peixun）均不命中。DB 侧无 book_type 列，batch_id 形态
# 是 quarantine 清理与完整性门禁的唯一可靠判别（磁盘侧另有 is_teacher_guide(manifest)）。
_TEACHER_BATCH_RE = re.compile(r"(?:^|-)t(?:x)?bixi")


def is_teacher_guide_batch_id(batch_id: str | None) -> bool:
    """按 batch_id 形态判定教师用书批次（红线：教师用书永不入库、入库必 QUARANTINED）。"""
    return bool(batch_id and _TEACHER_BATCH_RE.search(batch_id))


# 允许的迁移表：value -> 可直接到达的状态集合
_ALLOWED_TRANSITIONS: dict[RightsStatus, frozenset[RightsStatus]] = {
    RightsStatus.QUARANTINED: frozenset({RightsStatus.STAGED}),
    RightsStatus.STAGED: frozenset({RightsStatus.QUARANTINED, RightsStatus.ACTIVE}),
    RightsStatus.ACTIVE: frozenset({RightsStatus.STAGED, RightsStatus.QUARANTINED}),
}


def can_transition(current: RightsStatus | str, target: RightsStatus | str) -> bool:
    """判断 right 状态迁移是否被允许（防御非法字符串）。"""
    try:
        cur = RightsStatus(str(current))
        dst = RightsStatus(str(target))
    except ValueError:
        return False
    return dst in _ALLOWED_TRANSITIONS[cur]


def assert_can_transition(current: RightsStatus | str, target: RightsStatus | str) -> None:
    """不允许的迁移直接抛 ValueError；合法迁移静默通过。"""
    if not can_transition(current, target):
        raise ValueError(f"非法权利状态迁移: {current} -> {target}")


def active_scope() -> ColumnElement[bool]:
    """构造 '仅 ACTIVE 可见' 的 SQLAlchemy 过滤谓词，供生产检索统一复用。

    用法（见 R08 检索实现）：
        select(TextbookSource).where(active_scope())
    任何未授权（QUARANTINED/STAGED）来源都会被过滤掉。
    实数来源必须在同一个谓词上叠加租户（school_id）过滤。
    """
    from app.textbook_v2.models import TextbookSource

    return TextbookSource.rights_status == RightsStatus.ACTIVE.value


def visible_scope() -> ColumnElement[bool]:
    """构造 '检索可见' 谓词：STAGED + ACTIVE 可进检索结果，QUARANTINED 永不（R08）。

    语义扩展自 active_scope()（保留其仅 ACTIVE 门禁语义，供升级 ACTIVE 的发布门使用）：
    教材已获队伍授权并完成 R06 切片入库的 STAGED 数据在 R08 检索层即可见；
    R08 检索实现必须叠加租户（school_id）过滤，越权返回空。
    """
    from app.textbook_v2.models import TextbookSource

    return TextbookSource.rights_status.in_((RightsStatus.STAGED.value, RightsStatus.ACTIVE.value))
