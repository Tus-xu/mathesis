"""R14/R18 Teacher Prep 领域状态机（确定性、纯函数、表驱动）。

铁律（DECISIONS.md §4/§6，R13 state_machines.py 同构推广到业务实体）：
- 表驱动迁移，禁止散落的 if/else 状态分支；
- LLM 只产生 proposal 载荷；确认状态（confirmed/rejected）只能由
  `confirm_blueprint / reject_blueprint` 命令且 actor 具备教学角色时推进——
  本模块提供 `assert_transition` 与 `assert_can_confirm` 两道纯函数闸门；
- 非法迁移 → `PrepStateTransitionInvalid`（HTTP 409）。

状态总览：
  TeachingSession:  draft → active → cancelled/archived；archived 终态。
                   「继续历史」= 对 active 会话 open（不改变状态，仅留 resumed 事件）。
  TeachingBlueprint: proposal ⇄ rejected / proposal → confirmed → proposal（修订再提案）。
                   每次状态变化（含再次 proposal）都会产生新的 BlueprintRevision 快照。
  DiagnosticTask（R18）: proposal → draft（教师编辑）→ published/skipped（终态）；
                   proposal → published（不编辑直接发布）/ skipped（明确跳过）也可。
                   发布是高风险写操作：published 需经持久 PendingAction + 8000
                   Coursework Adapter 下发 TaskAssignment（见 orchestrator 发布路径）。
"""

from __future__ import annotations

import re

SESSION_DRAFT, SESSION_ACTIVE, SESSION_CANCELLED, SESSION_ARCHIVED = (
    "draft", "active", "cancelled", "archived",
)

BLUEPRINT_PROPOSAL, BLUEPRINT_CONFIRMED, BLUEPRINT_REJECTED = "proposal", "confirmed", "rejected"

# R18 学前诊断任务状态
DIAGNOSTIC_PROPOSAL, DIAGNOSTIC_DRAFT, DIAGNOSTIC_PUBLISHED, DIAGNOSTIC_SKIPPED = (
    "proposal", "draft", "published", "skipped",
)

# R20 版本化班级学习画像状态
PROFILE_COLLECTING, PROFILE_PREVIEW, PROFILE_PUBLISHED, PROFILE_ARCHIVED = (
    "collecting", "preview", "published", "archived",
)

# ── 会话生命周期 ──────────────────────────────────────────────────────────────
SESSION_TRANSITIONS: dict[str, tuple[str, ...]] = {
    SESSION_DRAFT: (SESSION_ACTIVE, SESSION_CANCELLED, SESSION_ARCHIVED),
    SESSION_ACTIVE: (SESSION_CANCELLED, SESSION_ARCHIVED),
    SESSION_CANCELLED: (SESSION_ARCHIVED,),
    SESSION_ARCHIVED: (),  # 终态
}

# ── 蓝图生命周期 ──────────────────────────────────────────────────────────────
BLUEPRINT_TRANSITIONS: dict[str, tuple[str, ...]] = {
    BLUEPRINT_PROPOSAL: (BLUEPRINT_CONFIRMED, BLUEPRINT_REJECTED),
    BLUEPRINT_CONFIRMED: (BLUEPRINT_PROPOSAL,),   # 教师修改后再次提案（revision proposal）
    BLUEPRINT_REJECTED: (BLUEPRINT_PROPOSAL,),    # 驳回后可重新提案（re-propose）
}

# ── 学前诊断任务生命周期（R18）────────────────────────────────────────────────
DIAGNOSTIC_TRANSITIONS: dict[str, tuple[str, ...]] = {
    DIAGNOSTIC_PROPOSAL: (DIAGNOSTIC_DRAFT, DIAGNOSTIC_PUBLISHED, DIAGNOSTIC_SKIPPED),
    DIAGNOSTIC_DRAFT: (DIAGNOSTIC_PUBLISHED, DIAGNOSTIC_SKIPPED),
    DIAGNOSTIC_PUBLISHED: (),  # 终态
    DIAGNOSTIC_SKIPPED: (),    # 终态
}

# ── 版本化班级学习画像生命周期（R20）──────────────────────────────────────────
# published → published 为「更新再发布」（revision++，同态幂等，assert_transition
# 的 current==target 早退覆盖）；archived 为终态（已归档画像不可再发布/引用）。
PROFILE_TRANSITIONS: dict[str, tuple[str, ...]] = {
    PROFILE_COLLECTING: (PROFILE_PREVIEW, PROFILE_PUBLISHED),
    PROFILE_PREVIEW: (PROFILE_PUBLISHED, PROFILE_ARCHIVED),
    PROFILE_PUBLISHED: (PROFILE_ARCHIVED,),
    PROFILE_ARCHIVED: (),  # 终态
}

