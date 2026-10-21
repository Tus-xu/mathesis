"""教师端 teacher_platform 全新库引导（补建双轨运行时表 → 跑 alembic）。

## 为什么需要它（实测缺陷，项目自己已登记）

`alembic upgrade head` 在**全新库**上必然失败：

- 迁移 `01921a4ef94b` 给 `folder_question_refs` 建的外键指向 `quiz_questions`，
  而该表**没有任何迁移负责创建** —— 它是 **ORM create_all / corpus 管线内联 DDL
  双轨建出**的运行时表。

项目自己的契约测试 `tests/contracts/test_registry_truth_reconciliation.py`
已经把这件事写死在 `MEASURED_UNTracked_LIVE_TABLES` 里，并明说：
「这些表**不在** alembic/versions 里，因此 `alembic upgrade head` 无法重建全库现状。」

本地库能跑通，只是因为它早年已被应用/corpus 管线建过这些表 —— 全新部署则卡死。

## 处置

1. 用**权威 DDL**（corpus/pipelines/load_to_db.py 的 `QUIZ_QUESTIONS_DDL` / `KP_NODES_DDL`，
   与本地库实测列完全一致）建 2 张表；
2. 用 ORM 元数据建 4 张双轨表（knowledge_documents / knowledge_chunks / memories / web_search_cache），
   `create_all(..., tables=子集, checkfirst=True)` 自动处理依赖顺序；
3. 跑 `alembic upgrade head`；若仍报缺表且该表在 ORM 元数据里，则补建后重试（兜底迭代）。

用法（容器内）：python /tmp/teacher_db_bootstrap.py
"""
from __future__ import annotations

import asyncio
import importlib
import os
import pkgutil
import re
import subprocess
import sys

# ---- 权威 DDL：逐字取自 teacher-platform/corpus/pipelines/load_to_db.py ----
QUIZ_QUESTIONS_DDL = """
CREATE TABLE IF NOT EXISTS quiz_questions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kp_code text, kp_path text[],
  q_type text CHECK (q_type IN ('choice','fill','solve','image')),
  difficulty text,
  stem_latex text, stem_image text,
  options jsonb, answer text, solution_image text NULL, analysis text,
  source text, year text,
  teacher_confirmed bool DEFAULT false,
  usage_count int DEFAULT 0, created_by uuid, created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  stem_hash text
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_quiz_source_year_stemhash
  ON quiz_questions (source, year, stem_hash);
"""
KP_NODES_DDL = """
CREATE TABLE IF NOT EXISTS kp_nodes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  code text UNIQUE, name text, parent_id uuid NULL REFERENCES kp_nodes(id),
  path text[], source text, sort int,
  metadata jsonb
);
"""
RAW_DDL_TABLES = ("quiz_questions", "kp_nodes")
ORM_TABLES = ("knowledge_documents", "knowledge_chunks", "memories", "web_search_cache")

MISSING_RE = re.compile(r'relation "([a-z_0-9]+)" does not exist')


def load_metadata():
    """导入整个 app 包，让所有 ORM 模型注册进 Base.metadata。"""
    from app.db.base import Base

    import app  # noqa: PLC0415

    for m in pkgutil.walk_packages(app.__path__, app.__name__ + "."):
        try:
            importlib.import_module(m.name)
        except Exception:  # noqa: BLE001 — 尽力而为
            pass
    return Base


def _engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(os.environ["DATABASE_URL"])


async def _run_sql(engine, sql: str) -> None:
    from sqlalchemy import text

    async with engine.begin() as conn:
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            await conn.execute(text(stmt))


async def _create_orm_tables(engine, names) -> None:
    Base = load_metadata()
    tables = [Base.metadata.tables[n] for n in names if n in Base.metadata.tables]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables, checkfirst=True))


async def _bootstrap_schema(names=RAW_DDL_TABLES + ORM_TABLES) -> None:
    """整个引导必须在**同一个事件循环**里跑完：engine 的连接池与 loop 绑定，
    跨 asyncio.run 复用 engine 会抛 'attached to a different loop'。"""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(os.environ["DATABASE_URL"])
    try:
        # 扩展必须先于任何 ORM 表：memories.embedding 是 VECTOR(768)，
        # 而全新库里没有 vector 类型（同样不属于任何迁移的职责）。
        print("[bootstrap] ⓪ CREATE EXTENSION IF NOT EXISTS vector")
        await _run_sql(engine, "CREATE EXTENSION IF NOT EXISTS vector;")
        if "quiz_questions" in names:
            print("[bootstrap] ① 建内联 DDL 双轨表: quiz_questions / kp_nodes")
            await _run_sql(engine, QUIZ_QUESTIONS_DDL)
            await _run_sql(engine, KP_NODES_DDL)
        orm_names = [n for n in names if n in ORM_TABLES]
        if orm_names:
            print("[bootstrap] ② 建 ORM 双轨表:", ", ".join(orm_names))
            await _create_orm_tables(engine, orm_names)
    finally:
        await engine.dispose()


def bootstrap_schema() -> None:
    asyncio.run(_bootstrap_schema())


def create_missing(name: str) -> None:
    asyncio.run(_bootstrap_schema(names=(name,)))


def _engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    return create_async_engine(os.environ["DATABASE_URL"])


def main() -> int:
    bootstrap_schema()

    for i in range(1, 21):
        print(f"\n[bootstrap] 第 {i} 轮：alembic upgrade head")
        p = subprocess.run(
            ["uv", "run", "alembic", "upgrade", "head"],
            capture_output=True, text=True, cwd="/app",
        )
        out = (p.stdout or "") + (p.stderr or "")
        if p.returncode == 0:
            print("[bootstrap] ✅ 迁移全部完成")
            return 0
        m = MISSING_RE.search(out)
        if not m:
            print("[bootstrap] ❌ 非「缺表」类错误：")
            print(out[-2500:])
            return 1
        name = m.group(1)
        Base = load_metadata()
        if name not in Base.metadata.tables:
            print(f"[bootstrap] ❌ ORM 元数据里没有 {name}，无法自动补建：")
            print(out[-1500:])
            return 2
        print(f"[bootstrap] 仍缺 {name} → 从 ORM 元数据补建后重试")
        create_missing(name)

    print("[bootstrap] ❌ 20 轮未收敛")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
