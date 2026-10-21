"""迭代05 KB 域五端点测试（阶段 1.7，审计 B-P1-14）

覆盖（SSOT §5.8 / API 文档 §5 / ADR-016/024）：
1. 角色门禁：学生访问 → 403
2. 整批退回制：三件套缺失 / 公式配对失败 / kp_codes 不存在 / JSON 非法
3. 正常导入（mock storage + embedding）→ accepted + chunks 落库
4. 列表与切片查询（kp_ids → code 回填、source_ref）
5. 检索台参数校验；eval/recall 空态
"""

import json
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
from app.models.role_binding import RoleBinding

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
    phone = f"138{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    return data["token"], data["user"]["id"]


async def _make_teacher(client) -> tuple[str, str]:
    """学生账号 + DB 直接加 teacher 角色（沿用 test_classroom 模式）"""
    token, user_id = await _register_and_login(client)
    async with _test_session_factory() as db:
        existing = await db.execute(
            select(RoleBinding).where(
                RoleBinding.user_id == user_id, RoleBinding.role == "teacher"
            )
        )
        if existing.scalar_one_or_none() is None:
            db.add(RoleBinding(user_id=user_id, role="teacher", verified=True))
            await db.commit()
    # 换发含 teacher 角色的 JWT
    switch = await client.post(
        "/api/auth/role/switch",
        json={"role": "teacher"},
        headers={"Authorization": f"Bearer {token}"},
    )
    new_token = switch.json().get("data", {}).get("token") or token
    return new_token, user_id


