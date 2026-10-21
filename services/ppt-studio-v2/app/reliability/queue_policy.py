"""8200 队列/命名隔离策略（owner = ppt_studio_v2）。

数值与 R11.5-C 注册模板一致（strm:ps8200 / ps8200-workers / ps8200 / 8 / 300 / pps2/），
但物理实现**属于 8200 自持**：不 import 8100 app.reliability.queue_policy，只对齐共识值。
任何新 owner 必须先注册（assert_policies_disjoint 防前缀碰撞）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

OWNER_SERVICE_8200 = "ppt_studio_v2"
# 与 8100 物理隔离的 owner 名单（对账用；本链只服务 8200）
REGISTERED_OWNERS = {
    "teacher_platform": "tp8100",  # 8100（他仓）
    "ppt_studio_v2": "ps8200",     # 8200（本服务）
}


@dataclass(frozen=True)
class OwnerQueuePolicy:
    owner_service: str
    stream_prefix: str   # Valkey Streams 键前缀（全局唯一）
    consumer_group: str  # consumer group 名
    dedupe_scope: str    # 去重键命名空间后缀
    max_pool: int        # worker 并发上限
    quota_per_min: int   # 每分钟入队配额（0 = 不限）
    object_prefix: str   # MinIO 对象键前缀


OWNER_POLICIES: dict[str, OwnerQueuePolicy] = {
    OWNER_SERVICE_8200: OwnerQueuePolicy(
        owner_service=OWNER_SERVICE_8200,
        stream_prefix="strm:ps8200",
        consumer_group="ps8200-workers",
        dedupe_scope="ps8200",
        max_pool=8,
        quota_per_min=300,
        object_prefix="pps2/",
    ),
}


def policy_for(owner_service: str) -> OwnerQueuePolicy:
    try:
        return OWNER_POLICIES[owner_service]
    except KeyError as exc:
        raise KeyError(
            f"未知 owner_service {owner_service!r}；必须在 app.reliability.queue_policy 注册"
        ) from exc


def stream_of(owner_service: str, topic: str) -> str:
    """规范化 Valkey Streams 键：{prefix}:{topic}（topic 逆域名风格，如 ppt.job）。"""
    return f"{policy_for(owner_service).stream_prefix}:{topic}"


def assert_policies_disjoint() -> list[str]:
    """隔离门禁：注册 owner 的五个隔离维度两两互异（本链仅 8200 → 空列表）。"""
    violations: list[str] = []
    owners = list(OWNER_POLICIES.values())
    for i in range(len(owners)):
        for j in range(i + 1, len(owners)):
            a, b = owners[i], owners[j]
            for dim in ("stream_prefix", "consumer_group", "dedupe_scope", "object_prefix"):
                if getattr(a, dim) == getattr(b, dim):
                    violations.append(f"{a.owner_service}.{dim} == {b.owner_service}.{dim}")
    return violations


def policy_json() -> dict[str, dict[str, Any]]:
    return {
        o: {
            "stream_prefix": p.stream_prefix,
            "consumer_group": p.consumer_group,
            "dedupe_scope": p.dedupe_scope,
            "max_pool": p.max_pool,
            "quota_per_min": p.quota_per_min,
            "object_prefix": p.object_prefix,
        }
        for o, p in OWNER_POLICIES.items()
    }
