"""RV06 · scope 权威：一次性冻结，provider 只能收窄。

设计要点：
- scope 由 `resolve_scope()` 从网关注入的 AccessContext 派生，**冻结**成
  `ResolvedScope`；它没有任何 `widen()` / `add_project()` 之类的入口，
  provider 拿到的只是**读**它的一份引用。
- 每个命中都要经 `check_hit_scope()` 复核；越界命中被剔除并记
  `SCOPE_VIOLATION`，而不是"相信 provider 会自己过滤"。
- 跨 tenant / 跨 project / 跨 school（班级）三类负例都在这里一处收口，
  避免三条读取路径各自实现导致口径漂移。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from .contracts import ProviderHit
from .errors import ScopeViolation

#: 进入 research 域的最低能力（与 RV02 `api/deps.py` 的 READ_CAPABILITY 同值）。
RESEARCH_ENTER_CAPABILITY = "workspace.research.enter"


class ViolationCode(str, Enum):
    TENANT_MISMATCH = "TENANT_MISMATCH"
    PROJECT_MISMATCH = "PROJECT_MISMATCH"
    SCHOOL_MISMATCH = "SCHOOL_MISMATCH"
    PROVIDER_NOT_ALLOWED = "PROVIDER_NOT_ALLOWED"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ResolvedScope:
    """已冻结的检索范围。字段齐备后不可变；无任何放宽入口。"""

    actor_id: str
    tenant_id: str
    project_ids: frozenset[str]
    session_id: str | None
    school_id: str | None
    capabilities: frozenset[str]
    provider_allowlist: tuple[str, ...]
    frozen_at: str = ""

    def allows_provider(self, provider: str) -> bool:
        return provider in self.provider_allowlist

    def allows_project(self, project_id: str | None) -> bool:
        if not self.project_ids:
            return True
        return project_id is not None and project_id in self.project_ids

    def allows_school(self, school_id: str | None) -> bool:
        """未限定 school 的 scope 不做 school 维度判断（避免误杀非教材来源）。"""
        if self.school_id is None:
            return True
        if school_id is None:
            return True  # 平台级种子（school_id IS NULL）对所有 school 可见
        return school_id == self.school_id

    def as_dict(self) -> dict[str, object]:
        return {
            "actor_id": self.actor_id,
            "tenant_id": self.tenant_id,
            "project_ids": sorted(self.project_ids),
            "session_id": self.session_id,
            "school_id": self.school_id,
            "provider_allowlist": list(self.provider_allowlist),
            "frozen_at": self.frozen_at,
        }


def resolve_scope(
    *,
    actor_id: str,
    tenant_id: str,
    project_ids: Iterable[str] | None,
    session_id: str | None,
    school_id: str | None,
    capabilities: Iterable[str],
    provider_allowlist: Sequence[str],
    frozen_at: str | None = None,
) -> ResolvedScope:
    """派生并冻结 scope。缺 research 进入能力即 fail closed。"""
    caps = frozenset(str(c) for c in capabilities)
    if RESEARCH_ENTER_CAPABILITY not in caps:
        raise ScopeViolation(
            f"缺少 {RESEARCH_ENTER_CAPABILITY} 能力（fail closed，不降级为只读放行）"
        )
    if not tenant_id:
        raise ScopeViolation("scope 必须绑定 tenant")
    allow = tuple(dict.fromkeys(str(p) for p in provider_allowlist if str(p)))
    return ResolvedScope(
        actor_id=str(actor_id),
        tenant_id=str(tenant_id),
        project_ids=frozenset(str(p) for p in (project_ids or ()) if p),
        session_id=session_id,
        school_id=school_id,
        capabilities=caps,
        provider_allowlist=allow,
        frozen_at=frozen_at or _utcnow_iso(),
    )


def check_hit_scope(hit: ProviderHit, scope: ResolvedScope) -> ViolationCode | None:
    """复核单条命中的归属。返回 `None` 表示合法。"""
    if not scope.allows_provider(hit.provider):
        return ViolationCode.PROVIDER_NOT_ALLOWED
    meta = hit.scope_meta
    if str(meta.tenant_id) != scope.tenant_id:
        return ViolationCode.TENANT_MISMATCH
    if hit.provider not in PROJECT_AGNOSTIC_PROVIDERS and not scope.allows_project(meta.project_id):
        return ViolationCode.PROJECT_MISMATCH
    if not scope.allows_school(meta.school_id):
        return ViolationCode.SCHOOL_MISMATCH
    return None


#: 不按 project 隔离的 provider：只读**平台级/校级**语料，本就不属于任何 project。
#:
#: `foundational_math` 读的是 Teacher direct-chunk —— 教材整册入库，没有 project 归属；
#: 强行要求它匹配某个 project 只会把教材全部误杀。它的隔离维度是 **school**，
#: 仍由 `allows_school()` 与 rights 门把守，不放宽 tenant。
#:
#: `literature` 不在此列：RV04 的 `literature_record` 有真实 `project_id`，应当按 project 隔离。
PROJECT_AGNOSTIC_PROVIDERS: frozenset[str] = frozenset({"foundational_math"})


__all__ = [
    "ViolationCode",
    "ResolvedScope",
    "resolve_scope",
    "check_hit_scope",
    "RESEARCH_ENTER_CAPABILITY",
    "PROJECT_AGNOSTIC_PROVIDERS",
]
