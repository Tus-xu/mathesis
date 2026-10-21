"""迭代05 chat attachments + SSE 新事件测试（阶段 1.3）

覆盖（审计清单 B-P1-11 / ADR-018 / SSOT §5.4）：
1. attachments 解析态检查：parsing/failed/不存在 → clarify 提示（不进入路由，不走 LLM）
2. latex_rendered 事件：speech_inject 标记 → token 段之前发出
3. file_parsed 事件：parsed 附件 → status 段发出 + 内容 <attachment> 包裹注入 params

需要 PostgreSQL + Redis 运行中（与 test_student_pipeline 同环境/同模式）。
"""

import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.gateway import agent_router as ar
from app.kernel.router import RouteDecision
from app.main import app
from app.models.database import get_db
from app.models.file import File, FileAsset

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


@pytest_asyncio.fixture
async def auth_client(client):
    phone = f"138{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    login_resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = login_resp.json()["data"]
    return client, data["token"], data["user"]["id"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _sse_events(text: str) -> list[str]:
    return [ln[7:] for ln in text.split("\n") if ln.startswith("event: ")]


def _sse_data(text: str, event_type: str) -> dict | None:
    lines = text.split("\n")
    for i, ln in enumerate(lines):
        if ln == f"event: {event_type}" and i + 1 < len(lines) and lines[i + 1].startswith("data: "):
            return json.loads(lines[i + 1][6:])
    return None


async def _seed_file(user_id: str, status: str, content: str | None = None) -> str:
    """种一个测试文件（含可选 markdown 产物），返回 file_id"""
    async with _test_session_factory() as s:
        f = File(
            user_id=uuid.UUID(user_id),
            filename=f"测试_{uuid.uuid4().hex[:6]}.md",
            mime="text/markdown",
            size_bytes=100,
            sha256=uuid.uuid4().hex,
            file_type="md",
            status=status,
            parse_engine="direct" if status == "parsed" else None,
        )
        s.add(f)
        await s.flush()
        if status == "parsed" and content is not None:
            s.add(FileAsset(file_id=f.id, asset_type="markdown", page_no=1, content=content))
        await s.commit()
        return str(f.id)


def _chat_body(client_msg_id: str, **extra) -> dict:
    body = {
        "message": "帮我看看这个文件里的题",
        "context": {"client_msg_id": client_msg_id, "workspace": "student"},
    }
    body.update(extra)
    return body


# ==================== 附件解析态检查 ====================


class TestAttachmentStateCheck:
    async def test_parsing_attachment_clarify(self, auth_client):
        """附件解析中 → clarify 提示 + done（不进路由，ADR-018）"""
        client, token, user_id = auth_client
        file_id = await _seed_file(user_id, status="parsing")
        resp = await client.post(
            "/api/agent/chat",
            json=_chat_body(str(uuid.uuid4()), attachments=[{"file_id": file_id, "kind": "doc"}]),
            headers=_headers(token),
        )
        assert resp.status_code == 200
        events = _sse_events(resp.text)
        assert "clarify" in events
        assert "done" in events
        data = _sse_data(resp.text, "clarify")
        assert "解析" in data["question"]

    async def test_failed_attachment_clarify(self, auth_client):
        """附件解析失败 → clarify 提示"""
        client, token, user_id = auth_client
        file_id = await _seed_file(user_id, status="failed")
        resp = await client.post(
            "/api/agent/chat",
            json=_chat_body(str(uuid.uuid4()), attachments=[{"file_id": file_id, "kind": "doc"}]),
            headers=_headers(token),
        )
        data = _sse_data(resp.text, "clarify")
        assert data is not None
        assert "解析失败" in data["question"]

    async def test_foreign_attachment_clarify(self, auth_client):
        """附件不存在/越权 → clarify（不泄露存在性）"""
        client, token, _ = auth_client
        resp = await client.post(
            "/api/agent/chat",
            json=_chat_body(str(uuid.uuid4()), attachments=[{"file_id": str(uuid.uuid4()), "kind": "doc"}]),
            headers=_headers(token),
        )
        data = _sse_data(resp.text, "clarify")
        assert data is not None
        assert "不存在" in data["question"]


# ==================== latex_rendered 事件 ====================


class TestLatexRenderedEvent:
    async def test_latex_rendered_before_clarify(self, auth_client):
        """speech_inject 标记 → latex_rendered 事件发出（token 段之前；此处用附件未就绪使流提前结束验证顺序）"""
        client, token, user_id = auth_client
        file_id = await _seed_file(user_id, status="parsing")
        resp = await client.post(
            "/api/agent/chat",
            json=_chat_body(
                str(uuid.uuid4()),
                attachments=[{"file_id": file_id, "kind": "doc"}],
            ) | {"context": {
                "client_msg_id": str(uuid.uuid4()),
                "workspace": "student",
                "speech_inject": {"latex": "x=\\frac{1}{2}", "ambiguous": False},
            }},
            headers=_headers(token),
        )
        events = _sse_events(resp.text)
        assert "latex_rendered" in events
        # latex_rendered 必须先于 clarify（token 段之前的纪律）
        assert events.index("latex_rendered") < events.index("clarify")
        data = _sse_data(resp.text, "latex_rendered")
        assert data["latex"] == "x=\\frac{1}{2}"
        assert data["source"] == "speech"
        assert data["ambiguous"] is False


# ==================== file_parsed 事件 + 内容注入 ====================


class _FakeSkill:
    """假 skill：记录收到的 params（验证附件注入），输出单 token"""

    received_params: dict = {}

    async def run(self, params, ctx):
        _FakeSkill.received_params = dict(params)
        yield {"type": "token", "data": {"text": "ok"}}


class _FakeRegistry:
    def get(self, skill_id):
        return _FakeSkill()


class _FakeRouter:
    async def route(self, *args, **kwargs):
        return RouteDecision(skill_id="chat", confidence=0.95, params={})


class TestFileParsedEvent:
    async def test_parsed_attachment_file_parsed_and_inject(self, auth_client):
        """parsed 附件 → file_parsed 事件（status 段）+ 内容 <attachment> 包裹注入 params"""
        client, token, user_id = auth_client
        file_id = await _seed_file(user_id, status="parsed", content="已知 sin x = 1/2，求 x 的值。")

        _FakeSkill.received_params = {}
        with patch.object(ar, "get_intent_router", return_value=_FakeRouter()), \
             patch.object(ar, "get_skill_registry", return_value=_FakeRegistry()):
            resp = await client.post(
                "/api/agent/chat",
                json=_chat_body(str(uuid.uuid4()), attachments=[{"file_id": file_id, "kind": "doc"}]),
                headers=_headers(token),
            )
        assert resp.status_code == 200
        events = _sse_events(resp.text)
        assert "file_parsed" in events
        # file_parsed 位于 status 段：在 meta 之后
        assert events.index("meta") < events.index("file_parsed")
        data = _sse_data(resp.text, "file_parsed")
        assert data["file_id"] == file_id
        assert data["status"] == "parsed"
        assert "sin" in data["summary"]
        # 附件内容以 <attachment> 包裹注入 params（防注入纪律）
        injected = _FakeSkill.received_params.get("question", "")
        assert "<attachment>" in injected
        assert "已知 sin x = 1/2" in injected
        assert _FakeSkill.received_params.get("attachment_context")

    async def test_parsed_text_attachment_direct(self, auth_client):
        """kind=parsed_text → 前端回传文本直接注入（后端不再解析，仅长度校验）"""
        client, token, _ = auth_client
        _FakeSkill.received_params = {}
        with patch.object(ar, "get_intent_router", return_value=_FakeRouter()), \
             patch.object(ar, "get_skill_registry", return_value=_FakeRegistry()):
            resp = await client.post(
                "/api/agent/chat",
                json=_chat_body(
                    str(uuid.uuid4()),
                    attachments=[{"file_id": "n/a", "kind": "parsed_text", "parsed_text": "回传的解析文本：cos 2x 公式"}],
                ),
                headers=_headers(token),
            )
        assert resp.status_code == 200
        injected = _FakeSkill.received_params.get("question", "")
        assert "回传的解析文本：cos 2x 公式" in injected
