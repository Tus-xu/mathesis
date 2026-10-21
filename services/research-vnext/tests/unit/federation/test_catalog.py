"""RV06 · 五类 provider catalog 测试。

失败先行。钉死：
- 五类规范全部登记（含 Dataset / Web-External）；
- 本轮只让 Literature / ProjectAsset / FoundationalMath 进 Golden Path，
  Dataset / Web-External **保留 contract 且默认 disabled**；
- spec 必须携带 rights（含 rights_evidence_ref + redistributable）与 health；
- `UNKNOWN` health 不得被折算成 HEALTHY。
"""

from __future__ import annotations

import pytest

from research_vnext.core.federation.catalog import (
    DEFAULT_CATALOG,
    ProviderSpec,
    get_spec,
)
from research_vnext.core.federation.errors import ProviderSpecInvalid
from research_vnext.core.federation.types import EvidenceType, ProviderHealth


def test_all_five_evidence_types_have_a_spec() -> None:
    kinds = {spec.evidence_type for spec in DEFAULT_CATALOG.values()}
    assert kinds == set(EvidenceType)


def test_golden_path_three_are_enabled_by_default() -> None:
    for name in ("literature", "project_asset", "foundational_math"):
        assert get_spec(name).enabled is True


def test_dataset_and_web_are_disabled_by_default_but_contract_kept() -> None:
    for name in ("dataset", "web_external"):
        spec = get_spec(name)
        assert spec.enabled is False, f"{name} 本轮必须默认 disabled"
        assert spec.version, "contract 必须保留版本"
        assert spec.capabilities, "contract 必须保留能力声明"


def test_every_spec_is_read_only() -> None:
    for spec in DEFAULT_CATALOG.values():
        assert spec.read_only is True


def test_spec_rights_must_declare_evidence_ref_and_redistributable() -> None:
    with pytest.raises(ProviderSpecInvalid):
        ProviderSpec(
            name="bad",
            evidence_type=EvidenceType.LITERATURE,
            version="1.0.0",
            capabilities=("search",),
            read_only=True,
            enabled=True,
            data_policy={"retention": "NONE", "training_use": False, "region": "CN", "pii_allowed": False},
            timeout_seconds=10,
            rate_limit={"rpm": 10},
            retry={"max_attempts": 2, "backoff": "FIXED", "retry_on": ["TIMEOUT"]},
            rights={"redistributable": False},
            health=ProviderHealth.UNKNOWN,
        )


def test_unknown_health_stays_unknown() -> None:
    for spec in DEFAULT_CATALOG.values():
        if spec.health is ProviderHealth.UNKNOWN:
            assert spec.health is not ProviderHealth.HEALTHY
            assert spec.assume_healthy is False


def test_literature_spec_declares_doi_capability() -> None:
    lit = get_spec("literature")
    assert lit.evidence_type is EvidenceType.LITERATURE
    assert any("doi" in c for c in lit.capabilities)


def test_foundational_math_spec_has_no_doi_or_journal_capability() -> None:
    fm = get_spec("foundational_math")
    assert fm.evidence_type is EvidenceType.FOUNDATIONAL_MATH
    joined = " ".join(fm.capabilities).lower()
    assert "doi" not in joined
    assert "journal" not in joined
    assert "venue" not in joined


def test_unknown_provider_name_raises() -> None:
    with pytest.raises(KeyError):
        get_spec("nope")
