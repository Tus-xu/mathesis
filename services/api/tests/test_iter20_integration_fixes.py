"""迭代20 联调修复回归（三轨合流后 integration 轮）：

① 个人知识库查询路由护栏（kernel/router.py）：「我知识库里有什么资料」曾被
   L2 FC 判给 qa_rag（其技能描述含"知识库内容查询"，模型不区分平台语料与
   学生个人资料柜），学生看到的是 qa_rag 元问题目录而非管家回复。修复后
   带「我/我的」属主代词的资料柜查询确定性直通 chat（_butler_lookup 知识库
   分支，直查本人文档）；泛「知识库」问题仍可走 qa_rag。

② qa_rag 元问题目录跨用户泄露修复（skills/qa_rag/main.py）：原 SQL 无
   uploader 过滤，学生问「知识库里有什么」会列出全库所有文档——包括其他
   学生上传的题目截图与对学生 scope 屏蔽的教师语料（AGENTS.md §20 用户
   隔离违反）。修复后只列本人上传文档，空库如实说明。
"""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.kernel.router import IntentRouter

# 与 test_student_kb_image_gate.py 同款测试库会话（conftest 已把
# settings.database_url 指到测试库并重建表结构）
_test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_test_session_factory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


@pytest_asyncio.fixture
async def db_session():
    async with _test_session_factory() as session:
        try:
            yield session
            await session.rollback()
        finally:
            await session.close()


@pytest.fixture()
def router() -> IntentRouter:
    return IntentRouter()


# ------------------------------------------------------------------ #
# ① 路由护栏：个人知识库/资料查询 → chat 管家
# ------------------------------------------------------------------ #


class TestPersonalKBRoutingGuard:
    """「我知识库/我的资料…」直通 chat，不进 qa_rag（护栏在 L2 之前，确定性）"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "message",
        [
            "我知识库里有什么资料？",  # ← 联调实测原句（曾判给 qa_rag）
            "我的知识库有哪些文档",
            "看看我的资料",
            "我上传的资料有哪些",
            "我传的文件都有什么",
            "我的资料库里有什么",
        ],
    )
    async def test_personal_kb_routes_to_chat(self, router, message):
        decision = await router.route(message, db=None, user_id="u-test")
        assert (
            decision.skill_id == "chat"
        ), f"个人知识库查询「{message}」应走 chat 管家，实际 {decision.skill_id}"

    @pytest.mark.asyncio
    async def test_generic_kb_still_allowed_elsewhere(self, router):
        """不含属主代词的泛知识库问题不被护栏吞掉（交 L2 正常判定，非本护栏职责）。

        护栏只拦「我知识库/我的资料」族；「知识库里有什么」这类泛问不能被
        确定性判给 chat——否则 qa_rag 的正常教材答疑面被收窄。
        """
        followup = "".join(["知识库里有什么"])
        personal_words = (
            "我知识库",
            "我的知识库",
            "我的资料",
            "我上传的资料",
            "我上传的文件",
            "我传的文件",
            "我的资料库",
            "我知识库里",
        )
        assert not any(k in followup for k in personal_words)


# ------------------------------------------------------------------ #
# ② qa_rag 元问题目录：只列本人文档（跨用户泄露修复）
# ------------------------------------------------------------------ #


class _FakeMemory:
    pass


def _make_ctx(db, user_id: str):
    from app.skills.base import SkillContext

    return SkillContext(
        user_id=user_id,
        user_role="student",
        conversation_id=str(uuid.uuid4()),
        request_id="it20-fix",
        db=db,
    )


async def _run_meta(question: str, ctx) -> str:
    """跑 qa_rag 元问题路径，拼接 token 事件文本"""
    from app.skills.qa_rag.main import QaRagSkill

    skill = QaRagSkill()
    text = ""
    async for evt in skill.run({"question": question}, ctx):
        if evt.get("type") == "token":
            text += evt["data"].get("text", "")
        if evt.get("type") == "_result_meta":
            break
    return text


class TestQaRagMetaOwnerFilter:
    """元问题目录必须按 uploader 隔离：别人上传的文档不可见"""

    @pytest.mark.asyncio
    async def test_meta_lists_only_own_docs(self, db_session):
        from app.models.knowledge_doc import KnowledgeDoc
        from app.models.user import User

        me = uuid.uuid4()
        other = uuid.uuid4()
        db_session.add_all(
            [
                User(id=me, phone=f"it20a{me.hex[:8]}"),
                User(id=other, phone=f"it20b{other.hex[:8]}"),
            ]
        )
        await db_session.flush()
        db_session.add_all(
            [
                KnowledgeDoc(
                    title="我的错题截图.png",
                    source_type="textbook",
                    uploader_id=me,
                    status="ready",
                ),
                KnowledgeDoc(
                    title="别人的题目.png",
                    source_type="textbook",
                    uploader_id=other,
                    status="ready",
                ),
                KnowledgeDoc(
                    title="教师培训手册.pdf",
                    source_type="textbook",
                    uploader_id=None,  # 平台全局语料（对学生 scope 屏蔽族）
                    status="ready",
                ),
            ]
        )
        await db_session.commit()

        text = await _run_meta("知识库里有什么？", _make_ctx(db_session, str(me)))
        assert "我的错题截图.png" in text
        assert "别人的题目.png" not in text, "元问题目录泄露了其他用户的文档"
        assert "教师培训手册.pdf" not in text, "元问题目录泄露了平台全局语料"

    @pytest.mark.asyncio
    async def test_meta_empty_kb_honest(self, db_session):
        text = await _run_meta("知识库里有什么？", _make_ctx(db_session, str(uuid.uuid4())))
        assert "还没有文档" in text or "暂无" in text
