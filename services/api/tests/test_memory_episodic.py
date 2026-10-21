"""情景记忆（学生长期记忆）测试

覆盖：
1. 信号量泄漏回归：get_model_router_for_user 抛异常 → 槽位计数复原；
   占槽后预处理异常（guard 抛错）→ 槽位同样释放（try/except 兜底路径）
2. 提取写库：mock LLM 返回 facts → 行写入（kind/content/source/conversation_id，
   embedding 失败落 NULL）；空 facts → 不写；重复 content → 去重跳过；
   LLM 不可用 → 静默跳过
3. P6 注入：预置记忆 → 装配文本含【学生长期记忆】+ 日期；kind 优先级排序；
   超 200 token 预算截断；30 天外记忆不注入
4. memories 端点：GET 合并 episodic 项（episodic:kind）；DELETE 软删生效；
   越权删除返回 40401
5. chat 全链路接线：done 后 BackgroundTasks 提取写库（fake skill + mock LLM）

需要 PostgreSQL + Redis 运行中（与 test_iter05_chat 同环境/同模式）。
"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.gateway import agent_router as ar
from app.kernel import memory as memory_mod
from app.kernel.context import ContextAssembler, _estimate_tokens
from app.kernel.memory import MemoryManager
from app.kernel.router import RouteDecision
from app.main import app
from app.models.conversation import Conversation
from app.models.database import get_db
from app.models.episodic_memory import EpisodicMemory
from app.models.user import User
from app.providers.embedding import EmbeddingProvider

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


def _chat_body(client_msg_id: str, message: str = "我不理解诱导公式，能换个方式讲讲吗") -> dict:
    return {
        "message": message,
        "context": {"client_msg_id": client_msg_id, "workspace": "student"},
    }


def _sse_events(text: str) -> list[str]:
    return [ln[7:] for ln in text.split("\n") if ln.startswith("event: ")]


async def _seed_memory(
    user_id: str,
    kind: str,
    content: str,
    *,
    created_at: datetime | None = None,
) -> str:
    """直接种一条情景记忆（embedding NULL），返回 memory id"""
    async with _test_session_factory() as s:
        mem = EpisodicMemory(user_id=uuid.UUID(user_id), kind=kind, content=content, source="chat")
        if created_at is not None:
            mem.created_at = created_at
        s.add(mem)
        await s.commit()
        return str(mem.id)


class _FakeChatRouter:
    """假 ModelRouter：chat 返回固定 content，并记录调用参数"""

    intended_provider = "fake"

    def __init__(self, content: str):
        self._content = content
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return {"content": self._content, "provider": "fake"}


async def _embed_boom(self, texts, **kwargs):
    """embedding 服务不可用（假）：任何调用直接抛错"""
    raise RuntimeError("embedding service down")


# ==================== 1. 信号量泄漏回归 ====================


class TestSseSemaphoreRelease:
    async def test_router_failure_keeps_semaphore(self, auth_client):
        """get_model_router_for_user 抛异常 → 信号量/用户计数复原（历史 bug 回归）"""
        client, token, user_id = auth_client
        before = ar._global_sse_semaphore._value

        async def _boom(*args, **kwargs):
            raise RuntimeError("router boom")

        with (
            patch.object(ar, "get_model_router_for_user", side_effect=_boom),
            pytest.raises(RuntimeError),
        ):
            await client.post(
                "/api/agent/chat",
                json=_chat_body(str(uuid.uuid4())),
                headers=_headers(token),
            )

        assert ar._global_sse_semaphore._value == before
        assert ar._user_sse_counts.get(user_id, 0) == 0

    async def test_pre_stream_exception_releases_semaphore(self, auth_client):
        """占槽后预处理异常（guard 抛错）→ try/except 兜底释放槽位"""
        client, token, user_id = auth_client
        before = ar._global_sse_semaphore._value

        class _BoomGuard:
            async def check_input(self, *args, **kwargs):
                raise RuntimeError("guard boom")

        with (
            patch.object(ar, "get_guard", return_value=_BoomGuard()),
            pytest.raises(RuntimeError),
        ):
            await client.post(
                "/api/agent/chat",
                json=_chat_body(str(uuid.uuid4())),
                headers=_headers(token),
            )

        assert ar._global_sse_semaphore._value == before
        assert user_id not in ar._user_sse_counts


# ==================== 2. 提取写库 ====================


class TestEpisodicExtractStore:
    async def test_facts_written_with_null_embedding(self, auth_client):
        """mock LLM 返回 facts → 写行；embedding 失败落 NULL 不影响写库；未知 kind 归 note"""
        _, _, user_id = auth_client
        conv_id = str(uuid.uuid4())
        facts_json = json.dumps(
            {
                "facts": [
                    {"kind": "weak_kp", "content": "三角恒等变换"},
                    {"kind": "preference", "content": "喜欢分步推导"},
                    {"kind": "bad_kind", "content": "应归为note"},
                ]
            },
            ensure_ascii=False,
        )
        fake = _FakeChatRouter(facts_json)
        with (
            patch.object(memory_mod, "get_model_router", return_value=fake),
            patch.object(EmbeddingProvider, "embed", _embed_boom),
        ):
            async with _db() as s:
                n = await MemoryManager().extract_and_store_episodic(
                    user_id=user_id,
                    conversation_id=conv_id,
                    user_message="我还是不懂三角恒等变换",
                    assistant_message="我们分步推导一遍……",
                    db=s,
                    request_id="t1",
                )
        assert n == 3

        # 在 session 内物化为字典（_db 退出时 rollback 会过期 ORM 属性）
        async with _db() as s:
            rows = (
                (await s.execute(select(EpisodicMemory).where(EpisodicMemory.user_id == user_id)))
                .scalars()
                .all()
            )
            by_content = {
                r.content: {
                    "kind": r.kind,
                    "source": r.source,
                    "conversation_id": str(r.conversation_id),
                    "embedding": r.embedding,
                }
                for r in rows
            }
        assert by_content["三角恒等变换"]["kind"] == "weak_kp"
        assert by_content["喜欢分步推导"]["kind"] == "preference"
        assert by_content["应归为note"]["kind"] == "note"  # 未知 kind 防脏值
        for info in by_content.values():
            assert info["source"] == "chat"
            assert info["conversation_id"] == conv_id
            assert info["embedding"] is None  # embedding 失败落 NULL

        # 轻量调用纪律：t=0、max_tokens≤300、提取 prompt 只取学习事实
        call = fake.calls[0]
        assert call["temperature"] == 0
        assert call["max_tokens"] <= 300
        prompt_text = call["messages"][0]["content"]
        assert "忽略闲聊与个人隐私" in prompt_text

    async def test_empty_facts_writes_nothing(self, auth_client):
        """空 facts（无可提取内容）→ 不写库"""
        _, _, user_id = auth_client
        fake = _FakeChatRouter('{"facts": []}')
        with patch.object(memory_mod, "get_model_router", return_value=fake):
            async with _db() as s:
                n = await MemoryManager().extract_and_store_episodic(
                    user_id=user_id,
                    conversation_id=str(uuid.uuid4()),
                    user_message="你好",
                    assistant_message="你好，有什么可以帮你？",
                    db=s,
                )
        assert n == 0
        async with _db() as s:
            cnt = await s.execute(
                select(func.count())
                .select_from(EpisodicMemory)
                .where(EpisodicMemory.user_id == user_id)
            )
        assert cnt.scalar() == 0

    async def test_duplicate_content_skipped(self, auth_client):
        """已有同 content 活跃行 → 跳过；软删行不挡去重（可重新写入）"""
        _, _, user_id = auth_client
        await _seed_memory(user_id, "weak_kp", "三角恒等变换")

        facts_json = (
            '{"facts":[{"kind":"weak_kp","content":"三角恒等变换"},'
            '{"kind":"goal","content":"备战期中考试"}]}'
        )
        fake = _FakeChatRouter(facts_json)
        with (
            patch.object(memory_mod, "get_model_router", return_value=fake),
            patch.object(EmbeddingProvider, "embed", _embed_boom),
        ):
            async with _db() as s:
                n = await MemoryManager().extract_and_store_episodic(
                    user_id=user_id,
                    conversation_id=str(uuid.uuid4()),
                    user_message="u",
                    assistant_message="a",
                    db=s,
                )
        assert n == 1  # 重复的 weak_kp 跳过，只写新 goal

        # 在 session 内物化（_db 退出时 rollback 会过期 ORM 属性）
        async with _db() as s:
            rows = (
                (await s.execute(select(EpisodicMemory).where(EpisodicMemory.user_id == user_id)))
                .scalars()
                .all()
            )
            contents = [r.content for r in rows]
        assert sorted(contents) == ["三角恒等变换", "备战期中考试"]
        assert contents.count("三角恒等变换") == 1  # 未产生重复行

        # 软删该行后再次提取同 content → 活跃行缺失，应重新写入
        async with _test_session_factory() as s:
            row = (
                await s.execute(
                    select(EpisodicMemory).where(
                        EpisodicMemory.user_id == user_id,
                        EpisodicMemory.content == "三角恒等变换",
                    )
                )
            ).scalar_one()
            row.deleted_at = datetime.now(UTC)
            await s.commit()
        with (
            patch.object(memory_mod, "get_model_router", return_value=fake),
            patch.object(EmbeddingProvider, "embed", _embed_boom),
        ):
            async with _db() as s:
                n = await MemoryManager().extract_and_store_episodic(
                    user_id=user_id,
                    conversation_id=str(uuid.uuid4()),
                    user_message="u",
                    assistant_message="a",
                    db=s,
                )
        assert n == 1

    async def test_llm_failure_silent_skip(self, auth_client):
        """LLM 不可用（用户未配置模型）→ 静默跳过，不写库不抛异常"""
        _, _, user_id = auth_client

        class _BoomRouter:
            async def chat(self, *args, **kwargs):
                raise RuntimeError("no model configured")

        with patch.object(memory_mod, "get_model_router", return_value=_BoomRouter()):
            async with _db() as s:
                n = await MemoryManager().extract_and_store_episodic(
                    user_id=user_id,
                    conversation_id=str(uuid.uuid4()),
                    user_message="u",
                    assistant_message="a",
                    db=s,
                )
        assert n == 0
        async with _db() as s:
            cnt = await s.execute(
                select(func.count())
                .select_from(EpisodicMemory)
                .where(EpisodicMemory.user_id == user_id)
            )
        assert cnt.scalar() == 0


# ==================== 3. P6 注入 ====================


class TestP6Injection:
    async def test_working_memory_to_p6_text(self, auth_client):
        """预置记忆 → get_working_memory 检索 → 装配文本含【学生长期记忆】且超长截断"""
        _, _, user_id = auth_client
        uid = uuid.UUID(user_id)
        async with _test_session_factory() as s:
            conv = Conversation(user_id=uid, active_role="student", title="t")
            s.add(conv)
            await s.flush()
            conv_id = str(conv.id)
            s.add_all(
                [
                    # 长 note：单行约 230 token，超 200 预算应被丢弃
                    EpisodicMemory(
                        user_id=uid, kind="note", content="长内容" + "x" * 900, source="chat"
                    ),
                    EpisodicMemory(user_id=uid, kind="preference", content="喜欢分步推导", source="chat"),
                    EpisodicMemory(user_id=uid, kind="weak_kp", content="三角恒等变换", source="chat"),
                    # 30 天窗口外的旧记忆：不应注入
                    EpisodicMemory(
                        user_id=uid,
                        kind="weak_kp",
                        content="三十年前的旧记忆",
                        source="chat",
                        created_at=datetime.now(UTC) - timedelta(days=40),
                    ),
                ]
            )
            await s.commit()

        async with _db() as s:
            wm = await MemoryManager().get_working_memory(conv_id, s)
        # 降级路径（无向量行）：kind 优先级 weak_kp > preference > note
        assert [m["kind"] for m in wm.episodic_memories] == ["weak_kp", "preference", "note"]

        messages = await ContextAssembler().assemble(
            user_message="再讲讲三角恒等变换", working_memory=wm
        )
        system_text = messages[0]["content"]
        assert "【学生长期记忆】" in system_text
        assert "常错：三角恒等变换（" in system_text
        assert "偏好：喜欢分步推导" in system_text
        assert system_text.index("常错：") < system_text.index("偏好：")  # weak_kp 优先
        assert "三十年前的旧记忆" not in system_text  # 30 天窗口外
        assert "长内容" not in system_text  # 超 200 token 预算截断

    def test_format_episodic_budget_and_shape(self):
        """纯单元：注入格式（标题/前缀/日期）与 ≤200 token 预算截断"""
        mems = [
            {"kind": "weak_kp", "content": "三角恒等变换", "created_at": datetime(2026, 8, 1)},
            {"kind": "note", "content": "x" * 900, "created_at": None},
        ]
        text = ContextAssembler()._format_episodic_memories(mems)
        assert text.startswith("【学生长期记忆】\n")
        assert "- 常错：三角恒等变换（2026-08-01）" in text
        assert "x" * 100 not in text  # 超长条目整条丢弃
        assert _estimate_tokens(text) <= 200
        # 空输入 → 空文本（不注入裸标题）
        assert ContextAssembler()._format_episodic_memories([]) == ""

    async def test_cosine_recall_with_vectors(self, auth_client):
        """有向量行且 query 可算 embedding → cosine 距离 top3（mock embedding 服务）"""
        _, _, user_id = auth_client
        uid = uuid.UUID(user_id)
        # 三条向量记忆：与 query 同向的最近、反向的最远
        near = [1.0] + [0.0] * 1023
        mid = [0.5] * 1024
        far = [-1.0] + [0.0] * 1023
        async with _test_session_factory() as s:
            s.add_all(
                [
                    EpisodicMemory(user_id=uid, kind="note", content="最远记忆", source="chat", embedding=far),
                    EpisodicMemory(user_id=uid, kind="note", content="中间记忆", source="chat", embedding=mid),
                    EpisodicMemory(user_id=uid, kind="note", content="最近记忆", source="chat", embedding=near),
                ]
            )
            await s.commit()

        async def _fake_embed(self, texts, **kwargs):
            return [[1.0] + [0.0] * 1023 for _ in texts]

        with patch.object(EmbeddingProvider, "embed", _fake_embed):
            async with _db() as s:
                mems = await MemoryManager().get_episodic_memories(
                    uid, s, query_text="三角恒等变换"
                )
        assert [m["content"] for m in mems] == ["最近记忆", "中间记忆", "最远记忆"]

    async def test_no_memory_no_p6(self, auth_client):
        """无任何记忆 → system 文本不含【学生长期记忆】（注入失败降级为空）"""
        _, _, user_id = auth_client
        uid = uuid.UUID(user_id)
        async with _test_session_factory() as s:
            conv = Conversation(user_id=uid, active_role="student", title="t")
            s.add(conv)
            await s.flush()
            conv_id = str(conv.id)
            await s.commit()
        async with _db() as s:
            wm = await MemoryManager().get_working_memory(conv_id, s)
        assert wm.episodic_memories == []
        messages = await ContextAssembler().assemble(user_message="你好", working_memory=wm)
        assert "学生长期记忆" not in messages[0]["content"]


# ==================== 4. memories 端点 ====================


class TestMemoriesEndpoint:
    async def test_get_includes_episodic(self, auth_client):
        """GET /api/agent/memories 合并 episodic 行（kind 前缀 episodic:）"""
        client, token, user_id = auth_client
        mem_id = await _seed_memory(user_id, "weak_kp", "三角恒等变换")

        resp = await client.get("/api/agent/memories", headers=_headers(token))
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == 0
        epi_items = [i for i in body["data"]["items"] if i["memory_id"].startswith("epi_")]
        assert len(epi_items) == 1
        item = epi_items[0]
        assert item["memory_id"] == f"epi_{mem_id}"
        assert item["kind"] == "episodic:weak_kp"
        assert item["content"] == "三角恒等变换"
        assert item["created_at"] and item["updated_at"]

    async def test_delete_episodic_soft_delete(self, auth_client):
        """DELETE /api/agent/memories/epi_<id> → 软删生效，GET 不再出现"""
        client, token, user_id = auth_client
        mem_id = await _seed_memory(user_id, "goal", "备战期中考试")

        resp = await client.delete(f"/api/agent/memories/epi_{mem_id}", headers=_headers(token))
        assert resp.json()["code"] == 0

        async with _db() as s:
            row = await s.get(EpisodicMemory, uuid.UUID(mem_id))
            assert row.deleted_at is not None  # 软删（与全库删除风格一致）

        resp2 = await client.get("/api/agent/memories", headers=_headers(token))
        assert all(
            i["memory_id"] != f"epi_{mem_id}" for i in resp2.json()["data"]["items"]
        )
        # 重复删除 → 40401
        resp3 = await client.delete(f"/api/agent/memories/epi_{mem_id}", headers=_headers(token))
        assert resp3.json()["code"] == 40401

    async def test_delete_episodic_foreign_not_found(self, auth_client):
        """删除他人记忆 → 40401（越权不泄露存在性）"""
        client, token, _ = auth_client
        # 直接库内种另一个用户（避免测试体内再走 sms-code 登录：fixture 与测试
        # 分属不同 event loop，redis 客户端跨 loop 复用会抛 RuntimeError）
        async with _test_session_factory() as s:
            other = User(phone=f"139{str(uuid.uuid4().int)[:8]}", nickname="他人")
            s.add(other)
            await s.commit()
            other_user_id = str(other.id)
        mem_id = await _seed_memory(other_user_id, "weak_kp", "他人的记忆")

        resp = await client.delete(f"/api/agent/memories/epi_{mem_id}", headers=_headers(token))
        assert resp.json()["code"] == 40401
        async with _db() as s:
            row = await s.get(EpisodicMemory, uuid.UUID(mem_id))
            assert row.deleted_at is None  # 未被删除


# ==================== 5. chat 全链路接线（BackgroundTasks 提取） ====================


class _FakeSkill:
    """假 skill：输出单 token（与 test_iter05_chat 同套路）"""

    async def run(self, params, ctx):
        yield {"type": "token", "data": {"text": "ok"}}


class _FakeRegistry:
    def get(self, skill_id):
        return _FakeSkill()


class _FakeIntentRouter:
    async def route(self, *args, **kwargs):
        return RouteDecision(skill_id="chat", confidence=0.95, params={})


class TestChatEpisodicWiring:
    async def test_chat_done_triggers_episodic_extract(self, auth_client):
        """chat done 后 BackgroundTasks 提取本轮对话写库（source=chat，携带 conversation_id）"""
        client, token, user_id = auth_client
        facts_json = (
            '{"facts":[{"kind":"weak_kp","content":"诱导公式"},'
            '{"kind":"preference","content":"喜欢配图讲解"}]}'
        )
        fake = _FakeChatRouter(facts_json)
        with (
            patch.object(ar, "get_intent_router", return_value=_FakeIntentRouter()),
            patch.object(ar, "get_skill_registry", return_value=_FakeRegistry()),
            patch.object(memory_mod, "get_model_router", return_value=fake),
            patch.object(ar, "get_model_router_for_user", new=AsyncMock(return_value=fake)),
            patch.object(EmbeddingProvider, "embed", _embed_boom),
        ):
            resp = await client.post(
                "/api/agent/chat",
                json=_chat_body(str(uuid.uuid4())),
                headers=_headers(token),
            )
        assert resp.status_code == 200
        assert "done" in _sse_events(resp.text)

        # BackgroundTasks 随 ASGI 响应周期执行完毕：情景记忆应已写库
        async with _db() as s:
            rows = (
                (await s.execute(select(EpisodicMemory).where(EpisodicMemory.user_id == user_id)))
                .scalars()
                .all()
            )
            stored = [
                {
                    "content": r.content,
                    "source": r.source,
                    "conversation_id": r.conversation_id,
                    "embedding": r.embedding,
                }
                for r in rows
            ]
        assert sorted(r["content"] for r in stored) == ["喜欢配图讲解", "诱导公式"]
        for r in stored:
            assert r["source"] == "chat"
            assert r["conversation_id"] is not None
            assert r["embedding"] is None  # embedding 服务不可用 → NULL
