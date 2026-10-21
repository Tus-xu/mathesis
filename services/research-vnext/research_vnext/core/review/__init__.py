"""RV15 · Review Finding 域导出（`research_vnext.core.review`）。

模块划分：
- `taxonomy`   —— 封闭词表 / 提案门 / 状态机 / waiver 门 / 指纹（纯函数）
- `service`    —— DB 编排（propose→PendingAction / apply 落行 / transition /
                   resolve / waive / 重审去重 / stale 标记 / blocking gate）
- `reviewer`   —— Reviewer AI run（propose-only，prompt 已发布版本）
"""

from . import service, taxonomy  # noqa: F401
from .service import BLOCKING_SEVERITIES  # noqa: F401
from .taxonomy import (  # noqa: F401
    ALLOWED_TARGET_TYPES,
    ALLOWED_TRANSITIONS,
    FINDING_CATEGORIES,
    FINDING_STATUSES,
    FindingTransitionInvalid,
    ReviewDomainError,
    ReviewerPolicyViolation,
    SEVERITIES,
    SEVERITY_DEFAULTS,
    assert_reviewer_operation_allowed,
    dedupe_action,
    default_due,
    fingerprint_of,
    validate_finding_proposal,
    validate_resolution,
    validate_transition,
    validate_waiver,
    waiver_is_expired,
)
