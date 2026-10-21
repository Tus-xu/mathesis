"""RV09–RV13 领域对象的**领域规则**（纯函数 + 显式状态迁移）。

这里放的是"什么算成立"的判定，而不是 IO。三条最重要的规则：

1. **`exit_code == 0` 不等于"结论成立"**
   `claim_holds` 与 `exit_code` 是两个正交字段；执行器返回 0 只说明程序跑完，
   不说明假设被验证。

2. **`UNKNOWN` 永远不被强制转成 `PASS`**
   独立验证的三种结果必须保持可区分。把"没验证"折叠成"通过"是这个产品
   最危险的失真，因此在**类型层**上就不给默认值。

3. **失效证据 / 不可复现实验可以重开 SOLVED claim**
   否则"已解决"会变成一个无法被推翻的终态 —— 那与科研的运作方式相反。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CLAIM_STATUSES = ("SOLVED", "CONDITIONAL", "INCONCLUSIVE", "REFUTED")
CLAIM_SUPPORT = ("UNSUPPORTED", "PARTIAL", "SUPPORTED", "CONFLICTED")
VERIFICATION_RESULTS = ("PASS", "FAIL", "UNKNOWN")
#: 审稿发现的封闭分类法。越界即拒（不给"其它"这种逃跑口）。
REVIEW_CATEGORIES = (
    "EVIDENCE_GAP",
    "MATH_ERROR",
    "DATA_LEAKAGE",
    "REPRO_GAP",
    "CITATION_MISMATCH",
    "OVERCLAIM",
    "WRITING_ISSUE",
)
PAPER_BLOCK_TYPES = (
    "section",
    "paragraph",
    "equation",
    "figure",
    "table",
    "citation",
    "claim_ref",
    "evidence_ref",
    "review_mark",
)

#: 重开 SOLVED claim 的原因码。
REOPEN_EVIDENCE_INVALIDATED = "EVIDENCE_INVALIDATED"
REOPEN_NOT_REPRODUCIBLE = "NOT_REPRODUCIBLE"
REOPEN_VERIFICATION_FAILED = "VERIFICATION_FAILED"


class DomainRuleViolation(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# claim 支持质量
# --------------------------------------------------------------------------


def derive_support_quality(
    *, evidence_refs: list[str], valid_evidence_ids: set[str], verification_results: list[str]
) -> str:
    """从**已生效**的证据与验证结果推导支持质量。

    - 有 `FAIL` 验证 ⇒ `CONFLICTED`（存在反对证据，不是"支持不足"）；
    - 无可用证据 ⇒ `UNSUPPORTED`；
    - 全部证据失效/未授权 ⇒ `UNSUPPORTED`；
    - 有证据且有 `PASS` ⇒ `SUPPORTED`；
    - 有证据但**没有任何** `PASS`（只有 UNKNOWN）⇒ `PARTIAL`。

    注意最后一条：`UNKNOWN` 不算支持。把"未验证"当作"部分支持"是常见滑坡，
    这里明确排除。
    """
    if any(r == "FAIL" for r in verification_results):
        return "CONFLICTED"
    usable = [e for e in evidence_refs if e in valid_evidence_ids]
    if not usable:
        return "UNSUPPORTED"
    if any(r == "PASS" for r in verification_results):
        return "SUPPORTED"
    return "PARTIAL"


def validate_claim_transition(
    *,
    current_status: str,
    new_status: str,
    evidence_refs: list[str],
    valid_evidence_ids: set[str],
    support_quality: str,
) -> None:
    """claim 状态迁移的硬规则。违反即拒绝（不静默降级）。"""
    if current_status not in CLAIM_STATUSES:
        raise DomainRuleViolation("CLAIM_STATUS_UNKNOWN", f"未知当前状态: {current_status!r}")
    if new_status not in CLAIM_STATUSES:
        raise DomainRuleViolation("CLAIM_STATUS_UNKNOWN", f"未知目标状态: {new_status!r}")

    if new_status == "SOLVED":
        usable = [e for e in evidence_refs if e in valid_evidence_ids]
        if not usable:
            raise DomainRuleViolation(
                "CLAIM_SOLVED_WITHOUT_EVIDENCE",
                "SOLVED 必须有**有效**证据支撑；无证据或证据已失效只能 INCONCLUSIVE",
            )
        if support_quality not in ("SUPPORTED", "PARTIAL"):
            raise DomainRuleViolation(
                "CLAIM_SOLVED_WITH_WEAK_SUPPORT",
                f"SOLVED 需要 SUPPORTED/PARTIAL 的支持质量，实际 {support_quality}",
            )
        if support_quality == "CONFLICTED":
            raise DomainRuleViolation(
                "CLAIM_SOLVED_WITH_CONFLICT", "存在 FAIL 验证（CONFLICTED）时不得标 SOLVED"
            )


# --------------------------------------------------------------------------
# 实验运行
# --------------------------------------------------------------------------


@dataclass
class ReproMetadata:
    image_digest: str = ""
    dependency_lock_hash: str = ""
    seed: int | None = None
    code_hash: str = ""
    input_hash: str = ""
    output_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "imageDigest": self.image_digest,
            "dependencyLockHash": self.dependency_lock_hash,
            "seed": self.seed,
            "codeHash": self.code_hash,
            "inputHash": self.input_hash,
            "outputHash": self.output_hash,
        }


def evaluate_run(
    *,
    exit_code: int | None,
    repro: dict[str, Any],
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把一次运行的原始产物翻译成**不撒谎**的记录。

    返回的 `claim_holds` **永远是 None**：一次运行本身不能证明结论成立，
    只能提供证据。判定必须来自 `VerificationRecord`（独立验证）。
    """
    missing = [
        k
        for k in ("imageDigest", "dependencyLockHash", "seed", "codeHash")
        if not (repro or {}).get(k)
    ]
    reproducible = not missing
    return {
        "exit_code": exit_code,
        # 显式 None：程序跑完 ≠ 假设成立
        "claim_holds": None,
        "reproducible": reproducible,
        "not_reproducible_reason": (
            "" if reproducible else f"缺少可复现元数据: {', '.join(missing)}"
        ),
        "metrics": dict(metrics or {}),
    }


