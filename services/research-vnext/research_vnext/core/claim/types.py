"""RV12 · Claim 域的封闭词表与判定参数。

**一处定义，处处引用**：状态、支持质量、链接角色、支持带、门禁码都在这里。
把它们散在多个模块里是这个产品最容易出的"两处判据漂移"类缺陷。
"""

from __future__ import annotations

from enum import Enum

# --------------------------------------------------------------------------
# 状态（任务书的四个状态，**没有第五个**）
# --------------------------------------------------------------------------


class ClaimStatus(str, Enum):
    SOLVED = "SOLVED"
    CONDITIONAL = "CONDITIONAL"
    INCONCLUSIVE = "INCONCLUSIVE"
    REFUTED = "REFUTED"


#: 用户可见标签。**颜色不是唯一状态表达** —— 每个状态都必须有文字标签
#: （无障碍要求 + 打印/灰度场景）。
STATUS_LABELS: dict[str, str] = {
    "SOLVED": "已解决（满足预先定义的证据与独立验证判据）",
    "CONDITIONAL": "有条件成立（列出了成立所需条件）",
    "INCONCLUSIVE": "未定（列出了缺口，未被证伪）",
    "REFUTED": "已被推翻（保留反证与见证物）",
}

STATUS_DESCRIPTIONS: dict[str, str] = {
    "SOLVED": "证据可用、支持强度达标，且存在独立验证 PASS，无反证。",
    "CONDITIONAL": "有支撑证据，但部分判据未满足（条件见 conditions）。",
    "INCONCLUSIVE": "可用证据或独立验证不足；这不等于成立，也不等于被推翻。",
    "REFUTED": "存在可复现的反证/反例，结论在当前范围内不成立。",
}


class SupportQuality(str, Enum):
    UNSUPPORTED = "UNSUPPORTED"
    PARTIAL = "PARTIAL"
    SUPPORTED = "SUPPORTED"
    CONFLICTED = "CONFLICTED"


class EvidenceLinkRole(str, Enum):
    SUPPORTS = "SUPPORTS"
    REFUTES = "REFUTES"
    CONTEXT = "CONTEXT"


class ExperimentLinkRole(str, Enum):
    SUPPORTS = "SUPPORTS"
    REFUTES = "REFUTES"
    CONTEXT = "CONTEXT"


class SupportBand(str, Enum):
    """支持强度的**偏序**（不是等距分数）。"""

    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    NONE = "NONE"


#: 允许进入 SOLVED 的最低支持带。
MIN_SOLVED_BAND = "MODERATE"

#: 支持带的偏序秩（用于最低要求比较）。
BAND_RANK: dict[str, int] = {"NONE": 0, "WEAK": 1, "MODERATE": 2, "STRONG": 3}


class CounterEvidenceSource(str, Enum):
    EVIDENCE = "EVIDENCE"
    VERIFICATION = "VERIFICATION"
    EXPERIMENT = "EXPERIMENT"
    HUMAN = "HUMAN"


#: 让 claim 进入 `requires_review` 的机读原因码（封闭集合）。
REVIEW_EVIDENCE_NOT_USABLE = "EVIDENCE_NOT_USABLE"
REVIEW_RIGHTS_UNKNOWN = "RIGHTS_UNKNOWN"
REVIEW_SOURCE_RETRACTED = "SOURCE_RETRACTED"
REVIEW_EVIDENCE_STALE = "EVIDENCE_STALE"
REVIEW_LOCATOR_BROKEN = "LOCATOR_UNRESOLVABLE"
REVIEW_LOCATOR_STALE = "LOCATOR_STALE"
REVIEW_CONFLICTED_EVIDENCE = "CONFLICTED_EVIDENCE"
REVIEW_VERIFICATION_UNKNOWN = "VERIFICATION_UNKNOWN"
REVIEW_VERIFICATION_FAIL = "VERIFICATION_FAIL"
REVIEW_VERIFIER_UNKNOWN = "VERIFIER_UNKNOWN"
REVIEW_SENSITIVITY_UNSTABLE = "SENSITIVITY_UNSTABLE"
REVIEW_ROBUSTNESS_UNSTABLE = "ROBUSTNESS_SEED_SENSITIVE"
REVIEW_COUNTEREXAMPLE_REPRODUCED = "COUNTEREXAMPLE_REPRODUCED"
REVIEW_EXPERIMENT_NOT_REPRODUCIBLE = "EXPERIMENT_NOT_REPRODUCIBLE"
REVIEW_REFERENCE_STALE = "REFERENCE_STALE"
REVIEW_AI_PROPOSED_NOT_VERIFIED = "AI_PROPOSED_NOT_VERIFIED"
REVIEW_BOUNDARY_VIOLATION = "BOUNDARY_VIOLATION"

#: 任务书 §必须完成 1：Claim 必须携带的字段（对外键名即契约）。
REQUIRED_CLAIM_FIELDS: frozenset[str] = frozenset(
    {
        "statement",
        "scope",
        "assumptions",
        "status",
        "confidenceBasis",
        "evidenceLinks",
        "experimentLinks",
        "counterEvidence",
        "limitations",
        "version",
        "owner",
        "acl",
        "audit",
    }
)

__all__ = [
    "BAND_RANK",
    "MIN_SOLVED_BAND",
    "REQUIRED_CLAIM_FIELDS",
    "STATUS_DESCRIPTIONS",
    "STATUS_LABELS",
    "CounterEvidenceSource",
    "ClaimStatus",
    "EvidenceLinkRole",
    "ExperimentLinkRole",
    "SupportBand",
    "SupportQuality",
]
