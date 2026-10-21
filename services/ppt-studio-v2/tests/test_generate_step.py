"""ppt.deck.generate step（R21）：页级 checkpoint / 局部重试 / 取消 / 预算 / 单产物。"""
from __future__ import annotations

import base64
import uuid
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.artifact_store import LocalArtifactStore
from app.config import Settings
from app.decks import ingest_deck
from app.persistence.models import PptV2Artifact, PptV2DeckPage, PptV2Job
from app.reliability.jobs import JobEngine
from app.reliability.steps import DeckCancelled, StepContext, StepFailure, run_step
from tests._r21_helpers import Fake8201, build_spec, sha


def _settings(tmp_path) -> Settings:
    return Settings(artifact_root=str(tmp_path / "artifacts"))


async def _mk_deck(session, tmp_path, *, with_assets=False) -> str:  # noqa: ANN001
    import app.artifact_store as astore

    orig = astore.get_settings
    astore.get_settings = lambda: _settings(tmp_path)  # type: ignore[assignment]
    try:
        res = await ingest_deck(session, build_spec(with_assets=with_assets))
        await session.commit()
        return res["deck_id"]
    finally:
        astore.get_settings = orig  # type: ignore[assignment]


async def _mk_job(session, eng: JobEngine, deck_id: str) -> str:  # noqa: ANN001
    job = await eng.create(session, job_type="ppt.deck.generate", payload={"deck_id": deck_id},
                           max_attempts=3, timeout_s=1800, dedupe_key=f"t:{uuid4().hex}")
    await session.commit()
    return job["id"]


def _ctx(session, eng, job_id, fake, tmp_path) -> StepContext:  # noqa: ANN001
    return StepContext(presenton=fake, session=session, engine=eng, job_id=job_id,
                       claim_token="tok", settings=_settings(tmp_path))


async def test_generate_happy_path(session, tmp_path) -> None:  # noqa: ANN001
    eng = JobEngine(lease_seconds=600)
    deck_id = await _mk_deck(session, tmp_path)
    job_id = await _mk_job(session, eng, deck_id)
    fake = Fake8201()
    res = await run_step("ppt.deck.generate", {"deck_id": deck_id}, _ctx(session, eng, job_id, fake, tmp_path))
    assert res["summary"] == {"total_pages": 8, "rendered": 8, "checkpoint_reused": 0}
    assert res["artifact"]["size_bytes"] > 0
    assert res["openable"]["opened"] is True
    assert len(res["pages"]) == 8
    # 产物登记：8×2 页级 + DECK_PPTX + CONTACT_SHEET = 18
    rows = (await session.execute(select(PptV2Artifact))).scalars().all()
    kinds = sorted(r.kind for r in rows)
    assert kinds.count("PAGE_FRAGMENT") == 8 and kinds.count("PAGE_PNG") == 8
    assert kinds.count("DECK_PPTX") == 1 and kinds.count("CONTACT_SHEET") == 1
    # 8200 本地留存文件存在且哈希一致
    deck_row = next(r for r in rows if r.kind == "DECK_PPTX")
    store = LocalArtifactStore(root=_settings(tmp_path).artifact_root)
    blob = store.get(deck_row.object_key)
    assert blob and LocalArtifactStore.sha256_bytes(blob) == deck_row.sha256


async def test_partial_retry_reuses_checkpoint(session, tmp_path) -> None:  # noqa: ANN001
    """8201 第 3 页一次瞬态失败 → attempt1 失败（可重试）→ attempt2 只补第 3 页；其余页零重建。"""
    eng = JobEngine(lease_seconds=600)
    deck_id = await _mk_deck(session, tmp_path)
    job_id = await _mk_job(session, eng, deck_id)
    fake = Fake8201()
    fake.fail = {3: "transient"}
    fake.fail_once = {3}
    with pytest.raises(StepFailure) as ei:
        await run_step("ppt.deck.generate", {"deck_id": deck_id}, _ctx(session, eng, job_id, fake, tmp_path))
    assert ei.value.retriable is True
    # attempt 2：1/2/4…8 页命中 checkpoint，仅第 3 页重新渲染
    res = await run_step("ppt.deck.generate", {"deck_id": deck_id}, _ctx(session, eng, job_id, fake, tmp_path))
    assert res["summary"]["checkpoint_reused"] == 7
    assert res["summary"]["rendered"] == 1
    assert fake.render_calls[3] == 2  # 失败一次 + 重试一次
    assert all(fake.render_calls.get(p, 0) == 1 for p in (1, 2, 4, 5, 6, 7, 8))


