"""LessonPlanSpec 编辑编排（T24）：与 deck 侧同构的提案/修订/撤销/重生成。

与 deck 的差别只有两点：粒度是 block（不是 slide），上游事实是 blueprint_ref 而不是 projection。
不变量与 deck 完全一致（INV-1..5），所以两边的测试断言是同一套。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, ErrorCode

from . import patch as patch_domain
from .lesson_plan_schema import ChangeLogEntry, LessonPlanSpec
from .models import LessonPlanEditProposalRow, LessonPlanSpecRevisionRow


def canonical_sha256(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def load_spec_row(session: AsyncSession, spec_id: str, *, for_update: bool = False):
    from .models import LessonPlanSpecRow

    try:
        uid = uuid.UUID(spec_id)
    except (ValueError, AttributeError) as exc:
        raise ApiError(ErrorCode.NOT_FOUND, f"教案 {spec_id} 不存在") from exc
    stmt = select(LessonPlanSpecRow).where(LessonPlanSpecRow.id == uid)
    if for_update:
        stmt = stmt.with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, f"教案 {spec_id} 不存在")
    return row


def spec_of(row: Any) -> LessonPlanSpec:
    return LessonPlanSpec.model_validate(row.body)


async def _snapshot(session: AsyncSession, spec_id: uuid.UUID, revision: int):
    return (
        await session.execute(
            select(LessonPlanSpecRevisionRow).where(
                LessonPlanSpecRevisionRow.spec_id == spec_id,
                LessonPlanSpecRevisionRow.revision == revision,
            )
        )
    ).scalar_one_or_none()


async def ensure_snapshot(session: AsyncSession, row: Any) -> LessonPlanSpecRevisionRow:
    existing = await _snapshot(session, row.id, row.revision)
    if existing is not None:
        return existing
    snap = LessonPlanSpecRevisionRow(
        spec_id=row.id,
        revision=row.revision,
        kind="baseline",
        body=row.body,
        body_sha256=canonical_sha256(row.body),
        base_revision=None,
        summary="基线快照",
    )
    session.add(snap)
    await session.flush()
    return snap


async def _write_revision(
    session: AsyncSession, row: Any, *, spec: LessonPlanSpec, kind: str, base_revision: int | None,
    summary: str, actor_id: str | None,
) -> LessonPlanSpecRevisionRow:
    body = spec.model_dump(mode="json")
    row.body = body
    row.revision = spec.revision
    session.add(row)
    snap = LessonPlanSpecRevisionRow(
        spec_id=row.id,
        revision=spec.revision,
        kind=kind,
        body=body,
        body_sha256=canonical_sha256(body),
        base_revision=base_revision,
        summary=summary,
        actor_id=actor_id,
    )
    session.add(snap)
    await session.flush()
    return snap


def _block_hashes(spec_body: dict[str, Any]) -> dict[str, str]:
    spec = LessonPlanSpec.model_validate(spec_body)
    return {b["block_id"]: canonical_sha256(b) for b in patch_domain.lesson_plan_blocks(spec)}


async def _conflict(
    session: AsyncSession, row: Any, *, expected_revision: int, operations: list[dict[str, Any]], reason: str = "revision_conflict"
) -> ApiError:
    base = await _snapshot(session, row.id, expected_revision)
    base_body = (base.body if base is not None else {}) or {}
    current_body = row.body or {}
    base_blocks = _block_hashes(base_body) if base_body else {}
    cur_blocks = _block_hashes(current_body)
    changed = sorted(b for b in set(base_blocks) | set(cur_blocks) if base_blocks.get(b) != cur_blocks.get(b))
    fields = []
    for op in operations:
        target = str(op.get("target") or "")
        fields.append(
            {
                "target": target,
                "field": str(op.get("field") or ""),
                "base_value": base_blocks.get(target),
                "server_value": cur_blocks.get(target),
            }
        )
    payload = {
        "kind": reason,
        "expected_revision": expected_revision,
        "current_revision": row.revision,
        "server_spec_sha256": canonical_sha256(current_body),
        "diff": {
            "summary_zh": f"教案已在别处更新到版本 {row.revision}（你基于版本 {expected_revision}），{len(changed)} 个块有变化",
            "fields": fields,
            "slide_ids": changed,
            "block_ids": changed,
        },
        "operations": [
            {"index": i, "status": "unknown", "message": "请重新加载后决定是否重放"} for i in range(len(operations))
        ],
        "server_snapshot": {"revision": row.revision},
    }
    return ApiError(ErrorCode.CONFLICT, payload["diff"]["summary_zh"], data=payload)


def _view(proposal: LessonPlanEditProposalRow) -> dict[str, Any]:
    return {
        "patch_id": str(proposal.id),
        "spec_id": str(proposal.spec_id),
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


async def create_proposal(
    session: AsyncSession,
    spec_id: str,
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
    row = await load_spec_row(session, spec_id)
    spec = spec_of(row)
    await ensure_snapshot(session, row)
    if expected_revision != row.revision:
        raise await _conflict(session, row, expected_revision=expected_revision, operations=operations)

    if idempotency_key:
        existing = (
            await session.execute(
                select(LessonPlanEditProposalRow).where(
                    LessonPlanEditProposalRow.spec_id == row.id,
                    LessonPlanEditProposalRow.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return _view(existing)

    try:
        proposal = patch_domain.LessonPlanPatchProposal(
            spec_id=spec.id,
            expected_revision=spec.revision,
            summary=summary,
            source=source,  # type: ignore[arg-type]
            operations=operations,  # type: ignore[arg-type]
        )
        new_spec, patch, _entry = patch_domain.apply_proposal(spec, proposal)
    except patch_domain.RevisionConflictError as exc:
        raise await _conflict(session, row, expected_revision=expected_revision, operations=operations) from exc
    except (patch_domain.PatchValidationError, ValueError, PydanticValidationError) as exc:
        # 构造期与领域校验期都算入参问题：500 会把"你传错了"误报成"服务端坏了"
        raise ApiError(ErrorCode.VALIDATION, f"提案未通过领域校验: {exc}") from exc

    record = LessonPlanEditProposalRow(
        spec_id=row.id,
        status="proposed",
        source=source,
        expected_revision=expected_revision,
        base_revision=row.revision,
        summary=summary,
        instruction=instruction,
        operations=operations,
        refs=refs,
        affected=patch.affected.model_dump(mode="json"),
        actor_id=actor_id,
        idempotency_key=idempotency_key,
    )
    session.add(record)
    await session.flush()
    view = _view(record)
    view["changed_block_ids"] = sorted(patch.affected.block_ids)
    view["preview_revision"] = new_spec.revision
    return view


async def apply_proposal_by_id(
    session: AsyncSession, spec_id: str, patch_id: str, *, expected_revision: int, actor_id: str | None = None
) -> dict[str, Any]:
    row = await load_spec_row(session, spec_id, for_update=True)
    try:
        uid = uuid.UUID(patch_id)
    except (ValueError, AttributeError) as exc:
        raise ApiError(ErrorCode.NOT_FOUND, f"提案 {patch_id} 不存在") from exc
    record = (
        await session.execute(
            select(LessonPlanEditProposalRow).where(
                LessonPlanEditProposalRow.id == uid, LessonPlanEditProposalRow.spec_id == row.id
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise ApiError(ErrorCode.NOT_FOUND, f"提案 {patch_id} 不存在")
    if record.status != "proposed":
        raise ApiError(ErrorCode.CONFLICT, f"提案状态为 {record.status}，不可再 apply")
    if expected_revision != row.revision:
        raise await _conflict(session, row, expected_revision=expected_revision, operations=record.operations)

    spec = spec_of(row)
    proposal = patch_domain.LessonPlanPatchProposal(
        spec_id=spec.id,
        expected_revision=spec.revision,
        summary=record.summary,
        source=record.source,  # type: ignore[arg-type]
        operations=record.operations,  # type: ignore[arg-type]
    )
    try:
        new_spec, patch, entry = patch_domain.apply_proposal(spec, proposal)
    except (patch_domain.RevisionConflictError,) as exc:
        raise await _conflict(session, row, expected_revision=expected_revision, operations=record.operations) from exc
    except (patch_domain.PatchValidationError, ValueError) as exc:
        record.status = "failed"
        record.error = str(exc)
        record.decided_at = datetime.now(UTC)
        await session.flush()
        raise ApiError(ErrorCode.VALIDATION, f"提案 apply 时未通过校验: {exc}") from exc

    await _write_revision(
        session, row, spec=new_spec, kind="regen" if record.source == "regen" else "patch",
        base_revision=row.revision, summary=record.summary, actor_id=actor_id,
    )
    record.status = "applied"
    record.applied_revision = new_spec.revision
    record.decided_at = datetime.now(UTC)
    record.affected = patch.affected.model_dump(mode="json")
    await session.flush()
    return {
        "spec_id": spec_id,
        "revision": new_spec.revision,
        "applied_patch_id": patch.id,
        "affected": patch.affected.model_dump(mode="json"),
        "change_log_entry": entry,
    }


async def reject_proposal(session: AsyncSession, spec_id: str, patch_id: str, *, reason: str | None = None) -> dict[str, Any]:
    row = await load_spec_row(session, spec_id)
    uid = uuid.UUID(patch_id)
    record = (
        await session.execute(
            select(LessonPlanEditProposalRow).where(
                LessonPlanEditProposalRow.id == uid, LessonPlanEditProposalRow.spec_id == row.id
            )
        )
    ).scalar_one_or_none()
    if record is None:
        raise ApiError(ErrorCode.NOT_FOUND, f"提案 {patch_id} 不存在")
    if record.status != "proposed":
        raise ApiError(ErrorCode.CONFLICT, f"提案状态为 {record.status}，不可再 reject")
    record.status = "rejected"
    record.error = reason
    record.decided_at = datetime.now(UTC)
    await session.flush()
    return {"patch_id": patch_id, "status": "rejected"}


async def list_revisions(session: AsyncSession, spec_id: str) -> dict[str, Any]:
    row = await load_spec_row(session, spec_id)
    await ensure_snapshot(session, row)
    rows = (
        await session.execute(
            select(LessonPlanSpecRevisionRow)
            .where(LessonPlanSpecRevisionRow.spec_id == row.id)
            .order_by(LessonPlanSpecRevisionRow.revision)
        )
    ).scalars().all()
    out = []
    prev: dict[str, str] = {}
    for r in rows:
        cur = _block_hashes(r.body)
        changed = [] if not prev else sorted(b for b in set(prev) | set(cur) if prev.get(b) != cur.get(b))
        out.append(
            {
                "revision": r.revision,
                "kind": r.kind,
                "summary": r.summary,
                "actor_id": r.actor_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "spec_sha256": r.body_sha256,
                "base_revision": r.base_revision,
                "affected_block_ids": changed,
            }
        )
        prev = cur
    return {"spec_id": spec_id, "current_revision": row.revision, "revisions": out}


async def revert_to_revision(
    session: AsyncSession, spec_id: str, target_revision: int, *, expected_revision: int,
    actor_id: str | None = None, summary: str | None = None,
) -> dict[str, Any]:
    row = await load_spec_row(session, spec_id, for_update=True)
    if expected_revision != row.revision:
        raise await _conflict(session, row, expected_revision=expected_revision, operations=[])
    snap = await _snapshot(session, row.id, target_revision)
    if snap is None:
        raise ApiError(ErrorCode.NOT_FOUND, f"教案 {spec_id} 没有 revision {target_revision} 的快照")
    restored = LessonPlanSpec.model_validate(snap.body)
    new_revision = row.revision + 1
    revert_summary = summary or f"撤销回版本 {target_revision}"
    restored = restored.model_copy(
        update={
            "revision": new_revision,
            "change_log": [
                *(restored.change_log or []),
                ChangeLogEntry(revision=new_revision, ts=datetime.now(UTC), kind="revert", summary=revert_summary),
            ],
        },
        deep=True,
    )
    await _write_revision(
        session, row, spec=restored, kind="revert", base_revision=row.revision,
        summary=revert_summary, actor_id=actor_id,
    )
    return {
        "spec_id": spec_id,
        "revision": new_revision,
        "reverted_to": target_revision,
        "affected": {"block_ids": [], "whole_plan": True, "rationale": revert_summary},
    }


def spec_view(row: Any) -> dict[str, Any]:
    spec = LessonPlanSpec.model_validate(row.body)
    blocks = patch_domain.lesson_plan_blocks(spec)
    return {
        "spec_id": str(row.id),
        "revision": row.revision,
        "schema_version": row.schema_version,
        "spec_sha256": canonical_sha256(row.body),
        "metadata": spec.metadata.model_dump(mode="json"),
        "spec": row.body,
        "blocks": blocks,
        "block_count": len(blocks),
        "lineage": {
            "approved_blueprint_id": str(row.blueprint_id),
            "blueprint_revision": row.approved_blueprint_revision,
            "context_snapshot_hash": row.context_snapshot_hash,
            "prompt_ref": (row.lineage or {}).get("prompt_ref"),
            "evidence_refs": list((row.lineage or {}).get("evidence_refs") or []),
            "ai_run_id": (row.lineage or {}).get("ai_run_id"),
        },
        "recent_change_log": (row.body.get("change_log") or [])[-5:],
    }
