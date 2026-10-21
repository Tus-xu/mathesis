"""RV09 · 记忆命名空间（纯逻辑部分）。DB 侧隔离见
`tests/integration/test_rv09_memory_isolation.py`。"""

from __future__ import annotations

import pytest

from research_vnext.core.memory import namespaces as NS


def test_namespace_key_is_four_part() -> None:
    key = NS.namespace_key(
        tenant_id="t1", user_id="u1", research_id="r1", purpose="ASSISTANT_STYLE"
    )
    assert key.split(NS.SEP) == ["t1", "u1", "r1", "ASSISTANT_STYLE"]


def test_purpose_is_a_closed_vocabulary() -> None:
    with pytest.raises(NS.MemoryPurposeUnknown):
        NS.make_namespace(tenant_id="t", user_id="u", research_id="r", purpose="whatever")


def test_purpose_normalises_case() -> None:
    assert NS.normalize_purpose("assistant_style") == "ASSISTANT_STYLE"


@pytest.mark.parametrize("field", ["tenant_id", "user_id", "research_id"])
def test_incomplete_namespace_is_rejected_not_widened(field: str) -> None:
    """空 tenant/user/research **不是**通配符，是非法命名空间。"""
    kwargs = {"tenant_id": "t", "user_id": "u", "research_id": "r", "purpose": "NOTATION"}
    kwargs[field] = ""
    with pytest.raises(NS.MemoryNamespaceViolation) as ei:
        NS.make_namespace(**kwargs)
    assert ei.value.code == "MEMORY_NAMESPACE_INCOMPLETE"


@pytest.mark.parametrize(
    "source",
    ["teacher_memory", "teacher-platform-pref", "other_project", "cross-tenant", "otherproject"],
)
def test_forbidden_sources_raise(source: str) -> None:
    with pytest.raises(NS.MemoryNamespaceViolation) as ei:
        NS.assert_source_allowed(source)
    assert ei.value.code == "MEMORY_SOURCE_FORBIDDEN"


def test_normal_source_is_allowed() -> None:
    assert NS.assert_source_allowed("user") == "user"
    assert NS.assert_source_allowed("") == "user"


def test_same_namespace_helper() -> None:
    a = NS.make_namespace(tenant_id="t", user_id="u", research_id="r", purpose="NOTATION")
    b = NS.make_namespace(tenant_id="t", user_id="u", research_id="r", purpose="NOTATION")
    NS.assert_same_namespace(a, b)
    c = NS.make_namespace(tenant_id="t", user_id="u", research_id="r2", purpose="NOTATION")
    with pytest.raises(NS.MemoryNamespaceViolation):
        NS.assert_same_namespace(a, c)