async def _make_admin(client) -> tuple[str, str]:
    """ADMIN_PHONES 白名单手机号登录（真实引导路径），返回 (token, user_id)"""
    phone = f"137{str(uuid.uuid4().int)[:8]}"
    from unittest.mock import patch

    with patch.object(settings, "admin_phones", phone):
        await client.post("/api/auth/sms-code", json={"phone": phone})
        resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = resp.json()["data"]
    # 统一认证下默认 active_role 为 student；admin 端点要求显式切换到 admin
    switch = await client.post(
        "/api/auth/role/switch",
        json={"role": "admin"},
        headers={"Authorization": f"Bearer {data['token']}"},
    )
    assert switch.json()["code"] == 0, switch.text
    return switch.json()["data"]["token"], data["user"]["id"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _jsonl(items: list[dict]) -> bytes:
    return "\n".join(json.dumps(it, ensure_ascii=False) for it in items).encode("utf-8")


class _FakeStorage:
    def __init__(self, data: bytes):
        self._data = data

    def get_bytes(self, uri):
        return self._data


class _FakeEmbedder:
    async def embed(self, texts):
        return [[0.01] * 1024 for _ in texts]  # chunks.embedding 为 Vector(1024)


def _import_body(batch_id: str, manifest: dict | None = None) -> dict:
    return {
        "batch_id": batch_id,
        "manifest": manifest if manifest is not None else {"license": "Apache-2.0", "data_level": "L0", "copyright_light": "green"},
        "chunks_file_url": "fake://chunks.jsonl",
    }


# ==================== 角色门禁 ====================


class TestKbRoleGate:
    async def test_student_forbidden_manage(self, client):
        """学生访问 KB 管理域（docs/import）→ 403；检索域（retrieve）→ 200（scope=student 隔离）"""
        token, _ = await _register_and_login(client)
        # 管理端点仍限 teacher/researcher
        resp = await client.get("/api/kb/docs", headers=_headers(token))
        assert resp.status_code == 403
        # 检索端点学生可用（端隔离，仅 student 域）
        resp2 = await client.post(
            "/api/kb/retrieve",
            json={"query": "函数", "top_k": 2},
            headers=_headers(token),
        )
        assert resp2.status_code == 200
        data = resp2.json().get("data") or {}
        assert "chunks" in data
        assert data.get("scope") == "student"


class TestKbAdminAccess:
    """admin 角色可访问检索试验台端点（阶段 6B：/admin/kb-bench）"""

    async def test_admin_can_list_docs_and_eval(self, client):
        token, _ = await _make_admin(client)
        resp = await client.get("/api/kb/docs", headers=_headers(token))
        assert resp.status_code == 200
        assert "items" in resp.json()["data"]

        resp2 = await client.get("/api/kb/eval/recall", headers=_headers(token))
        assert resp2.status_code == 200
        assert resp2.json()["code"] == 0

    async def test_admin_can_retrieve(self, client):
        token, _ = await _make_admin(client)
        resp = await client.post(
            "/api/kb/retrieve",
            json={"query": "函数单调性", "top_k": 2},
            headers=_headers(token),
        )
        assert resp.status_code == 200
        data = resp.json().get("data") or {}
        assert "chunks" in data


# ==================== 整批退回制 ====================


class TestKbImportReject:
    async def test_manifest_missing_fields(self, client):
        """三件套缺失 → accepted=false（整批退回）"""
        token, _ = await _make_teacher(client)
        resp = await client.post(
            "/api/kb/docs/import",
            json=_import_body(f"b-{uuid.uuid4().hex[:8]}", manifest={"license": "MIT"}),
            headers=_headers(token),
        )
        data = resp.json()["data"]
        assert data["accepted"] is False
        assert "data_level" in data["rejected_reason"]

    async def test_manifest_invalid_level(self, client):
        """data_level 非法 → 整批退回"""
        token, _ = await _make_teacher(client)
        resp = await client.post(
            "/api/kb/docs/import",
            json=_import_body(
                f"b-{uuid.uuid4().hex[:8]}",
                manifest={"license": "MIT", "data_level": "L9", "copyright_light": "green"},
            ),
            headers=_headers(token),
        )
        assert resp.json()["data"]["accepted"] is False

    async def test_latex_unpaired_rejected(self, client):
        """公式配对失败（$ 奇数）→ 整批退回（ADR-024 红线）"""
        token, _ = await _make_teacher(client)
        items = [{"content": "已知 $sin x = 1/2 求 x（公式未闭合）", "doc_title": "t"}]
        with patch("app.gateway.kb_router.get_storage", return_value=_FakeStorage(_jsonl(items))):
            resp = await client.post(
                "/api/kb/docs/import",
                json=_import_body(f"b-{uuid.uuid4().hex[:8]}"),
                headers=_headers(token),
            )
        data = resp.json()["data"]
        assert data["accepted"] is False
        assert "公式配对" in data["rejected_reason"]

    async def test_unknown_kp_rejected(self, client):
        """kp_codes 不在 knowledge_points → 整批退回"""
        token, _ = await _make_teacher(client)
        items = [{"content": "题目 $x^2=4$", "kp_codes": ["NOT-EXIST-KP-999"], "doc_title": "t"}]
        with patch("app.gateway.kb_router.get_storage", return_value=_FakeStorage(_jsonl(items))), \
             patch("app.gateway.kb_router.EmbeddingProvider", return_value=_FakeEmbedder()):
            resp = await client.post(
                "/api/kb/docs/import",
                json=_import_body(f"b-{uuid.uuid4().hex[:8]}"),
                headers=_headers(token),
            )
        data = resp.json()["data"]
        assert data["accepted"] is False
        assert "NOT-EXIST-KP-999" in data["rejected_reason"]

    async def test_invalid_json_line_rejected(self, client):
        """JSON 非法行 → 整批退回"""
        token, _ = await _make_teacher(client)
        with patch("app.gateway.kb_router.get_storage", return_value=_FakeStorage(b"{broken json")):
            resp = await client.post(
                "/api/kb/docs/import",
                json=_import_body(f"b-{uuid.uuid4().hex[:8]}"),
                headers=_headers(token),
            )
        assert resp.json()["data"]["accepted"] is False


# ==================== 正常导入 + 查询 ====================


class TestKbImportAndQuery:
    async def test_import_success_and_query(self, client):
        """正常导入 → accepted=true + chunks 落库 + 列表/切片查询（kp code 回填）"""
        token, _ = await _make_teacher(client)
        batch_id = f"t-{uuid.uuid4().hex[:8]}"
        items = [
            {
                "content": "已知 $\\sin x=\\frac{1}{2}$，求 $x$ 在 $[0,2\\pi]$ 内的值。答案：$x=\\frac{\\pi}{6}$ 或 $\\frac{5\\pi}{6}$。",
                "content_type": "question",
                "kp_codes": ["MATH-PEP-BIXI1-B21C8FA0DE"],
                "doc_title": "测试题集",
            },
            {
                "content": "求 $\\cos 60^\\circ$ 的值。答案：$\\frac{1}{2}$。",
                "content_type": "question",
                "kp_codes": ["MATH-PEP-BIXI1-B21C8FA0DE"],
                "doc_title": "测试题集",
            },
        ]
        with patch("app.gateway.kb_router.get_storage", return_value=_FakeStorage(_jsonl(items))), \
             patch("app.gateway.kb_router.EmbeddingProvider", return_value=_FakeEmbedder()):
            resp = await client.post(
                "/api/kb/docs/import",
                json=_import_body(batch_id),
                headers=_headers(token),
            )
        body = resp.json()
        assert body["code"] == 0
        data = body["data"]
        assert data["accepted"] is True
        assert data["doc_id"]

        doc_id = data["doc_id"]
        # chunks 落库（embedding 非 NULL 红线）——在会话内提取值，避免 session 关闭后属性 expire
        async with _db() as s:
            chunks = (await s.execute(select(Chunk).where(Chunk.doc_id == uuid.UUID(doc_id)))).scalars().all()
            chunk_count = len(chunks)
            emb_flags = [c.embedding is not None for c in chunks]
        assert chunk_count == 2
        assert all(emb_flags)

        # 文档列表
        lst = await client.get("/api/kb/docs", headers=_headers(token))
        found = [d for d in lst.json()["data"]["items"] if d["doc_id"] == doc_id]
        assert found and found[0]["batch_id"] == batch_id and found[0]["status"] == "ready"

        # 切片列表（kp_codes 回填 code、source_ref）
        ch = await client.get(f"/api/kb/docs/{doc_id}/chunks", headers=_headers(token))
        ch_data = ch.json()["data"]
        assert ch_data["total"] == 2
        assert ch_data["items"][0]["kp_codes"] == ["MATH-PEP-BIXI1-B21C8FA0DE"]
        assert ch_data["items"][0]["has_embedding"] is True
        assert ch_data["items"][0]["source_ref"]["doc_id"] == doc_id

    async def test_import_idempotent_skip(self, client):
        """batch_id 幂等：重复导入跳过（不产生重复文档）"""
        token, _ = await _make_teacher(client)
        batch_id = f"idem-{uuid.uuid4().hex[:8]}"
        items = [{"content": "幂等测试题 $1+1=2$", "doc_title": "t"}]
        body = _import_body(batch_id)
        with patch("app.gateway.kb_router.get_storage", return_value=_FakeStorage(_jsonl(items))), \
             patch("app.gateway.kb_router.EmbeddingProvider", return_value=_FakeEmbedder()):
            r1 = await client.post("/api/kb/docs/import", json=body, headers=_headers(token))
        assert r1.json()["data"]["accepted"] is True
        # 第二次：同一 batch_id → 幂等命中（accepted=false + 原因含"已存在"）或直接 accepted 不新建
        with patch("app.gateway.kb_router.get_storage", return_value=_FakeStorage(_jsonl(items))), \
             patch("app.gateway.kb_router.EmbeddingProvider", return_value=_FakeEmbedder()):
            r2 = await client.post("/api/kb/docs/import", json=body, headers=_headers(token))
        d2 = r2.json()["data"]
        assert d2["accepted"] is False and "已存在" in d2["rejected_reason"]

    async def test_chunks_of_missing_doc_40400(self, client):
        token, _ = await _make_teacher(client)
        resp = await client.get(f"/api/kb/docs/{uuid.uuid4()}/chunks", headers=_headers(token))
        assert resp.json()["code"] == 40400


# ==================== 检索台与评测 ====================


class TestKbRetrieveAndEval:
    async def test_retrieve_invalid_content_type(self, client):
        token, _ = await _make_teacher(client)
        resp = await client.post(
            "/api/kb/retrieve",
            json={"query": "三角函数", "content_type": "hacked"},
            headers=_headers(token),
        )
        assert resp.json()["code"] == 40001

    async def test_eval_recall_shape(self, client):
        """eval/recall：无数据 → data=null；有数据 → 五字段"""
        token, _ = await _make_teacher(client)
        resp = await client.get("/api/kb/eval/recall", headers=_headers(token))
        body = resp.json()
        assert body["code"] == 0
        if body["data"] is not None:
            for k in ("eval_set", "recall_at_5", "mrr", "run_at", "meta"):
                assert k in body["data"]
