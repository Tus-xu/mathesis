"""RV12 · Verification 域的封闭词表、工具策略与结构化记录。

**三条铁律：**

1. 验证结果只有 `PASS | FAIL | UNKNOWN`；**未知永不强制转 PASS**
   （`normalize` 走 fail-closed 方向，拼错的 `"passed"` 不是 PASS）。
2. Verifier **只观察**：`VerificationRecord` 里没有"已改了什么"的字段，
   只有 `observationOnly=True`；写状态是 claim 域的事。
3. 工具策略是**可机读的权限表**：修改原实验、删除反证、改证据、发布 claim
   都在 `FORBIDDEN_OPERATIONS` 里，任何越界调用当场抛错。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ..domain.errors import DomainError

# --------------------------------------------------------------------------
# 封闭词表
# --------------------------------------------------------------------------


class VerificationKind(str, Enum):
    MATH_CHECK = "MATH_CHECK"
    DATA_REPRO = "DATA_REPRO"
    CODE_REPRO = "CODE_REPRO"
    SENSITIVITY = "SENSITIVITY"
    ROBUSTNESS = "ROBUSTNESS"
    COUNTEREXAMPLE = "COUNTEREXAMPLE"
    BOUNDARY = "BOUNDARY"


RESULTS: frozenset[str] = frozenset({"PASS", "FAIL", "UNKNOWN"})


def normalize_result(raw: Any) -> str:
    """规范成封闭三值。**无法识别 ⇒ UNKNOWN**（绝不放行成 PASS）。"""
    text = str(raw or "").strip().upper()
    return text if text in RESULTS else "UNKNOWN"


# --------------------------------------------------------------------------
# 操作与工具策略
# --------------------------------------------------------------------------

#: 允许的操作（白名单）。
READ_EVIDENCE = "READ_EVIDENCE"
READ_EXPERIMENT_RESULT = "READ_EXPERIMENT_RESULT"
READ_CLAIM = "READ_CLAIM"
RUN_CHECK = "RUN_CHECK"
WRITE_VERIFICATION_RECORD = "WRITE_VERIFICATION_RECORD"
WRITE_ARTIFACT = "WRITE_ARTIFACT"

#: **明确禁止**的操作。前四项是任务书 §8 点名的越权面。
MUTATE_EXPERIMENT = "MUTATE_EXPERIMENT"
DELETE_COUNTEREVIDENCE = "DELETE_COUNTEREVIDENCE"
MODIFY_EVIDENCE = "MODIFY_EVIDENCE"
WRITE_DOMAIN_STATE = "WRITE_DOMAIN_STATE"
PUBLISH_CLAIM = "PUBLISH_CLAIM"
NETWORK_ACCESS = "NETWORK_ACCESS"

FORBIDDEN_OPERATIONS: frozenset[str] = frozenset(
    {
        MUTATE_EXPERIMENT,
        DELETE_COUNTEREVIDENCE,
        MODIFY_EVIDENCE,
        WRITE_DOMAIN_STATE,
        PUBLISH_CLAIM,
        NETWORK_ACCESS,
    }
)

ALLOWED_OPERATIONS: frozenset[str] = frozenset(
    {
        READ_EVIDENCE,
        READ_EXPERIMENT_RESULT,
        READ_CLAIM,
        RUN_CHECK,
        WRITE_VERIFICATION_RECORD,
        WRITE_ARTIFACT,
    }
)


class ToolPolicyViolation(DomainError):
    """Verifier 试图执行被禁止的操作（越权）。"""

    code = "TOOL_POLICY_VIOLATION"
    http_status = 403


@dataclass(frozen=True)
class ToolPolicy:
    allowed_operations: frozenset[str]
    forbidden_operations: frozenset[str]
    network: str = "deny"
    max_cost_usd: float = 0.25
    max_artifact_refs: int = 32
    max_wall_ms: int = 60_000

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowedOperations": sorted(self.allowed_operations),
            "forbiddenOperations": sorted(self.forbidden_operations),
            "network": self.network,
            "maxCostUsd": self.max_cost_usd,
            "maxArtifactRefs": self.max_artifact_refs,
            "maxWallMs": self.max_wall_ms,
        }


DEFAULT_TOOL_POLICY = ToolPolicy(
    allowed_operations=ALLOWED_OPERATIONS,
    forbidden_operations=FORBIDDEN_OPERATIONS,
)


def assert_operation_allowed(policy: ToolPolicy, operation: str) -> None:
    """白名单之外一律拒绝（**未知操作 = 拒绝**，不是默认放行）。"""
    op = str(operation or "").strip().upper()
    if op in policy.forbidden_operations:
        raise ToolPolicyViolation(
            f"Verifier 无权执行 {op}：验证只观察，不改事实",
            operation=op,
        )
    if op not in policy.allowed_operations:
        raise ToolPolicyViolation(
            f"未知操作 {op!r}：Verifier 的操作是封闭白名单", operation=op
        )


# --------------------------------------------------------------------------
# 记录
# --------------------------------------------------------------------------


class VerificationResultInvalid(DomainError):
    code = "VERIFICATION_RESULT_UNKNOWN"
    http_status = 422


@dataclass(frozen=True)
class VerificationCriterion:
    code: str
    passed: bool
    detail: str = ""
    blocking: str = ""
    observed: Any = None
    expected: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "passed": bool(self.passed),
            "detail": self.detail,
            "blocking": self.blocking,
            "observed": self.observed,
            "expected": self.expected,
        }


@dataclass(frozen=True)
class VerificationRecord:
    """一次验证的**完整**审计形状。

    `cost_usd is None` 表示"不知道花了多少钱"，与 0 美元的语义**不同**
    （零成本与未知成本在运维上是两回事）。
    """

    record_id: str
    claim_id: str
    session_id: str
    tenant_id: str
    verifier_id: str
    verifier_version: str
    kind: str
    result: str
    criteria: tuple[VerificationCriterion, ...]
    artifact_refs: tuple[str, ...]
    detail: dict[str, Any] = field(default_factory=dict)
    independent: bool = False
    prompt_ref: str = ""
    context_manifest_ref: str = ""
    tool_policy: ToolPolicy = DEFAULT_TOOL_POLICY
    cost_usd: float | None = None
    cost_basis: str = "deterministic-no-llm"
    ai_run_id: str = ""
    trace_id: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        normalized = normalize_result(self.result)
        if str(self.result).strip().upper() not in RESULTS:
            raise VerificationResultInvalid(
                f"未知验证结果 {self.result!r}（只允许 PASS/FAIL/UNKNOWN）"
            )
        object.__setattr__(self, "result", normalized)
        object.__setattr__(self, "criteria", tuple(self.criteria))
        object.__setattr__(self, "artifact_refs", tuple(str(a) for a in self.artifact_refs))

    def to_dict(self) -> dict[str, Any]:
        return {
            "recordId": self.record_id,
            "claimId": self.claim_id,
            "sessionId": self.session_id,
            "tenantId": self.tenant_id,
            "verifierId": self.verifier_id,
            "verifierVersion": self.verifier_version,
            "kind": self.kind,
            "result": self.result,
            "criteria": [c.as_dict() for c in self.criteria],
            "artifactRefs": list(self.artifact_refs),
            "detail": dict(self.detail),
            "independent": bool(self.independent),
            "promptRef": self.prompt_ref,
            "contextManifestRef": self.context_manifest_ref,
            "toolPolicy": self.tool_policy.as_dict(),
            "costUsd": self.cost_usd,
            "costBasis": self.cost_basis,
            "aiRunId": self.ai_run_id,
            "traceId": self.trace_id,
            "createdAt": self.created_at,
            # Verifier 只观察：记录里不存在"已改事实"的字段。
            "observationOnly": True,
        }


@dataclass(frozen=True)
class VerifierOutcome:
    """适配器的输出（纯数据，不含 IO）。"""

    result: str
    criteria: tuple[dict[str, Any], ...] = ()
    detail: dict[str, Any] = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "result", normalize_result(self.result))
        object.__setattr__(self, "artifacts", tuple(str(a) for a in self.artifacts))
        object.__setattr__(self, "unknowns", tuple(str(u) for u in self.unknowns))

    def to_criteria(self) -> tuple[VerificationCriterion, ...]:
        out: list[VerificationCriterion] = []
        for c in self.criteria:
            if isinstance(c, VerificationCriterion):
                out.append(c)
            else:
                m = dict(c)
                out.append(
                    VerificationCriterion(
                        code=str(m.get("code") or ""),
                        passed=bool(m.get("passed")),
                        detail=str(m.get("detail") or ""),
                        blocking=str(m.get("blocking") or ""),
                        observed=m.get("observed"),
                        expected=m.get("expected"),
                    )
                )
        return tuple(out)

    def as_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "criteria": [c if isinstance(c, Mapping) else c.as_dict() for c in self.criteria],
            "detail": dict(self.detail),
            "artifacts": list(self.artifacts),
            "unknowns": list(self.unknowns),
        }


__all__ = [
    "ALLOWED_OPERATIONS",
    "DEFAULT_TOOL_POLICY",
    "DELETE_COUNTEREVIDENCE",
    "FORBIDDEN_OPERATIONS",
    "MODIFY_EVIDENCE",
    "MUTATE_EXPERIMENT",
    "NETWORK_ACCESS",
    "PUBLISH_CLAIM",
    "READ_CLAIM",
    "READ_EVIDENCE",
    "READ_EXPERIMENT_RESULT",
    "RESULTS",
    "RUN_CHECK",
    "ToolPolicy",
    "ToolPolicyViolation",
    "WRITE_ARTIFACT",
    "WRITE_DOMAIN_STATE",
    "WRITE_VERIFICATION_RECORD",
    "VerificationCriterion",
    "VerificationKind",
    "VerificationRecord",
    "VerificationResultInvalid",
    "VerifierOutcome",
    "assert_operation_allowed",
    "normalize_result",
]
