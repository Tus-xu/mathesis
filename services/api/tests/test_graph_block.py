"""F11 graph block 契约回归测试（test_graph_block.py）

覆盖：
1. validate_graph_block 契约校验（engine 必须 jsxgraph，schema 必须 dict；非法降级 None 不抛异常）
2. graph SSE 事件产出（smart_quiz result_meta 带 graph → 实时流 graph 事件）
3. 信封落库含 graph block（位于 card 之后）
4. 幂等重放还原 graph 事件（含落库坏数据防御跳过）
5. 非法 graph（engine 错 / schema 非 dict）被丢弃，链路正常不 500

SSE 链路用例需要 PostgreSQL 运行中（与 test_m2_fixes 同环境）。
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.kernel.graph_block import validate_graph_block
from app.main import app
from app.models.database import get_db
from app.models.message import Message
from app.models.role_binding import RoleBinding
from app.models.user import User

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


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _parse_sse_events(text: str) -> dict[str, list[dict]]:
    """解析 SSE 文本为 {event_type: [data, ...]}"""
    events: dict[str, list[dict]] = {}
    current_event = None
    for line in text.split("\n"):
        if line.startswith("event: "):
            current_event = line[7:]
            events.setdefault(current_event, [])
        elif line.startswith("data: ") and current_event:
            events[current_event].append(json.loads(line[6:]))
    return events


GRAPH_SCHEMA = {"functions": [{"expr": "x^2", "color": "#1e88e5"}]}


# ========== 1. validate_graph_block 契约校验（纯单测） ==========


class TestValidateGraphBlock:
    """engine 必须 "jsxgraph"，schema 必须 dict；非法降级 None，绝不抛异常"""

    def test_valid_normalized(self):
        raw = {"engine": "jsxgraph", "schema": GRAPH_SCHEMA}
        assert validate_graph_block(raw) == {"engine": "jsxgraph", "schema": GRAPH_SCHEMA}

    def test_extra_keys_ignored(self):
        """LLM 多给的字段不判死：规范化后只留 engine/schema"""
        raw = {"engine": "jsxgraph", "schema": {}, "caption": "函数图像"}
        assert validate_graph_block(raw) == {"engine": "jsxgraph", "schema": {}}

    def test_engine_wrong_dropped(self):
        assert validate_graph_block({"engine": "geogebra", "schema": {}}) is None

    def test_schema_not_dict_dropped(self):
        assert validate_graph_block({"engine": "jsxgraph", "schema": "oops"}) is None

    def test_schema_missing_dropped(self):
        assert validate_graph_block({"engine": "jsxgraph"}) is None

    def test_payload_not_dict_dropped(self):
        assert validate_graph_block("graph") is None
        assert validate_graph_block([{"engine": "jsxgraph"}]) is None


# ========== 2. 重放路径（纯单测 _replay_response，不碰 DB） ==========


async def _collect_replay(envelope: dict) -> dict[str, list[dict]]:
    from app.gateway.agent_router import _replay_response

    msg = Message(
        conversation_id=uuid.uuid4(),
        client_msg_id="ai_replay",
        role="assistant",
        content="正文",
        envelope=envelope,
        skill_id="smart_quiz",
    )
    resp = _replay_response(msg, "req-test")
    text = ""
    async for chunk in resp.body_iterator:
        text += chunk if isinstance(chunk, str) else chunk.decode()
    return _parse_sse_events(text)


class TestReplayGraphBlock:
    """幂等重放：graph block 还原为 graph 事件；落库坏数据跳过不炸流"""

    ENVELOPE = {
        "msg_id": "m1",
        "role": "assistant",
        "blocks": [
            {"type": "markdown", "content": "看图"},
            {"type": "card", "data": {"type": "quiz_set", "items": []}},
            {"type": "graph", "engine": "jsxgraph", "schema": GRAPH_SCHEMA},
        ],
        "meta": {"skill": "smart_quiz", "confidence": 0.9, "provider": "spark"},
    }

    async def test_replay_graph_event(self):
        events = await _collect_replay(self.ENVELOPE)
        assert "graph" in events, f"重放缺 graph 事件: {list(events)}"
        assert events["graph"][0] == {"engine": "jsxgraph", "schema": GRAPH_SCHEMA}

    async def test_replay_invalid_graph_skipped(self):
        envelope = {
            **self.ENVELOPE,
            "blocks": [
                {"type": "markdown", "content": "看图"},
                {"type": "graph", "engine": "geogebra", "schema": {}},
            ],
        }
        events = await _collect_replay(envelope)
        assert "graph" not in events
        # 重放流其余部分不受影响
        assert events["token"][0] == {"text": "看图"}
        assert "done" in events


# ========== 3. SSE 主链路（需要 PostgreSQL） ==========


class TestGraphPipeline:
    """graph 事件产出 + 信封落库 + 幂等重放 + 非法降级"""

    QUIZ_JSON_WITH_GRAPH = json.dumps(
        {
            "question_text": "作出函数 $f(x)=x^2$ 的图像并求顶点坐标",
            "q_type": "short_answer",
            "options": [],
            "answer": "$(0,0)$",
            "answer_analysis": "开口向上，顶点为原点",
            "graph": {"engine": "jsxgraph", "schema": GRAPH_SCHEMA},
        },
        ensure_ascii=False,
    )

    QUIZ_JSON_BAD_ENGINE = json.dumps(
        {
            "question_text": "作出函数 $f(x)=x^2$ 的图像并求顶点坐标",
            "q_type": "short_answer",
            "options": [],
            "answer": "$(0,0)$",
            "answer_analysis": "开口向上，顶点为原点",
            "graph": {"engine": "geogebra", "schema": {}},
        },
        ensure_ascii=False,
    )

    QUIZ_JSON_BAD_SCHEMA = json.dumps(
        {
            "question_text": "作出函数 $f(x)=x^2$ 的图像并求顶点坐标",
            "q_type": "short_answer",
            "options": [],
            "answer": "$(0,0)$",
            "answer_analysis": "开口向上，顶点为原点",
            "graph": {"engine": "jsxgraph", "schema": "not-a-dict"},
        },
        ensure_ascii=False,
    )

    def _make_mocks(self, quiz_json: str):
        from app.kernel.router import RouteDecision

        decision = RouteDecision(
            skill_id="smart_quiz",
            confidence=0.9,
            params={"question": "给我一道导数题"},
        )
        mock_intent = MagicMock()
        mock_intent.route = AsyncMock(return_value=decision)

        mock_router = MagicMock()
        mock_router.intended_provider = "spark"
        mock_router.chat = AsyncMock(
            return_value={
                "content": quiz_json,
                "provider": "spark",
                "model": "spark-ultra",
                "input_tokens": 100,
                "output_tokens": 80,
                "latency_ms": 200,
                "tool_calls": None,
            }
        )
        return mock_intent, mock_router

    async def _create_user(self) -> User:
        async with _test_session_factory() as session:
            # ASGITransport 不执行 lifespan，skills 表需显式同步（skill_runs 外键依赖）
            from app.skills.registry import get_skill_registry

            await get_skill_registry().sync_to_db(session)
            user = User(phone=f"139{uuid.uuid4().int % 100000000:08d}", nickname="")
            session.add(user)
            await session.flush()
            session.add(RoleBinding(user_id=user.id, role="student", verified=True))
            await session.commit()
            return user

    def _auth_headers(self, user: User) -> dict:
        from app.gateway.jwt import create_token_with_role

        token = create_token_with_role(
            user_id=str(user.id), role="student", roles=["student"], verified=True
        )
        return {"Authorization": f"Bearer {token}"}

    async def _chat(self, client, headers, client_msg_id: str, quiz_json: str):
        mock_intent, mock_router = self._make_mocks(quiz_json)
        with (
            patch("app.gateway.agent_router.get_intent_router", return_value=mock_intent),
            patch(
                "app.gateway.agent_router.get_model_router_for_user",
                new=AsyncMock(return_value=mock_router),
            ),
            patch("app.gateway.agent_router._bg_summary", new=AsyncMock()),
            patch("app.gateway.agent_router._start_title_task", return_value=None),
        ):
            return await client.post(
                "/api/agent/chat",
                json={
                    "message": "给我一道导数题",
                    "context": {"client_msg_id": client_msg_id, "workspace": "student"},
                },
                headers=headers,
            )

    async def _load_assistant_envelope(self, client_msg_id: str) -> dict:
        async with _test_session_factory() as session:
            result = await session.execute(
                select(Message).where(Message.client_msg_id == f"ai_{client_msg_id}")
            )
            msg = result.scalar_one()
            return msg.envelope or {}

    async def test_graph_event_persisted_and_replayed(self, client):
        user = await self._create_user()
        headers = self._auth_headers(user)
        client_msg_id = uuid.uuid4().hex[:20]

        resp = await self._chat(client, headers, client_msg_id, self.QUIZ_JSON_WITH_GRAPH)
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)

        # ① 实时流产出 graph 事件（{engine, schema}）
        assert "graph" in events, f"SSE 缺 graph 事件: {list(events)}"
        assert events["graph"][0] == {"engine": "jsxgraph", "schema": GRAPH_SCHEMA}

        # ② 信封落库含 graph block（位于 card 之后），extra_meta 不含 graph 键
        envelope = await self._load_assistant_envelope(client_msg_id)
        blocks = envelope.get("blocks", [])
        block_types = [b.get("type") for b in blocks]
        assert "graph" in block_types, f"信封缺 graph block: {block_types}"
        assert block_types.index("graph") > block_types.index("card")
        graph_block = blocks[block_types.index("graph")]
        assert graph_block["engine"] == "jsxgraph"
        assert graph_block["schema"] == GRAPH_SCHEMA
        extra = (envelope.get("meta") or {}).get("extra") or {}
        assert "graph" not in extra, f"extra_meta 不应含 graph 键: {extra}"

        # ③ 幂等重放还原 graph 事件
        resp2 = await client.post(
            "/api/agent/chat",
            json={
                "message": "给我一道导数题",
                "context": {"client_msg_id": client_msg_id, "workspace": "student"},
            },
            headers=headers,
        )
        assert resp2.status_code == 200
        assert resp2.headers.get("X-Idempotent-Replay", "").lower() == "true"
        replay_events = _parse_sse_events(resp2.text)
        assert "graph" in replay_events, f"重放缺 graph 事件: {list(replay_events)}"
        assert replay_events["graph"][0] == {"engine": "jsxgraph", "schema": GRAPH_SCHEMA}

    async def test_invalid_graph_engine_dropped_no_500(self, client):
        """engine 非 jsxgraph：graph 被丢弃记日志，链路正常（有 done、信封无 graph block）"""
        user = await self._create_user()
        headers = self._auth_headers(user)
        client_msg_id = uuid.uuid4().hex[:20]

        resp = await self._chat(client, headers, client_msg_id, self.QUIZ_JSON_BAD_ENGINE)
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        assert "graph" not in events, f"非法 graph 不应产出事件: {events.get('graph')}"
        assert "done" in events

        envelope = await self._load_assistant_envelope(client_msg_id)
        block_types = [b.get("type") for b in envelope.get("blocks", [])]
        assert "graph" not in block_types, f"信封不应含非法 graph block: {block_types}"
        # 其余链路不受影响：markdown/card 仍在
        assert "markdown" in block_types and "card" in block_types

    async def test_invalid_graph_schema_dropped_no_500(self, client):
        """schema 非 dict：同上降级丢弃"""
        user = await self._create_user()
        headers = self._auth_headers(user)
        client_msg_id = uuid.uuid4().hex[:20]

        resp = await self._chat(client, headers, client_msg_id, self.QUIZ_JSON_BAD_SCHEMA)
        assert resp.status_code == 200
        events = _parse_sse_events(resp.text)
        assert "graph" not in events
        assert "done" in events

        envelope = await self._load_assistant_envelope(client_msg_id)
        block_types = [b.get("type") for b in envelope.get("blocks", [])]
        assert "graph" not in block_types
