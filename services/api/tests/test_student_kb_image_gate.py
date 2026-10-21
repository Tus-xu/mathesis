"""学生端知识库 P3 改造测试（2026-10-10）

覆盖：
1. P3① 图片闸门分流：图片 OCR 文本 <80 字不再 failed —— doc 仍 ready、
   meta.index_state="partial"、meta.ocr_len 如实记录、零 Chunk（旧切片软删）；
   OCR 文本 ≥80 字照常切片向量化（index_state="full"）；
   非图片（pdf）文本 <80 字仍如实 failed（含「不足 80 字」文案）。
2. P3② 分类：upload category（超白名单回落「其他」）、PATCH /documents/{id}
   （合法改类 / 非属主 40301 / 非法分类 40001 / 不存在 40400）、
   GET /documents 增量字段与 category 筛选、存量无 category 回落「其他」。

说明：ingest 后台任务直查真实 DB（background_session_factory），与 test_iter05_kb
同口径连 settings.database_url；OCR/向量为 mock（不依赖外部服务）。
"""

import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.chunk import Chunk
from app.models.database import get_db
from app.models.file import File
from app.models.knowledge_doc import KnowledgeDoc

_test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_test_session_factory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


async def _override_get_db():
    async with _test_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


app.dependency_overrides[get_db] = _override_get_db


@asynccontextmanager
async def _db():
    async with _test_session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _register_and_login(client) -> tuple[str, str]:
    phone = f"139{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    return data["token"], data["user"]["id"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


async def _mk_image_doc(user_id: str, meta: dict | None = None) -> tuple[str, str]:
    """直插 File(image) + KnowledgeDoc(pending)，返回 (doc_id, file_id)。"""
    uid = uuid.UUID(user_id)
    async with _db() as s:
        file_obj = File(
            user_id=uid,
            filename=f"题图-{uuid.uuid4().hex[:6]}.png",
            mime="image/png",
            size_bytes=64,
            sha256=uuid.uuid4().hex,
            file_type="image",
            status="uploaded",
            storage_uri="local:kb-test",  # 不落真实字节：图片闸门测试 mock 掉解析，不读盘
        )
        s.add(file_obj)
        await s.flush()
        doc = KnowledgeDoc(
            title=f"P3自测题图-{uuid.uuid4().hex[:6]}",
            source_type="textbook",
            file_uri=str(file_obj.id),
            uploader_id=uid,
            status="pending",
            meta_={"origin": "kb-upload", "category": "题目", "file_type": "image", **(meta or {})},
        )
        s.add(doc)
        await s.commit()
        return str(doc.id), str(file_obj.id)


class _FakeEmbedder:
    async def embed(self, texts):
        return [[0.01] * 1024 for _ in texts]  # chunks.embedding 为 Vector(1024)


def _fake_extract(text: str, engine: str):
    async def _fn(doc, file_obj, db):
        return text, engine

    return _fn


# ==================== P3① 图片闸门 ====================


