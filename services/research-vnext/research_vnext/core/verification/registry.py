"""RV12 · Verifier 注册表。

**fail closed**：不认识的方法一律拒绝（`UnknownVerifier`）。
"未知 ⇒ 默认通过"是这个产品最危险的默认值 —— 一个新 verifier 名字打错字
不应该让一条 claim 悄悄变成"已独立验证"。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain.errors import DomainError
from .types import VerificationKind

__all__ = [
    "VERIFIER_SPECS",
    "UnknownVerifier",
    "VerifierSpec",
    "get_verifier_spec",
    "require_verifier",
    "verifiers_for_kind",
]


class UnknownVerifier(DomainError):
    """请求了一个未注册的 verifier。"""

    code = "UNKNOWN_VERIFIER"
    http_status = 400


@dataclass(frozen=True)
class VerifierSpec:
    verifier_id: str
    version: str
    kind: str
    description: str
    requires_evidence: bool = False
    requires_experiment: bool = False
    cost_basis_default: str = "deterministic-no-llm"
    blocking_codes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "verifierId": self.verifier_id,
            "version": self.version,
            "kind": self.kind,
            "description": self.description,
            "requiresEvidence": self.requires_evidence,
            "requiresExperiment": self.requires_experiment,
            "costBasisDefault": self.cost_basis_default,
            "blockingCodes": list(self.blocking_codes),
        }


#: 七个 verifier，与 `VerificationKind` 一一对应。
VERIFIER_SPECS: dict[str, VerifierSpec] = {
    v.verifier_id: v
    for v in (
        VerifierSpec(
            "math.check",
            "1",
            VerificationKind.MATH_CHECK.value,
            "代数量纲/符号/极限检查：逐样本点算残差，超出容差即 FAIL。",
            blocking_codes=("MATH_IDENTITY_VIOLATED", "DIMENSION_MISMATCH"),
        ),
        VerifierSpec(
            "data.repro",
            "1",
            VerificationKind.DATA_REPRO.value,
            "数据复现：数据集指纹、行数、校验和两两对账。",
            requires_evidence=True,
            blocking_codes=("DATA_REPRO_MISMATCH",),
        ),
        VerifierSpec(
            "code.repro",
            "1",
            VerificationKind.CODE_REPRO.value,
            "代码复现：原运行与复现运行的 manifest 对比（EXACT_MATCH / MISMATCH）。",
            requires_experiment=True,
            blocking_codes=("CODE_REPRO_MISMATCH",),
        ),
        VerifierSpec(
            "sensitivity.sweep",
            "1",
            VerificationKind.SENSITIVITY.value,
            "敏感性：对参数扰动后指标相对变化是否在容差内。",
            blocking_codes=("SENSITIVITY_UNSTABLE",),
        ),
        VerifierSpec(
            "robustness.multi-seed",
            "1",
            VerificationKind.ROBUSTNESS.value,
            "robustness：多种子下的指标离散度是否在容差内。",
            blocking_codes=("ROBUSTNESS_SEED_SENSITIVE",),
        ),
        VerifierSpec(
            "counterexample.search",
            "1",
            VerificationKind.COUNTEREXAMPLE.value,
            "反例搜索：给定候选反例求值，命中即 FAIL 并保留见证物。",
            blocking_codes=("COUNTEREXAMPLE_REPRODUCED",),
        ),
        VerifierSpec(
            "boundary.cases",
            "1",
            VerificationKind.BOUNDARY.value,
            "边界检查：退化/极端参数下不变式是否仍成立。",
            blocking_codes=("BOUNDARY_VIOLATION",),
        ),
    )
}


def get_verifier_spec(verifier_id: str) -> VerifierSpec | None:
    return VERIFIER_SPECS.get(str(verifier_id or "").strip())


def require_verifier(verifier_id: str) -> VerifierSpec:
    """取 verifier；未注册即抛 `UnknownVerifier`（**永远不返回 None**）。"""
    spec = get_verifier_spec(verifier_id)
    if spec is None:
        raise UnknownVerifier(
            f"未知 verifier: {verifier_id!r}（已注册: {sorted(VERIFIER_SPECS)}）",
            verifierId=str(verifier_id),
            known=sorted(VERIFIER_SPECS),
        )
    return spec


def verifiers_for_kind(kind: str) -> list[VerifierSpec]:
    wanted = str(kind or "").upper()
    return [s for s in VERIFIER_SPECS.values() if s.kind == wanted]
