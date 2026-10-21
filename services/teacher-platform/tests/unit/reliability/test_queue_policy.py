"""8100/8200 队列隔离模板单测：前缀/分组/去重域/池/配额/对象前缀互斥 + fail-closed 未知 owner。"""
from __future__ import annotations

import pytest

from app.reliability.queue_policy import (
    OWNER_POLICIES,
    OWNER_SERVICE_8100,
    OWNER_SERVICE_8200,
    assert_policies_disjoint,
    policy_for,
    policy_json,
    stream_of,
)


def test_all_owners_registered() -> None:
    assert set(OWNER_POLICIES) == {OWNER_SERVICE_8100, OWNER_SERVICE_8200}
    for o in OWNER_POLICIES:
        assert policy_for(o).owner_service == o


def test_isolation_dimensions_disjoint() -> None:
    """五个隔离维度（stream prefix/consumer group/dedupe scope/object prefix）跨 owner 全异。"""
    violations = assert_policies_disjoint()
    hard = [v for v in violations if not v.startswith("max_pool")]
    assert hard == [], f"隔离维度碰撞: {hard}"


def test_stream_keys_unique_per_owner() -> None:
    s1 = stream_of(OWNER_SERVICE_8100, "deck.generate")
    s2 = stream_of(OWNER_SERVICE_8200, "deck.generate")
    assert s1.startswith("strm:tp8100:")
    assert s2.startswith("strm:ps8200:")
    assert s1 != s2


def test_unknown_owner_fails_closed() -> None:
    with pytest.raises(KeyError, match="未知 owner_service"):
        policy_for("not-registered")


def test_policy_json_audit_shape() -> None:
    js = policy_json()
    assert set(js) == set(OWNER_POLICIES)
    p = js[OWNER_SERVICE_8200]
    assert p["object_prefix"] == "pps2/"
    assert p["quota_per_min"] > 0
    assert p["consumer_group"] != js[OWNER_SERVICE_8100]["consumer_group"]
