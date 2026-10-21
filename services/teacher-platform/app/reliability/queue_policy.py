"""8100 / 8200 队列与命名隔离模板（R11.5-C 固化）。

依据 DECISIONS §2（8100 Teacher Prep 事实权威 / 8200 PPT Studio V2 独立进程、配置、
DB、queue/group/key/pool/quota/object prefix）。本模板定义各 owner 的：

- stream_prefix：Valkey Streams 键前缀（key 隔离）
- consumer_group：TaskIQ 消费组名（group 隔离）
- dedupe_suffix：Inbox/幂等 dedupe scope 后缀（key 隔离）
- max_pool：worker 并发上限（pool 隔离）
- quota_per_min：per-owner 入队配额（quota 隔离；由调用方/网关执行）
- object_prefix：MinIO 对象键前缀（object prefix 隔离）

使用：所有 QueueService/enqueue 必须带 owner 参数并经 `policy_for()` 解析；
任何新 owner 必须先注册（启动时 assert_policies_disjoint 防前缀碰撞）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "OwnerQueuePolicy",
    "OWNER_SERVICE_8100",
    "OWNER_SERVICE_8200",
    "OWNER_POLICIES",
    "policy_for",
    "stream_of",
    "assert_policies_disjoint",
]

OWNER_SERVICE_8100 = "teacher_platform"
OWNER_SERVICE_8200 = "ppt_studio_v2"


@dataclass(frozen=True)
class OwnerQueuePolicy:
    owner_service: str
    stream_prefix: str          # Valkey Streams 键前缀（大小写敏感，全局唯一）
    consumer_group: str         # TaskIQ consumer group 名
    dedupe_scope: str           # 去重键命名空间后缀
    max_pool: int               # worker 并发上限
    quota_per_min: int          # 每分钟入队配额上限（0 = 不限）
    object_prefix: str          # MinIO 对象键前缀


OWNER_POLICIES: dict[str, OwnerQueuePolicy] = {
    OWNER_SERVICE_8100: OwnerQueuePolicy(
        owner_service=OWNER_SERVICE_8100,
        stream_prefix="strm:tp8100",
        consumer_group="tp8100-workers",
        dedupe_scope="tp8100",
        max_pool=16,
        quota_per_min=600,
        object_prefix="tp8100/",
    ),
    # 8200 PPT Studio V2：独立进程/表/队列/对象前缀；浏览器只经 8200 访问（DECISIONS §2/ADR-3）
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
    except KeyError as exc:  # 未注册 owner = 配置缺陷 → fail-closed
        raise KeyError(
            f"未知 owner_service {owner_service!r}；必须在 app.reliability.queue_policy "
            f"OWNER_POLICIES 注册（清单：{sorted(OWNER_POLICIES)}）"
        ) from exc


def stream_of(owner_service: str, topic: str) -> str:
    """规范化 Valkey Streams 键：{prefix}:{topic}（topic 建议逆域名风格，如 deck.generate）。"""
    p = policy_for(owner_service)
    return f"{p.stream_prefix}:{topic}"


def assert_policies_disjoint() -> list[str]:
    """隔离门禁：任意两个 owner 的五个隔离维度必须互不相同（防跨 owner 串线）。

    返回违规说明列表（空 = 全部隔离成立）。
    """
    violations: list[str] = []
    owners = list(OWNER_POLICIES.values())
    for i in range(len(owners)):
        for j in range(i + 1, len(owners)):
            a, b = owners[i], owners[j]
            if a.owner_service == b.owner_service:
                continue
            for dim in ("stream_prefix", "consumer_group", "dedupe_scope", "object_prefix"):
                if getattr(a, dim) == getattr(b, dim):
                    violations.append(f"{a.owner_service}.{dim} == {b.owner_service}.{dim}")
            if a.max_pool == b.max_pool:
                violations.append(f"max_pool 相同（{a.owner_service}/{b.owner_service}）虽然不违规，建议差异化")
    return violations


def policy_json() -> dict[str, dict[str, Any]]:
    """模板导出（配置审计/文档引用）。"""
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