class TestImageGate:
    async def test_image_sparse_ocr_ready_partial(self, client):
        """图片 OCR 文本 30 字 → ready（不再 failed）+ index_state=partial + ocr_len=30 + 零 Chunk"""
        token, user_id = await _register_and_login(client)
        doc_id, file_id = await _mk_image_doc(user_id)
        with patch(
            "app.gateway.student_kb_router._extract_text", new=_fake_extract("x" * 30, "rapidocr")
        ):
            from app.gateway.student_kb_router import _run_kb_ingest_task

            await _run_kb_ingest_task(doc_id, file_id)

        async with _db() as s:
            doc = await s.get(KnowledgeDoc, uuid.UUID(doc_id))
            status, meta = doc.status, dict(doc.meta_ or {})
            live_chunks = (
                await s.execute(
                    select(Chunk).where(Chunk.doc_id == doc.id, Chunk.deleted_at.is_(None))
                )
            ).scalars().all()
        assert status == "ready", f"图片 OCR 稀疏不应 failed，实际 {status} error={meta.get('error')}"
        assert meta["index_state"] == "partial"
        assert meta["ocr_len"] == 30
        assert meta["error"] is None
        assert meta["chunk_count"] == 0
        assert live_chunks == []

    async def test_image_partial_reparse_soft_deletes_old_chunks(self, client):
        """图片此前已索引（有旧切片），reparse 后 OCR 稀疏 → 旧切片软删，不残留 partial 假状态"""
        token, user_id = await _register_and_login(client)
        doc_id, file_id = await _mk_image_doc(user_id)
        async with _db() as s:
            s.add(
                Chunk(
                    doc_id=uuid.UUID(doc_id),
                    content="上一轮成功解析留下的旧切片",
                    embedding=[0.01] * 1024,
                    kp_ids=[],
                    chunk_index=0,
                )
            )
            await s.commit()

        with patch(
            "app.gateway.student_kb_router._extract_text", new=_fake_extract("", "rapidocr")
        ):
            from app.gateway.student_kb_router import _run_kb_ingest_task

            await _run_kb_ingest_task(doc_id, file_id)

        async with _db() as s:
            doc = await s.get(KnowledgeDoc, uuid.UUID(doc_id))
            status, meta = doc.status, dict(doc.meta_ or {})
            live = (
                await s.execute(
                    select(Chunk).where(Chunk.doc_id == doc.id, Chunk.deleted_at.is_(None))
                )
            ).scalars().all()
        assert status == "ready"
        assert meta["index_state"] == "partial"
        assert live == []

    async def test_image_rich_ocr_full_index(self, client):
        """图片 OCR 文本 200 字 → 正常切片向量化，index_state=full + chunk_count 如实"""
        token, user_id = await _register_and_login(client)
        doc_id, file_id = await _mk_image_doc(user_id)
        rich = "如图所示，已知正方体 ABCD-A1B1C1D1，棱长为 2，求证：BD 垂直于平面 ACC1A1，并求异面直线 BD 与 A1C1 所成角的大小，写出完整推导过程。" * 2
        with patch(
            "app.gateway.student_kb_router._extract_text", new=_fake_extract(rich, "rapidocr")
        ), patch("app.gateway.student_kb_router.EmbeddingProvider", return_value=_FakeEmbedder()):
            from app.gateway.student_kb_router import _run_kb_ingest_task

            await _run_kb_ingest_task(doc_id, file_id)

        async with _db() as s:
            doc = await s.get(KnowledgeDoc, uuid.UUID(doc_id))
            status, meta = doc.status, dict(doc.meta_ or {})
            live_chunks = (
                await s.execute(
                    select(Chunk).where(Chunk.doc_id == doc.id, Chunk.deleted_at.is_(None))
                )
            ).scalars().all()
            emb_flags = [c.embedding is not None for c in live_chunks]
        assert status == "ready"
        assert meta["index_state"] == "full"
        assert meta["chunk_count"] == len(live_chunks) > 0
        assert all(emb_flags)  # embedding 非空红线（fail-closed）
        assert meta["error"] is None

    async def test_pdf_sparse_text_still_failed(self, client):
        """非图片（pdf）文本 30 字 → 仍如实 failed，含「不足 80 字」，不设 index_state"""
        token, user_id = await _register_and_login(client)
        uid = uuid.UUID(user_id)
        async with _db() as s:
            file_obj = File(
                user_id=uid,
                filename=f"扫描件-{uuid.uuid4().hex[:6]}.pdf",
                mime="application/pdf",
                size_bytes=64,
                sha256=uuid.uuid4().hex,
                file_type="pdf",
                status="uploaded",
                storage_uri="local:kb-test",
            )
            s.add(file_obj)
            await s.flush()
            doc = KnowledgeDoc(
                title=f"P3自测PDF-{uuid.uuid4().hex[:6]}",
                source_type="textbook",
                file_uri=str(file_obj.id),
                uploader_id=uid,
                status="pending",
                meta_={"origin": "kb-upload", "category": "教材", "file_type": "pdf"},
            )
            s.add(doc)
            await s.commit()
            doc_id, file_id = str(doc.id), str(file_obj.id)

        with patch(
            "app.gateway.student_kb_router._extract_text", new=_fake_extract("x" * 30, "pymupdf")
        ):
            from app.gateway.student_kb_router import _run_kb_ingest_task

            await _run_kb_ingest_task(doc_id, file_id)

        async with _db() as s:
            doc = await s.get(KnowledgeDoc, uuid.UUID(doc_id))
            status, meta = doc.status, dict(doc.meta_ or {})
        assert status == "failed"
        assert "不足 80 字" in (meta.get("error") or "")
        assert "index_state" not in meta


# ==================== P3② 分类 ====================


