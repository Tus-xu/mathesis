"""deck ingest 闸门 + 幂等（R21）。"""
from __future__ import annotations

import base64

import pytest

from app.decks import ingest_deck
from app.errors import ApiError
from tests._r21_helpers import build_spec


async def test_ingest_happy_path(session) -> None:  # noqa: ANN001
    res = await ingest_deck(session, build_spec())
    assert res["deduped"] is False
    assert res["page_count"] == 8
    assert res["projection"]["approved_blueprint_id"]
    assert len(res["profile_citations"]) == 2
    for c in res["profile_citations"]:
        assert {"sample", "window", "coverage", "confidence"} <= set(c)


async def test_ingest_requires_approved_projection(session) -> None:  # noqa: ANN001
    with pytest.raises(ApiError) as ei:
        await ingest_deck(session, build_spec(bad={"not_approved": True}))
    assert ei.value.error_key == "deck_not_approved"


async def test_ingest_rejects_page_refs(session) -> None:  # noqa: ANN001
    with pytest.raises(ApiError) as ei:
        await ingest_deck(session, build_spec(bad={"page_refs": True}))
    assert ei.value.error_key == "page_refs_forbidden"


async def test_ingest_rejects_non_locator(session) -> None:  # noqa: ANN001
    with pytest.raises(ApiError) as ei:
        await ingest_deck(session, build_spec(bad={"locator": True}))
    assert ei.value.error_key == "locator_format"


async def test_ingest_rejects_incomplete_profile_citation(session) -> None:  # noqa: ANN001
    with pytest.raises(ApiError) as ei:
        await ingest_deck(session, build_spec(bad={"profile": True}))
    assert ei.value.error_key == "profile_citation_invalid"


@pytest.mark.parametrize("n", [7, 13])
async def test_ingest_rejects_page_budget(session, n: int) -> None:  # noqa: ANN001
    with pytest.raises(ApiError) as ei:
        await ingest_deck(session, build_spec(bad={"pages": n}))
    assert ei.value.error_key == "page_budget_violation"


async def test_ingest_rejects_ghost_asset_ref(session) -> None:  # noqa: ANN001
    with pytest.raises(ApiError) as ei:
        await ingest_deck(session, build_spec(with_assets=True, bad={"asset_ref_missing": True}))
    assert ei.value.error_key in ("spec_invalid", "asset_ref_missing")


async def test_ingest_idempotent_same_spec(session) -> None:  # noqa: ANN001
    spec = build_spec()
    first = await ingest_deck(session, spec)
    await session.commit()
    second = await ingest_deck(session, spec)
    assert first["deduped"] is False and second["deduped"] is True
    assert first["deck_id"] == second["deck_id"]
    assert first["spec_sha256"] == second["spec_sha256"]


