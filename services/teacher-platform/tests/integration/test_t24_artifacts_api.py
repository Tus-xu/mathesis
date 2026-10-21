"""T24 真实 API 集成测试：真实 PG + 真实 MinIO + 真实签名票据。

为什么用真实基础设施而不是 SQLite/mock：本轮的核心断言是"409 真的发生了"、
"短时链接真的会失效"、"产物的字节真的和登记 hash 一致"——这些在 mock 上不成立。

全程同一个事件循环（async + ASGITransport）：混用 anyio.run 会让 asyncpg 连接跨 loop，
把真实失败伪装成 Event loop is closed。

自清理：本文件创建的 deck / artifact / 票据在 teardown 删除，不污染 dev 库。
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import jwt
import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.db.session import session_factory
from app.ppt_v2.persistence.models import PptV2DeckSpecRecord, PptV2EditProposal, PptV2SpecRevision
from app.storage.models import TeacherArtifact, TeacherArtifactDownloadAudit, TeacherArtifactTicket

_FIXTURE = Path(__file__).resolve().parents[1] / "unit" / "ppt_v2" / "fixtures" / "deck_golden_a.json"
_TEACHER_ID = "55555555-6666-7777-8888-999999999999"


def _token(teacher_id: str = _TEACHER_ID) -> str:
    settings = get_settings()
    return jwt.encode(
        {
            "sub": teacher_id,
            "name": "T24 验收教师",
            "role": "teacher",
            "exp": datetime.now(UTC) + timedelta(minutes=30),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _spec() -> dict:
    raw = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    raw["revision"] = 3
    return raw


@pytest_asyncio.fixture(autouse=True)
async def _reset_db_engine():
    """每个测试一个事件循环：模块级 engine/pool 必须随循环释放，否则下一个测试拿到已关闭 loop 的连接。"""
    yield
    from app.db.session import close_engine

    await close_engine()


@pytest_asyncio.fixture
async def client():
    from main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://t24.test",
        headers={"Authorization": f"Bearer {_token()}"},
    ) as c:
        yield c


@pytest_asyncio.fixture
async def seeded_deck():
    deck_id = uuid.uuid4()
    async with session_factory()() as session:
        session.add(
            PptV2DeckSpecRecord(id=deck_id, school_id=None, schema_version="2.0.0", revision=3, spec=_spec())
        )
        await session.commit()
    yield str(deck_id)
    async with session_factory()() as session:
        await session.execute(delete(PptV2EditProposal).where(PptV2EditProposal.deck_id == deck_id))
        await session.execute(delete(PptV2SpecRevision).where(PptV2SpecRevision.deck_id == deck_id))
        row = (
            await session.execute(select(PptV2DeckSpecRecord).where(PptV2DeckSpecRecord.id == deck_id))
        ).scalar_one_or_none()
        if row is not None:
            await session.delete(row)
        await session.commit()


async def test_deck_read_and_patch_apply_then_stale_409(client: httpx.AsyncClient, seeded_deck: str) -> None:
    deck_id = seeded_deck
    r = await client.get(f"/api/teacher-v3/artifacts/decks/{deck_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 0
    assert body["data"]["revision"] == 3
    assert body["data"]["slide_count"] == 8

    # 标签 A：提案 → 确认（写服务端）
    r = await client.post(
        f"/api/teacher-v3/artifacts/decks/{deck_id}/patches",
        json={
            "expected_revision": 3,
            "summary": "A 标签页改写第 2 页",
            "source": "human",
            "operations": [{"op": "set_slide", "target": "s2", "field": "content", "value": "A 的正文"}],
        },
    )
    assert r.status_code == 200, r.text
    patch_id = r.json()["data"]["patch_id"]
    assert r.json()["data"]["changed_slide_ids"] == ["s2"]
    # 提案阶段不得改事实（INV-4）
    r = await client.get(f"/api/teacher-v3/artifacts/decks/{deck_id}")
    assert r.json()["data"]["revision"] == 3

    r = await client.post(
        f"/api/teacher-v3/artifacts/decks/{deck_id}/patches/{patch_id}/apply",
        json={"expected_revision": 3},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["revision"] == 4

    # 标签 B：基于旧版本提交 → 必须 409 且带服务端 diff（不是 last-write-wins）
    r = await client.post(
        f"/api/teacher-v3/artifacts/decks/{deck_id}/patches",
        json={
            "expected_revision": 3,
            "summary": "B 标签页也在改同一页",
            "source": "human",
            "operations": [{"op": "set_slide", "target": "s2", "field": "content", "value": "B 的正文"}],
        },
    )
    assert r.status_code == 409, r.text
    payload = r.json()
    assert payload["code"] == 40900
    conflict = payload["data"]
    assert conflict["expected_revision"] == 3 and conflict["current_revision"] == 4
    assert conflict["diff"]["slide_ids"] == ["s2"]
    assert any(f["target"] == "s2" for f in conflict["diff"]["fields"])
    assert conflict["server_snapshot"]["revision"] == 4

    # 冲突不得产生副作用
    r = await client.get(f"/api/teacher-v3/artifacts/decks/{deck_id}")
    assert r.json()["data"]["revision"] == 4

    # 撤销到版本 3 → 新 revision 5（历史保留）
    r = await client.post(
        f"/api/teacher-v3/artifacts/decks/{deck_id}/revisions/3/revert",
        json={"expected_revision": 4, "summary": "撤销 A 的修改"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["revision"] == 5

    r = await client.get(f"/api/teacher-v3/artifacts/decks/{deck_id}/revisions")
    revs = r.json()["data"]["revisions"]
    assert [x["revision"] for x in revs] == [3, 4, 5]
    assert [x["kind"] for x in revs] == ["baseline", "patch", "revert"]


async def test_artifact_ticket_download_and_invalidation(client: httpx.AsyncClient) -> None:
    """真实 MinIO + 真实签名票据：下载成功 → 单次使用 → 撤权失效 + 审计留痕。"""
    from app.storage.artifacts import register_artifact
    from app.storage.minio import get_storage

    artifact_id = None
    try:
        async with session_factory()() as session:
            row = await register_artifact(
                session,
                store=get_storage(),
                spec_kind="teaching_deck",
                spec_id="t24-integration",
                spec_revision=3,
                kind="pdf",
                media_type="application/pdf",
                data=b"%PDF-1.7\nT24 integration artifact\n%%EOF\n",
                filename="T24-集成.pdf",
                owner_id=uuid.UUID(_TEACHER_ID),
                created_by_run_id="task:t24-integration",
                source_lineage={"spec_kind": "teaching_deck", "derived_from": []},
            )
            await session.commit()
            artifact_id = str(row.id)

        r = await client.get(f"/api/teacher-v3/artifacts/{artifact_id}")
        assert r.status_code == 200, r.text
        view = r.json()["data"]
        assert view["content_sha256"] and view["size_bytes"] > 0
        assert view["source_lineage"]["spec_kind"] == "teaching_deck"
        assert view["created_by_run_id"] == "task:t24-integration"
        assert view["download"]["ticket_required"] is True

        r = await client.post(f"/api/teacher-v3/artifacts/{artifact_id}/tickets", json={"ttl_seconds": 60})
        assert r.status_code == 200, r.text
        ticket = r.json()["data"]
        assert ticket["single_use"] is True and ticket["ttl_seconds"] == 60

        r = await client.get(f"/api/teacher-v3/artifacts/downloads/{ticket['ticket']}")
        assert r.status_code == 200, r.text
        assert r.content.startswith(b"%PDF-")
        assert r.headers["x-content-sha256"] == view["content_sha256"]

        # 单次使用：第二次必须 410
        r = await client.get(f"/api/teacher-v3/artifacts/downloads/{ticket['ticket']}")
        assert r.status_code == 410, r.text

        # 再签一张 → 撤权 → 立即可见失效（403，而不是等自然过期）
        r = await client.post(f"/api/teacher-v3/artifacts/{artifact_id}/tickets", json={"ttl_seconds": 60})
        ticket2 = r.json()["data"]["ticket"]
        r = await client.post(f"/api/teacher-v3/artifacts/{artifact_id}/revoke")
        assert r.status_code == 200, r.text
        assert r.json()["data"]["revoked_tickets"] >= 1
        r = await client.get(f"/api/teacher-v3/artifacts/downloads/{ticket2}")
        assert r.status_code == 403, r.text

        r = await client.get(f"/api/teacher-v3/artifacts/{artifact_id}/audit")
        entries = r.json()["data"]["entries"]
        assert ("allow", "ok") in [(e["decision"], e["reason"]) for e in entries]
        assert any(e["decision"] == "deny" for e in entries)
    finally:
        if artifact_id is not None:
            aid = uuid.UUID(artifact_id)
            async with session_factory()() as session:
                await session.execute(delete(TeacherArtifactTicket).where(TeacherArtifactTicket.artifact_id == aid))
                await session.execute(
                    delete(TeacherArtifactDownloadAudit).where(TeacherArtifactDownloadAudit.artifact_id == aid)
                )
                row = (
                    await session.execute(select(TeacherArtifact).where(TeacherArtifact.id == aid))
                ).scalar_one_or_none()
                if row is not None:
                    try:
                        get_storage().client.remove_object(row.bucket, row.object_key)
                    except Exception:  # noqa: BLE001 - 清理失败不掩盖断言结果
                        pass
                    await session.delete(row)
                await session.commit()


async def test_unauthenticated_is_401(seeded_deck: str) -> None:
    from main import create_app

    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t24.test") as c:
        r = await c.get(f"/api/teacher-v3/artifacts/decks/{seeded_deck}")
    assert r.status_code == 401, r.text


async def test_unknown_deck_is_404(client: httpx.AsyncClient) -> None:
    r = await client.get(f"/api/teacher-v3/artifacts/decks/{uuid.uuid4()}")
    assert r.status_code == 404, r.text


@pytest.mark.parametrize("fmt,expected", [("docx", 422), ("pptx", 200)])
async def test_deck_export_format_gate(client: httpx.AsyncClient, seeded_deck: str, fmt: str, expected: int) -> None:
    """格式门控：deck 只接受 pptx/pdf。这条同时证明导出端点已挂载（不是 404）。"""
    r = await client.post(
        f"/api/teacher-v3/artifacts/decks/{seeded_deck}/exports",
        json={"format": fmt, "expected_revision": 3},
    )
    assert r.status_code == expected, r.text
    if expected == 200:
        data = r.json()["data"]
        assert data["job_id"] and data["status"] == "pending"
        # 取消刚提交的作业，避免污染真实队列
        c = await client.post(f"/api/teacher-v3/artifacts/exports/{data['job_id']}/cancel")
        assert c.status_code == 200, c.text
