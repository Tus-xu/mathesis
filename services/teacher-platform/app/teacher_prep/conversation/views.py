"""R17 工作区投影（views）：timeline + artifact + evidence + actions + job summary。

权威性（DECISIONS.md §7）：
- 所有内容从服务端事实行投影（tp_sessions / tp_conversation_messages /
  tp_context_snapshots / tp_blueprints / tp_message_runs / reliability_jobs），
  不引入任何客户端状态；
- 组件引用一律经 catalog.validate_component 规范化后下发（allowlist 契约）；
- available actions 由状态机 + 当前事实推导（不猜、不假进度）；
- 无步骤条：本模块不输出任何 steps / wizard / currentStep 字段（契约测试钉死）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ppt_v2.persistence.models import PptV2DeckSpecRecord
from app.reliability.models import ReliabilityJob

from ..models import TeachingBlueprintRow
from ..service import blueprint_dto, load_class_names, session_dto
from ..specs.models import LessonPlanSpecRow, SpecProjectionRow
from ..text_safety import strip_media_syntax, truncate_math_aware
from . import catalog
from .models import ContextSnapshotRow, ConversationMessageRow, MessageRunRow
from .schema import MessageView, WorkspaceSnapshot

# evidence snippet 截断长度（不把教材全文灌给前端）
_SNIPPET_LEN = 160


def message_view(row: ConversationMessageRow, run_status: str | None = None) -> MessageView:
    component: dict[str, Any] | None = None
    if row.component_type and row.component_props is not None:
        # 落库前已校验；此处再次过闸门（防脏数据 / 迁移遗留）
        component = {
            "type": row.component_type,
            "props": catalog.validate_component(row.component_type, row.component_props),
        }
    return MessageView(
        message_id=str(row.id),
        seq=row.seq,
        role=row.role,
        kind=row.kind,
        component=component,
        text=row.text,
        intent=row.intent,
        context_snapshot_id=str(row.context_snapshot_id) if row.context_snapshot_id else None,
        blueprint_id=str(row.blueprint_id) if row.blueprint_id else None,
        run_status=run_status,
        error_label=row.error_label,
        error_code=row.error_code,
        editable=row.editable,
        created_at=row.created_at.isoformat() if row.created_at else "",
    )


def _run_status_of(row: MessageRunRow | None) -> str | None:
    return row.status if row is not None else None


def _context_summary(snap: ContextSnapshotRow | None) -> dict[str, Any] | None:
    """living artifact.context：上下文快照的展示摘要（服务端权威投影）。"""
    if snap is None:
        return None
    data = snap.snapshot or {}
    return {
        "snapshot_id": str(snap.id),
        "snapshot_hash": snap.snapshot_hash,
        "schema_version": data.get("schema_version"),
        "selection": data.get("selection"),
        # R3 教材依据卡：章节级 SourceLocator ref（教材查看端点可解析的权威锚）
        "locator": data.get("locator"),
        "source": {
            "id": (data.get("source") or {}).get("id"),
            "title": _source_title(data.get("source")),
        },
        "section": {
            "id": (data.get("section") or {}).get("id"),
            "title": (data.get("section") or {}).get("title"),
            "section_path": (data.get("section") or {}).get("section_path"),
        },
        "objectives": [
            {"text": f.get("text"), "source_ref": f.get("source_ref")}
            for f in (data.get("objectives") or [])
        ][:12],
        "knowledge_points": (data.get("selection") or {}).get("knowledge_points") or [],
        "coverage": data.get("coverage"),
        "confidence": data.get("confidence"),
        "warnings": [w.get("code") for w in (data.get("warnings") or [])],
    }


def _source_title(source: dict[str, Any] | None) -> str | None:
    """教材展示名（黄金图口径：`出版社 · 高中数学 · 册次`）。

    - 去重：导入源存在冗余（苏教版 edition == volume == 选择性必修第一册），
      逐字拼接会渲染出「选择性必修第一册 选择性必修第一册」（2026-09-21 截图实测）；
    - 学科统一「高中数学」：本模块只服务高中数学备课（视觉唯一真相源 = 黄金图 PPT 页眉）。
    """
    if not source:
        return None
    from .options import label_of

    publisher = str(source.get("publisher") or "").strip()
    volume = str(source.get("edition") or source.get("volume") or "").strip()
    return label_of(publisher, "高中数学", volume) or None


def _blueprint_summary(row: TeachingBlueprintRow | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        **blueprint_dto(row),
        "body": row.body or {},
    }


def _evidence_refs(snap: ContextSnapshotRow | None, blueprint: TeachingBlueprintRow | None) -> list[dict[str, Any]]:
    """evidence rail：来自授权上下文事实 + 蓝图提案引用的可解析 SourceLocator（去重保序）。"""
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    data = snap.snapshot if snap else {}

    def _push(ref: str | None, kind: str, title: str, snippet: str) -> None:
        if not ref or ref in seen:
            return
        seen.add(ref)
        # T2.5 读时清洗：存量快照/蓝图行可能含 markdown 图片串（不回写历史，渲染前确定性剥离）。
        # 先清洗后截断——若先按 60/160 字符截断再清洗，截断点落在损坏 URL 中间会漏剥。
        clean = strip_media_syntax(snippet) or strip_media_syntax(title)
        # 数学感知截断：截断点不切进 $…$ 定界符内部（防未闭合 $ 外泄到前端）；
        # 长度只减不增，仍满足 catalog EvidenceRefProps 的 max_length 闸门。
        refs.append(
            {
                "ref": ref,
                "kind": kind,
                "title": truncate_math_aware(clean, 60),
                "snippet": truncate_math_aware(clean, _SNIPPET_LEN),
            }
        )

    for bucket in ("objectives", "prior_knowledge", "concepts", "theorems", "examples", "inquiries", "design_intent", "key_difficult_points"):
        for fact in data.get(bucket) or []:
            _push(fact.get("source_ref") or fact.get("chunk_ref"), bucket, (fact.get("text") or "")[:60], fact.get("text") or "")
    for fact in data.get("facts") or []:
        _push(fact.get("source_ref") or fact.get("chunk_ref"), fact.get("kind") or "fact", (fact.get("text") or "")[:60], fact.get("text") or "")
    # 蓝图提案中的 source_ref（必须 ∈ 上下文可解析集合——R16 grounding 已保证）
    if blueprint is not None:
        body = blueprint.body or {}
        for item in body.get("objectives") or []:
            _push(item.get("source_ref"), "blueprint", (item.get("text") or "")[:60], item.get("text") or "")
        for stage in body.get("lesson_flow") or []:
            for item in stage.get("items") or []:
                _push(item.get("source_ref"), "blueprint", (item.get("text") or "")[:60], item.get("text") or "")
    return refs


def _latest_failed_run(runs: list[MessageRunRow]) -> MessageRunRow | None:
    """最近一次生成尝试（runs 按 created_at 升序）是否停在失败终态。

    旧口径取「任意一条失败 run」：老师重试成功后旧的失败行仍在库，界面会永久挂着
    一个「重试」假入口（可重复重跑已成功的消息）。故只看**最近一次**尝试。
    """
    for r in reversed(runs):
        if r.run_type in ("diagnostic", "diagnostic_publish"):
            continue
        return r if r.status in ("FAILED", "BASIC_MODE") else None
    return None


def derive_available_actions(
    *,
    session_status: str,
    blueprint: TeachingBlueprintRow | None,
    runs: list[MessageRunRow],
    diagnostic: Any | None = None,
) -> list[dict[str, str | bool | None]]:
    """available actions：由状态机 + 当前事实推导（copy_key 走统一 i18n，不散落文案）。

    R18：诊断卡片动作（edit/publish/skip）仅在 proposal/draft 状态可派生；
    发布 run 在途时不再派生 publish（防重复确认入口）。
    """
    actions: list[dict[str, str | bool | None]] = []
    running = next(
        (r for r in runs if r.status in ("PENDING", "RUNNING") and r.run_type != "diagnostic_publish"),
        None,
    )
    failed = _latest_failed_run(runs) if running is None else None

    if running is not None:
        actions.append(
            {
                "action": "cancel_run",
                "target": str(running.id),
                # 与 cancel_session 区分：本动作只中断**本次生成**，会话与已落库事实保留。
                # 两者曾共用 copy_key="common.cancel"，前端渲染出两个一模一样的「取消」，
                # 教师点错即不可逆结束整个备课会话（2026-09-14 真实事故）。
                "copy_key": "prep.action.cancel_run",
                "disabled": False,
            }
        )
    if failed is not None:
        actions.append(
            {
                "action": "retry_run",
                "target": str(failed.message_id or failed.id),
                "copy_key": "common.retry",
                "disabled": False,
            }
        )
    if blueprint is not None:
        if blueprint.status == "proposal":
            actions.append(
                {"action": "confirm_proposal", "target": str(blueprint.id), "copy_key": "prep.action.confirm_proposal", "disabled": False}
            )
            actions.append(
                {"action": "reject_proposal", "target": str(blueprint.id), "copy_key": "prep.action.reject_proposal", "disabled": False}
            )
        elif blueprint.status == "confirmed":
            actions.append(
                {"action": "propose_revision", "target": str(blueprint.id), "copy_key": "prep.action.propose_revision", "disabled": False}
            )
            # L-221-2：AI 修订提案（需 published 学情画像；无画像时仍暴露入口，执行时 409 说明）
            actions.append(
                {
                    "action": "propose_blueprint_revision",
                    "target": str(blueprint.id),
                    "copy_key": "prep.action.propose_blueprint_revision",
                    "disabled": False,
                }
            )
            # L-221-2：双 Spec 投影（confirmed blueprint + context）
            actions.append(
                {
                    "action": "project_specs",
                    "target": str(blueprint.id),
                    "copy_key": "prep.action.project_specs",
                    "disabled": False,
                }
            )
            # R18：已确认蓝图且无活跃诊断 → 可生成学前诊断建议
            if diagnostic is None and not any(
                r.run_type == "diagnostic" and r.status in ("PENDING", "RUNNING") for r in runs
            ):
                actions.append(
                    {"action": "propose_diagnostic", "target": str(blueprint.id), "copy_key": "prep.diagnostic.propose", "disabled": False}
                )
    if diagnostic is not None and diagnostic.status in ("proposal", "draft"):
        publishing = any(
            r.run_type == "diagnostic_publish" and r.status in ("PENDING", "RUNNING")
            and str(getattr(r, "result_ref", "") or "") == str(diagnostic.id)
            for r in runs
        )
        actions.append(
            {"action": "edit_diagnostic", "target": str(diagnostic.id), "copy_key": "prep.diagnostic.edit", "disabled": bool(publishing)}
        )
        if not publishing:
            actions.append(
                {"action": "publish_diagnostic", "target": str(diagnostic.id), "copy_key": "prep.diagnostic.publish", "disabled": False}
            )
        actions.append(
            {"action": "skip_diagnostic", "target": str(diagnostic.id), "copy_key": "prep.diagnostic.skip", "disabled": False}
        )
    if session_status in ("draft", "active"):
        actions.append(
            # 终态动作（不可逆）：文案必须自陈后果，不得与「停止生成」同名。
            {"action": "cancel_session", "target": None, "copy_key": "prep.action.cancel_session", "disabled": False}
        )
    return actions


async def _job_summary(session: AsyncSession, session_id: str) -> list[dict[str, Any]]:
    """job summary：本会话关联的可靠性作业（DB 权威，无假进度）。"""
    rows = (
        (
            await session.execute(
                select(ReliabilityJob)
                .where(
                    ReliabilityJob.owner_service == "teacher_prep",
                    ReliabilityJob.job_type == "conversation.run",
                    ReliabilityJob.payload["session_id"].as_string() == str(session_id),
                )
                .order_by(ReliabilityJob.created_at.desc())
                .limit(5)
            )
        )
        .scalars()
        .all()
    )
    out: list[dict[str, Any]] = []
    for j in rows:
        out.append(
            {
                "job_id": str(j.id),
                "job_type": j.job_type,
                "status": j.status,
                "attempt": j.attempt,
                "max_attempts": j.max_attempts,
                "updated_at": j.updated_at.isoformat() if j.updated_at else None,
                "last_error": (j.last_error or "")[:200] or None,
            }
        )
    return out


def _diagnostic_summary(
    row: Any | None,
    runs: list[MessageRunRow],
) -> dict[str, Any] | None:
    """living artifact.diagnostic：诊断任务权威投影（教师审阅视图，含在途发布标记）。"""
    if row is None:
        return None
    body = row.body or {}
    publishing = any(
        r.run_type == "diagnostic_publish"
        and r.status in ("PENDING", "RUNNING")
        and str(getattr(r, "result_ref", "") or "") == str(row.id)
        for r in runs
    )
    failed_publish = any(
        r.run_type == "diagnostic_publish"
        and r.status == "FAILED"
        and str(getattr(r, "result_ref", "") or "") == str(row.id)
        for r in runs
    )
    return {
        "diagnostic_id": str(row.id),
        "blueprint_id": str(row.blueprint_id),
        "status": row.status,
        "revision": row.revision,
        "title": row.title or body.get("title") or "学前诊断",
        "description": row.description,
        "estimated_duration_minutes": row.estimated_duration_minutes,
        "knowledge_points": body.get("knowledge_points") or [],
        "items": body.get("items") or [],
        "pending_publish": bool(publishing),
        "publish_failed": bool(failed_publish) and row.status != "published",
        "published_assignment_id": (row.provision or {}).get("assignment_id"),
        "skipped_reason": row.skipped_reason,
        "copy_keys": {
            "edit": "prep.diagnostic.edit",
            "publish": "prep.diagnostic.publish",
            "skip": "prep.diagnostic.skip",
        },
    }


async def _class_profile_summary(session: AsyncSession, class_id: Any) -> str | None:
    """R3「已考虑」胶囊：最新 published 班级画像 → 一句学情摘要（确定性推导，无画像 → None）。

    数据全部来自 tp_class_learning_profiles（R19/R20 权威事实）；推导口径：
    class_mean_correct_rate ≥0.85 基础较好 / ≥0.6 基础中等 / <0.6 基础薄弱。
    """
    if class_id is None:
        return None
    from app.teacher_prep.profile.models import ClassLearningProfileRow

    row = (
        await session.execute(
            select(ClassLearningProfileRow)
            .where(ClassLearningProfileRow.class_id == class_id, ClassLearningProfileRow.status == "published")
            .order_by(ClassLearningProfileRow.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    metrics = ((row.body or {}).get("metrics") or {}) if isinstance(row.body, dict) else {}
    rate = metrics.get("class_mean_correct_rate")
    if isinstance(rate, (int, float)) and 0 <= rate <= 1:
        if rate >= 0.85:
            return "班级基础较好"
        if rate >= 0.6:
            return "班级基础中等"
        return "班级基础薄弱"
    return None


async def build_workspace(
    session: AsyncSession,
    *,
    srow: Any,
    message_rows: list[ConversationMessageRow],
    run_rows: list[MessageRunRow],
    blueprint: TeachingBlueprintRow | None,
    context_snap: ContextSnapshotRow | None,
    diagnostic: Any | None = None,
    timeline_limit: int = 100,
) -> WorkspaceSnapshot:
    """从权威事实行组装工作区（纯投影，无外部调用）。"""
    run_by_msg: dict[str, MessageRunRow | None] = {}
    for m in message_rows:
        run_by_msg[str(m.id)] = next((r for r in run_rows if r.message_id == m.id), None)

    timeline = [
        message_view(m, run_status=_run_status_of(run_by_msg.get(str(m.id))))
        for m in message_rows[-timeline_limit:]
    ]
    actions = derive_available_actions(
        session_status=srow.status,
        blueprint=blueprint,
        runs=run_rows,
        diagnostic=diagnostic,
    )
    job_summary = await _job_summary(session, str(srow.id))
    basic_mode = any(m.kind == "basic_mode" and m.editable for m in message_rows)
    names = await load_class_names(session, {srow.class_id})
    lesson_plan = await _lesson_plan_summary(session, srow.id)
    teaching_deck = await _teaching_deck_summary(session, srow.id)
    profile_summary = await _class_profile_summary(session, srow.class_id)
    return WorkspaceSnapshot(
        session=session_dto(srow, class_name=names.get(str(srow.class_id))),
        timeline=timeline,
        artifact={
            "context": _context_summary(context_snap),
            "blueprint": _blueprint_summary(blueprint),
            "diagnostic": _diagnostic_summary(diagnostic, run_rows),
            "lesson_plan": lesson_plan,
            "teaching_deck": teaching_deck,
            "class_profile_summary": profile_summary,
        },
        evidence=_evidence_refs(context_snap, blueprint),
        available_actions=actions,
        job_summary=job_summary,
        basic_mode=basic_mode,
    )


async def _lesson_plan_summary(session: AsyncSession, session_id: Any) -> dict[str, Any] | None:
    """L-221-2：living artifact.lesson_plan（最新 LessonPlanSpec 权威投影摘要）。"""
    from sqlalchemy import select

    row = (
        await session.execute(
            select(LessonPlanSpecRow)
            .where(LessonPlanSpecRow.session_id == session_id)
            .order_by(LessonPlanSpecRow.revision.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    body = row.body or {}
    title = body.get("title") or body.get("topic") or "教案"
    return {
        "spec_id": str(row.id),
        "blueprint_id": str(row.blueprint_id),
        "revision": row.revision,
        "title": title if isinstance(title, str) else "教案",
        "approved_blueprint_revision": row.approved_blueprint_revision,
        "context_snapshot_hash": row.context_snapshot_hash,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _teaching_deck_summary(session: AsyncSession, session_id: Any) -> dict[str, Any] | None:
    """R20：从本会话的 deck 投影反查 8100 权威 TeachingDeckSpec。

    `tp_spec_projections` 只保存审计锚点，正文仍以 `ppt_v2_deck_specs.spec` 为唯一事实；
    因此前端收到的是完整 spec（另带服务端 deck_id/spec_id 别名），可直接交给 8200 ingest
    做恢复，而不会从 lesson-plan 或旧 Deck 形状猜课件内容。
    """
    projection = (
        await session.execute(
            select(SpecProjectionRow)
            .where(
                SpecProjectionRow.session_id == session_id,
                SpecProjectionRow.kind == "deck",
                SpecProjectionRow.spec_target_id.is_not(None),
            )
            .order_by(SpecProjectionRow.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if projection is None or projection.spec_target_id is None:
        return None
    row = await session.get(PptV2DeckSpecRecord, projection.spec_target_id)
    if row is None:
        return None
    body = dict(row.spec or {})
    metadata = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    lineage = body.get("projection") if isinstance(body.get("projection"), dict) else {}
    body.update(
        {
            "deck_id": str(row.id),
            "spec_id": str(body.get("id") or row.id),
            "revision": row.revision,
            "title": metadata.get("title") or "课件",
            "approved_blueprint_revision": lineage.get("approved_blueprint_revision"),
            "context_snapshot_hash": lineage.get("context_snapshot_hash"),
            "blueprint_id": lineage.get("approved_blueprint_id"),
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }
    )
    return body


__all__ = [
    "message_view",
    "derive_available_actions",
    "build_workspace",
    "_context_summary",
    "_diagnostic_summary",
    "_evidence_refs",
]