SESSION_STATES = frozenset(SESSION_TRANSITIONS)
BLUEPRINT_STATES = frozenset(BLUEPRINT_TRANSITIONS)
DIAGNOSTIC_STATES = frozenset(DIAGNOSTIC_TRANSITIONS)
PROFILE_STATES = frozenset(PROFILE_TRANSITIONS)

# 命令类型（审计口径）
COMMAND_TYPES = (
    "create_session",
    "open_session",      # 首次激活 / 继续历史（resume 语义，状态不变）
    "cancel_session",
    "archive_session",
    "propose_blueprint",
    "confirm_blueprint",
    "reject_blueprint",
    "propose_diagnostic",   # R18 学前诊断：生成提案（LLM，worker 执行）
    "edit_diagnostic",      # R18 教师编辑草稿（改题/删题/调整时长）
    "publish_diagnostic",   # R18 高风险发布（PendingAction + 8000 Coursework Adapter）
    "skip_diagnostic",      # R18 明确跳过（审计）
    "publish_profile",      # R20 学情证据发布/更新（版本化画像）
    "recalculate_profile",  # R19 经 Adapter 拉取最小素材 → 确定性重算画像（幂等）
    "interpret_profile",    # R19 AI 解释 proposal（LLM 只产 proposal，失败不影响统计）
    "propose_blueprint_revision",  # R20 已确认蓝图 + 画像 → 修订提案（未确认不下发）
    "project_specs",        # R20 双 Spec 投影（approved blueprint + immutable context）
)

# 事件类型（lineage 口径：command 事件为链头，业务事件以它为 parent）
EVENT_TYPES = (
    "session.created",
    "session.opened",
    "session.resumed",
    "session.cancelled",
    "session.archived",
    "blueprint.proposed",
    "blueprint.confirmed",
    "blueprint.rejected",
    "diagnostic.proposed",      # R18
    "diagnostic.edited",        # R18
    "diagnostic.published",     # R18
    "diagnostic.publish.failed",  # R18（8000/队列暂时失败，可安全重试）
    "diagnostic.skipped",       # R18
    "diagnostic.pending.expired",  # R18（PendingAction 过期审计）
    "profile.published",    # R20 画像发布（首版）
    "profile.updated",      # R20 画像更新（revision++）
    "profile.recalculated",  # R19 确定性重算（updated=新版本 / replayed=指纹相同 no-op）
    "profile.interpretation.proposed",  # R19 AI 解释 proposal 已存（ai_suggestion）
    "profile.interpretation.failed",    # R19 AI 解释失败（basic_mode；统计不受影响）
    "blueprint.revision_proposed",  # R20 修订提案（含 diff 摘要）
    "specs.projected",      # R20 双 Spec 投影
    "command.applied",
)

# 教学角色白名单（确认/驳回等状态落定必须）——与 8000 ClassMember member_role 交集
TEACHING_ROLES = ("teacher", "owner", "assistant")

IDEMPOTENCY_KEY_RE = r"^[A-Za-z0-9._:-]{8,128}$"


class PrepStateTransitionInvalid(ValueError):
    """非法状态迁移（409 语义）。"""


class PrepConfirmForbidden(ValueError):
    """确认/驳回动作的角色/成员闸门（403 语义）。"""


def assert_transition(kind: str, current: str, target: str) -> None:
    """校验合法迁移；非法 → PrepStateTransitionInvalid。"""
    table = _transition_table(kind)
    if current not in table:
        raise PrepStateTransitionInvalid(kind, f"{current} → {target}")
    if current == target:
        return  # 幂等目标态（如 resume 时 status 不变）允许
    if target not in table[current]:
        raise PrepStateTransitionInvalid(kind, f"{current} → {target}")


def _transition_table(kind: str) -> dict[str, tuple[str, ...]]:
    if kind == "session":
        return SESSION_TRANSITIONS
    if kind == "blueprint":
        return BLUEPRINT_TRANSITIONS
    if kind == "diagnostic":
        return DIAGNOSTIC_TRANSITIONS
    if kind == "profile":
        return PROFILE_TRANSITIONS
    raise PrepStateTransitionInvalid(kind, f"未知状态机类型 {kind!r}")


def validate_state(kind: str, state: str) -> None:
    table = _transition_table(kind)
    if state not in table:
        raise PrepStateTransitionInvalid(kind, f"未知状态 {state!r}")


def assert_confirm_actor(actor_role: str) -> None:
    """确认/驳回必须由教学角色发起；LLM（system/ai）无权落定确认状态。"""
    if actor_role not in TEACHING_ROLES:
        raise PrepConfirmForbidden(
            f"确认/驳回仅允许 {TEACHING_ROLES} 角色，实际 actor_role={actor_role!r}"
        )


def validate_idempotency_key(value: str) -> None:
    if not re.fullmatch(IDEMPOTENCY_KEY_RE, value):
        raise PrepStateTransitionInvalid(f"idempotency_key 不符合规范（{IDEMPOTENCY_KEY_RE}）")
