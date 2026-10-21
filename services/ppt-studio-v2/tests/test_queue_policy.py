"""队列隔离策略（R15 定稿）：ps8200 各维度冻结 + 与 8100 注册值互异 + stream 键格式。"""
from __future__ import annotations

import pytest

from app.reliability.queue_policy import (
    OWNER_SERVICE_8200,
    REGISTERED_OWNERS,
    assert_policies_disjoint,
    policy_for,
    policy_json,
    stream_of,
)

# R11.5-C 注册的 8100 owner 值（对账基准；8200 与它必须全维度互异）
_TP8100 = {
    "stream_prefix": "strm:tp8100",
    "consumer_group": "tp8100-workers",
    "dedupe_scope": "tp8100",
    "max_pool": 16,
    "quota_per_min": 600,
    "object_prefix": "tp8100/",
}


def test_8200_policy_values_frozen() -> None:
    p = policy_for(OWNER_SERVICE_8200)
    assert p.stream_prefix == "strm:ps8200"
    assert p.consumer_group == "ps8200-workers"
    assert p.dedupe_scope == "ps8200"
    assert p.max_pool == 8
    assert p.quota_per_min == 300
    assert p.object_prefix == "pps2/"


def test_isolation_from_8100() -> None:
    p = policy_for(OWNER_SERVICE_8200)
    for dim in ("stream_prefix", "consumer_group", "dedupe_scope", "object_prefix"):
        assert getattr(p, dim) != _TP8100[dim], f"{dim} 与 8100 冲突"


def test_object_prefix_distinct() -> None:
    p = policy_for(OWNER_SERVICE_8200)
    assert p.object_prefix != _TP8100["object_prefix"]


def test_stream_of_format() -> None:
    assert stream_of(OWNER_SERVICE_8200, "ppt.job") == "strm:ps8200:ppt.job"
    assert stream_of(OWNER_SERVICE_8200, "deck.generate") == "strm:ps8200:deck.generate"


def test_disjoint_within_registry() -> None:
    assert assert_policies_disjoint() == []


def test_unknown_owner_fail_closed() -> None:
    with pytest.raises(KeyError):
        policy_for("nobody")


def test_registered_owners_table() -> None:
    assert REGISTERED_OWNERS["ppt_studio_v2"] == "ps8200"
    assert REGISTERED_OWNERS["teacher_platform"] == "tp8100"


def test_policy_json_shape() -> None:
    j = policy_json()
    assert j["ppt_studio_v2"]["stream_prefix"] == "strm:ps8200"
