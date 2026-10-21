"""M1 修复回归测试（test_m1_fixes.py）

覆盖审查发现的 P0 修复项：
1. Function Calling 路由解析（原 bug：providers 不解析 tool_calls，FC 恒落 chat）
2. 滚动摘要触发（原 bug：maybe_update_summary 无调用方，死代码）
3. 幂等重放保真（原 bug：断流重发放回的是用户提问原文）
4. RAG 拒答闸门（原 bug：trgm 用 similarity 全灭 + RRF 分对 0.35 闸门恒拒答）

需要 PostgreSQL 运行中（与 test_api_integration 同环境）。
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
from app.kernel.router import IntentRouter
from app.main import app
from app.models.conversation import Conversation
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


async def _make_user_and_conv(session, *, message_count: int = 0):
    """创建测试用户 + 会话，返回 (user, conv)"""
    user = User(phone=f"139{uuid.uuid4().int % 100000000:08d}", nickname="")
    session.add(user)
    await session.flush()
    session.add(RoleBinding(user_id=user.id, role="student", verified=True))
    await session.flush()
    conv = Conversation(
        user_id=user.id, active_role="student", title="回归测试", message_count=message_count
    )
    session.add(conv)
    await session.flush()
    return user, conv


# ========== 1. Function Calling 路由解析 ==========


class TestFunctionCallingParse:
    """kernel/router._parse_fc_response：优先结构化 tool_calls，content-JSON 兜底"""

    def test_parse_tool_calls_preferred(self):
        router = IntentRouter()
        result = {
            "content": "",
            "tool_calls": [{"name": "qa_rag", "arguments": {"question": "什么是指数函数"}}],
        }
        decision = router._parse_fc_response(result, "什么是指数函数", {"qa_rag", "quiz"})
        assert decision is not None
        assert decision.skill_id == "qa_rag"
        assert decision.confidence == 0.85
        assert decision.params["question"] == "什么是指数函数"

    def test_parse_arguments_string_decoded(self):
        """arguments 为 JSON 字符串时应解码为 dict"""
        router = IntentRouter()
        result = {
            "content": "",
            "tool_calls": [{"name": "qa_rag", "arguments": '{"question": "如何求导"}'}],
        }
        decision = router._parse_fc_response(result, "如何求导", {"qa_rag"})
        assert decision is not None
        assert decision.skill_id == "qa_rag"
        assert decision.params["question"] == "如何求导"

    def test_parse_unknown_skill_returns_none(self):
        """模型 hallucinate 不存在的函数 → None（走 chat 兜底）"""
        router = IntentRouter()
        result = {
            "content": "",
            "tool_calls": [{"name": "hallucinated_skill", "arguments": {}}],
        }
        decision = router._parse_fc_response(result, "你好", {"qa_rag"})
        assert decision is None

    def test_parse_content_json_fallback(self):
        """content 中的 JSON 作为兜底路径"""
        router = IntentRouter()
        result = {
            "content": '{"name": "qa_rag", "arguments": {"question": "什么是向量"}, "confidence": 0.8}',
            "tool_calls": None,
        }
        decision = router._parse_fc_response(result, "什么是向量", {"qa_rag"})
        assert decision is not None
        assert decision.skill_id == "qa_rag"
        assert decision.confidence == 0.8

    def test_parse_plain_text_returns_none(self):
        """模型只回了普通文本（未调工具）→ None（走 chat 兜底）"""
        router = IntentRouter()
        result = {"content": "我无法理解这个问题", "tool_calls": None}
        decision = router._parse_fc_response(result, "你好", {"qa_rag"})
        assert decision is None

    def test_slash_commands_m2(self):
        """M2 slash 映射：答疑/解题/出题三类命令齐全且指向正确 skill"""
        from app.kernel.router import SLASH_COMMANDS

        assert SLASH_COMMANDS["/qa"] == "qa_rag"
        assert SLASH_COMMANDS["/答疑"] == "qa_rag"
        assert SLASH_COMMANDS["/chat"] == "chat"
        assert SLASH_COMMANDS["/solve"] == "socratic_solver"
        assert SLASH_COMMANDS["/解题"] == "socratic_solver"
        assert SLASH_COMMANDS["/quiz"] == "smart_quiz"
        assert SLASH_COMMANDS["/出题"] == "smart_quiz"


class TestRouterSolveBias:
    """迭代02 路由修复：系统提示词明确"发题/求解→解题函数"，函数描述附 manifest 正例"""

    async def test_functions_include_examples_and_solve_guidance(self):
        from unittest.mock import patch

        captured: dict = {}

        class FakeRouter:
            async def chat(self, messages, **kwargs):
                captured["messages"] = messages
                captured["functions"] = kwargs.get("functions")
                return {
                    "content": "",
                    "tool_calls": [
                        {"name": "socratic_solver", "arguments": {"question": "x^2=1 怎么解"}}
                    ],
                }

        skills = [
            {
                "id": "socratic_solver",
                "name": "引导式解题",
                "manifest": {
                    "description": "引导式解题（苏格拉底式教学）。",
                    "examples_positive": ["帮我解这道题", "这题怎么做"],
                },
            }
        ]
        with patch("app.kernel.router.get_model_router", return_value=FakeRouter()):
            decision = await IntentRouter()._function_calling_route(
                "x^2=1 怎么解", skills, request_id="t"
            )

        assert decision is not None
        assert decision.skill_id == "socratic_solver"
        sys_prompt = captured["messages"][0]["content"]
        assert "求解" in sys_prompt and "怎么做" in sys_prompt
        solver_fn = next(f for f in captured["functions"] if f["name"] == "socratic_solver")
        assert "帮我解这道题" in solver_fn["description"]


# ========== 2. 滚动摘要触发 ==========


class TestSummaryTrigger:
    """kernel/memory.maybe_update_summary：达到 SUMMARY_TRIGGER_COUNT 条消息触发 LLM 摘要写回"""

    async def test_summary_updates_at_threshold(self):
        from app.kernel.memory import SUMMARY_TRIGGER_COUNT, get_memory_manager

        async with _test_session_factory() as session:
            user, conv = await _make_user_and_conv(session, message_count=SUMMARY_TRIGGER_COUNT)
            for i in range(SUMMARY_TRIGGER_COUNT):
                role = "user" if i % 2 == 0 else "assistant"
                session.add(
                    Message(
                        conversation_id=conv.id,
                        client_msg_id=f"m{i}_{uuid.uuid4().hex[:8]}",
                        role=role,
                        content=f"第{i}条：讨论指数函数的定义与性质",
                    )
                )
            await session.commit()

            mock_router = MagicMock()
            mock_router.chat = AsyncMock(
                return_value={
                    "content": "用户与AI讨论了指数函数的定义和性质",
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "latency_ms": 50,
                    "tool_calls": None,
                }
            )
            with patch("app.kernel.memory.get_model_router", return_value=mock_router):
                updated = await get_memory_manager().maybe_update_summary(
                    str(conv.id), session, "test-req-summary"
                )

            assert updated is True
            mock_router.chat.assert_called_once()
            # scene 必须为 summary（成本核算维度）
            assert mock_router.chat.call_args.kwargs.get("scene") == "summary"

            result = await session.execute(
                select(Conversation.summary).where(Conversation.id == conv.id)
            )
            summary = result.scalar()
            assert summary == "用户与AI讨论了指数函数的定义和性质"

    async def test_summary_not_triggered_below_threshold(self):
        from app.kernel.memory import SUMMARY_TRIGGER_COUNT, get_memory_manager

        async with _test_session_factory() as session:
            # 低于阈值且非倍数 → 不触发
            user, conv = await _make_user_and_conv(session, message_count=SUMMARY_TRIGGER_COUNT - 1)
            await session.commit()

            mock_router = MagicMock()
            mock_router.chat = AsyncMock()
            with patch("app.kernel.memory.get_model_router", return_value=mock_router):
                updated = await get_memory_manager().maybe_update_summary(
                    str(conv.id), session, "test-req-no-trigger"
                )

            assert updated is False
            mock_router.chat.assert_not_called()


# ========== 3. 幂等重放保真 ==========


class TestIdempotentReplayFidelity:
    """断流重发必须回放 AI 回答完整信封，而不是用户提问原文（修复前 bug）"""

    async def test_replay_returns_assistant_envelope(self, client):
        from app.gateway.jwt import create_token_with_role

        client_msg_id = uuid.uuid4().hex[:20]
        answer_text = "指数函数是形如 y=a^x（a>0 且 a≠1）的函数【1】"
        citation_items = [
            {"n": 1, "chunk_id": "chunk-abc", "source": "必修一教材", "loc": "切片 chunk-ab"}
        ]
        envelope = {
            "msg_id": str(uuid.uuid4()),
            "role": "assistant",
            "blocks": [
                {"type": "markdown", "content": answer_text},
                {"type": "citation", "items": citation_items},
            ],
            "meta": {
                "skill": "qa_rag",
                "confidence": 0.85,
                "provider": "deepseek",
                "latency_ms": 123,
                "usage": {"tokens_in": 100, "tokens_out": 50},
                "badge": "L2-知识库",
                "ai_generated": True,
            },
        }

        async with _test_session_factory() as session:
            user, conv = await _make_user_and_conv(session, message_count=2)
            session.add_all(
                [
                    Message(
                        conversation_id=conv.id,
                        client_msg_id=client_msg_id,
                        role="user",
                        content="什么是指数函数",
                        envelope={"msg_id": str(uuid.uuid4()), "role": "user", "blocks": []},
                    ),
                    Message(
                        conversation_id=conv.id,
                        client_msg_id=f"ai_{client_msg_id}",
                        role="assistant",
                        content=answer_text,
                        envelope=envelope,
                        skill_id="qa_rag",
                    ),
                ]
            )
            await session.commit()
            token = create_token_with_role(
                user_id=str(user.id), role="student", roles=["student"], verified=True
            )

        resp = await client.post(
            "/api/agent/chat",
            json={
                "message": "什么是指数函数",
                "context": {"client_msg_id": client_msg_id, "workspace": "student"},
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("X-Idempotent-Replay", "").lower() == "true"

        events = _parse_sse(resp.text)

        # meta 还原
        assert "meta" in events
        assert events["meta"][0]["skill"] == "qa_rag"
        assert events["meta"][0]["provider"] == "deepseek"

        # token 回放的是 AI 回答，不是用户提问原文
        tokens = "".join(d.get("text", "") for d in events.get("token", []))
        assert answer_text in tokens
        assert tokens != "什么是指数函数"

        # citation / badge 还原
        assert "citation" in events
        assert events["citation"][0]["items"][0]["source"] == "必修一教材"
        assert "badge" in events
        assert events["badge"][0]["level"] == "L2-知识库"

        # done.usage 为落库的真实值（in != out，修复前为估算假值且相等）
        done = events["done"][0]
        assert done["usage"]["tokens_in"] == 100
        assert done["usage"]["tokens_out"] == 50


async def test_replay_preserves_web_search_source_fields(client):
    """citation 扩展字段（title/url/snippet/retrieved_at）经信封重放完整保留（阶段 6B）。"""
    from app.gateway.jwt import create_token_with_role

    client_msg_id = uuid.uuid4().hex[:20]
    answer_text = "根据联网检索结果【1】，可参考以下来源"
    citation_items = [
        {
            "n": 1,
            "chunk_id": "chunk-web-1",
            "source": "百度百科",
            "loc": "切片 chunk-we",
            "title": "导数概念",
            "url": "https://example.com/derivative",
            "snippet": "导数是函数的局部变化率……",
            "retrieved_at": "2026-08-21T10:00:00+00:00",
        }
    ]
    envelope = {
        "msg_id": str(uuid.uuid4()),
        "role": "assistant",
        "blocks": [
            {"type": "markdown", "content": answer_text},
            {"type": "citation", "items": citation_items},
        ],
        "meta": {
            "skill": "qa_rag",
            "confidence": 0.85,
            "provider": "deepseek",
            "latency_ms": 123,
            "usage": {"tokens_in": 100, "tokens_out": 50},
            "badge": "L2-知识库",
            "ai_generated": True,
        },
    }

    async with _test_session_factory() as session:
        user, conv = await _make_user_and_conv(session, message_count=2)
        session.add_all(
            [
                Message(
                    conversation_id=conv.id,
                    client_msg_id=client_msg_id,
                    role="user",
                    content="帮我查一下导数的定义",
                    envelope={"msg_id": str(uuid.uuid4()), "role": "user", "blocks": []},
                ),
                Message(
                    conversation_id=conv.id,
                    client_msg_id=f"ai_{client_msg_id}",
                    role="assistant",
                    content=answer_text,
                    envelope=envelope,
                    skill_id="qa_rag",
                ),
            ]
        )
        await session.commit()
        token = create_token_with_role(
            user_id=str(user.id), role="student", roles=["student"], verified=True
        )

    resp = await client.post(
        "/api/agent/chat",
        json={
            "message": "帮我查一下导数的定义",
            "context": {"client_msg_id": client_msg_id, "workspace": "student"},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    events = _parse_sse(resp.text)
    item = events["citation"][0]["items"][0]
    assert item["title"] == "导数概念"
    assert item["url"] == "https://example.com/derivative"
    assert item["snippet"] == "导数是函数的局部变化率……"
    assert item["retrieved_at"] == "2026-08-21T10:00:00+00:00"


# ========== 4. RAG 拒答闸门 ==========


class TestRagRefuseGate:
    """RAG 三路召回 + 拒答闸门：教材内可答 / 教材外拒答（修复前三重断路全灭）"""

    async def test_in_book_answerable(self):
        from sqlalchemy import delete as sa_delete

        from app.models.chunk import Chunk
        from app.models.knowledge_doc import KnowledgeDoc

        marker = uuid.uuid4().hex[:6]
        content = (
            f"星耀定理{marker}指出：任意三角形的三条中线交于一点，"
            "该点称为三角形的重心，重心将每条中线分为二比一的两段。"
        )

        created_doc_id = None
        try:
            async with _test_session_factory() as session:
                doc = KnowledgeDoc(
                    title=f"回归测试教材{marker}", source_type="textbook", status="ready"
                )
                session.add(doc)
                await session.flush()
                created_doc_id = doc.id
                session.add(Chunk(doc_id=doc.id, content=content, chunk_index=0))
                await session.commit()

                from contextlib import asynccontextmanager

                from app.kernel.rag import RAGPipeline

                pipeline = RAGPipeline()
                # mock async_session_factory：并行任务复用测试 session，避免测试环境连接问题

                @asynccontextmanager
                async def _mock_session():
                    yield session

                with patch("app.kernel.rag.async_session_factory", side_effect=_mock_session):
                    # 空 history → 跳过 LLM 改写，测试纯检索路径
                    result = await pipeline.retrieve(
                        f"星耀定理{marker}的内容是什么",
                        db=session,
                        conversation_history=[],
                        request_id="test-rag-in",
                    )

                assert result.answerable is True
                assert len(result.chunks) >= 1
                assert any(marker in c.content for c in result.chunks)
                # citation 字段完备
                assert result.chunks[0].chunk_id
        finally:
            # teardown：清理测试产生的 knowledge_doc 和 chunk，防止垃圾残留
            if created_doc_id is not None:
                async with _test_session_factory() as cleanup_session:
                    await cleanup_session.execute(
                        sa_delete(Chunk).where(Chunk.doc_id == created_doc_id)
                    )
                    await cleanup_session.execute(
                        sa_delete(KnowledgeDoc).where(KnowledgeDoc.id == created_doc_id)
                    )
                    await cleanup_session.commit()

    async def test_out_of_book_refused(self):
        from app.kernel.rag import RAGPipeline

        pipeline = RAGPipeline()
        async with _test_session_factory() as session:
            result = await pipeline.retrieve(
                "量子计算机的工作原理是什么",
                db=session,
                conversation_history=[],
                request_id="test-rag-out",
            )

            assert result.answerable is False
            assert result.refuse_reason in ("no_knowledge", "low_relevance")
