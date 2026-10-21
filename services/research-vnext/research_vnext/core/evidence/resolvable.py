"""RV07 · 「哪些证据 id 能支撑一个 claim」的**唯一解析入口**（D-TABLE-1，2026-09-14）。

两表并存的历史
--------------
- `evidence_card`（迁移 `0005`）：CR-6 / RV09–RV13 的**扁平引用位**，
  判据是 `authorized IS TRUE AND invalidated_at IS NULL`；
- `research_evidence_card`（迁移 `0009`）：RV07 的**富事实模型**，
  判据是 `status == 'ACTIVE' AND stale_state != 'STALE'`（见 `is_grounded_card`）。

裁定（`RV07/DECISIONS-2026-09-14.md` §2）
-----------------------------------------
以 `research_evidence_card` 为**唯一权威**，`evidence_card` 降级为**遗留兼容**。
**物理合并（删表/迁数据）被显式推迟**：不可逆，且当前无消费侧需求。

为什么需要一个并集
------------------
`claim` 的 `evidenceRefs` 原先只查扁平表 ⇒ **RV07 的证据卡无法支撑 claim**
（跨轮接口断点，此前无测试覆盖）。本模块把两表的"可用证据"合并成**一个**集合。

**判据只写一处**：本模块是唯一入口；`domain_service.valid_evidence_ids` 与
`proposal_handlers._validate_claim` 都必须委托到这里。
刻意**不**建 SQL 视图 —— 把并集判据在 SQL 里再写一遍必然与这里漂移。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import EvidenceCardRow
from .service import grounded_card_ids_for_session


def legacy_evidence_ids(db: Session, *, session_id: uuid.UUID) -> set[str]:
    """**遗留**扁平表里可用的证据 id：已授权且未失效。"""
    rows = db.execute(
        select(EvidenceCardRow.id).where(
            EvidenceCardRow.session_id == session_id,
            EvidenceCardRow.authorized.is_(True),
            EvidenceCardRow.invalidated_at.is_(None),
        )
    ).all()
    return {str(r[0]) for r in rows}


def resolvable_evidence_ids(
    db: Session, *, session_id: uuid.UUID, tenant_id: uuid.UUID | None = None
) -> set[str]:
    """本会话内**可被 claim 引用**的证据 id：权威表 ∪ 遗留表。

    权威表的判据复用 `grounded_card_ids_for_session`（它内部复用
    `origin_grounding.is_grounded_card`），**不在这里重写**。
    """
    return grounded_card_ids_for_session(
        db, session_id=session_id, tenant_id=tenant_id
    ) | legacy_evidence_ids(db, session_id=session_id)


def evidence_cards_for_context(db: Session, *, session_id: uuid.UUID) -> list[dict]:
    """本会话证据卡的可裁剪投影（RV09 上下文清单用）。

    与 `resolvable_evidence_ids()` 的分工：
    - 那个函数回答"**哪些 id 能支撑 claim**"（只要一个集合）；
    - 本函数回答"**每一张卡长什么样、为什么可以/不可以进提示词**"，
      因此必须保留被排除的卡（未授权/已失效/stale）——
      否则 `ContextManifest` 只能记录"少了几张卡"，而说不出少了哪几张、为什么。

    返回项带 `authorized` / `invalidated` / `stale_state` 三个判据位，
    由 `context_manifest.build_manifest` 决定收/排并写 `trimReason`。
    """
    from ..evidence.models import EvidenceCardV2Row

    out: list[dict] = []

    # 权威表（RV07 富事实模型）
    for row in (
        db.query(EvidenceCardV2Row)
        .filter(EvidenceCardV2Row.session_id == session_id)
        .order_by(EvidenceCardV2Row.created_at)
        .all()
    ):
        label = str((row.source_ref or {}).get("title") or (row.source_ref or {}).get("label") or "")
        out.append(
            {
                "id": str(row.id),
                "ref": f"evidence:{row.id}",
                "label": label[:200],
                "snippet": (row.snippet or "")[:1200],
                "authorized": str(row.status).upper() == "ACTIVE",
                "invalidated": str(row.status).upper() != "ACTIVE",
                "stale_state": str(row.stale_state or "FRESH"),
                "source": "research",
            }
        )

    # 遗留扁平表（`0005`）
    for row in (
        db.query(EvidenceCardRow)
        .filter(EvidenceCardRow.session_id == session_id)
        .order_by(EvidenceCardRow.created_at)
        .all()
    ):
        out.append(
            {
                "id": str(row.id),
                "ref": f"evidence:{row.id}",
                "label": (row.label or "")[:200],
                "snippet": (row.snippet or "")[:1200],
                "authorized": bool(row.authorized),
                "invalidated": row.invalidated_at is not None,
                "stale_state": "FRESH",
                "source": "research",
            }
        )
    return out


__all__ = ["evidence_cards_for_context", "legacy_evidence_ids", "resolvable_evidence_ids"]