# --------------------------------------------------------------------------
# 验证
# --------------------------------------------------------------------------


def normalize_verification_result(raw: str) -> str:
    """把外部输入规范成 `PASS|FAIL|UNKNOWN`。

    **无法识别 ⇒ UNKNOWN**，绝不放行成 PASS。这是刻意的 fail-closed 方向：
    一个拼错的 `"passed"` 不应该被当成"验证通过"。
    """
    text = str(raw or "").strip().upper()
    if text in VERIFICATION_RESULTS:
        return text
    return "UNKNOWN"


def claim_effect_of_verification(result: str) -> dict[str, Any]:
    """一次验证对 claim 的影响（**不**直接改 claim，只给出建议动作）。"""
    r = normalize_verification_result(result)
    if r == "FAIL":
        return {"action": "consider_refute", "reopen": True, "reason": REOPEN_VERIFICATION_FAILED}
    if r == "PASS":
        return {"action": "supports", "reopen": False, "reason": ""}
    return {
        "action": "no_evidence",
        "reopen": False,
        "reason": "",
        "note": "UNKNOWN 不构成任何支持，也不构成反驳",
    }


def reopen_decision(
    *,
    claim_status: str,
    evidence_refs: list[str],
    valid_evidence_ids: set[str],
    reproducible: bool,
) -> dict[str, Any]:
    """判断是否必须**重开**一个 SOLVED claim。

    两种触发条件（都需要）：
    1. claim 当前是 `SOLVED`；
    2. 它依赖的证据已失效，或它依赖的实验不可复现。

    返回 `{"reopen": bool, "reason": str}`。重开后的状态建议为 `INCONCLUSIVE`
    ——不是 `REFUTED`：证据消失只说明"不再成立"，不说明"被证伪"。
    """
    if claim_status != "SOLVED":
        return {"reopen": False, "reason": ""}
    usable = [e for e in evidence_refs if e in valid_evidence_ids]
    if not usable:
        return {
            "reopen": True,
            "reason": REOPEN_EVIDENCE_INVALIDATED,
            "to_status": "INCONCLUSIVE",
            "detail": "全部依赖证据已失效或被撤权",
        }
    if not reproducible:
        return {
            "reopen": True,
            "reason": REOPEN_NOT_REPRODUCIBLE,
            "to_status": "INCONCLUSIVE",
            "detail": "依赖的实验缺少可复现元数据",
        }
    return {"reopen": False, "reason": ""}


# --------------------------------------------------------------------------
# PaperSpec block 树
# --------------------------------------------------------------------------


def apply_paper_patch(
    current_blocks: list[dict], patch_blocks: list[dict], *, base_revision: int, current_revision: int
) -> list[dict]:
    """把 AI patch 应用到 block 树上。

    **必须**先通过 `base_revision` 检查：AI 的 patch 是基于某个修订提出的，
    若当前修订已经前进，直接套用会静默覆盖别人的修改。
    """
    if base_revision != current_revision:
        raise DomainRuleViolation(
            "PAPER_BASE_REVISION_STALE",
            f"patch 基于 revision {base_revision}，当前 {current_revision}："
            "必须先让人确认，不得静默覆盖",
        )
    by_id = {b.get("id"): dict(b) for b in current_blocks}
    order = [b.get("id") for b in current_blocks]
    for pb in patch_blocks:
        pid = pb.get("id")
        if pid in by_id:
            by_id[pid] = dict(pb)
        else:
            by_id[pid] = dict(pb)
            order.append(pid)
    return [by_id[i] for i in order if i in by_id]
