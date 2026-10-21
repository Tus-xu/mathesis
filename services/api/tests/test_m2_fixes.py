"""M2 修复回归测试（test_m2_fixes.py）

覆盖审查发现的 M2 修复项：
F1 /tools/retrieve 按 RAGPipeline 真实签名调用（原 bug：RAGPipeline(db) + 错误关键字 → TypeError）
F2 语音降级 _local_spark_to_latex 补齐 ModelRouter.chat 必填参数（request_id/scene）
F3 smart_quiz card 事件入信封落库 + 幂等重放/历史消息还原（原 bug：刷新后题卡消失）
F5 thinking 模式 per-call 覆盖（spark/deepseek/ModelRouter 透传）
F6 星辰审计 log_ai_call 参数名修正（原 bug：tokens_in/tokens_out → TypeError 被吞，审计全丢）

F3 需要 PostgreSQL 运行中（与 test_m1_fixes 同环境）。
"""

import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
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


def _parse_sse(text: str) -> dict[str, list[dict]]:
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


# ========== F1. /tools/retrieve ==========


class TestToolsRetrieve:
    """tools_router 按 RAGPipeline 真实签名调用：retrieve(question, *, db, mode)"""

    async def test_retrieve_no_typeerror(self, client, monkeypatch):
        """mock RAGPipeline：端点 200 且按真实签名调用（修复前必 TypeError）"""
        from app.kernel.rag import RAGResult, ScoredChunk

        monkeypatch.setattr(settings, "tool_api_key", "test-tool-key")

        rag_result = RAGResult(
            chunks=[
                ScoredChunk(
                    chunk_id="chunk-1",
                    doc_id="doc-1",
                    content="导数的定义：函数在某点的瞬时变化率",
                    doc_title="必修一教材",
                    score=0.82,
                    raw_score=0.82,
                    kp_ids=["kp_derivative"],
                )
            ],
            answerable=True,
        )
        mock_pipeline = MagicMock()
        mock_pipeline.retrieve = AsyncMock(return_value=rag_result)

        @asynccontextmanager
        async def _mock_session():
            yield MagicMock()

        with (
            patch("app.kernel.rag.get_rag_pipeline", return_value=mock_pipeline),
            patch(
                "app.models.database.async_session_factory",
                side_effect=lambda: _mock_session(),
            ),
        ):
            resp = await client.post(
                "/tools/retrieve",
                json={"query": "什么是导数", "mode": "vector"},
                headers={"X-Tool-Key": "test-tool-key"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0, f"检索失败: {body}"

        # 按真实签名调用：question 位置参数 + db/mode 关键字
        mock_pipeline.retrieve.assert_awaited_once()
        call = mock_pipeline.retrieve.call_args
        assert call.args[0] == "什么是导数"
        assert call.kwargs["mode"] == "vector"
        assert "db" in call.kwargs

        data = body["data"]
        assert data["answerable"] is True
        assert data["gate"]["threshold"] == settings.rag_refuse_threshold
        assert data["gate"]["top1_score"] == 0.82
        assert data["chunks"][0]["chunk_id"] == "chunk-1"
        assert data["chunks"][0]["kp_codes"] == ["kp_derivative"]

    async def test_retrieve_bad_key_rejected(self, client, monkeypatch):
        """无效 X-Tool-Key → 40301"""
        monkeypatch.setattr(settings, "tool_api_key", "test-tool-key")
        resp = await client.post(
            "/tools/retrieve",
            json={"query": "什么是导数"},
            headers={"X-Tool-Key": "wrong-key"},
        )
        assert resp.json()["code"] == 40301


# ========== F2. 语音降级通道 ==========


class TestSpeechFallback:
    """_local_spark_to_latex 必须带 request_id/scene 调 ModelRouter.chat"""

    async def test_local_spark_to_latex_call_signature(self):
        from app.gateway.speech_router import _local_spark_to_latex

        mock_router = MagicMock()
        mock_router.chat = AsyncMock(
            return_value={
                "content": "$\\sqrt{2}$",
                "provider": "spark",
                "model": "spark-ultra",
                "input_tokens": 10,
                "output_tokens": 5,
                "latency_ms": 50,
                "tool_calls": None,
            }
        )

        with patch("app.providers.router.get_model_router", return_value=mock_router):
            latex = await _local_spark_to_latex("根号二", None)

        assert latex == "\\sqrt{2}"
        mock_router.chat.assert_awaited_once()
        # 修复前缺 request_id/scene → TypeError，被吞后降级通道坏死
        kwargs = mock_router.chat.call_args.kwargs
        assert "request_id" in kwargs
        assert "scene" in kwargs
        assert kwargs["max_tokens"] == 200

    async def test_local_spark_to_latex_failure_returns_none(self):
        """模型调用异常时降级返回 None（不抛给主链路）"""
        from app.gateway.speech_router import _local_spark_to_latex

        mock_router = MagicMock()
        mock_router.chat = AsyncMock(side_effect=RuntimeError("All model providers failed"))

        with patch("app.providers.router.get_model_router", return_value=mock_router):
            latex = await _local_spark_to_latex("根号二", None)

        assert latex is None


# ========== F3. smart_quiz card 落库与还原 ==========


class TestQuizCardEnvelope:
    """card 事件必须入信封：落库 blocks 含 card，幂等重放与 GET messages 可还原"""

    QUIZ_JSON = json.dumps(
        {
            "question_text": "求函数 $f(x)=x^2$ 在 $x=1$ 处的导数",
            "q_type": "short_answer",
            "options": [],
            "answer": "2",
            "answer_analysis": "由幂函数求导公式 $f'(x)=2x$，故 $f'(1)=2$",
        },
        ensure_ascii=False,
    )

    def _make_mocks(self, user_message: str):
        from app.kernel.router import RouteDecision

        decision = RouteDecision(
            skill_id="smart_quiz",
            confidence=0.9,
            params={"question": user_message},
        )
        mock_intent = MagicMock()
        mock_intent.route = AsyncMock(return_value=decision)

        mock_router = MagicMock()
        mock_router.intended_provider = "spark"
        mock_router.chat = AsyncMock(
            return_value={
                "content": self.QUIZ_JSON,
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

    async def test_card_persisted_and_replayed(self, client):
        user = await self._create_user()
        headers = self._auth_headers(user)
        client_msg_id = uuid.uuid4().hex[:20]
        user_message = "给我一道导数题"

        mock_intent, mock_router = self._make_mocks(user_message)

        with (
            patch("app.gateway.agent_router.get_intent_router", return_value=mock_intent),
            patch(
                "app.gateway.agent_router.get_model_router_for_user",
                new=AsyncMock(return_value=mock_router),
            ),
            patch("app.gateway.agent_router._bg_summary", new=AsyncMock()),
            patch("app.gateway.agent_router._start_title_task", return_value=None),
        ):
            resp = await client.post(
                "/api/agent/chat",
                json={
                    "message": user_message,
                    "context": {"client_msg_id": client_msg_id, "workspace": "student"},
                },
                headers=headers,
            )
            assert resp.status_code == 200
            events = _parse_sse(resp.text)

            # 实时流已透传 card 事件
            assert "card" in events, f"SSE 缺 card 事件: {list(events)}"
            assert events["card"][0]["type"] == "quiz_set"

            # ① 落库：assistant 信封 blocks 含 card block
            async with _test_session_factory() as session:
                result = await session.execute(
                    select(Message).where(Message.client_msg_id == f"ai_{client_msg_id}")
                )
                msg = result.scalar_one()
                blocks = (msg.envelope or {}).get("blocks", [])
                card_blocks = [b for b in blocks if b.get("type") == "card"]
                assert len(card_blocks) == 1, f"信封缺 card block: {blocks}"
                assert card_blocks[0]["data"]["type"] == "quiz_set"
                assert card_blocks[0]["data"]["items"][0]["answer"] == "2"

            # ② 幂等重放：同 client_msg_id 再发 → 还原 card 事件
            resp2 = await client.post(
                "/api/agent/chat",
                json={
                    "message": user_message,
                    "context": {"client_msg_id": client_msg_id, "workspace": "student"},
                },
                headers=headers,
            )
            assert resp2.status_code == 200
            assert resp2.headers.get("X-Idempotent-Replay", "").lower() == "true"
            replay_events = _parse_sse(resp2.text)
            assert "card" in replay_events, f"重放缺 card 事件: {list(replay_events)}"
            assert replay_events["card"][0]["type"] == "quiz_set"

            # ③ GET messages：历史消息信封含 card block
            conversation_id = replay_events["meta"][0]["conversation_id"]
            msg_resp = await client.get(
                f"/api/agent/conversations/{conversation_id}/messages",
                headers=headers,
            )
            assert msg_resp.status_code == 200
            items = msg_resp.json()["data"]["items"]
            assistant_items = [i for i in items if i["role"] == "assistant"]
            assert assistant_items, "历史消息缺 assistant 消息"
            env_blocks = (assistant_items[0]["envelope"] or {}).get("blocks", [])
            assert any(b.get("type") == "card" for b in env_blocks)


# ========== F5. thinking per-call 覆盖 ==========


class TestThinkingOverride:
    """thinking=None 按 provider 默认（关）；显式 thinking=True 才开启"""

    def test_spark_payload_thinking_default_disabled(self):
        with patch("app.providers.spark.settings") as mock_settings:
            mock_settings.spark_api_password = "test-password"
            mock_settings.spark_model = "spark-ultra"
            mock_settings.spark_thinking = False
            # 与真实 config 默认一致（app/config.py llm_omit_thinking_field=False）：
            # MagicMock 的自动属性恒真，不显式钉 False 会被误当作"全局省略 thinking"
            mock_settings.llm_omit_thinking_field = False
            from app.providers.spark import SparkProvider

            provider = SparkProvider()
            payload = provider._build_payload([{"role": "user", "content": "hi"}])
            assert payload["thinking"] == {"type": "disabled"}

    def test_spark_payload_thinking_per_call_enabled(self):
        with patch("app.providers.spark.settings") as mock_settings:
            mock_settings.spark_api_password = "test-password"
            mock_settings.spark_model = "spark-ultra"
            mock_settings.spark_thinking = False
            mock_settings.llm_omit_thinking_field = False  # 同真实 config 默认，见上
            from app.providers.spark import SparkProvider

            provider = SparkProvider()
            payload = provider._build_payload(
                [{"role": "user", "content": "hi"}], thinking=True
            )
            assert payload["thinking"] == {"type": "enabled"}

    def test_deepseek_payload_thinking_per_call_enabled(self):
        with patch("app.providers.deepseek.settings") as mock_settings:
            mock_settings.deepseek_api_key = "test-key"
            mock_settings.deepseek_model = "deepseek-v4-flash"
            mock_settings.deepseek_thinking = False
            mock_settings.llm_omit_thinking_field = False  # 同真实 config 默认
            from app.providers.deepseek import DeepSeekProvider

            provider = DeepSeekProvider()
            # 默认关：显式 disabled
            payload = provider._build_payload([{"role": "user", "content": "hi"}])
            assert payload["thinking"] == {"type": "disabled"}
            # per-call 开启：不下发 disabled（沿用 DeepSeek 既有语义）
            payload = provider._build_payload(
                [{"role": "user", "content": "hi"}], thinking=True
            )
            assert "thinking" not in payload

    async def test_model_router_chat_passes_thinking(self):
        from app.providers.base import ChatResult
        from app.providers.router import ModelRouter
        from app.providers.spark import SparkProvider

        spark = MagicMock(spec=SparkProvider)
        spark.available = True
        spark.chat = AsyncMock(
            return_value=ChatResult(
                content="ok",
                provider="spark",
                model="spark-ultra",
                input_tokens=1,
                output_tokens=1,
                latency_ms=10,
            )
        )
        deepseek = MagicMock()
        router = ModelRouter(spark=spark, deepseek=deepseek)

        await router.chat(
            [{"role": "user", "content": "hi"}],
            thinking=True,
            max_tokens=512,
            request_id="test-thinking",
            scene="chat",
        )
        kwargs = spark.chat.call_args.kwargs
        assert kwargs["thinking"] is True
        assert kwargs["max_tokens"] == 512


# ========== F6. 星辰审计参数名 ==========


class TestXingchenAudit:
    """_audit_log 必须用 log_ai_call 真实参数名（input_tokens/output_tokens）"""

    async def test_audit_log_calls_with_correct_kwargs(self):
        from app.providers.xingchen import _audit_log

        with patch("app.providers.audit.log_ai_call") as mock_log:
            await _audit_log(
                "wf_speech_to_latex",
                "user-1",
                "success",
                120,
                tokens_in=10,
                tokens_out=20,
                prompt_hash="abc123",
            )

        # 修复前 tokens_in=/tokens_out=/prompt_hash= → TypeError 被吞，审计全丢
        mock_log.assert_called_once()
        kwargs = mock_log.call_args.kwargs
        assert kwargs["provider"] == "xingchen"
        assert kwargs["scene"] == "wf_speech_to_latex"
        assert kwargs["input_tokens"] == 10
        assert kwargs["output_tokens"] == 20
        assert kwargs["latency_ms"] == 120
        assert kwargs["status"] == "success"
        assert "tokens_in" not in kwargs
        assert "tokens_out" not in kwargs
        assert "prompt_hash" not in kwargs

    async def test_audit_log_no_typeerror_with_real_signature(self):
        """与真实 log_ai_call 签名对齐：调用不应引发 TypeError"""
        import inspect

        from app.providers.audit import log_ai_call
        from app.providers.xingchen import _audit_log

        captured: dict = {}

        def _spy(**kwargs):
            # 用真实签名绑定参数，名字不对会在这里抛 TypeError
            inspect.signature(log_ai_call).bind(**kwargs)
            captured.update(kwargs)

        with patch("app.providers.audit.log_ai_call", side_effect=_spy):
            await _audit_log("quiz_generate", "user-2", "error", 5, error="HTTP 500")

        assert captured["scene"] == "wf_quiz_generate"  # 自动补 wf_ 前缀
        assert captured["input_tokens"] == 0
        assert captured["output_tokens"] == 0
        assert captured["error"] == "HTTP 500"
