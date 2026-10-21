"""RV09 · 组装"这一轮模型能看到什么"（`ContextManifest` 的数据来源）。

本模块只做**取数与授权**，裁剪与登记在 `llm/context_manifest.py`。
把两者分开的理由：取数是 IO（要碰 DB、要判权限），裁剪是纯函数。
混在一起就没法对"预算超了会裁掉哪一段"写确定性测试。

范围（任务书要求 3 的封闭词表）
------------------------------
- `request`：本轮用户请求（调用方直接传入）；
- `conversation`：本会话**有限**轮历史（`MAX_CONVERSATION_TURNS`，见 manifest）；
- `session_summary`：`SessionSummaryRow` 的最新一版（确定性投影，非 AI 生成）；
- `domain_state`：本会话领域对象的**存在性 + 修订号 + 最新问题地图摘要**；
- `preference`：长期偏好，**只取同一命名空间且已授权**的项；
- `evidence`：本会话**已授权且未失效**的证据卡。

刻意不做的事
------------
- 不读 Teacher 库 / 别的 project 的任何数据（`AGENTS.md` 跨边界约定 +
  `context_manifest.FORBIDDEN_SOURCES` 运行时守卫）；
- 不把整段会话历史塞进去（那是"上下文超预算"的成因）；
- 不生成任何"摘要文本"（那会是 AI 编造 —— 摘要必须来自确定性投影）。
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import ResearchMessageRow, ResearchSessionRow, SessionSummaryRow
from ..memory import long_term as LT
from ..memory.namespaces import PURPOSE_ASSISTANT_STYLE

#: 长期偏好按 `research_id = project_id` 归档。
#: 理由是"研究"在这个服务里的稳定标识就是项目（会话会分叉，项目不会）。
#: 该口径登记在 `RV09/MEMORY-ISOLATION.md`，改动它会改变既有偏好的可见性。
PURPOSE_FOR_PROMPT = PURPOSE_ASSISTANT_STYLE


def _iso(value: Any) -> str:
    return value.isoformat() if value is not None else ""


def conversation_items(
    db: Session, session_id: uuid.UUID, *, exclude_client_request_id: str
) -> list[dict[str, Any]]:
    """本会话的历史消息（**不含**本轮请求本身），按 seq 升序。

    用 `client_request_id` 排除本轮：本轮消息在 tx1 已经落库，
    若不去掉，模型会看到同一句话两遍（一次在 conversation，一次在 request），
    并可能把它当成"用户重复强调了这件事"。
    """
    rows = (
        db.query(ResearchMessageRow)
        .filter(ResearchMessageRow.session_id == session_id)
        .order_by(ResearchMessageRow.seq)
        .all()
    )
    out: list[dict[str, Any]] = []
    for row in rows:
        if exclude_client_request_id and row.client_request_id == exclude_client_request_id:
            continue
        if row.role == "assistant" and (row.payload or {}).get("notice"):
            # 降级提示不是研究内容，放回上下文只会让模型模仿道歉口吻。
            continue
        out.append(
            {
                "id": str(row.id),
                "ref": f"message:{row.seq}",
                "role": row.role,
                "text": (row.text or "")[:4000],
            }
        )
    return out


def session_summary(db: Session, session_id: uuid.UUID) -> dict[str, Any] | None:
    """最新一版确定性会话摘要。"""
    row = (
        db.query(SessionSummaryRow)
        .filter(SessionSummaryRow.session_id == session_id)
        .order_by(SessionSummaryRow.summary_version.desc())
        .first()
    )
    if row is None:
        return None
    return {
        "ref": f"summary:v{row.summary_version}",
        "generatedBy": row.generated_by,
        "content": dict(row.content or {}),
    }


def domain_state(db: Session, session_id: uuid.UUID) -> dict[str, Any] | None:
    """领域状态：对象族的存在性 + 修订号 + 问题地图当前状态。

    只给**摘要级**信息：把整份 ProblemMap 塞进提示词会让每一轮都在重复
    "已经确认过的地图"，既浪费预算，也让模型倾向于复述而不是推进。
    """
    from ..application.domain_service import object_summary
    from ..db.models import ProblemMapRow, ResearchMapRow

    summary = object_summary(db, session_id)
    if not any(v.get("exists") for v in summary.values()):
        return None

    problem_note = ""
    pm = (
        db.query(ProblemMapRow)
        .filter(ProblemMapRow.session_id == session_id)
        .order_by(ProblemMapRow.revision.desc())
        .first()
    )
    if pm is not None:
        content = dict(pm.content or {})
        gaps = [g.get("section") for g in (content.get("insufficiencies") or [])]
        problem_note = str((content.get("researchQuestion") or [{}])[0].get("text", ""))[:500]
        if gaps:
            problem_note += f"（未闭合段：{', '.join(str(g) for g in gaps[:6])}）"

    map_note = ""
    rm = (
        db.query(ResearchMapRow)
        .filter(ResearchMapRow.session_id == session_id)
        .order_by(ResearchMapRow.revision.desc())
        .first()
    )
    if rm is not None:
        content = dict(rm.content or {})
        lenses = [str(p.get("lens", "")) for p in (content.get("perspectives") or [])]
        if lenses:
            map_note = "；".join(lenses)[:400]
        elif content.get("schemaVariant") == "legacy-nodes":
            map_note = "（历史 nodes/edges 形状，RV09 起建议改用多视角地图）"

    return {
        "ref": "domain:session-objects",
        "objects": summary,
        "problemMap": {"revision": getattr(pm, "revision", None), "currentState": problem_note},
        "researchMap": {"revision": getattr(rm, "revision", None), "perspectives": map_note},
    }


def preference_items(
    db: Session, *, tenant_id: uuid.UUID, user_id: uuid.UUID, research_id: str
) -> list[dict[str, Any]]:
    """同一命名空间下**已授权**的偏好项（未授权的在 manifest 里会被排除并记录）。"""
    return LT.context_items(
        db,
        tenant_id=tenant_id,
        user_id=user_id,
        research_id=research_id,
        purpose=PURPOSE_FOR_PROMPT,
    )


def evidence_items(db: Session, session_id: uuid.UUID) -> list[dict[str, Any]]:
    """本会话证据卡（含授权位与失效位，交由 manifest 决定收/排并记录理由）。"""
    from ..evidence.resolvable import evidence_cards_for_context

    return evidence_cards_for_context(db, session_id=session_id)


def namespace_for(
    *, tenant_id: uuid.UUID, user_id: uuid.UUID, project_id: uuid.UUID
) -> dict[str, str]:
    return {
        "tenantId": str(tenant_id),
        "userId": str(user_id),
        "researchId": str(project_id),
        "purpose": PURPOSE_FOR_PROMPT,
    }


def build_context(
    db: Session,
    *,
    session: ResearchSessionRow,
    actor_id: uuid.UUID,
    request_text: str,
    exclude_client_request_id: str,
    request_scan_signals: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """返回 `(context_text, manifest_record)`。

    `manifest_record` 是落 `research_ai_run.context_manifest` 的形状 ——
    **不含正文**，只有引用 / 字符数 / 裁剪理由。
    """
    from ..llm.context_manifest import build_manifest

    namespace = namespace_for(
        tenant_id=session.tenant_id, user_id=actor_id, project_id=session.project_id
    )
    manifest_id = f"ctx:{session.id}:{uuid.uuid4().hex[:8]}"

    context_text, manifest = build_manifest(
        manifest_id=manifest_id,
        namespace=namespace,
        request_text=request_text,
        conversation=conversation_items(
            db, session.id, exclude_client_request_id=exclude_client_request_id
        ),
        session_summary=session_summary(db, session.id),
        domain_state=domain_state(db, session.id),
        preferences=preference_items(
            db,
            tenant_id=session.tenant_id,
            user_id=actor_id,
            research_id=str(session.project_id),
        ),
        evidence=evidence_items(db, session.id),
        injection_signals=list(request_scan_signals or []),
    )
    return context_text, manifest.to_record()


__all__ = [
    "PURPOSE_FOR_PROMPT",
    "build_context",
    "conversation_items",
    "domain_state",
    "evidence_items",
    "namespace_for",
    "preference_items",
    "session_summary",
]
