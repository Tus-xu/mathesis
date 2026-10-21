"""迭代10 v1.4 修复回归测试（test_iter10_v14_fixes.py）

覆盖本轮闭环审查的四个后端修复：
A1 解答题拍照作答 OCR 回填（_load_file_ocr_text；无产物 → pending_review 占位，不判 0 分污染错题本）
A2 引导完成后单说「举一反三」→ 变式种子回落（_recent_tutor_question 取最近 tutor_session 题干）
A3 练题中心意图拦截疑问/指代排除（"模拟卷第3题怎么做"不再误跳页）
A4 练题中心拦截 action 落 envelope + regenerate 重放拦截（历史回显/重新生成不丢按钮卡）

A4 为集成测试，需要 PostgreSQL 运行中（与其余 tests 同环境）。
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.main import app
from app.models.database import get_db

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


@pytest_asyncio.fixture
async def auth_client(client):
    phone = f"138{str(uuid.uuid4().int)[:8]}"
    await client.post("/api/auth/sms-code", json={"phone": phone})
    login_resp = await client.post("/api/auth/login", json={"phone": phone, "code": "123456"})
    data = login_resp.json()["data"]
    return client, data["token"], data["user"]["id"]


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _sse_datas(text: str, event_type: str) -> list[dict]:
    lines = text.split("\n")
    out = []
    for i, ln in enumerate(lines):
        if ln == f"event: {event_type}" and i + 1 < len(lines) and lines[i + 1].startswith("data: "):
            out.append(json.loads(lines[i + 1][6:]))
    return out


def _sse_data(text: str, event_type: str) -> dict | None:
    datas = _sse_datas(text, event_type)
    return datas[0] if datas else None


# ==================== A3. 意图拦截疑问/指代排除 ====================


class TestPracticeIntentQuestionExclusion:
    """match_practice_intent：疑问/指代消息不拦截，强意图仍拦截"""

    def test_strong_intent_still_intercepted(self):
        from app.services.platform_context import match_practice_intent

        hit = match_practice_intent("来一场60分钟全真模拟")
        assert hit is not None and hit["key"] == "exam"
        hit2 = match_practice_intent("帮我来一套模拟卷")
        assert hit2 is not None and hit2["key"] == "exam"
        hit3 = match_practice_intent("开始练题")
        assert hit3 is not None and hit3["key"] == "special"

    def test_question_messages_not_intercepted(self):
        from app.services.platform_context import match_practice_intent

        # 指代卷中具体题目
        assert match_practice_intent("这套模拟卷第3题怎么做") is None
        assert match_practice_intent("模拟卷第二道题讲讲") is None
        # 咨询区别/内容
        assert match_practice_intent("模拟考和专项训练有什么区别") is None
        assert match_practice_intent("模拟卷有哪些题型？") is None
        assert match_practice_intent("专项训练是什么") is None
        assert match_practice_intent("模拟考试能练什么内容呢") is None

    def test_inline_quiz_still_excluded(self):
        """迭代13 既有行为不回归：对话内出题词仍不拦截"""
        from app.services.platform_context import match_practice_intent

        assert match_practice_intent("给我出几道模拟卷里的题") is None
        assert match_practice_intent("来一道变式题") is None


# ==================== A2. 变式种子回落 ====================


class TestVariantSeedFallback:
    """引导完成后单说「举一反三」→ 回落取最近 tutor_session 题干作种子"""

    def test_detect_user_variant_requires_question(self):
        from app.skills.smart_quiz.main import SmartQuizExecutor

        # 单说触发词（无题目特征）→ 不直接命中变式链
        assert SmartQuizExecutor._detect_user_variant("举一反三") is False
        assert SmartQuizExecutor._detect_user_variant("再来一道") is False
        # 触发词 + 题目特征 → 命中（既有行为）
        assert (
            SmartQuizExecutor._detect_user_variant(
                "举一反三：已知函数 $f(x)=x^2$，求其导数"
            )
            is True
        )

    async def test_recent_tutor_question_found(self):
        from app.skills.smart_quiz.main import SmartQuizExecutor

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = "  已知四边形ABCD为梯形，求二面角  "
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)
        ctx = MagicMock()
        ctx.conversation_id = uuid.uuid4()
        ctx.db = db

        q = await SmartQuizExecutor()._recent_tutor_question(ctx)
        assert q == "已知四边形ABCD为梯形，求二面角"

    async def test_recent_tutor_question_no_db(self):
        from app.skills.smart_quiz.main import SmartQuizExecutor

        ctx = MagicMock()
        ctx.conversation_id = None
        ctx.db = None
        assert await SmartQuizExecutor()._recent_tutor_question(ctx) is None

    async def test_recent_tutor_question_empty(self):
        from app.skills.smart_quiz.main import SmartQuizExecutor

        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db = AsyncMock()
        db.execute = AsyncMock(return_value=result_mock)
        ctx = MagicMock()
        ctx.conversation_id = uuid.uuid4()
        ctx.db = db
        assert await SmartQuizExecutor()._recent_tutor_question(ctx) is None

    def test_parse_count_group_means_three(self):
        """「再来一组变式」（完成卡按钮话术）应出 3 道变式链（对齐愿景 1/3·2/3·3/3）"""
        from app.skills.smart_quiz.main import _parse_count

        assert _parse_count("再来一组难度递进的变式") == 3
        assert _parse_count("举一反三") == 1
        assert _parse_count("再来两道") == 2

    def test_latex_backslash_tolerant_parse(self):
        """变式链 JSON 数组含 LaTeX 反斜杠时，容错修复后可解析（v1.4/v1.5）

        两类形态都必须过：
        1. 非法单反斜杠（\\sqrt \\{）——补全后解析
        2. 合法双反斜杠（\\\\ge \\\\times，LaTeX 换行符）——必须原样保留，
           否则错补成三反斜杠直接炸解析（v1.5 实机实锤回归）
        """
        from app.skills.smart_quiz.main import _json_loads_lenient

        raw = (
            '[{"question_text": "已知 $\\{a_n\\}$ 求 $\\\\sqrt{x}$", '
            '"answer_analysis": "$S_5 = 2 \\\\times 5^2$，$n \\\\ge 2$"}]'
        )
        items = _json_loads_lenient(raw)
        assert isinstance(items, list)
        assert "sqrt" in items[0]["question_text"]
        assert "\\\\times" in items[0]["answer_analysis"] or "\\times" in items[0]["answer_analysis"]


# ==================== A1. 解答题 OCR 回填 ====================


class TestSolutionOcrBackfill:
    """_load_file_ocr_text：拍照解答的 OCR 产物回填"""

    async def test_backfill_success(self):
        from app.gateway.student_router import _load_file_ocr_text

        uid = uuid.uuid4()
        fid = uuid.uuid4()
        f = MagicMock()
        f.deleted_at = None
        f.user_id = uid
        f.status = "parsed"

        asset1 = MagicMock()
        asset1.content = "解：设 BC=a"
        asset2 = MagicMock()
        asset2.content = "则 AD=2a"
        assets_result = MagicMock()
        assets_result.scalars.return_value.all.return_value = [asset1, asset2]

        db = AsyncMock()
        db.get = AsyncMock(return_value=f)
        db.execute = AsyncMock(return_value=assets_result)

        text = await _load_file_ocr_text(db, str(fid), uid)
        assert text == "解：设 BC=a\n则 AD=2a"

    async def test_not_parsed_returns_none(self):
        from app.gateway.student_router import _load_file_ocr_text

        uid = uuid.uuid4()
        f = MagicMock()
        f.deleted_at = None
        f.user_id = uid
        f.status = "parsing"  # 仍在解析
        db = AsyncMock()
        db.get = AsyncMock(return_value=f)

        assert await _load_file_ocr_text(db, str(uuid.uuid4()), uid) is None

    async def test_cross_user_returns_none(self):
        from app.gateway.student_router import _load_file_ocr_text

        f = MagicMock()
        f.deleted_at = None
        f.user_id = uuid.uuid4()  # 别人传的文件
        f.status = "parsed"
        db = AsyncMock()
        db.get = AsyncMock(return_value=f)

        assert await _load_file_ocr_text(db, str(uuid.uuid4()), uuid.uuid4()) is None

    async def test_invalid_file_id_returns_none(self):
        from app.gateway.student_router import _load_file_ocr_text

        db = AsyncMock()
        assert await _load_file_ocr_text(db, "not-a-uuid", uuid.uuid4()) is None


# ==================== A2+. 变式触发词前置路由 ====================


class TestVariantPreRoute:
    """agent_router._variant_route_decision：「举一反三」必须进 smart_quiz，
    不能被 LLM 意图路由误判成 chat 闲聊（E2E 实测复现的真实缺口）"""

    def test_trigger_routes_to_smart_quiz(self):
        from app.gateway.agent_router import _variant_route_decision

        d = _variant_route_decision("举一反三")
        assert d is not None and d.skill_id == "smart_quiz"
        assert d.params["question"] == "举一反三"
        d2 = _variant_route_decision("再来一道类似的题")
        assert d2 is not None and d2.skill_id == "smart_quiz"

    def test_normal_message_no_preroute(self):
        from app.gateway.agent_router import _variant_route_decision

        assert _variant_route_decision("什么是导数") is None
        assert _variant_route_decision("") is None


# ==================== A4. action 落 envelope + regenerate 重放 ====================


class TestPracticeIntentActionPersistence:
    """练题中心拦截：SSE 有 action 事件，envelope.blocks 落 action 块；
    regenerate 重放拦截（新兄弟版本 + 旧版本 supersede）。"""

    async def _send_mock_intent(self, client, token):
        cmid = str(uuid.uuid4())
        resp = await client.post(
            "/api/agent/chat",
            json={
                "message": "来一场60分钟全真模拟",
                "context": {"client_msg_id": cmid, "workspace": "student"},
            },
            headers=_headers(token),
        )
        assert resp.status_code == 200
        return resp.text

    async def test_intercept_sse_and_envelope_action_block(self, auth_client):
        client, token, _uid = auth_client
        text = await self._send_mock_intent(client, token)

        # SSE：确认语 token + action 事件 + done
        tokens = "".join(d["text"] for d in _sse_datas(text, "token"))
        assert "全真模拟" in tokens
        action = _sse_data(text, "action")
        assert action is not None
        assert action["kind"] == "open_page"
        assert action["to"] == "/student/practice-lab"
        assert "mode=exam" in action["params"]
        done = _sse_data(text, "done")
        assert done and done.get("message_id")

        # 落库：envelope.blocks 含 action 块（历史回显可还原按钮卡）
        meta = _sse_data(text, "meta")
        conv_id = meta["conversation_id"]
        resp = await client.get(
            f"/api/agent/conversations/{conv_id}/messages", headers=_headers(token)
        )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assistant = [m for m in items if m["role"] == "assistant"]
        assert assistant, "assistant 消息应落库"
        blocks = (assistant[-1].get("envelope") or {}).get("blocks") or []
        action_blocks = [b for b in blocks if b.get("type") == "action"]
        assert action_blocks, "envelope.blocks 应包含 action 块（v1.4）"
        assert action_blocks[0]["data"]["to"] == "/student/practice-lab"

    async def test_regenerate_replays_intercept(self, auth_client):
        client, token, _uid = auth_client
        text = await self._send_mock_intent(client, token)
        meta = _sse_data(text, "meta")
        conv_id = meta["conversation_id"]
        msg_id = _sse_data(text, "done")["message_id"]

        # regenerate 同一消息 → 重放拦截（action 事件仍在），不走 chat skill 自由生成
        resp = await client.post(
            "/api/agent/chat/regenerate",
            json={"conversation_id": conv_id, "message_id": msg_id},
            headers=_headers(token),
        )
        assert resp.status_code == 200
        re_action = _sse_data(resp.text, "action")
        assert re_action is not None, "regenerate 应重放 action 事件（v1.4）"
        assert re_action["kind"] == "open_page"
        re_tokens = "".join(d["text"] for d in _sse_datas(resp.text, "token"))
        assert "全真模拟" in re_tokens

        # 兄弟版本：旧版本 supersede，仅一个新活动版本
        resp = await client.get(
            f"/api/agent/conversations/{conv_id}/messages", headers=_headers(token)
        )
        items = resp.json()["data"]["items"]
        assistants = [m for m in items if m["role"] == "assistant"]
        assert len(assistants) == 1, "旧版本应被 supersede，历史仅留活动版本"
        blocks = (assistants[0].get("envelope") or {}).get("blocks") or []
        assert any(b.get("type") == "action" for b in blocks)
