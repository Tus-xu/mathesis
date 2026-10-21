"""RV12 · claim 域单元测试夹具。

**不使用 mock 库替代领域对象**：这里构造的是与真实 `research_evidence_card`
行**同形的字典视图**，因此支持度判定读到的字段与生产路径一致。
任何"测试里能过、生产里字段名不同"的漂移都会在这里被暴露。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

NOW = datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)


def evidence_view(
    *,
    evidence_id: str = "ev-1",
    evidence_type: str = "PAPER_FULL_TEXT",
    rights_status: str = "OPEN_ACCESS",
    status: str = "ACTIVE",
    stale_state: str = "FRESH",
    locator: dict[str, Any] | None = None,
    locator_stale: bool = False,
    retrieved_at: datetime | None = None,
    age_days: int = 0,
    citation_eligible: bool | None = None,
    source_record_kind: str = "FULL_TEXT",
    snippet: str = "原文片段",
    structured_fact: dict[str, Any] | None = None,
    retracted: bool = False,
    upstream_card_ids: list[str] | None = None,
) -> dict[str, Any]:
    """一张证据卡的**行视图**（与 EvidenceCardV2Row 同形 + 少量派生位）。"""
    when = retrieved_at or (NOW - timedelta(days=age_days))
    if citation_eligible is None:
        citation_eligible = evidence_type in {"PAPER_FULL_TEXT", "PAPER_ABSTRACT", "DATASET", "WEB"}
    return {
        "evidence_id": evidence_id,
        "evidence_type": evidence_type,
        "rights_status": rights_status,
        "status": status,
        "stale_state": stale_state,
        "locator": locator if locator is not None else {"kind": "EXTERNAL", "doi": "10.1/x"},
        "locator_stale": locator_stale,
        "retrieved_at": when.isoformat(),
        "citation_eligible": citation_eligible,
        "source_record_kind": source_record_kind,
        "snippet": snippet,
        "structured_fact": structured_fact,
        "retracted": retracted,
        "upstream_card_ids": list(upstream_card_ids or []),
    }


def evidence_link_view(
    *,
    evidence_id: str = "ev-1",
    role: str = "SUPPORTS",
    claim_kind: str = "PARAPHRASE",
    band: str = "STRONG",
    score: float = 0.95,
    usable: bool = True,
    blocking: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "role": role,
        "claim_kind": claim_kind,
        "band": band,
        "score": score,
        "usable": usable,
        "blocking": tuple(blocking),
    }


def experiment_link_view(
    *,
    run_id: str = "run-1",
    role: str = "SUPPORTS",
    reproducible: bool = True,
    exit_code: int | None = 0,
    repro_missing: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "role": role,
        "reproducible": reproducible,
        "exit_code": exit_code,
        "repro_missing": tuple(repro_missing),
    }


def verification_view(
    *,
    record_id: str = "ver-1",
    kind: str = "MATH_CHECK",
    result: str = "PASS",
    criteria_satisfied: bool = True,
    independent: bool = True,
    blocking: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "kind": kind,
        "result": result,
        "criteria_satisfied": criteria_satisfied,
        "independent": independent,
        "blocking": tuple(blocking),
    }


def counter_evidence_view(
    *,
    ref_id: str = "ce-1",
    source_kind: str = "VERIFICATION",
    summary: str = "反例命中：参数 a=0 时不等式失效",
    severity: str = "BLOCKING",
) -> dict[str, Any]:
    return {
        "source_kind": source_kind,
        "ref_id": ref_id,
        "summary": summary,
        "severity": severity,
    }


def solved_snapshot(**overrides: Any) -> dict[str, Any]:
    """一个**满足 SOLVED 全部预先定义判据**的最小快照。"""
    base: dict[str, Any] = {
        "claim_kind": "PARAPHRASE",
        "support_band": "STRONG",
        "quality": "SUPPORTED",
        "evidence_links": [evidence_link_view()],
        "experiment_links": [experiment_link_view()],
        "verifications": [
            verification_view(record_id="ver-math", kind="MATH_CHECK", result="PASS"),
            verification_view(record_id="ver-sens", kind="SENSITIVITY", result="PASS"),
            verification_view(record_id="ver-cex", kind="COUNTEREXAMPLE", result="PASS"),
        ],
        "counter_evidence": [],
        "revision_fresh": True,
        "reference_conflicts": (),
    }
    base.update(overrides)
    return base
