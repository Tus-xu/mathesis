"""错题本录入确定性链路测试（2026-10-21）。

覆盖：
1. precheck 命中 error_book 阶段并路由 chat（含 error_book_intent 参数）；
2. `_upsert_error_record` 写入/合并学生自述错因（note）；
3. ErrorRecordCreate 契约接受 note。

需要 PostgreSQL 运行中。
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.kernel.precheck import run_precheck
from app.gateway.student_router import ErrorRecordCreate, _upsert_error_record
from app.models.coursework import ErrorRecord

_test_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_test_session_factory = async_sessionmaker(
    _test_engine, class_=AsyncSession, expire_on_commit=False
)


def test_precheck_routes_error_book_intent():
    result = run_precheck("把这道题加入错题本：已知 f(x)=x^3-3x+1，求极值。我错在只算了 f'(x)=0。")
    assert result is not None and result.kind == "route" and result.stage == "error_book"
    assert result.decision.skill_id == "chat"
    assert result.decision.params.get("error_book_intent") is True


def test_precheck_open_error_book_still_page_intent():
    result = run_precheck("打开错题本")
    assert result is not None and result.kind == "page_intent"


def test_error_record_create_accepts_note():
    req = ErrorRecordCreate(
        question_text="已知 f(x)=x^3-3x+1，求极值。",
        source_channel="chat_command",
        note="我错在只算了 f'(x)=0。",
    )
    assert req.note == "我错在只算了 f'(x)=0。"


@pytest.mark.asyncio
async def test_upsert_error_record_merges_note():
    async with _test_session_factory() as session:
        from app.models.role_binding import RoleBinding
        from app.models.user import User

        user = User(phone=f"139{uuid.uuid4().int % 100000000:08d}", nickname="错题本录入测试")
        session.add(user)
        await session.flush()
        session.add(RoleBinding(user_id=user.id, role="student", verified=True))
        await session.commit()

        q = "已知 f(x)=x^3-3x+1，求 f(x) 的极值。"
        record, created = await _upsert_error_record(
            session,
            user.id,
            question_text=q,
            source_channel="chat_command",
            error_type="logic",
            origin="chat_quiz",
            note="我错在只算了 f'(x)=0。",
        )
        await session.commit()
        assert created is True
        assert record.note == "我错在只算了 f'(x)=0。"

        record2, created2 = await _upsert_error_record(
            session,
            user.id,
            question_text=q,
            source_channel="chat_command",
            origin="chat_quiz",
            note="我错在只算了 f'(x)=0，没检查变号。",
        )
        await session.commit()
        assert created2 is False
        assert record2.note == "我错在只算了 f'(x)=0，没检查变号。"

        rows = (
            await session.execute(
                select(ErrorRecord).where(
                    ErrorRecord.user_id == user.id, ErrorRecord.deleted_at.is_(None)
                )
            )
        ).scalars().all()
        assert len(rows) == 1
