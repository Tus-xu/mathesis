"""RV15 · Review Finding 域的**封闭词表与纯函数判定**。

设计立场（与 FINDING-TAXONOMY.md 一一对应，测试钉死防漂移）：

1. taxonomy 是**封闭十类**，不给"其它"逃跑口；
2. 提案即验证：无 locator、无 evidence 的强结论 finding 在入口就被拒；
3. Reviewer AI 只能 **propose**：类型层的 ToolPolicy 把"改论文/改 claim/
   删反证/批豁免"全部封死；
4. 状态机六态封闭，RESOLVED/WAIVED 各有硬门；
5. 指纹去重：重审不复制 finding，而是更新或重开。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..domain.errors import DomainError

# ==========================================================================
# 错误
# ==========================================================================


class ReviewDomainError(DomainError):
    code = "REVIEW_DOMAIN_ERROR"
    http_status = 422

    def __init__(self, code: str | None = None, message: str = "", **detail: object) -> None:
        # code 为 None 时使用类级 code（子类如 FindingTransitionInvalid 不传 code，
        # 第一个位置参数就是 message）；显式传 code 则覆盖（如 _err 工厂）。
        if code is None:
            super().__init__(message or type(self).code, **detail)
            self.code = type(self).code
        elif code in _KNOWN_CODES:
            super().__init__(message or code, code=code, **detail)
            self.code = code
        else:  # 兼容"首参是 message"的调用形状
            super().__init__(code or type(self).code, code=type(self).code, **detail)
            self.code = type(self).code


class ReviewerPolicyViolation(ReviewDomainError):
    """Reviewer 试图执行被禁止的操作（越权）。"""

    code = "REVIEWER_POLICY_VIOLATION"
    http_status = 403


_KNOWN_CODES: frozenset[str] = frozenset(
    {
        "REVIEW_TAXONOMY_UNKNOWN", "REVIEW_TARGET_INVALID", "REVIEW_LOCATOR_REQUIRED",
        "REVIEW_DESCRIPTION_REQUIRED", "REVIEW_EVIDENCE_REQUIRED", "REVIEW_EVIDENCE_INVALID",
        "REVIEW_SERVER_OWNED_FIELD", "REVIEWER_POLICY_VIOLATION",
        "FINDING_TRANSITION_INVALID", "FINDING_RESOLUTION_NO_REVISION",
        "FINDING_RESOLUTION_STALE_TARGET", "FINDING_WAIVER_REASON_REQUIRED",
        "FINDING_WAIVER_REASON_INVALID", "FINDING_WAIVER_EXPIRY_REQUIRED",
        "FINDING_WAIVER_EXPIRY_INVALID", "FINDING_WAIVER_APPROVER_REQUIRED",
        "FINDING_ASSIGNEE_REQUIRED", "REVIEW_DOMAIN_ERROR",
    }
)


class FindingTransitionInvalid(ReviewDomainError):
    code = "FINDING_TRANSITION_INVALID"
    http_status = 422


# ==========================================================================
# 1 · Taxonomy（FINDING-TAXONOMY.md §2 的唯一代码真源）
# ==========================================================================

#: 封闭十类（v2）。旧 7 类的读取映射见 LEGACY_ALIASES。
FINDING_CATEGORIES: tuple[str, ...] = (
    "EVIDENCE_GAP",
    "CITATION_ERROR",
    "OVERCLAIM",
    "MATH_ERROR",
    "METHOD_RISK",
    "REPRODUCIBILITY",
    "CONTRADICTION",
    "RIGHTS_PRIVACY",
    "EXPORT_MISMATCH",
    "ACCESSIBILITY",
)

SEVERITIES: tuple[str, ...] = ("P0", "P1", "P2")

#: 每类的默认 severity（FINDING-TAXONOMY.md §3）。
SEVERITY_DEFAULTS: dict[str, str] = {
    "EVIDENCE_GAP": "P1",
    "CITATION_ERROR": "P1",
    "OVERCLAIM": "P0",
    "MATH_ERROR": "P0",
    "METHOD_RISK": "P2",
    "REPRODUCIBILITY": "P1",
    "CONTRADICTION": "P0",
    "RIGHTS_PRIVACY": "P0",
    "EXPORT_MISMATCH": "P1",
    "ACCESSIBILITY": "P2",
}

#: 每类的最小证据要求（FINDING-TAXONOMY.md §2"最小证据要求"列）。
#: True ⇒ 提案必须携带至少一条 evidence；False ⇒ 仅要求 description 非空。
REQUIRES_EVIDENCE: dict[str, bool] = {
    cat: cat in {"OVERCLAIM", "MATH_ERROR", "CONTRADICTION", "RIGHTS_PRIVACY",
                 "EXPORT_MISMATCH", "EVIDENCE_GAP", "CITATION_ERROR", "REPRODUCIBILITY"}
    for cat in FINDING_CATEGORIES
}

#: 每类允许的 target type（封闭；proposal 指向别处即拒）。
ALLOWED_TARGET_TYPES: dict[str, frozenset[str]] = {
    "EVIDENCE_GAP": frozenset({"paper_node", "claim", "evidence_ref_node"}),
    "CITATION_ERROR": frozenset({"paper_node", "citation"}),
    "OVERCLAIM": frozenset({"paper_node", "claim"}),
    "MATH_ERROR": frozenset({"paper_node", "claim"}),
    "METHOD_RISK": frozenset({"paper_node"}),
    "REPRODUCIBILITY": frozenset({"experiment_run", "paper_node"}),
    "CONTRADICTION": frozenset({"claim", "paper_node", "evidence_ref_node"}),
    "RIGHTS_PRIVACY": frozenset({"paper_node", "evidence", "citation"}),
    "EXPORT_MISMATCH": frozenset({"export_page", "paper_node"}),
    "ACCESSIBILITY": frozenset({"paper_node", "export_page"}),
}

#: 旧（RV13）→ 新（RV15）读取映射。
LEGACY_ALIASES: dict[str, str] = {
    "EVIDENCE_GAP": "EVIDENCE_GAP",
    "MATH_ERROR": "MATH_ERROR",
    "DATA_LEAKAGE": "RIGHTS_PRIVACY",
    "REPRO_GAP": "REPRODUCIBILITY",
    "CITATION_MISMATCH": "CITATION_ERROR",
    "OVERCLAIM": "OVERCLAIM",
    "WRITING_ISSUE": "METHOD_RISK",
}


def normalize_taxonomy(raw: str) -> str:
    """读取路径的旧值归一：新代码原样过（仍须在封闭集内），旧值映射。"""
    text = str(raw or "").strip().upper()
    return LEGACY_ALIASES.get(text, text)


# ==========================================================================
# 2 · 提案 schema 门
# ==========================================================================

SERVER_OWNED_FIELDS: frozenset[str] = frozenset(
    {"status", "resolution", "waiver", "assignee", "due", "reopenCount",
     "seenCount", "fingerprint", "stale", "needsRecheck", "id"}
)

_DESCRIPTION_MIN = 8
_MAX_DESCRIPTION = 4000
_MAX_EVIDENCE_ITEMS = 16
_EVIDENCE_KINDS = frozenset(
    {"verification", "evidence_card", "citation", "experiment_run",
     "paper_node", "export_page", "quote", "db_fact"}
)


def _err(code: str, message: str, **detail: object) -> ReviewDomainError:
    return ReviewDomainError(code, message, **detail)


def validate_finding_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    """单条 finding 提案的 schema/语义门。通过后返回规范化提案。

    拒绝码（机读）：
    - REVIEW_SERVER_OWNED_FIELD  提案携带 status/resolution/ops 等
    - REVIEW_TAXONOMY_UNKNOWN    taxonomy 不在封闭十类
    - REVIEW_TARGET_INVALID      target 形状非法 / type 越界 / 无 version
    - REVIEW_LOCATOR_REQUIRED    target 无 locator（或空 locator）
    - REVIEW_DESCRIPTION_REQUIRED  description 缺失或过短
    - REVIEW_EVIDENCE_REQUIRED   强结论类无 evidence
    - REVIEW_EVIDENCE_INVALID    evidence 条目形状非法
    """
    if not isinstance(proposal, dict):
        raise _err("REVIEW_TARGET_INVALID", "finding 提案必须是对象")
    # server-owned 字段：存在即拒（不做静默剥离）——含"夹带 patch/ops"的越权面
    for owned in SERVER_OWNED_FIELDS:
        if owned in proposal:
            raise _err(
                "REVIEW_SERVER_OWNED_FIELD",
                f"字段 {owned} 由服务端管理，Reviewer 不得自带",
                field=owned,
            )
    for smuggled in ("ops", "patch", "blocks", "deleteCounterevidence"):
        if smuggled in proposal:
            raise _err(
                "REVIEW_SERVER_OWNED_FIELD",
                f"Reviewer 提案不得携带 {smuggled}（AI 只能 propose finding，不能改 target）",
                field=smuggled,
            )

    tax = normalize_taxonomy(str(proposal.get("taxonomy") or ""))
    if tax not in FINDING_CATEGORIES:
        raise _err("REVIEW_TAXONOMY_UNKNOWN", f"未知 finding 类别: {tax!r}")

    target = proposal.get("target")
    if not isinstance(target, dict):
        raise _err("REVIEW_TARGET_INVALID", "target 必须是对象")
    ttype = str(target.get("type") or "")
    if ttype not in ALLOWED_TARGET_TYPES[tax]:
        raise _err(
            "REVIEW_TARGET_INVALID",
            f"类别 {tax} 不允许 target type {ttype!r}",
            allowed=sorted(ALLOWED_TARGET_TYPES[tax]),
        )
    tid = str(target.get("id") or "").strip()
    if not tid or len(tid) > 128:
        raise _err("REVIEW_TARGET_INVALID", "target.id 必须是非空 id")
    version = target.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 0:
        raise _err("REVIEW_TARGET_INVALID", "target.version 必须是非负整数")
    locator = target.get("locator")
    if not isinstance(locator, dict) or not locator:
        raise _err(
            "REVIEW_LOCATOR_REQUIRED",
            "finding 必须能精确回到目标（target.locator 缺失或为空）",
        )

    description = str(proposal.get("description") or "").strip()
    if len(description) < _DESCRIPTION_MIN:
        raise _err(
            "REVIEW_DESCRIPTION_REQUIRED",
            f"description 至少 {_DESCRIPTION_MIN} 字符（要说明问题，不是标签）",
        )
    if len(description) > _MAX_DESCRIPTION:
        raise _err("REVIEW_DESCRIPTION_REQUIRED", "description 超长")

    raw_evidence = proposal.get("evidence")
    if raw_evidence is None:
        raw_evidence = []
    if not isinstance(raw_evidence, list):
        raise _err("REVIEW_EVIDENCE_INVALID", "evidence 必须是数组")
    evidence: list[dict[str, Any]] = []
    for item in raw_evidence[:_MAX_EVIDENCE_ITEMS]:
        if not isinstance(item, dict):
            raise _err("REVIEW_EVIDENCE_INVALID", "evidence 条目必须是对象")
        kind = str(item.get("kind") or "")
        ref = str(item.get("ref") or "").strip()
        if kind not in _EVIDENCE_KINDS or not ref:
            raise _err(
                "REVIEW_EVIDENCE_INVALID",
                f"evidence 条目需要合法 kind（{_sorted(_EVIDENCE_KINDS)}）与非空 ref",
            )
        # "执行类" evidence = 越权指令（删除反证等）⇒ 拒收（独立机读码）
        if str(item.get("op") or "").strip():
            raise _err(
                "REVIEW_OPERATION_IN_EVIDENCE",
                "evidence 只能是事实引用，不得携带操作指令（op）",
            )
        evidence.append(
            {"kind": kind, "ref": ref[:128],
             "summary": str(item.get("summary") or "")[:500]}
        )
    if REQUIRES_EVIDENCE[tax] and not evidence:
        raise _err(
            "REVIEW_EVIDENCE_REQUIRED",
            f"{tax} 是强结论类 finding：必须携带至少一条证据引用",
        )

    severity_raw = str(proposal.get("severity") or "").strip().upper()
    severity = severity_raw if severity_raw in SEVERITIES else SEVERITY_DEFAULTS[tax]

    return {
        "taxonomy": tax,
        "severity": severity,
        "target": {"type": ttype, "id": tid, "version": version,
                   "locator": _clean_locator(locator)},
        "description": description,
        "evidence": evidence,
    }


def _clean_locator(locator: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in sorted(locator, key=str):
        if k in {"blockIndex", "nodeId", "nodeRev", "page", "bbox", "sectionPath",
                 "chunk", "artifactId", "format", "claimKey", "runId", "field"}:
            out[str(k)[:64]] = locator[k]
    if not out:  # 清洗后为空 = 不可回源
        raise _err("REVIEW_LOCATOR_REQUIRED", "locator 不含任何可回源键")
    return out


def _sorted(x: frozenset[str]) -> str:
    return ",".join(sorted(x))


# ==========================================================================
# 3 · Reviewer AI 工具策略（propose-only）
# ==========================================================================

REVIEWER_ALLOWED_OPERATIONS: frozenset[str] = frozenset(
    {"PROPOSE_FINDING", "READ_TARGET"}
)

REVIEWER_FORBIDDEN_OPERATIONS: frozenset[str] = frozenset(
    {
        "MUTATE_PAPER", "MUTATE_CLAIM", "MUTATE_EXPERIMENT", "MUTATE_EVIDENCE",
        "DELETE_COUNTEREVIDENCE", "DELETE_FINDING", "RESOLVE_FINDING",
        "APPROVE_WAIVER", "TRANSITION_SESSION", "PUBLISH_CLAIM",
    }
)


def assert_reviewer_operation_allowed(operation: str) -> None:
    if operation in REVIEWER_FORBIDDEN_OPERATIONS or operation not in REVIEWER_ALLOWED_OPERATIONS:
        raise ReviewerPolicyViolation(
            "REVIEWER_POLICY_VIOLATION",
            f"Reviewer 只能 propose finding；{operation} 被工具策略禁止",
            operation=operation,
        )


# ==========================================================================
# 4 · 状态机（STATE-MACHINE.md §1 的唯一代码真源）
# ==========================================================================

FINDING_STATUSES: tuple[str, ...] = (
    "OPEN", "ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED", "WAIVED", "REOPENED",
)

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "OPEN": frozenset({"ACKNOWLEDGED", "IN_PROGRESS", "WAIVED"}),
    "ACKNOWLEDGED": frozenset({"IN_PROGRESS", "WAIVED"}),
    "IN_PROGRESS": frozenset({"RESOLVED", "WAIVED"}),
    "RESOLVED": frozenset({"REOPENED"}),
    "WAIVED": frozenset({"REOPENED"}),
    "REOPENED": frozenset({"ACKNOWLEDGED", "IN_PROGRESS", "WAIVED"}),
}

#: 进入这些状态必须已认领（assignee 非空）。
_NEEDS_ASSIGNEE = frozenset({"ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED"})


def validate_transition(current: str, new: str) -> None:
    if current not in ALLOWED_TRANSITIONS:
        raise FindingTransitionInvalid(f"未知当前状态: {current!r}")
    if new not in ALLOWED_TRANSITIONS[current]:
        raise FindingTransitionInvalid(
            f"不允许的 finding 状态迁移: {current} -> {new}"
        )


# ==========================================================================
# 5 · RESOLVED / WAIVED 门（STATE-MACHINE.md §2/§3）
# ==========================================================================

WAIVER_REASON_CODES: frozenset[str] = frozenset(
    {"ACCEPTED_RISK", "FIXED_ELSEWHERE", "NO_IMPACT", "COST_BOUNDED"}
)


def validate_resolution(
    resolution: Any, *, current_target_version: int
) -> dict[str, Any]:
    """RESOLVED 门：修复必须引用晚于发现的修订 + 至少一条证据。"""
    if not isinstance(resolution, dict):
        raise _err("FINDING_RESOLUTION_NO_REVISION", "resolution 缺失")
    rev_ref = resolution.get("revisionRef")
    if not isinstance(rev_ref, dict):
        raise _err("FINDING_RESOLUTION_NO_REVISION", "resolution.revisionRef 缺失")
    revision = rev_ref.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool):
        raise _err("FINDING_RESOLUTION_NO_REVISION", "revisionRef.revision 必须是整数")
    if revision <= int(current_target_version):
        raise _err(
            "FINDING_RESOLUTION_NO_REVISION",
            f"修复修订（{revision}）必须晚于发现时的 target version（{current_target_version}）",
        )
    evidence_refs = [str(x) for x in (resolution.get("evidenceRefs") or []) if str(x).strip()]
    if not evidence_refs:
        raise _err(
            "FINDING_RESOLUTION_NO_REVISION",
            "RESOLVED 必须引用至少一条修复证据（验证记录/新证据卡）",
        )
    note = str(resolution.get("note") or "").strip()
    if not note:
        raise _err("FINDING_RESOLUTION_NO_REVISION", "resolution.note 必须说明修复方式")
    return {
        "revisionRef": {"kind": str(rev_ref.get("kind") or "paper_spec")[:32],
                        "revision": revision},
        "evidenceRefs": [e[:128] for e in evidence_refs[:16]],
        "note": note[:2000],
    }


def validate_waiver(waiver: Any, *, today: date | None = None) -> dict[str, Any]:
    """WAIVED 门：理由（封闭码）+ 人工批准 + 有期限（晚于今天）。"""
    if not isinstance(waiver, dict):
        raise _err("FINDING_WAIVER_REASON_REQUIRED", "waiver 缺失")
    reason = waiver.get("reason")
    if not isinstance(reason, dict):
        raise _err("FINDING_WAIVER_REASON_REQUIRED", "waiver.reason 缺失")
    code = str(reason.get("code") or "").strip().upper()
    if code not in WAIVER_REASON_CODES:
        raise _err("FINDING_WAIVER_REASON_INVALID",
                   f"waiver.reason.code 必须在封闭集内: {sorted(WAIVER_REASON_CODES)}")
    text = str(reason.get("text") or "").strip()
    if not text:
        raise _err("FINDING_WAIVER_REASON_REQUIRED", "waiver.reason.text 必须非空")

    expiry_raw = str(waiver.get("expiry") or "").strip()
    if not expiry_raw:
        raise _err(
            "FINDING_WAIVER_EXPIRY_REQUIRED",
            "豁免必须有到期日——无限期豁免不存在",
        )
    try:
        expiry = date.fromisoformat(expiry_raw[:10])
    except ValueError as e:
        raise _err("FINDING_WAIVER_EXPIRY_INVALID", f"expiry 不是合法日期: {expiry_raw!r}") from e

    approved_by = str(waiver.get("approvedBy") or "").strip()
    if not approved_by:
        raise _err("FINDING_WAIVER_APPROVER_REQUIRED", "waiver 必须有批准人")

    today = today or datetime.now(timezone.utc).date()
    if expiry <= today:
        raise _err("FINDING_WAIVER_EXPIRY_INVALID",
                   f"expiry（{expiry}）必须晚于今天（{today}）")
    return {
        "reason": {"code": code, "text": text[:1000]},
        "expiry": expiry.isoformat(),
        "approvedBy": approved_by[:128],
    }


def waiver_is_expired(waiver: dict[str, Any] | None, *, today: date | None = None) -> bool:
    if not waiver or not waiver.get("expiry"):
        return False
    try:
        expiry = date.fromisoformat(str(waiver["expiry"])[:10])
    except ValueError:
        return True
    return expiry <= (today or datetime.now(timezone.utc).date())


# ==========================================================================
# 6 · 指纹与去重（STATE-MACHINE.md §4/§5）
# ==========================================================================

_NORMALIZE_NOISE = re.compile(r"[\s，。；、,.:：;！!？?\-—_/\\()\[\]{}\"'`]+")


def fingerprint_of(proposal: dict[str, Any]) -> str:
    """同问题在同 target 同位置 ⇒ 同指纹（确定性）。"""
    target = proposal.get("target") or {}
    body = {
        "taxonomy": normalize_taxonomy(str(proposal.get("taxonomy") or "")),
        "targetType": str(target.get("type") or ""),
        "targetId": str(target.get("id") or ""),
        "locator": json.dumps(target.get("locator") or {}, sort_keys=True,
                              ensure_ascii=False, default=str),
        # 描述的"含义键"：去标点/去空白后取小写前 120 字符——
        # 不同措辞的同一问题仍命中同一指纹；不同位置/不同类别必不同。
        "descKey": _NORMALIZE_NOISE.sub("", str(proposal.get("description") or "")).lower()[:120],
    }
    blob = json.dumps(body, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def dedupe_action(
    fingerprint: str, existing: dict[str, Any], *, new_target_version: int,
    today: date | None = None,
) -> str:
    """STATE-MACHINE.md §5 的判定表。返回：
    UPDATE_EXISTING | KEEP_RESOLVED | REOPEN | NEW
    """
    if not fingerprint or not existing:
        return "NEW"
    status = str(existing.get("status") or "")
    old_version = int(existing.get("targetVersion") or 0)
    if status in {"OPEN", "ACKNOWLEDGED", "IN_PROGRESS", "REOPENED"}:
        return "UPDATE_EXISTING"
    if status == "RESOLVED":
        return "KEEP_RESOLVED" if new_target_version <= old_version else "REOPEN"
    if status == "WAIVED":
        if waiver_is_expired(existing.get("waiver"), today=today):
            return "REOPEN"
        return "UPDATE_EXISTING" if new_target_version > old_version else "KEEP_RESOLVED"
    return "NEW"


def reopen_reason_for(action: str) -> str:
    return {
        "REOPEN": "REVIEW_REFOUND_AFTER_FIX",
        "UPDATE_EXISTING": "",
    }.get(action, "")


#: 豁免到期自动重开的原因码（STATE-MACHINE.md §3）。
WAIVER_EXPIRED_REASON = "WAIVER_EXPIRED"


def default_due(severity: str, *, from_date: date | None = None) -> date:
    """severity → 建议修复期限（P0 3 天 / P1 14 天 / P2 90 天）。"""
    base = from_date or datetime.now(timezone.utc).date()
    days = {"P0": 3, "P1": 14, "P2": 90}[severity if severity in {"P0", "P1", "P2"} else "P2"]
    return base + timedelta(days=days)


def severity_of_category(tax: str) -> str:
    return SEVERITY_DEFAULTS.get(normalize_taxonomy(tax), "P2")


__all__ = [
    "ALLOWED_TARGET_TYPES",
    "ALLOWED_TRANSITIONS",
    "FINDING_CATEGORIES",
    "FINDING_STATUSES",
    "FindingTransitionInvalid",
    "LEGACY_ALIASES",
    "REQUIRES_EVIDENCE",
    "REVIEWER_FORBIDDEN_OPERATIONS",
    "ReviewDomainError",
    "ReviewerPolicyViolation",
    "SEVERITIES",
    "SEVERITY_DEFAULTS",
    "SERVER_OWNED_FIELDS",
    "WAIVER_EXPIRED_REASON",
    "WAIVER_REASON_CODES",
    "assert_reviewer_operation_allowed",
    "dedupe_action",
    "default_due",
    "fingerprint_of",
    "normalize_taxonomy",
    "reopen_reason_for",
    "severity_of_category",
    "validate_finding_proposal",
    "validate_resolution",
    "validate_transition",
    "validate_waiver",
    "waiver_is_expired",
]