async def test_checkpoint_persists_across_step_runs(session, tmp_path) -> None:  # noqa: ANN001
    """两次独立 step 执行（模拟 kill 后新 attempt）：第二遍全部命中 checkpoint。"""
    eng = JobEngine(lease_seconds=600)
    deck_id = await _mk_deck(session, tmp_path)
    job_id = await _mk_job(session, eng, deck_id)
    fake = Fake8201()
    await run_step("ppt.deck.generate", {"deck_id": deck_id}, _ctx(session, eng, job_id, fake, tmp_path))
    calls_after_first = sum(fake.render_calls.values())
    res2 = await run_step("ppt.deck.generate", {"deck_id": deck_id}, _ctx(session, eng, job_id, fake, tmp_path))
    assert res2["summary"]["checkpoint_reused"] == 8
    assert sum(fake.render_calls.values()) == calls_after_first  # 零重建


async def test_bad_page_fails_non_retriable_and_keeps_pages(session, tmp_path) -> None:  # noqa: ANN001
    """坏页（QA 不合格）→ 继续渲染其余页 → 非瞬态 FAILED；已完成页 checkpoint 保留（不丢整套）。"""
    eng = JobEngine(lease_seconds=600)
    deck_id = await _mk_deck(session, tmp_path)
    job_id = await _mk_job(session, eng, deck_id)
    fake = Fake8201()
    fake.fail = {5: "qa"}
    with pytest.raises(StepFailure) as ei:
        await run_step("ppt.deck.generate", {"deck_id": deck_id}, _ctx(session, eng, job_id, fake, tmp_path))
    assert ei.value.retriable is False
    assert "1/8 页失败" in ei.value.message
    rows = (await session.execute(select(PptV2DeckPage)
                                  .where(PptV2DeckPage.status == "COMPLETED"))).scalars().all()
    assert {r.page_no for r in rows} == {1, 2, 3, 4, 6, 7, 8}  # 其余 7 页全部保留
    failed = (await session.execute(select(PptV2DeckPage)
                                    .where(PptV2DeckPage.status == "FAILED"))).scalars().all()
    assert len(failed) == 1 and failed[0].page_no == 5 and "overflow" in (failed[0].last_error or "")


async def test_engine_version_change_forces_rerender(session, tmp_path) -> None:  # noqa: ANN001
    eng = JobEngine(lease_seconds=600)
    deck_id = await _mk_deck(session, tmp_path)
    job_id = await _mk_job(session, eng, deck_id)
    fake = Fake8201()
    await run_step("ppt.deck.generate", {"deck_id": deck_id}, _ctx(session, eng, job_id, fake, tmp_path))
    fake.version = "r21-engine-2.0.0"  # 引擎升级 → checkpoint 失效 → 全量重渲染
    res2 = await run_step("ppt.deck.generate", {"deck_id": deck_id}, _ctx(session, eng, job_id, fake, tmp_path))
    assert res2["summary"]["checkpoint_reused"] == 0
    assert res2["summary"]["rendered"] == 8


async def test_cancel_midrun_raises_and_job_cancellable(session, tmp_path) -> None:  # noqa: ANN001
    """页间取消：DeckCancelled 冒泡；worker 层转 CANCELLED；已完成页保留。"""
    eng = JobEngine(lease_seconds=600)
    deck_id = await _mk_deck(session, tmp_path)
    job_id = await _mk_job(session, eng, deck_id)
    # 预置 cancel_requested（第 2 页前生效）
    job = await session.get(PptV2Job, uuid.UUID(job_id))
    job.cancel_requested = True
    await session.commit()
    fake = Fake8201()
    with pytest.raises(DeckCancelled):
        await run_step("ppt.deck.generate", {"deck_id": deck_id}, _ctx(session, eng, job_id, fake, tmp_path))
    assert fake.render_calls == {}  # 取消点在每页之前 → 第 1 页前即取消，零渲染
    assert fake.assemble_calls == 0  # 不会走到装配


async def test_page_size_budget_via_store(session, tmp_path) -> None:  # noqa: ANN001
    """资产字节必须先入 8200 store（ingest assets_inline + 哈希一致性闸门）。"""
    import app.artifact_store as astore

    astore.get_settings = lambda: _settings(tmp_path)  # type: ignore[assignment]
    try:
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 512
        spec = build_spec(with_assets=True)
        spec["assets"][0]["sha256"] = sha(png)  # AssetRef 必须与真实字节哈希一致
        res = await ingest_deck(session, spec, {"asset-fig-1": base64.b64encode(png).decode()})
        await session.commit()
        assert res["assets"][0]["object_key"] == f"pps2/assets/{sha(png)}"
        rows = (await session.execute(select(PptV2Artifact)
                                      .where(PptV2Artifact.kind == "ASSET"))).scalars().all()
        assert len(rows) == 1 and rows[0].size_bytes == len(png)
        # 哈希不一致 → 拒绝
        from app.errors import ApiError

        bad = build_spec(with_assets=True)
        with pytest.raises(ApiError) as ei:
            await ingest_deck(session, bad, {"asset-fig-1": base64.b64encode(b"tampered").decode()})
        assert ei.value.error_key == "asset_hash_mismatch"
    finally:
        pass
