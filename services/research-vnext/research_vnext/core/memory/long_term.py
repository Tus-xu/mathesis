"""RV09 · 长期偏好 = 按 `tenant/user/research/purpose` 命名空间的键值对。

与短期记忆的分界
----------------
- 短期（`short_term.py`）：**这条对话走到哪了**，随会话生灭；
- 长期（本模块）：**这个人做研究时希望被如何对待**，跨会话存续。

因此长期偏好只有四类真实内容：回答风格、领域偏好、记号约定、交付物格式。
任何"关于某个具体问题的结论"都不属于这里 —— 那是领域对象，不是偏好。
本模块用封闭 `purpose` 词表把这条边界钉死。

授权位（`authorized`）
---------------------
只有**用户明确设定过**的偏好才 `authorized=True`。模型推断出来的偏好只能
`authorized=False` —— 它可以被展示为"我猜你希望这样"，但**不得**进入提示词
（`context_manifest` 会把未授权偏好排除并记 `unauthorized`）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import ResearchPreferenceRow
from .namespaces import (
    Namespace,
    assert_source_allowed,
    make_namespace,
    namespace_key,
)

MAX_KEY_CHARS = 64
MAX_VALUE_CHARS = 1000


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Preference:
    id: str
    namespace: str
    tenant_id: str
    user_id: str
    research_id: str
    purpose: str
    key: str
    value: str
    authorized: bool
    source: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "namespace": self.namespace,
            "tenantId": self.tenant_id,
            "userId": self.user_id,
            "researchId": self.research_id,
            "purpose": self.purpose,
            "key": self.key,
            "value": self.value,
            "authorized": self.authorized,
            "source": self.source,
            "updatedAt": self.updated_at,
        }

    def as_context_item(self) -> dict[str, Any]:
        """喂给 `context_manifest.build_manifest(preferences=...)` 的形状。"""
        return {
            "ref": f"preference:{self.key}",
            "key": self.key,
            "value": self.value,
            "authorized": self.authorized,
            "namespace": self.namespace,
            "source": self.source,
        }


def _row_to_pref(row: ResearchPreferenceRow) -> Preference:
    ns = namespace_key(
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        research_id=row.research_id,
        purpose=row.purpose,
    )
    return Preference(
        id=str(row.id),
        namespace=ns,
        tenant_id=str(row.tenant_id),
        user_id=str(row.user_id),
        research_id=str(row.research_id),
        purpose=row.purpose,
        key=row.key,
        value=row.value,
        authorized=bool(row.authorized),
        source=row.source or "",
        updated_at=row.updated_at.isoformat(),
    )


def put_preference(
    db: Session,
    *,
    tenant_id: Any,
    user_id: Any,
    research_id: Any,
    purpose: Any,
    key: str,
    value: str,
    authorized: bool = True,
    source: str = "user",
) -> Preference:
    """写入/更新一条偏好。命名空间由 `make_namespace` 强制完整。"""
    ns: Namespace = make_namespace(
        tenant_id=tenant_id, user_id=user_id, research_id=research_id, purpose=purpose
    )
    safe_source = assert_source_allowed(source)
    clean_key = str(key or "").strip()
    if not clean_key:
        raise ValueError("偏好 key 不能为空")
    if len(clean_key) > MAX_KEY_CHARS:
        raise ValueError(f"偏好 key 超过 {MAX_KEY_CHARS} 字符")

    row = (
        db.query(ResearchPreferenceRow)
        .filter(
            ResearchPreferenceRow.tenant_id == ns.tenant_id,
            ResearchPreferenceRow.user_id == ns.user_id,
            ResearchPreferenceRow.research_id == ns.research_id,
            ResearchPreferenceRow.purpose == ns.purpose,
            ResearchPreferenceRow.key == clean_key,
        )
        .one_or_none()
    )
    value_text = str(value if value is not None else "")[:MAX_VALUE_CHARS]
    if row is None:
        row = ResearchPreferenceRow(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(ns.tenant_id),
            user_id=uuid.UUID(ns.user_id),
            research_id=ns.research_id,
            purpose=ns.purpose,
            key=clean_key,
            value=value_text,
            authorized=bool(authorized),
            source=safe_source,
            created_at=_now(),
            updated_at=_now(),
        )
        db.add(row)
    else:
        row.value = value_text
        row.authorized = bool(authorized)
        row.source = safe_source
        row.updated_at = _now()
    db.flush()
    return _row_to_pref(row)


def get_preference(
    db: Session,
    *,
    tenant_id: Any,
    user_id: Any,
    research_id: Any,
    purpose: Any,
    key: str,
) -> Preference | None:
    ns = make_namespace(
        tenant_id=tenant_id, user_id=user_id, research_id=research_id, purpose=purpose
    )
    row = (
        db.query(ResearchPreferenceRow)
        .filter(
            ResearchPreferenceRow.tenant_id == ns.tenant_id,
            ResearchPreferenceRow.user_id == ns.user_id,
            ResearchPreferenceRow.research_id == ns.research_id,
            ResearchPreferenceRow.purpose == ns.purpose,
            ResearchPreferenceRow.key == str(key or "").strip(),
        )
        .one_or_none()
    )
    return _row_to_pref(row) if row is not None else None


def list_preferences(
    db: Session,
    *,
    tenant_id: Any,
    user_id: Any,
    research_id: Any,
    purpose: Any,
    include_unauthorized: bool = False,
) -> list[Preference]:
    """列出**同一命名空间**的偏好。

    `research_id` 是精确匹配，不是前缀匹配 —— "同一用户的所有研究"
    会重新引入跨项目污染，那正是本模块要消除的东西。
    """
    ns = make_namespace(
        tenant_id=tenant_id, user_id=user_id, research_id=research_id, purpose=purpose
    )
    q = db.query(ResearchPreferenceRow).filter(
        ResearchPreferenceRow.tenant_id == ns.tenant_id,
        ResearchPreferenceRow.user_id == ns.user_id,
        ResearchPreferenceRow.research_id == ns.research_id,
        ResearchPreferenceRow.purpose == ns.purpose,
    )
    if not include_unauthorized:
        q = q.filter(ResearchPreferenceRow.authorized.is_(True))
    rows = q.order_by(ResearchPreferenceRow.key).all()
    return [_row_to_pref(r) for r in rows]


def context_items(
    db: Session,
    *,
    tenant_id: Any,
    user_id: Any,
    research_id: Any,
    purpose: Any,
) -> list[dict[str, Any]]:
    """喂给上下文清单的偏好项（**只**含已授权项）。"""
    return [
        p.as_context_item()
        for p in list_preferences(
            db,
            tenant_id=tenant_id,
            user_id=user_id,
            research_id=research_id,
            purpose=purpose,
        )
    ]


def delete_preference(
    db: Session,
    *,
    tenant_id: Any,
    user_id: Any,
    research_id: Any,
    purpose: Any,
    key: str,
) -> bool:
    ns = make_namespace(
        tenant_id=tenant_id, user_id=user_id, research_id=research_id, purpose=purpose
    )
    n = (
        db.query(ResearchPreferenceRow)
        .filter(
            ResearchPreferenceRow.tenant_id == ns.tenant_id,
            ResearchPreferenceRow.user_id == ns.user_id,
            ResearchPreferenceRow.research_id == ns.research_id,
            ResearchPreferenceRow.purpose == ns.purpose,
            ResearchPreferenceRow.key == str(key or "").strip(),
        )
        .delete(synchronize_session=False)
    )
    db.flush()
    return bool(n)


__all__ = [
    "MAX_KEY_CHARS",
    "MAX_VALUE_CHARS",
    "Preference",
    "context_items",
    "delete_preference",
    "get_preference",
    "list_preferences",
    "put_preference",
]
