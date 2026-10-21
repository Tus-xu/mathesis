"""TeacherMemory 三层轻量记忆（V2.1 §4.10；接口冻结于 02-ARCHITECTURE §8）。

- L1 工作区事实：record_fact 原文行（必须带 source_refs——采纳/编辑距离来自 ai_runs 飞轮）；
- L2 单域总结：promote() 异步 worker 从 L1 source_refs 生成（KNR 红线：无 source_refs 不入库）；
- L3 教师画像：少量 profile/preferences 行。
- 去重：L1 内容 MD5；审计：每次 add/promote 落 teacher_memory_history。
- 召回：Embedder 注入时走向量检索（B2 交付后）；缺席时 LIKE 兜底（P0 可跑）。
- 存储目标：§4.9 `memories` 表（B2 域）；**R11.5-B 接管**：teacher_memories /
  teacher_memory_history 由正式 Alembic 迁移（d17b2c3e4f5a）所有，移除运行期幂等自建 DDL，
  缺表 → RuntimeOwnershipError（fail-closed）。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Protocol

from sqlalchemy import text


class MemoryStore(Protocol):
    async def add(self, row: dict[str, Any]) -> str: ...
    async def find_duplicates(self, teacher_id: str, layer: str, md5: str) -> int: ...
    async def list_l1(self, teacher_id: str, domain: str) -> list[dict[str, Any]]: ...
    async def recall_rows(self, teacher_id: str, *, layer: str | None, domain: str | None, query: str | None, top_k: int) -> list[dict[str, Any]]: ...
    async def history(self, memory_id: str, action: str, payload: dict[str, Any]) -> None: ...


class InMemoryMemoryStore:
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        self._history: list[dict[str, Any]] = []

    async def add(self, row: dict[str, Any]) -> str:
        row = {**row, "id": row.get("id") or str(uuid.uuid4())}
        self._rows.append(row)
        return row["id"]

    async def find_duplicates(self, teacher_id: str, layer: str, md5: str) -> int:
        return sum(
            1
            for r in self._rows
            if r["teacher_id"] == teacher_id and r["layer"] == layer and r.get("content_md5") == md5 and not r.get("superseded_by")
        )

    async def list_l1(self, teacher_id: str, domain: str) -> list[dict[str, Any]]:
        return [r for r in self._rows if r["teacher_id"] == teacher_id and r["layer"] == "L1" and r.get("domain") == domain]

    async def recall_rows(self, teacher_id: str, *, layer: str | None, domain: str | None, query: str | None, top_k: int) -> list[dict[str, Any]]:
        rows = [r for r in self._rows if r["teacher_id"] == teacher_id and not r.get("superseded_by")]
        if layer:
            rows = [r for r in rows if r["layer"] == layer]
        if domain:
            rows = [r for r in rows if r.get("domain") == domain or r["layer"] == "L3"]
        if query:
            rows = [r for r in rows if any(kw in r["content"] for kw in query.split() if kw)] or rows
        return rows[:top_k]

    async def history(self, memory_id: str, action: str, payload: dict[str, Any]) -> None:
        self._history.append({"memory_id": memory_id, "action": action, **payload})


class PostgresMemoryStore:
    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def _ensure(self, session) -> None:
        """所有权校验：表由迁移 d17b2c3e4f5a 所有，缺失 → fail-closed（不建表）。"""
        row = await session.execute(
            text(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = 'teacher_memories'"
            )
        )
        if row.scalar() is None:
            from app.db.ownership import RuntimeOwnershipError

            raise RuntimeOwnershipError(
                "teacher_memories 表缺失（需先执行 Alembic 迁移 d17b2c3e4f5a）"
            )

    async def add(self, row: dict[str, Any]) -> str:
        mid = row.get("id") or str(uuid.uuid4())
        async with self._session_factory() as session:
            await self._ensure(session)
            await session.execute(
                text(
                    "INSERT INTO teacher_memories (id, teacher_id, layer, domain, kind, content, content_md5, source_refs)"
                    " VALUES (:id, :teacher_id, :layer, :domain, :kind, :content, :md5, :refs)"
                ),
                {
                    "id": uuid.UUID(mid),
                    "teacher_id": uuid.UUID(row["teacher_id"]),
                    "layer": row["layer"],
                    "domain": row.get("domain"),
                    "kind": row.get("kind"),
                    "content": row["content"],
                    "md5": row["content_md5"],
                    "refs": json.dumps(row.get("source_refs") or []),
                },
            )
            await session.commit()
        return mid

    async def find_duplicates(self, teacher_id: str, layer: str, md5: str) -> int:
        async with self._session_factory() as session:
            await self._ensure(session)
            n = (
                await session.execute(
                    text("SELECT count(*) FROM teacher_memories WHERE teacher_id=:t AND layer=:l AND content_md5=:m AND superseded_by IS NULL"),
                    {"t": uuid.UUID(teacher_id), "l": layer, "m": md5},
                )
            ).scalar()
        return int(n or 0)

    async def list_l1(self, teacher_id: str, domain: str) -> list[dict[str, Any]]:
        async with self._session_factory() as session:
            await self._ensure(session)
            rows = (
                await session.execute(
                    text("SELECT id, content, source_refs FROM teacher_memories WHERE teacher_id=:t AND layer='L1' AND domain=:d AND superseded_by IS NULL ORDER BY created_at"),
                    {"t": uuid.UUID(teacher_id), "d": domain},
                )
            ).mappings().all()
        return [{"id": str(r["id"]), "content": r["content"], "source_refs": json.loads(r["source_refs"] or "[]")} for r in rows]

    async def recall_rows(self, teacher_id: str, *, layer: str | None, domain: str | None, query: str | None, top_k: int) -> list[dict[str, Any]]:
        like = f"%{query[:40]}%" if query else None
        async with self._session_factory() as session:
            await self._ensure(session)
            rows = (
                await session.execute(
                    text(
                        "SELECT id, layer, domain, content, source_refs FROM teacher_memories"
                        " WHERE teacher_id=:t AND superseded_by IS NULL"
                        " AND (:layer IS NULL OR layer=:layer)"
                        " AND (:domain IS NULL OR domain=:domain OR layer='L3')"
                        " ORDER BY created_at DESC LIMIT :k"
                    ),
                    {"t": uuid.UUID(teacher_id), "layer": layer, "domain": domain, "k": top_k},
                )
            ).mappings().all()
        return [{"id": str(r["id"]), "layer": r["layer"], "domain": r["domain"], "content": r["content"], "source_refs": json.loads(r["source_refs"] or "[]")} for r in rows]

    async def history(self, memory_id: str, action: str, payload: dict[str, Any]) -> None:
        async with self._session_factory() as session:
            await self._ensure(session)
            await session.execute(
                text("INSERT INTO teacher_memory_history (id, memory_id, action, payload) VALUES (:id, :mid, :a, :p)"),
                {"id": uuid.uuid4(), "mid": uuid.UUID(memory_id), "a": action, "p": json.dumps(payload, ensure_ascii=False, default=str)},
            )
            await session.commit()


def _md5(content: str) -> str:
    return hashlib.md5(content.encode("utf-8")).hexdigest()


class TeacherMemoryImpl:
    """§8 冻结签名：recall / record_fact / promote。summarizer 由调用方注入
    （生产 = gateway.long_form + B2 prompts；测试/未交付 = fake）。"""

    def __init__(self, store: MemoryStore, *, summarizer=None) -> None:
        self._store = store
        self._summarizer = summarizer  # async (domain, facts: list[str]) -> str

    async def recall(self, teacher_id: str, *, domain: str | None = None, top_k: int = 3) -> list[dict[str, Any]]:
        """每次对话前注入 system prompt 的记忆（L3 画像优先，L2 次之，L1 兜底）。"""
        rows: list[dict[str, Any]] = []
        for layer in ("L3", "L2", "L1"):
            if len(rows) >= top_k:
                break
            got = await self._store.recall_rows(teacher_id, layer=layer, domain=domain, query=None, top_k=top_k - len(rows))
            rows.extend(got)
        return rows[:top_k]

    async def record_fact(self, teacher_id: str, kind: str, content: str, source_refs: list[str], *, domain: str | None = None) -> str | None:
        """L1 事实行：MD5 去重；source_refs 必填（无来源不入库，KNR 红线 9）。"""
        if not source_refs:
            raise ValueError("L1 事实必须有 source_refs（KNR 红线：无来源不入库）")
        md5 = _md5(content)
        if await self._store.find_duplicates(teacher_id, "L1", md5):
            return None
        mid = await self._store.add(
            {"teacher_id": teacher_id, "layer": "L1", "domain": domain, "kind": kind, "content": content, "content_md5": md5, "source_refs": source_refs}
        )
        await self._store.history(mid, "ADD", {"kind": kind, "domain": domain})
        return mid

    async def record_preference(self, teacher_id: str, content: str, source_refs: list[str]) -> str | None:
        """L3 画像行（少量、可覆盖）。"""
        if not source_refs:
            raise ValueError("L3 画像必须有 source_refs（KNR 红线：无来源不入库）")
        md5 = _md5(content)
        if await self._store.find_duplicates(teacher_id, "L3", md5):
            return None
        mid = await self._store.add(
            {"teacher_id": teacher_id, "layer": "L3", "domain": None, "kind": "preference", "content": content, "content_md5": md5, "source_refs": source_refs}
        )
        await self._store.history(mid, "ADD", {"kind": "preference"})
        return mid

    async def promote(self, teacher_id: str, domain: str) -> str | None:
        """L1 → L2 单域总结（异步 worker 调用）。

        总结文本来自注入的 summarizer（B2 prompts 就绪前为抽取式兜底：拼 L1 要点）；
        L2 行携带全部 L1 source_refs（红线），并写 PROMOTE 审计。
        """
        facts = await self._store.list_l1(teacher_id, domain)
        if not facts:
            return None
        refs = [ref for f in facts for ref in (f.get("source_refs") or [])] or [f["id"] for f in facts]
        if self._summarizer is not None:
            summary = await self._summarizer(domain, [f["content"] for f in facts])
        else:  # 抽取式兜底（不编造；正式总结待 B2 prompts）
            summary = "；".join(f["content"][:60] for f in facts[:5])
        content = f"[{domain} 域总结] {summary}"
        md5 = _md5(content)
        if await self._store.find_duplicates(teacher_id, "L2", md5):
            return None
        mid = await self._store.add(
            {"teacher_id": teacher_id, "layer": "L2", "domain": domain, "kind": "summary", "content": content, "content_md5": md5, "source_refs": refs}
        )
        await self._store.history(mid, "PROMOTE", {"l1_count": len(facts), "source_refs": refs})
        return mid
