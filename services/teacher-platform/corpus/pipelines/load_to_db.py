"""corpus → teacher_platform DB 装载管线（M1「种子题库入库」落点，幂等）。

前置：teacher-postgres（pgvector/pgvector:pg16，5433）已运行——由并行的 B3 会话启动，
本管线只建自己所有权内的表与架构 §4.1 冻结 DDL 的两张域表，不碰 B3 平台表/容器。

表处理策略（并行开发期边界，见 docs/handoff/DEP-B2-dependencies-and-migrations.md）：
- knowledge_documents/knowledge_chunks/web_search_cache/memories —— B2 所有，ORM 建表；
- quiz_questions/kp_nodes —— §4.1 冻结 DDL 原样 CREATE TABLE IF NOT EXISTS（B4 未落地前的
  dev 引导）+ 两个 B2 请求列：quiz_questions.stem_hash（幂等唯一键）、
  kp_nodes.metadata（prerequisite 边宿主，B2 §5.2）。

幂等：INSERT … ON CONFLICT DO NOTHING（靠唯一约束，G8 纪律）；向量入库走
KnowledgeWorkspace.register（manifest 红线）+ (doc_ref, chunk_index) 唯一冲突跳过。

密钥：DB 口令经 docker inspect 进程内读取；SILICONFLOW key ← 学生端 .env 的
DEEPSEEK_API_KEY（SiliconFlow 通道）进程内读取——均不落盘不打印（B2 红线 3）。

运行：python -m corpus.pipelines.load_to_db [--skip-vectors]
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import selectors
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

STAGING = Path(__file__).resolve().parents[1] / "staging"
LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "load_to_db.log"
DB_HOST, DB_PORT, DB_NAME, DB_USER = "localhost", "5433", "teacher_platform", "teacher"

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


def _db_password() -> str:
    out = subprocess.run(
        ["docker", "inspect", "teacher-postgres",
         "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
        capture_output=True, text=True, check=True).stdout
    for line in out.splitlines():
        if line.startswith("POSTGRES_PASSWORD="):
            return line.split("=", 1)[1]
    raise RuntimeError("teacher-postgres 容器无 POSTGRES_PASSWORD（并行 B3 栈变了？）")


def _load_env_keys() -> None:
    """进程内注入 key（不打印不落盘）。"""
    for line in Path("D:/math-arena/.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)
    os.environ.setdefault("SILICONFLOW_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _quiz_chunk_content(r: dict) -> str:
    parts = [r["stem_latex"]]
    if r.get("options"):
        parts += [f"{k}. {v}" for k, v in r["options"].items()]
    if r.get("answer"):
        parts.append(f"答案：{r['answer']}")
    if r.get("analysis"):
        parts.append(f"解析：{r['analysis']}")
    return "\n".join(parts)


async def _ensure_tables(engine) -> list[str]:
    from sqlalchemy import text

    from app.db.models.knowledge import Base as KnowledgeBase

    async with engine.begin() as conn:
        before = {r[0] for r in (await conn.execute(text(
            "select tablename from pg_tables where schemaname='public'")))}
        await conn.run_sync(lambda c: KnowledgeBase.metadata.create_all(c, checkfirst=True))
        await conn.execute(text(QUIZ_QUESTIONS_DDL))
        await conn.execute(text(KP_NODES_DDL))
        after = {r[0] for r in (await conn.execute(text(
            "select tablename from pg_tables where schemaname='public'")))}
    return sorted(after - before)


async def _load_quiz(session, rows: list[dict]) -> int:
    from sqlalchemy import text

    inserted = 0
    for r in rows:
        opts = r.get("options")
        res = await session.execute(text("""
            INSERT INTO quiz_questions (kp_code, kp_path, q_type, difficulty, stem_latex,
                options, answer, analysis, source, year, stem_hash, created_by)
            VALUES (:kp_code, :kp_path, :q_type, :difficulty, :stem_latex,
                CAST(:options AS jsonb), :answer, :analysis, :source, :year, :stem_hash, NULL)
            ON CONFLICT (source, year, stem_hash) DO NOTHING
        """), {
            "kp_code": None, "kp_path": [], "q_type": r["q_type"], "difficulty": r["difficulty"],
            "stem_latex": r["stem_latex"],
            "options": json.dumps(opts, ensure_ascii=False) if opts else None,
            "answer": r.get("answer"), "analysis": r.get("analysis"),
            "source": r["source"], "year": r["year"], "stem_hash": r["stem_hash"],
        })
        inserted += res.rowcount or 0
    return inserted


async def _load_kp_nodes(session, rows: list[dict]) -> int:
    from sqlalchemy import text

    inserted = 0
    for r in rows:
        res = await session.execute(text("""
            INSERT INTO kp_nodes (code, name, parent_id, path, source, sort, metadata)
            VALUES (:code, :name, NULL, :path, :source, :sort, CAST(:meta AS jsonb))
            ON CONFLICT (code) DO NOTHING
        """), {
            "code": r["code"], "name": r["name"], "path": r.get("path") or [],
            "source": r["source"], "sort": None,
            "meta": json.dumps(r.get("metadata") or {}, ensure_ascii=False),
        })
        inserted += res.rowcount or 0
    return inserted


async def _register_doc(workspace, doc_ref: str, title: str, source_type: str,
                        manifest_key: str) -> str:
    reg = await workspace.register(doc_ref, title, source_type, manifest_key)
    return reg.doc_id


async def _ingest_chunks(engine, session_factory, embedder, *, doc_id: str, doc_ref: str,
                         collection: str, items: list[tuple[str, dict]], batch: int = 32) -> int:
    """分块 embedding + 入库；(doc_ref, chunk_index) 冲突跳过（幂等）。"""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.db.models.knowledge import KnowledgeChunk
    from app.rag.workspace import KnowledgeWorkspace  # noqa: F401 — 语义对齐说明用

    await workspace_mark_indexing(session_factory, doc_id)
    inserted = 0
    for start in range(0, len(items), batch):
        batch_items = items[start:start + batch]
        vectors = await embedder.embed([c for c, _m in batch_items])
        async with session_factory() as session:
            for i, ((content, meta), vec) in enumerate(zip(batch_items, vectors)):
                idx = meta.get("chunk_index", start + i)
                stmt = pg_insert(KnowledgeChunk).values(
                    collection=collection, doc_ref=doc_ref, chunk_index=idx,
                    content=content, embedding=vec, metadata_=meta,
                    content_hash=hashlib_hex(content),
                ).on_conflict_do_nothing(index_elements=["doc_ref", "chunk_index"])
                res = await session.execute(stmt)
                inserted += res.rowcount or 0
            await session.commit()
    await workspace_mark_ready(session_factory, doc_id)
    return inserted


async def workspace_mark_indexing(session_factory, doc_id: str) -> None:
    from sqlalchemy import update

    from app.db.models.knowledge import KnowledgeDocument

    async with session_factory() as s:
        await s.execute(update(KnowledgeDocument).where(
            KnowledgeDocument.id == doc_id).values(status="indexing"))
        await s.commit()


async def workspace_mark_ready(session_factory, doc_id: str) -> None:
    from sqlalchemy import select, update

    from app.db.models.knowledge import KnowledgeDocument

    async with session_factory() as s:
        row = (await s.execute(select(KnowledgeDocument).where(
            KnowledgeDocument.id == doc_id))).scalar_one()
        row.status = "ready"
        row.index_version = (row.index_version or 0) + 1
        await s.commit()


def hashlib_hex(content: str) -> str:
    import hashlib

    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def _verify_retrieval(session_factory, embedder) -> list[dict]:
    """真实检索冒烟：三条教学查询，返回 top3 定位与分数。"""
    from app.rag.retriever import Retriever

    retriever = Retriever(embedder, session_factory)
    queries = ["椭圆的第一定义", "等比数列公比的限制", "二面角的取值范围"]
    out = []
    for q in queries:
        chunks = await retriever.retrieve(q, collections=["quiz_bank"], top_k=3, rerank=True)
        out.append({"query": q,
                    "hits": [{"locator": c.locator(), "score": round(c.score, 4),
                              "preview": c.content[:60]} for c in chunks]})
    return out


async def run(skip_vectors: bool) -> dict:
    _load_env_keys()
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models.knowledge import KnowledgeDocument  # noqa: F401
    from app.rag.embedder import Embedder
    from app.rag.workspace import KnowledgeWorkspace

    url = f"postgresql+psycopg://{DB_USER}:{_db_password()}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    engine = create_async_engine(url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    report: dict = {"generated_at": datetime.now(timezone.utc).isoformat(), "db": DB_NAME}

    report["created_tables"] = await _ensure_tables(engine)

    async with session_factory() as session:
        quiz_rows = _read_jsonl(STAGING / "quiz_gaokao.jsonl")
        report["quiz_inserted"] = await _load_quiz(session, quiz_rows)
        kp_rows = _read_jsonl(STAGING / "kp_nodes.jsonl")
        report["kp_nodes_inserted"] = await _load_kp_nodes(session, kp_rows)
        await session.commit()

    if not skip_vectors:
        embedder = Embedder()
        workspace = KnowledgeWorkspace(session_factory,
                                       manifest_index={"gaokao_bench": "GAOKAO-Bench",
                                                       "k12_kgraph": "K12-KGraph"})
        quiz_doc_id = await _register_doc(
            workspace, "corpus:gaokao_quiz_bank:v1", "GAOKAO 数学说题库（区库）",
            "corpus", "gaokao_bench")
        quiz_items = []
        for i, r in enumerate(_read_jsonl(STAGING / "quiz_gaokao.jsonl")):
            quiz_items.append((_quiz_chunk_content(r), {
                "chunk_index": i, "locator": f"{r['year']}年 {r['category']}卷 "
                                             f"{'选择' if r['q_type'] == 'choice' else '填空' if r['q_type'] == 'fill' else '解答'}题",
                "q_type": r["q_type"], "year": r["year"],
                "stem_hash": r["stem_hash"]}))
        report["quiz_chunks_inserted"] = await _ingest_chunks(
            engine, session_factory, embedder, doc_id=quiz_doc_id,
            doc_ref="corpus:gaokao_quiz_bank:v1", collection="quiz_bank", items=quiz_items)

        kp_scale = "full" if len(kp_rows) > 500 else "demo"
        kp_doc_ref = f"corpus:k12_kgraph_{kp_scale}:v1"
        kp_doc_title = ("K12-KGraph 数学子图（全量 23 册，hf-mirror 下载）" if kp_scale == "full"
                        else "K12-KGraph 数学子图（demo 册）")
        kp_doc_id = await _register_doc(
            workspace, kp_doc_ref, kp_doc_title, "corpus", "k12_kgraph")
        kp_items = []
        for i, r in enumerate(_read_jsonl(STAGING / "kp_nodes.jsonl")):
            content = f"知识点 {r['name']}（{r['label']}）：路径 {' / '.join(r.get('path') or [])}" \
                      f"；前置：{[p['code'] for p in (r.get('metadata') or {}).get('prerequisites', [])]}"
            kp_items.append((content, {"chunk_index": i, "locator": r["code"],
                                       "kp_code": r["code"], "label": r["label"]}))
        report["kp_chunks_inserted"] = await _ingest_chunks(
            engine, session_factory, embedder, doc_id=kp_doc_id,
            doc_ref=kp_doc_ref, collection="kp_graph", items=kp_items)

        report["retrieval_smoke"] = await _verify_retrieval(session_factory, embedder)

    # 权威计数（不依赖 psycopg rowcount 语义）
    from sqlalchemy import text as _text

    async with session_factory() as s:
        for table in ("quiz_questions", "kp_nodes", "knowledge_documents", "knowledge_chunks"):
            report[f"db_count_{table}"] = (await s.execute(
                _text(f"SELECT count(*) FROM {table}"))).scalar_one()
    report["db_counts_note"] = "inserted 字段为 psycopg rowcount 口径（DO NOTHING 冲突跳过行为负数），以 db_count_* 为准"

    await engine.dispose()
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-vectors", action="store_true")
    args = ap.parse_args(argv)
    try:
        # Windows: psycopg3 async 需要 SelectorEventLoop（Proactor 不支持 add_reader）
        report = asyncio.run(
            run(skip_vectors=args.skip_vectors),
            loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()),
        )
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as lf:
        lf.write(json.dumps(report, ensure_ascii=False) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
