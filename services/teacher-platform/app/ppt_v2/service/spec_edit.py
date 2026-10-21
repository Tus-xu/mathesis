"""deck 规格编辑（T24）：修订快照 / 编辑提案 / 乐观并发 / 撤销。

为什么提案与修订都要落库：
- 「先提案后落库」（INV-4）要求提案本身是可查询的事实，而不是一次 HTTP 请求的内存态；
- 「撤销」（INV-5）必须以全量快照生成**新** revision，删除历史就等于伪造可追溯性。

并发边界：apply 时对 deck 行 `FOR UPDATE` 并在事务内复检 revision —— 两个标签页
同时提交时后者必然看到前者的新 revision，从而得到 409 + 字段级 diff，而不是静默覆盖。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, ErrorCode
from app.ppt_v2.domain.errors import (
    PatchValidationError,
    RevisionConflictError,
)
from app.ppt_v2.domain.patch import (
    DeckPatchProposal,
    apply_proposal,
    new_patch_id,
)
from app.ppt_v2.domain.reader import DeckSpecReader
from app.ppt_v2.domain.spec import ChangeLogEntry, TeachingDeckSpec
from app.ppt_v2.persistence.models import (
    PptV2DeckSpecRecord,
    PptV2EditProposal,
    PptV2SpecRevision,
)

_MAX_PATCH_OPS = 40


def canonical_sha256(payload: Any) -> str:
    """规范 JSON（键排序、紧凑、保留非 ASCII）的 sha256 —— 与前端/8200 的取数口径一致。"""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _not_found(deck_id: str) -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, f"deck {deck_id} 不存在")


async def load_deck_row(
    session: AsyncSession, deck_id: str, *, for_update: bool = False
) -> PptV2DeckSpecRecord:
    try:
        uid = uuid.UUID(deck_id)
    except (ValueError, AttributeError) as exc:
        raise _not_found(deck_id) from exc
    stmt = select(PptV2DeckSpecRecord).where(PptV2DeckSpecRecord.id == uid)
    if for_update:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise _not_found(deck_id)
    return row


def load_spec(row: PptV2DeckSpecRecord) -> TeachingDeckSpec:
    """经 reader 读取（未知主版本 fail-closed，不做静默降级）。"""
    try:
        spec, _warnings = DeckSpecReader.read(row.spec)
        return spec
    except Exception as exc:  # noqa: BLE001 - reader 的失败原因要原样透出
        raise ApiError(ErrorCode.INTERNAL, f"deck spec 无法读取: {exc}") from exc


async def _snapshot_row(
    session: AsyncSession, deck_id: uuid.UUID, revision: int
) -> PptV2SpecRevision | None:
    return (
        await session.execute(
            select(PptV2SpecRevision).where(
                PptV2SpecRevision.deck_id == deck_id, PptV2SpecRevision.revision == revision
            )
        )
    ).scalar_one_or_none()


async def ensure_snapshot(
    session: AsyncSession, row: PptV2DeckSpecRecord, *, kind: str = "baseline"
) -> PptV2SpecRevision:
    """保证当前 revision 有快照。历史 deck（R20 投影入库）没有快照时补一份 baseline。

    没有快照就无法给出 409 的字段级 diff，也无法撤销到该 revision —— 所以是懒补齐而不是报错。
    """
    existing = await _snapshot_row(session, row.id, row.revision)
    if existing is not None:
        return existing
    snap = PptV2SpecRevision(
        deck_id=row.id,
        revision=row.revision,
        kind=kind,
        spec=row.spec,
        spec_sha256=canonical_sha256(row.spec),
        base_revision=None,
        summary="基线快照" if kind == "baseline" else "",
    )
    session.add(snap)
    await session.flush()
    return snap


async def _write_revision(
    session: AsyncSession,
    row: PptV2DeckSpecRecord,
    *,
    spec: TeachingDeckSpec,
    kind: str,
    base_revision: int | None,
    summary: str,
    actor_id: str | None,
) -> PptV2SpecRevision:
    spec_dict = spec.model_dump(mode="json")
    row.spec = spec_dict
    row.revision = spec.revision
    session.add(row)
    snap = PptV2SpecRevision(
        deck_id=row.id,
        revision=spec.revision,
        kind=kind,
        spec=spec_dict,
        spec_sha256=canonical_sha256(spec_dict),
        base_revision=base_revision,
        summary=summary,
        actor_id=actor_id,
    )
    session.add(snap)
    await session.flush()
    return snap


def _affected_dict(affected: Any) -> dict[str, Any]:
    """AffectedSlideSet 是 frozen dataclass（frozenset 不可 JSON 化），落库前必须展开为列表。"""
    return {
        "slide_ids": sorted(affected.slide_ids),
        "layout_affected": sorted(affected.layout_affected),
        "content_affected": sorted(affected.content_affected),
        "assets_affected": sorted(affected.assets_affected),
        "whole_deck_layout": bool(affected.whole_deck_layout),
        "rationale": affected.rationale,
    }


def _slide_content_sha(slide: Any) -> str:
    return canonical_sha256(slide)


def _diff_fields(base_spec: dict[str, Any], current_spec: dict[str, Any], operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按提案 ops 的目标字段给出 base vs server 的字段级差异（客户端据此决定 reapply）。"""
    out: list[dict[str, Any]] = []
    base_slides = {s.get("id"): s for s in (base_spec.get("slides") or [])}
    cur_slides = {s.get("id"): s for s in (current_spec.get("slides") or [])}
    seen: set[tuple[str, str]] = set()
    for op in operations:
        target = op.get("target")
        field = op.get("field")
        if not target or not field:
            continue
        key = (str(target), str(field))
        if key in seen:
            continue
        seen.add(key)
        if op.get("op") == "set_slide":
            out.append(
                {
                    "target": target,
                    "field": field,
                    "base_value": (base_slides.get(target) or {}).get(field),
                    "server_value": (cur_slides.get(target) or {}).get(field),
                }
            )
        elif op.get("op") == "set_metadata":
            out.append(
                {
                    "target": "metadata",
                    "field": field,
                    "base_value": (base_spec.get("metadata") or {}).get(field),
                    "server_value": (current_spec.get("metadata") or {}).get(field),
                }
            )
    return out