class TestCategory:
    async def test_upload_category_written_and_fallback(self, client):
        """upload：合法 category 落 meta；超白名单回落「其他」不报错（ingest mock 不跑真管线）"""
        token, user_id = await _register_and_login(client)

        async def _noop_ingest(doc_id, file_id):
            return

        with patch("app.gateway.student_kb_router._run_kb_ingest_task", new=_noop_ingest):
            r1 = await client.post(
                "/api/v1/kb/upload",
                files={"file": (f"教材切片-{uuid.uuid4().hex[:6]}.txt", "甲乙丙丁戊己庚辛壬癸".encode(), "text/plain")},
                data={"category": "教材"},
                headers=_headers(token),
            )
            r2 = await client.post(
                "/api/v1/kb/upload",
                files={"file": (f"乱分类-{uuid.uuid4().hex[:6]}.txt", "子丑寅卯辰巳午未申酉".encode(), "text/plain")},
                data={"category": "盗版资料"},
                headers=_headers(token),
            )
        assert r1.json()["code"] == 0, r1.text
        assert r2.json()["code"] == 0, r2.text
        async with _db() as s:
            d1 = await s.get(KnowledgeDoc, uuid.UUID(r1.json()["data"]["doc_id"]))
            d2 = await s.get(KnowledgeDoc, uuid.UUID(r2.json()["data"]["doc_id"]))
            m1, m2 = dict(d1.meta_ or {}), dict(d2.meta_ or {})
        assert m1["category"] == "教材"
        assert m2["category"] == "其他"  # 超白名单回落
        assert m1["file_type"] == "txt"

    async def test_list_incremental_fields_and_legacy_fallback(self, client):
        """GET /documents：增量字段齐全；存量无 category → 回落「其他」"""
        token, user_id = await _register_and_login(client)
        doc_id, file_id = await _mk_image_doc(user_id, meta={"index_state": "partial", "ocr_len": 30})
        resp = await client.get("/api/v1/kb/documents", headers=_headers(token))
        assert resp.json()["code"] == 0
        item = next(d for d in resp.json()["data"]["items"] if d["id"] == doc_id)
        assert item["category"] == "题目"
        assert item["file_id"] == file_id
        assert item["ocr_len"] == 30
        assert item["index_state"] == "partial"
        assert "parse_engine" in item and "file_type" in item

        # 存量兼容：无 category 键的文档 → 缺省「其他」
        uid = uuid.UUID(user_id)
        async with _db() as s:
            legacy = KnowledgeDoc(
                title=f"存量文档-{uuid.uuid4().hex[:6]}",
                source_type="textbook",
                uploader_id=uid,
                status="ready",
                meta_={"origin": "v1-ingest"},
            )
            s.add(legacy)
            await s.commit()
            legacy_id = str(legacy.id)
        resp2 = await client.get("/api/v1/kb/documents", headers=_headers(token))
        legacy_item = next(d for d in resp2.json()["data"]["items"] if d["id"] == legacy_id)
        assert legacy_item["category"] == "其他"
        assert legacy_item["index_state"] is None

    async def test_category_filter(self, client):
        """GET /documents?category= 只返回该分类"""
        token, user_id = await _register_and_login(client)
        await _mk_image_doc(user_id)  # category=题目
        uid = uuid.UUID(user_id)
        async with _db() as s:
            s.add(
                KnowledgeDoc(
                    title=f"教材文档-{uuid.uuid4().hex[:6]}",
                    source_type="textbook",
                    uploader_id=uid,
                    status="ready",
                    meta_={"origin": "kb-upload", "category": "教材"},
                )
            )
            await s.commit()
        resp = await client.get("/api/v1/kb/documents", params={"category": "教材"}, headers=_headers(token))
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["category"] == "教材"

    async def test_patch_category_roundtrip(self, client):
        """PATCH 合法分类 → 200 code=0；刷新（重新 GET）后保持"""
        token, user_id = await _register_and_login(client)
        doc_id, _ = await _mk_image_doc(user_id)
        resp = await client.patch(
            f"/api/v1/kb/documents/{doc_id}", json={"category": "试卷"}, headers=_headers(token)
        )
        body = resp.json()
        assert body["code"] == 0, body
        assert body["data"] == {"doc_id": doc_id, "category": "试卷"}
        lst = await client.get("/api/v1/kb/documents", headers=_headers(token))
        item = next(d for d in lst.json()["data"]["items"] if d["id"] == doc_id)
        assert item["category"] == "试卷"  # 刷新仍在（真实持久化）

    async def test_patch_not_owner_40301(self, client):
        """PATCH 非属主 → 40301"""
        owner_token, owner_id = await _register_and_login(client)
        doc_id, _ = await _mk_image_doc(owner_id)
        other_token, _ = await _register_and_login(client)
        resp = await client.patch(
            f"/api/v1/kb/documents/{doc_id}", json={"category": "笔记"}, headers=_headers(other_token)
        )
        assert resp.json()["code"] == 40301

    async def test_patch_invalid_category_40001(self, client):
        """PATCH 非法分类 → 40001（显式修改不静默回落）"""
        token, user_id = await _register_and_login(client)
        doc_id, _ = await _mk_image_doc(user_id)
        resp = await client.patch(
            f"/api/v1/kb/documents/{doc_id}", json={"category": "盗版资料"}, headers=_headers(token)
        )
        assert resp.json()["code"] == 40001

    async def test_patch_missing_doc_40400(self, client):
        """PATCH 不存在的文档 → 40400"""
        token, _ = await _register_and_login(client)
        resp = await client.patch(
            f"/api/v1/kb/documents/{uuid.uuid4()}", json={"category": "笔记"}, headers=_headers(token)
        )
        assert resp.json()["code"] == 40400
