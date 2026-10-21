"""RV09 · 记忆命名空间 —— `tenant / user / research / purpose` 四元组。

为什么是四元组而不是"一个 user id"
--------------------------------
三类真实事故都由"键太短"造成：

1. **跨租户**：只按 `user_id` 存偏好 ⇒ 同一个用户在 A 校和 B 校的偏好互相污染；
2. **跨项目**：只按 `tenant+user` 存 ⇒ 用户在做"数列"研究时说的
   "优先用初等方法"被带进"概率建模"研究，而那句话在那个语境下是错的；
3. **跨用途**：只按 `tenant+user+research` 存 ⇒ 用户为"回答风格"设定的
   "尽量简短"被当成"交付物格式"要求，于是论文也被写成一句话。

因此每个用途（`purpose`）是**独立的命名空间**，而不是同一个桶里的一个 tag。

Teacher / 其它项目是**结构性禁止**
--------------------------------
Research 服务不 import Teacher 代码（见 `AGENTS.md` 跨边界约定），
本模块另加一层运行时守卫：任何带 `teacher*` / `other-project*` / `cross-tenant*`
来源的写入或读取**直接抛错**，而不是静默忽略 —— 静默忽略会让越权读取在审计里消失。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 用途封闭词表。新增用途必须改这里（而不是随手传字符串），
#: 因为"随手的字符串"就是命名空间泄漏的第一来源。
PURPOSE_ASSISTANT_STYLE = "ASSISTANT_STYLE"
PURPOSE_DOMAIN_PREFERENCE = "DOMAIN_PREFERENCE"
PURPOSE_NOTATION = "NOTATION"
PURPOSE_DELIVERABLE_FORMAT = "DELIVERABLE_FORMAT"
PURPOSE_TERMINOLOGY = "TERMINOLOGY"

PURPOSES: tuple[str, ...] = (
    PURPOSE_ASSISTANT_STYLE,
    PURPOSE_DOMAIN_PREFERENCE,
    PURPOSE_NOTATION,
    PURPOSE_DELIVERABLE_FORMAT,
    PURPOSE_TERMINOLOGY,
)

#: 禁止来源（子串匹配，大小写不敏感）。
FORBIDDEN_SOURCES: tuple[str, ...] = (
    "teacher",
    "teacher-platform",
    "teacher_platform",
    "other-project",
    "other_project",
    "otherproject",
    "cross-tenant",
    "cross_tenant",
)

SEP = "\x1f"  # unit separator：不可能出现在 id 里，故不会造成拼接歧义


class MemoryNamespaceError(Exception):
    def __init__(self, code: str, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


class MemoryNamespaceViolation(MemoryNamespaceError):
    """试图跨命名空间读写，或来源属于禁止域。"""


class MemoryPurposeUnknown(MemoryNamespaceError):
    """`purpose` 不在封闭词表内。"""


@dataclass(frozen=True)
class Namespace:
    tenant_id: str
    user_id: str
    research_id: str
    purpose: str

    @property
    def key(self) -> str:
        return SEP.join((self.tenant_id, self.user_id, self.research_id, self.purpose))

    def as_dict(self) -> dict[str, str]:
        return {
            "tenantId": self.tenant_id,
            "userId": self.user_id,
            "researchId": self.research_id,
            "purpose": self.purpose,
        }


def normalize_purpose(purpose: Any) -> str:
    value = str(purpose or "").strip().upper()
    if value not in PURPOSES:
        raise MemoryPurposeUnknown(
            "MEMORY_PURPOSE_UNKNOWN",
            f"未知 purpose {purpose!r}；允许值：{list(PURPOSES)}",
        )
    return value


def make_namespace(
    *, tenant_id: Any, user_id: Any, research_id: Any, purpose: Any
) -> Namespace:
    ns = Namespace(
        tenant_id=str(tenant_id or "").strip(),
        user_id=str(user_id or "").strip(),
        research_id=str(research_id or "").strip(),
        purpose=normalize_purpose(purpose),
    )
    missing = [k for k, v in (("tenant_id", ns.tenant_id), ("user_id", ns.user_id), ("research_id", ns.research_id)) if not v]
    if missing:
        # 缺一段就不是"更宽的命名空间"，而是**非法**命名空间：
        # 空 tenant 会把所有租户折叠到一起。
        raise MemoryNamespaceViolation(
            "MEMORY_NAMESPACE_INCOMPLETE", f"命名空间缺少 {missing}：不允许用空值当通配符"
        )
    return ns


def namespace_key(
    *, tenant_id: Any, user_id: Any, research_id: Any, purpose: Any
) -> str:
    return make_namespace(
        tenant_id=tenant_id, user_id=user_id, research_id=research_id, purpose=purpose
    ).key


def assert_source_allowed(source: Any) -> str:
    """来源守卫。返回规范化来源；越界即抛错。"""
    value = str(source or "user").strip()
    lowered = value.lower()
    for bad in FORBIDDEN_SOURCES:
        if bad in lowered:
            raise MemoryNamespaceViolation(
                "MEMORY_SOURCE_FORBIDDEN",
                f"来源 {value!r} 属于禁止域：Research 不得读取 Teacher / 其它项目的记忆",
                source=value,
            )
    return value


def assert_same_namespace(a: Namespace, b: Namespace) -> None:
    if a.key != b.key:
        raise MemoryNamespaceViolation(
            "MEMORY_NAMESPACE_MISMATCH",
            f"命名空间不一致：{a.as_dict()} vs {b.as_dict()}",
            left=a.as_dict(),
            right=b.as_dict(),
        )


__all__ = [
    "FORBIDDEN_SOURCES",
    "MemoryNamespaceError",
    "MemoryNamespaceViolation",
    "MemoryPurposeUnknown",
    "Namespace",
    "PURPOSES",
    "PURPOSE_ASSISTANT_STYLE",
    "PURPOSE_DELIVERABLE_FORMAT",
    "PURPOSE_DOMAIN_PREFERENCE",
    "PURPOSE_NOTATION",
    "PURPOSE_TERMINOLOGY",
    "assert_same_namespace",
    "assert_source_allowed",
    "make_namespace",
    "namespace_key",
    "normalize_purpose",
]