async def conflict_payload(
    session: AsyncSession,
    row: PptV2DeckSpecRecord,
    *,
    expected_revision: int,
    operations: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    built = await _build_conflict(session, row, expected_revision=expected_revision, operations=operations)
    built["kind"] = reason
    return built


async def _build_conflict(
    session: AsyncSession,
    row: PptV2DeckSpecRecord,
    *,
    expected_revision: int,
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    base = await _snapshot_row(session, row.id, expected_revision)
    base_spec = (base.spec if base is not None else {}) or {}
    current_spec = row.spec or {}
    base_hashes = {s.get("id", ""): _slide_content_sha(s) for s in (base_spec.get("slides") or [])}
    cur_hashes = {s.get("id", ""): _slide_content_sha(s) for s in (current_spec.get("slides") or [])}
    changed = sorted(
        sid
        for sid in set(base_hashes) | set(cur_hashes)
        if base_hashes.get(sid) != cur_hashes.get(sid)
    )
    fields = _diff_fields(base_spec, current_spec, operations)
    op_status = []
    for idx, op in enumerate(operations):
        target, field = str(op.get("target") or ""), str(op.get("field") or "")
        row_diff = next((f for f in fields if str(f["target"]) == target and str(f["field"]) == field), None)
        if row_diff is None:
            op_status.append({"index": idx, "status": "unknown", "message": "服务端已无该字段（目标可能已被删除）"})
        elif row_diff["base_value"] == row_diff["server_value"]:
            op_status.append({"index": idx, "status": "applied", "message": "该字段服务端未变，可重新提交"})
        else:
            op_status.append({"index": idx, "status": "conflict", "message": "该字段已被其他编辑者改过"})
    return {
        "kind": "revision_conflict",
        "expected_revision": expected_revision,
        "current_revision": row.revision,
        "server_spec_sha256": canonical_sha256(current_spec),
        "diff": {
            "summary_zh": (
                f"服务端已推进到版本 {row.revision}（提交时基于版本 {expected_revision}），"
                f"另有 {len(changed)} 页内容变化"
            ),
            "fields": fields,
            "slide_ids": changed,
        },
        "operations": op_status,
        "server_snapshot": {"revision": row.revision},
    }


def _conflict_error(payload: dict[str, Any]) -> ApiError:
    return ApiError(ErrorCode.CONFLICT, payload["diff"]["summary_zh"], data=payload)


def _parse_operations(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not raw:
        raise ApiError(ErrorCode.VALIDATION, "operations 不能为空")
    if len(raw) > _MAX_PATCH_OPS:
        raise ApiError(ErrorCode.VALIDATION, f"单次提案操作数上限 {_MAX_PATCH_OPS}")
    return raw


def _domain_source(source: str) -> str:
    """领域提案只区分 human/ai（R10 契约）；regen 是 ai 的一种来源，不扩领域枚举。"""
    return "human" if source == "human" else "ai"


def _dry_run(
    spec: TeachingDeckSpec, operations: list[dict[str, Any]], *, summary: str, source: str
) -> tuple[TeachingDeckSpec, Any]:
    """在副本上预演一次 apply：既验证 ops，又得到 AffectedSlideSet。"""
    proposal = DeckPatchProposal(
        deck_id=spec.id,
        expected_revision=spec.revision,
        summary=summary,
        source=_domain_source(source),  # type: ignore[arg-type]
        operations=operations,  # type: ignore[arg-type]
    )
    try:
        new_spec, patch, _log = apply_proposal(spec, proposal)
    except RevisionConflictError as exc:  # 预演用当前 revision，正常不会发生
        raise ApiError(ErrorCode.CONFLICT, str(exc)) from exc
    except PatchValidationError as exc:
        raise ApiError(ErrorCode.VALIDATION, f"提案未通过领域校验: {exc}") from exc
    except ValidationError as exc:
        raise ApiError(ErrorCode.VALIDATION, f"提案字段非法: {exc}") from exc
    return new_spec, patch


def _client_base_mismatch(
    base_spec: dict[str, Any], operations: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """客户端声明的 `before` 必须与它声称的 base 版本一致，否则它连自己的底稿都不是。"""
    base_slides = {s.get("id"): s for s in (base_spec.get("slides") or [])}
    clashes: list[dict[str, Any]] = []
    for op in operations:
        if "before" not in op:
            continue
        target, field = op.get("target"), op.get("field")
        actual = (base_slides.get(target) or {}).get(field) if op.get("op") == "set_slide" else None
        if op.get("op") == "set_metadata":
            actual = (base_spec.get("metadata") or {}).get(field)
        if actual != op["before"]:
            clashes.append({"target": target, "field": field, "claimed_before": op["before"], "base_value": actual})
    return clashes


async def create_proposal(
    session: AsyncSession,
    deck_id: str,
    *,
    expected_revision: int,
    summary: str,
    source: str,
    operations: list[dict[str, Any]],
    instruction: str | None = None,
    refs: dict[str, Any] | None = None,
    actor_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    ops = _parse_operations(operations)
    row = await load_deck_row(session, deck_id)
    spec = load_spec(row)
    await ensure_snapshot(session, row)

    if expected_revision != row.revision:
        raise _conflict_error(
            await _build_conflict(session, row, expected_revision=expected_revision, operations=ops)
        )

    if idempotency_key:
        existing = (
            await session.execute(
                select(PptV2EditProposal).where(
                    PptV2EditProposal.deck_id == row.id,
                    PptV2EditProposal.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _proposal_view(existing)

    base_snap = await _snapshot_row(session, row.id, expected_revision)
    clashes = _client_base_mismatch((base_snap.spec if base_snap else {}) or {}, ops)
    if clashes:
        payload = await _build_conflict(session, row, expected_revision=expected_revision, operations=ops)
        payload["kind"] = "client_base_mismatch"
        payload["diff"]["fields"] = clashes
        payload["diff"]["summary_zh"] = "客户端底稿与服务端同版本内容不一致，请重新加载后再改"
        raise _conflict_error(payload)

    new_spec, patch = _dry_run(spec, ops, summary=summary, source=source)

    proposal = PptV2EditProposal(
        deck_id=row.id,
        status="proposed",
        source=source,
        expected_revision=expected_revision,
        base_revision=row.revision,
        summary=summary,
        instruction=instruction,
        operations=ops,
        refs=refs,
        affected=_affected_dict(patch.affected),
        actor_id=actor_id,
        idempotency_key=idempotency_key,
    )
    session.add(proposal)
    await session.flush()
    view = _proposal_view(proposal)
    view["changed_slide_ids"] = sorted(patch.affected.slide_ids)
    view["preview_revision"] = new_spec.revision
    return view


async def apply_proposal_by_id(
    session: AsyncSession,
    deck_id: str,
    patch_id: str,
    *,
    expected_revision: int,
    actor_id: str | None = None,
) -> dict[str, Any]:
    """确认后写服务端：复检 revision → apply → revision+1，并把影响的页一并回报。"""
    row = await load_deck_row(session, deck_id, for_update=True)
    proposal = await _load_proposal(session, row.id, patch_id)
    if proposal.status != "proposed":
        raise ApiError(ErrorCode.CONFLICT, f"提案 {patch_id} 状态为 {proposal.status}，不可再 apply")

    if expected_revision != row.revision:
        raise _conflict_error(
            await _build_conflict(
                session, row, expected_revision=expected_revision, operations=proposal.operations
            )
        )

    spec = load_spec(row)
    domain_proposal = DeckPatchProposal(
        deck_id=spec.id,
        expected_revision=spec.revision,
        summary=proposal.summary,
        source=_domain_source(proposal.source),  # type: ignore[arg-type]
        operations=proposal.operations,  # type: ignore[arg-type]
    )
    patch_id_effective = new_patch_id()
    try:
        new_spec, patch, log_entry = apply_proposal(spec, domain_proposal, patch_id=patch_id_effective)
    except RevisionConflictError as exc:
        raise _conflict_error(
            await _build_conflict(
                session, row, expected_revision=expected_revision, operations=proposal.operations
            )
        ) from exc
    except (PatchValidationError, ValidationError) as exc:
        proposal.status = "failed"
        proposal.error = str(exc)
        proposal.decided_at = datetime.now(UTC)
        await session.flush()
        raise ApiError(ErrorCode.VALIDATION, f"提案在 apply 时未通过校验: {exc}") from exc

    await _write_revision(
        session,
        row,
        spec=new_spec,
        kind="regen" if proposal.source == "regen" else "patch",
        base_revision=row.revision,
        summary=proposal.summary,
        actor_id=actor_id,
    )
    proposal.status = "applied"
    proposal.applied_revision = new_spec.revision
    proposal.decided_at = datetime.now(UTC)
    proposal.affected = _affected_dict(patch.affected)
    await session.flush()
    return {
        "deck_id": deck_id,
        "revision": new_spec.revision,
        "applied_patch_id": patch.id,
        "affected": _affected_dict(patch.affected),
        "change_log_entry": log_entry.model_dump(mode="json"),
    }


async def reject_proposal(
    session: AsyncSession, deck_id: str, patch_id: str, *, reason: str | None = None
) -> dict[str, Any]:
    row = await load_deck_row(session, deck_id)
    proposal = await _load_proposal(session, row.id, patch_id)
    if proposal.status != "proposed":
        raise ApiError(ErrorCode.CONFLICT, f"提案 {patch_id} 状态为 {proposal.status}，不可再 reject")
    proposal.status = "rejected"
    proposal.error = reason
    proposal.decided_at = datetime.now(UTC)
    await session.flush()
    return {"patch_id": patch_id, "status": "rejected"}


async def _load_proposal(session: AsyncSession, deck_id: uuid.UUID, patch_id: str) -> PptV2EditProposal:
    try:
        uid = uuid.UUID(patch_id)
    except (ValueError, AttributeError) as exc:
        raise ApiError(ErrorCode.NOT_FOUND, f"提案 {patch_id} 不存在") from exc
    proposal = (
        await session.execute(
            select(PptV2EditProposal).where(
                PptV2EditProposal.id == uid, PptV2EditProposal.deck_id == deck_id
            )
        )
    ).scalar_one_or_none()
    if proposal is None:
        raise ApiError(ErrorCode.NOT_FOUND, f"提案 {patch_id} 不存在")
    return proposal


def _proposal_view(proposal: PptV2EditProposal) -> dict[str, Any]:
    return {
        "patch_id": str(proposal.id),
        "deck_id": str(proposal.deck_id),
        "status": proposal.status,
        "source": proposal.source,
        "base_revision": proposal.base_revision,
        "expected_revision": proposal.expected_revision,
        "summary": proposal.summary,
        "operations": proposal.operations,
        "affected": proposal.affected,
        "refs": proposal.refs,
        "applied_revision": proposal.applied_revision,
        "error": proposal.error,
    }


async def list_revisions(session: AsyncSession, deck_id: str) -> dict[str, Any]:
    row = await load_deck_row(session, deck_id)
    await ensure_snapshot(session, row)
    rows = (
        await session.execute(
            select(PptV2SpecRevision)
            .where(PptV2SpecRevision.deck_id == row.id)
            .order_by(PptV2SpecRevision.revision)
        )
    ).scalars().all()
    # 影响页由相邻快照的页内容哈希差集算出——比读 change_log 更可信（快照是事实，日志是描述）
    prev_hashes: dict[str, str] = {}
    revisions: list[dict[str, Any]] = []
    for r in rows:
        cur_hashes = {s.get("id", ""): _slide_content_sha(s) for s in (r.spec.get("slides") or [])}
        changed = (
            []
            if not prev_hashes
            else sorted(
                sid
                for sid in set(prev_hashes) | set(cur_hashes)
                if prev_hashes.get(sid) != cur_hashes.get(sid)
            )
        )
        revisions.append(
            {
                "revision": r.revision,
                "kind": r.kind,
                "summary": r.summary,
                "actor_id": r.actor_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "spec_sha256": r.spec_sha256,
                "base_revision": r.base_revision,
                "affected_slide_ids": changed,
            }
        )
        prev_hashes = cur_hashes
    return {"deck_id": deck_id, "current_revision": row.revision, "revisions": revisions}


async def revert_to_revision(
    session: AsyncSession,
    deck_id: str,
    target_revision: int,
    *,
    expected_revision: int,
    actor_id: str | None = None,
    summary: str | None = None,
) -> dict[str, Any]:
    """撤销 = 用历史快照生成**新** revision（不删历史），这样多标签页仍能各自前进。"""
    row = await load_deck_row(session, deck_id, for_update=True)
    if expected_revision != row.revision:
        raise _conflict_error(
            await _build_conflict(session, row, expected_revision=expected_revision, operations=[])
        )
    snap = await _snapshot_row(session, row.id, target_revision)
    if snap is None:
        raise ApiError(ErrorCode.NOT_FOUND, f"deck {deck_id} 没有 revision {target_revision} 的快照")
    restored = TeachingDeckSpec.model_validate(snap.spec)
    new_revision = row.revision + 1
    revert_summary = summary or f"撤销回版本 {target_revision}"
    restored = restored.model_copy(
        update={
            "revision": new_revision,
            "change_log": [
                *(restored.change_log or []),
                ChangeLogEntry(
                    revision=new_revision,
                    ts=datetime.now(UTC),
                    kind="revert",
                    summary=revert_summary,
                    patch_ids=[],
                    affected_slide_ids=[],
                ),
            ],
        },
        deep=True,
    )
    rev = await _write_revision(
        session,
        row,
        spec=restored,
        kind="revert",
        base_revision=row.revision,
        summary=revert_summary,
        actor_id=actor_id,
    )
    return {
        "deck_id": deck_id,
        "revision": new_revision,
        "reverted_to": target_revision,
        "spec_sha256": rev.spec_sha256,
        "affected": {"slide_ids": [], "layout_affected": [], "content_affected": [],
                     "assets_affected": [], "whole_deck_layout": True,
                     "rationale": f"撤销回版本 {target_revision}"},
    }


def deck_view(row: PptV2DeckSpecRecord) -> dict[str, Any]:
    spec = row.spec or {}
    slides = spec.get("slides") or []
    meta = spec.get("metadata") or {}
    projection = spec.get("projection") or {}
    return {
        "deck_id": str(row.id),
        "spec_id": spec.get("id"),
        "revision": row.revision,
        "schema_version": row.schema_version,
        "spec_sha256": canonical_sha256(spec),
        "metadata": meta,
        "slide_count": len(slides),
        "spec": spec,
        "slides": [
            {
                "id": s.get("id"),
                "index": i,
                "title": s.get("title"),
                "layout_hint": s.get("layout_hint"),
                "qa_status": s.get("qa_status"),
                "content_sha256": _slide_content_sha(s),
            }
            for i, s in enumerate(slides)
        ],
        "lineage": {
            "approved_blueprint_id": projection.get("approved_blueprint_id"),
            "blueprint_revision": projection.get("approved_blueprint_revision"),
            "context_snapshot_hash": projection.get("context_snapshot_hash"),
            "prompt_release_id": projection.get("prompt_release_id"),
            "ai_run_id": projection.get("ai_run_id"),
            "run_hash": projection.get("run_hash"),
            "body_hash": projection.get("body_hash"),
            "projected_at": projection.get("projected_at"),
        },
        "recent_change_log": (spec.get("change_log") or [])[-5:],
    }