async def test_ingest_same_asset_bytes_across_decks_no_500(session, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """质量根因 #6 回归：不同 deck 携带同一内容寻址资产 → ASSET 登记幂等，禁止 uq 冲突 500。"""
    import app.artifact_store as astore
    from app.config import Settings as S

    monkeypatch.setattr(astore, "get_settings", lambda: S(artifact_root=str(tmp_path / "artifacts")))
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 256
    spec_a = build_spec(with_assets=True)
    spec_a["assets"][0]["sha256"] = sha_bytes(png)
    spec_a["revision"] = 1
    spec_b = build_spec(with_assets=True)
    spec_b["assets"][0]["sha256"] = sha_bytes(png)
    spec_b["revision"] = 2  # 不同 spec（新 deck），同一资产字节
    b64 = base64.b64encode(png).decode()
    res_a = await ingest_deck(session, spec_a, {"asset-fig-1": b64})
    await session.commit()
    res_b = await ingest_deck(session, spec_b, {"asset-fig-1": b64})  # 修复前：IntegrityError→500
    await session.commit()
    assert res_a["deck_id"] != res_b["deck_id"]
    assert res_a["assets"][0]["object_key"] == res_b["assets"][0]["object_key"]


def sha_bytes(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


async def test_ingest_duplicate_asset_content_same_deck_idempotent(session, tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """R21.1 缺陷 B 回归：同 deck 两个 AssetRef 同内容（不同 id、同 object_key）→
    幂等登记（artifact 1 行 / asset record 1 行），禁止 uq_pptasset_deck_key → 500。"""
    import json

    from sqlalchemy import func, select

    import app.artifact_store as astore
    from app.config import Settings as S
    from app.persistence.models import PptV2Artifact, PptV2AssetRecord

    monkeypatch.setattr(astore, "get_settings", lambda: S(artifact_root=str(tmp_path / "artifacts")))
    png = b"\x89PNG\r\n\x1a\n" + b"y" * 256
    spec = build_spec(with_assets=True)
    spec["assets"][0]["sha256"] = sha_bytes(png)
    spec["assets"][0]["object_key"] = f"pps2/assets/{sha_bytes(png)}"
    dup = json.loads(json.dumps(spec["assets"][0]))
    dup["id"] = "asset-fig-1-dup"
    spec["assets"].append(dup)
    spec["slides"][2]["assets"] = ["asset-fig-1", "asset-fig-1-dup"]
    b64 = base64.b64encode(png).decode()
    res = await ingest_deck(session, spec, {"asset-fig-1": b64, "asset-fig-1-dup": b64})
    await session.commit()
    keys = [a["object_key"] for a in res["assets"]]
    assert len(keys) == 2 and keys[0] == keys[1]
    assert res["assets"][1].get("deduped") is True
    n_art = (await session.execute(
        select(func.count()).select_from(PptV2Artifact).where(PptV2Artifact.object_key == keys[0])
    )).scalar_one()
    n_rec = (await session.execute(
        select(func.count()).select_from(PptV2AssetRecord).where(PptV2AssetRecord.object_key == keys[0])
    )).scalar_one()
    assert n_art == 1, f"artifact 应 1 行，实际 {n_art}"
    assert n_rec == 1, f"asset record 应 1 行，实际 {n_rec}"


def test_object_key_conflict_classifier_scope() -> None:
    """R21.1 缺陷 B 防御范围：仅 object_key/deck_key 唯一冲突被视为幂等，其余唯一冲突一律不吞。"""
    from sqlalchemy.exc import IntegrityError

    from app.decks import _is_object_key_unique_conflict

    pg_art = IntegrityError("s", {}, Exception('duplicate key value violates unique constraint "uq_pptartifact_object_key"'))
    assert _is_object_key_unique_conflict(pg_art, "ppt_v2_artifacts")
    lite_art = IntegrityError("s", {}, Exception("UNIQUE constraint failed: ppt_v2_artifacts.object_key"))
    assert _is_object_key_unique_conflict(lite_art, "ppt_v2_artifacts")
    pg_asset = IntegrityError("s", {}, Exception('duplicate key value violates unique constraint "uq_pptasset_deck_key"'))
    assert _is_object_key_unique_conflict(pg_asset, "ppt_v2_assets")
    lite_asset = IntegrityError("s", {}, Exception(
        "UNIQUE constraint failed: ppt_v2_assets.deck_id, ppt_v2_assets.object_key"))
    assert _is_object_key_unique_conflict(lite_asset, "ppt_v2_assets")
    other = IntegrityError("s", {}, Exception('duplicate key value violates unique constraint "uq_pptjobs_dedupe"'))
    assert not _is_object_key_unique_conflict(other, "ppt_v2_artifacts")
    assert not _is_object_key_unique_conflict(other, "ppt_v2_assets")


async def test_ingest_rejects_schema_invalid(session) -> None:  # noqa: ANN001
    spec = build_spec()
    spec["schema_version"] = "9.9.9"  # 未知 major → fail closed
    with pytest.raises(ApiError) as ei:
        await ingest_deck(session, spec)
    assert ei.value.error_key == "spec_invalid"
