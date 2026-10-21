"""teacher_session_contexts 存取（K1：会话级主键 session_id）。

R11.5-B 接管（DECISIONS §5 / 迁移 d17b2c3e4f5a）：表所有权归正式 Alembic 迁移，
**移除运行期幂等自建 DDL**；生产/本地缺表统一抛 RuntimeOwnershipError（fail-closed，
提示先跑迁移）。单测用 InMemoryStickyStore。
"""
from __future__ import annotations

import json
from typing import Any, Protocol

from sqlalchemy import text

from .models import TeacherSessionContext


class StickyContextStore(Protocol):
    async def get(self, session_id: str) -> TeacherSessionContext | None: ...
    async def upsert(self, ctx: TeacherSessionContext) -> None: ...
    async def latest_for_teacher(self, teacher_id: str, *, exclude_session: str) -> TeacherSessionContext | None: ...


class InMemoryStickyStore:
    def __init__(self) -> None:
        self._rows: dict[str, TeacherSessionContext] = {}

    async def get(self, session_id: str) -> TeacherSessionContext | None:
        return self._rows.get(session_id)

    async def upsert(self, ctx: TeacherSessionContext) -> None:
        self._rows[ctx.session_id] = ctx.model_copy(deep=True)

    async def latest_for_teacher(self, teacher_id: str, *, exclude_session: str) -> TeacherSessionContext | None:
        candidates = [
            c for c in self._rows.values() if c.teacher_id == teacher_id and c.session_id != exclude_session
        ]
        return candidates[-1] if candidates else None


class PostgresStickyStore:
    def __init__(self, session_factory) -> None:  # async_sessionmaker[AsyncSession]
        self._session_factory = session_factory

    async def _ensure_table(self, session) -> None:
        """所有权校验：表由迁移 d17b2c3e4f5a 所有，缺失 → fail-closed（不建表、不降级）。"""
        from sqlalchemy import text as sql_text

        row = await session.execute(
            sql_text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = 'teacher_session_contexts'"
            )
        )
        if row.scalar() is None:
            from app.db.ownership import RuntimeOwnershipError

            raise RuntimeOwnershipError(
                "teacher_session_contexts 表缺失（需先执行 Alembic 迁移 d17b2c3e4f5a）"
            )

    async def get(self, session_id: str) -> TeacherSessionContext | None:
        from sqlalchemy import text as sql_text

        async with self._session_factory() as session:
            await self._ensure_table(session)
            row = (
                await session.execute(
                    sql_text(
                        "SELECT session_id, teacher_id, textbook_id, chapter_path, class_id, deck_id,"
                        " retrieval_scope_ids, sticky FROM teacher_session_contexts WHERE session_id = :sid"
                    ),
                    {"sid": session_id},
                )
            )
            data = row.mappings().first()
        return self._to_model(data) if data else None

    async def upsert(self, ctx: TeacherSessionContext) -> None:
        from sqlalchemy import text as sql_text

        import uuid as uuid_mod

        async with self._session_factory() as session:
            await self._ensure_table(session)
            await session.execute(
                sql_text(
                    """
                    INSERT INTO teacher_session_contexts
                      (session_id, teacher_id, textbook_id, chapter_path, class_id, deck_id,
                       retrieval_scope_ids, sticky, updated_at)
                    VALUES (:sid, :tid, :textbook, :chapter, :class_id, :deck_id, :scope, :sticky, now())
                    ON CONFLICT (session_id) DO UPDATE SET
                      textbook_id = EXCLUDED.textbook_id,
                      chapter_path = EXCLUDED.chapter_path,
                      class_id = EXCLUDED.class_id,
                      deck_id = EXCLUDED.deck_id,
                      retrieval_scope_ids = EXCLUDED.retrieval_scope_ids,
                      sticky = EXCLUDED.sticky,
                      updated_at = now()
                    """
                ),
                {
                    "sid": uuid_mod.UUID(ctx.session_id),
                    "tid": uuid_mod.UUID(ctx.teacher_id),
                    "textbook": ctx.textbook_id,
                    "chapter": ctx.chapter_path,
                    "class_id": uuid_mod.UUID(ctx.class_id) if ctx.class_id else None,
                    "deck_id": uuid_mod.UUID(ctx.deck_id) if ctx.deck_id else None,
                    "scope": json.dumps(ctx.retrieval_scope_ids),
                    "sticky": json.dumps(ctx.sticky, ensure_ascii=False, default=str),
                },
            )
            await session.commit()

    async def latest_for_teacher(self, teacher_id: str, *, exclude_session: str) -> TeacherSessionContext | None:
        from sqlalchemy import text as sql_text

        import uuid as uuid_mod

        async with self._session_factory() as session:
            await self._ensure_table(session)
            row = (
                await session.execute(
                    sql_text(
                        "SELECT session_id, teacher_id, textbook_id, chapter_path, class_id, deck_id,"
                        " retrieval_scope_ids, sticky FROM teacher_session_contexts"
                        " WHERE teacher_id = :tid AND session_id <> :sid"
                        " ORDER BY updated_at DESC LIMIT 1"
                    ),
                    {"tid": uuid_mod.UUID(teacher_id), "sid": exclude_session},
                )
            )
            data = row.mappings().first()
        return self._to_model(data) if data else None

    @staticmethod
    def _to_model(data: Any) -> TeacherSessionContext:
        scope = data["retrieval_scope_ids"]
        sticky = data["sticky"]
        if isinstance(scope, str):
            scope = json.loads(scope)
        if isinstance(sticky, str):
            sticky = json.loads(sticky)
        return TeacherSessionContext(
            session_id=str(data["session_id"]),
            teacher_id=str(data["teacher_id"]),
            textbook_id=data["textbook_id"],
            chapter_path=data["chapter_path"],
            class_id=str(data["class_id"]) if data["class_id"] else None,
            deck_id=str(data["deck_id"]) if data["deck_id"] else None,
            retrieval_scope_ids=scope or [],
            sticky=sticky or {},
        )
