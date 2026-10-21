"""RV12 · Claim 域（ClaimLedger / 证据关联 / 支持 rubric / 状态机）。"""

from .aggregate import (
    ClaimAggregate,
    ClaimAssumption,
    ClaimProposal,
    ClaimScope,
    ConfidenceBasis,
    CounterEvidence,
    EvidenceLink,
    ExperimentLink,
    materialize_claim,
)
from .state_machine import (
    ClaimGateDecision,
    ClaimGateSnapshot,
    derive_status,
    evaluate_gate,
)
from .support import aggregate_support, assess_evidence_support
from .types import REQUIRED_CLAIM_FIELDS, ClaimStatus, EvidenceLinkRole, SupportQuality

__all__ = [
    "REQUIRED_CLAIM_FIELDS",
    "ClaimAggregate",
    "ClaimAssumption",
    "ClaimGateDecision",
    "ClaimGateSnapshot",
    "ClaimProposal",
    "ClaimScope",
    "ClaimStatus",
    "ConfidenceBasis",
    "CounterEvidence",
    "EvidenceLink",
    "EvidenceLinkRole",
    "ExperimentLink",
    "SupportQuality",
    "aggregate_support",
    "assess_evidence_support",
    "derive_status",
    "evaluate_gate",
    "materialize_claim",
]
