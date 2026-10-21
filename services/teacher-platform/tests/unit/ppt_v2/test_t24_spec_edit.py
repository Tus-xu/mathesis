"""T24 deck 规格编辑：提案 / 409 / 修订 / 撤销 / 局部性不变量。

这些断言的目的是让"静默覆盖"和"顺手改了别的页"在测试层面不可能通过。
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.core.errors import ApiError
from app.ppt_v2.persistence.models import PptV2DeckSpecRecord, PptV2EditProposal, PptV2SpecRevision
from app.ppt_v2.service import spec_edit

from .conftest import golden_spec


async def _seed_deck(db, *, revision: int = 3) -> PptV2DeckSpecRecord:
    raw = golden_spec()
    raw["revision"] = revision
    row = PptV2DeckSpecRecord(
        school_id=None, schema_version=raw["schema_version"], revision=revision, spec=raw
    )
    db.add(row)
    await db.flush()
    return row


def _norm_slide(s: dict) -> dict:
    """契约演进的语义归一化（用于「只动目标页」哈希比较）。

    - R4：SlideSpec 新增可选 math_object（缺省 None）；
    - §A：SlideSpec 新增可选 elements（缺省 []）。
    老 fixture（无这些键）与新 model_dump（含缺省值）是同一语义，须归一化后再比。
    """
    out = dict(s)
    if out.get("math_object") is None and "math_object" in out:
        out.pop("math_object")
    if out.get("elements") == [] and "elements" in out:
        out.pop("elements")
    return out


def _slide_hashes(spec_dict: dict) -> dict[str, str]:
    return {s["id"]: spec_edit.canonical_sha256(_norm_slide(s)) for s in spec_dict["slides"]}


_BP_ID = "11111111-2222-3333-4444-555555555555"
_HEX64 = "a" * 64


def _valid_projection() -> dict:
    """合法投影血缘：projection 是"已确认上游事实"的载体，形状错了就不该被接受。"""
    return {
        "schema_version": "1.0.0",
        "projector": "teacher_prep.specs.v1",
        "approved_blueprint_id": _BP_ID,
        "approved_blueprint_revision": 2,
        "context_snapshot_hash": _HEX64,
        "prompt_release_id": "pr-1",
        "ai_run_id": "run-1",
        "model": "fake-model",
        "run_hash": "b" * 64,
        "body_hash": "c" * 64,
        "projected_at": "2026-09-13T00:00:00Z",
    }


def _regen_refs() -> dict:
    return {
        "approved_blueprint_id": _BP_ID,
        "blueprint_revision": 2,
        "spec_revision": 3,
        "prompt_ref": {"prompt_id": "p-deck", "version": "1.0.0", "hash": "h"},
        "context_manifest_id": "ctx-1",
        "evidence_refs": ["textbook_v2/locator/v1/source/x/section/y/chunk/z"],
    }


async def _seed_deck_with_projection(db, *, revision: int = 3):
    row = await _seed_deck(db, revision=revision)
    raw = dict(row.spec)
    raw["projection"] = _valid_projection()
    row.spec = raw
    await db.flush()
    return row


async def test_propose_then_apply_bumps_revision_and_only_touches_target(db) -> None:
    row = await _seed_deck(db)
    before = _slide_hashes(row.spec)

    proposal = await spec_edit.create_proposal(
        db,
        str(row.id),
        expected_revision=3,
        summary="改写第 3 页正文",
        source="human",
        operations=[{"op": "set_slide", "target": "s3", "field": "content", "value": "新正文（T24）"}],
        actor_id="teacher-a",
    )
    assert proposal["status"] == "proposed"
    assert proposal["changed_slide_ids"] == ["s3"]

    # 提案阶段不得改事实（INV-4）
    assert row.revision == 3
    after_propose = _slide_hashes(row.spec)
    assert after_propose == before

    applied = await spec_edit.apply_proposal_by_id(
        db, str(row.id), proposal["patch_id"], expected_revision=3, actor_id="teacher-a"
    )
    assert applied["revision"] == 4

    after = _slide_hashes(row.spec)
    changed = {sid for sid in before if before[sid] != after[sid]}
    assert changed == {"s3"}, f"只有目标页可变，实际变化: {changed}"
    assert "新正文（T24）" in json.dumps(row.spec["slides"][2], ensure_ascii=False)

    snaps = (await db.execute(select(PptV2SpecRevision))).scalars().all()
    assert sorted(s.revision for s in snaps) == [3, 4]
    kinds = {s.revision: s.kind for s in snaps}
    assert kinds[3] == "baseline" and kinds[4] == "patch"


async def test_apply_with_stale_expected_revision_is_409_and_has_no_side_effect(db) -> None:
    row = await _seed_deck(db)
    first = await spec_edit.create_proposal(
        db, str(row.id), expected_revision=3, summary="A 的修改", source="human",
        operations=[{"op": "set_slide", "target": "s2", "field": "content", "value": "A 写了这句"}],
        actor_id="teacher-a",
    )
    await spec_edit.apply_proposal_by_id(db, str(row.id), first["patch_id"], expected_revision=3, actor_id="teacher-a")
    assert row.revision == 4

    second = await spec_edit.create_proposal(
        db, str(row.id), expected_revision=4, summary="B 的修改", source="human",
        operations=[{"op": "set_slide", "target": "s5", "field": "content", "value": "B 写了那句"}],
        actor_id="teacher-b",
    )
    spec_before = json.dumps(row.spec, sort_keys=True)
    proposals_before = len((await db.execute(select(PptV2EditProposal.id))).all())

    with pytest.raises(ApiError) as exc:
        await spec_edit.apply_proposal_by_id(
            db, str(row.id), second["patch_id"], expected_revision=3, actor_id="teacher-b"
        )
    err = exc.value
    assert err.status_code == 409 and err.code.name == "CONFLICT"

    payload = err.data
    assert payload["expected_revision"] == 3
    assert payload["current_revision"] == 4
    assert any(f["target"] == "s5" for f in payload["diff"]["fields"])
    assert "s2" in payload["diff"]["slide_ids"], "冲突载荷必须指出服务端已变化的页"

    # 零副作用
    assert row.revision == 4
    assert json.dumps(row.spec, sort_keys=True) == spec_before
    assert len((await db.execute(select(PptV2EditProposal.id))).all()) == proposals_before
    assert sorted(s.revision for s in (await db.execute(select(PptV2SpecRevision))).scalars().all()) == [3, 4]


async def test_double_apply_is_rejected(db) -> None:
    row = await _seed_deck(db)
    p = await spec_edit.create_proposal(
        db, str(row.id), expected_revision=3, summary="一次", source="human",
        operations=[{"op": "set_slide", "target": "s1", "field": "content", "value": "x"}],
    )
    await spec_edit.apply_proposal_by_id(db, str(row.id), p["patch_id"], expected_revision=3)
    with pytest.raises(ApiError) as exc:
        await spec_edit.apply_proposal_by_id(db, str(row.id), p["patch_id"], expected_revision=4)
    assert exc.value.status_code == 409


async def test_reject_keeps_facts_unchanged(db) -> None:
    row = await _seed_deck(db)
    p = await spec_edit.create_proposal(
        db, str(row.id), expected_revision=3, summary="会被拒", source="human",
        operations=[{"op": "set_slide", "target": "s4", "field": "content", "value": "y"}],
    )
    before = json.dumps(row.spec, sort_keys=True)
    out = await spec_edit.reject_proposal(db, str(row.id), p["patch_id"], reason="教师放弃")
    assert out["status"] == "rejected"
    assert row.revision == 3
    assert json.dumps(row.spec, sort_keys=True) == before


async def test_client_base_mismatch_is_409(db) -> None:
    row = await _seed_deck(db)
    with pytest.raises(ApiError) as exc:
        await spec_edit.create_proposal(
            db, str(row.id), expected_revision=3, summary="底稿不对", source="human",
            operations=[{"op": "set_slide", "target": "s3", "field": "content",
                         "value": "z", "before": "这段根本不是服务端的旧值"}],
        )
    assert exc.value.status_code == 409
    assert exc.value.data["kind"] == "client_base_mismatch"


async def test_invalid_op_is_422_and_projection_is_immutable(db) -> None:
    row = await _seed_deck(db)
    with pytest.raises(ApiError) as exc:
        await spec_edit.create_proposal(
            db, str(row.id), expected_revision=3, summary="改 projection", source="human",
            operations=[{"op": "set_metadata", "field": "approved_blueprint_id", "value": "forge"}],
        )
    assert exc.value.status_code == 422


async def test_revert_creates_new_revision_and_keeps_history(db) -> None:
    row = await _seed_deck(db)
    original = json.dumps(_norm_slide(row.spec["slides"][2]), sort_keys=True)
    p = await spec_edit.create_proposal(
        db, str(row.id), expected_revision=3, summary="改 s3", source="human",
        operations=[{"op": "set_slide", "target": "s3", "field": "content", "value": "改过的正文"}],
    )
    await spec_edit.apply_proposal_by_id(db, str(row.id), p["patch_id"], expected_revision=3)
    assert json.dumps(_norm_slide(row.spec["slides"][2]), sort_keys=True) != original

    out = await spec_edit.revert_to_revision(db, str(row.id), 3, expected_revision=4, actor_id="teacher-a")
    assert out["revision"] == 5 and out["reverted_to"] == 3
    assert json.dumps(_norm_slide(row.spec["slides"][2]), sort_keys=True) == original

    listing = await spec_edit.list_revisions(db, str(row.id))
    assert [r["revision"] for r in listing["revisions"]] == [3, 4, 5]
    assert listing["revisions"][-1]["kind"] == "revert"
    # 历史必须完整保留（撤销不是删除）
    assert listing["revisions"][1]["kind"] == "patch"


async def test_regen_scope_is_target_slide_only(db) -> None:
    """局部重生成只允许落在目标页；越界必须中止。"""
    from app.ppt_v2.service import regen as regen_service

    row = await _seed_deck_with_projection(db)

    class _Fake:
        async def chat(self, messages, *, use_case="chat", temperature=None, model=None):  # noqa: ANN001
            from app.ppt_v2.adapters.dto import ChatReply

            return ChatReply(text=json.dumps({"content": "重生成后的第 5 页正文"}), model="fake-model")

    refs = _regen_refs()
    before = _slide_hashes(row.spec)
    out = await regen_service.regenerate_slide(
        db, str(row.id), expected_revision=3, slide_id="s5",
        instruction="把这句话说得更适合板书", refs=refs, actor_id="teacher-a", adapter=_Fake(),
    )
    assert out["changed_slide_ids"] == ["s5"]
    assert out["status"] == "proposed"
    # 重生成也必须是提案，不得直接改事实
    assert row.revision == 3
    assert _slide_hashes(row.spec) == before
    # projection（上游事实）不得被改写
    assert row.spec["projection"] == _valid_projection()


async def test_regen_rejects_blueprint_swap(db) -> None:
    from app.ppt_v2.service import regen as regen_service

    row = await _seed_deck_with_projection(db)

    class _Fake:
        async def chat(self, *a, **k):  # noqa: ANN002, ANN003
            raise AssertionError("不该调用 provider")

    with pytest.raises(ApiError) as exc:
        await regen_service.regenerate_slide(
            db, str(row.id), expected_revision=3, slide_id="s5", instruction="x",
            refs={**_regen_refs(), "approved_blueprint_id": "99999999-9999-9999-9999-999999999999"},
            adapter=_Fake(),
        )
    assert exc.value.status_code == 409


async def test_regen_provider_failure_is_503_and_writes_nothing(db) -> None:
    from app.ppt_v2.service import regen as regen_service

    row = await _seed_deck_with_projection(db)

    class _Boom:
        async def chat(self, *a, **k):  # noqa: ANN002, ANN003
            raise TimeoutError("provider down")

    with pytest.raises(ApiError) as exc:
        await regen_service.regenerate_slide(
            db, str(row.id), expected_revision=3, slide_id="s5", instruction="x",
            refs=_regen_refs(),
            adapter=_Boom(),
        )
    assert exc.value.status_code == 503
    assert len((await db.execute(select(PptV2EditProposal.id))).all()) == 0
