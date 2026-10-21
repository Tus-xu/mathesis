"""M4 检索质量评测：自建 50 查询集 hit@5（B2 §7 阶段2 自验收）。

方法（诚实口径）：
1. 从 quiz_questions（785 题，DB 权威）分层抽样 50 题（q_type × 年代段）；
2. 每题由 LLM 转写成教师自然找题 query（≤20 字，禁止照抄题干数字/完整句式）——
   真值 = 源题 stem_hash（写在 metadata，检索后比对）；
3. Retriever 全链（embed → pgvector top20 → rerank top5）→ hit@5 + 延迟统计。

局限（如实声明）：query 由源题派生，属「自 retrieved 自」的语料健全性评测，
非独立需求集；B5 验收时可用 RESEARCH 场景查询集复核。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import selectors
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 本文件位于 services/teacher-platform/corpus/evals/，上溯 4 级到仓库根。
ENV_FILE = Path(__file__).resolve().parents[4] / ".env"

DB_HOST, DB_PORT, DB_NAME, DB_USER = "localhost", "5433", "teacher_platform", "teacher"
N_QUERIES = 50


def _db_password() -> str:
    out = subprocess.run(["docker", "inspect", "teacher-postgres", "--format",
                          "{{range .Config.Env}}{{println .}}{{end}}"],
                         capture_output=True, text=True, check=True).stdout
    return [l.split("=", 1)[1] for l in out.splitlines()
            if l.startswith("POSTGRES_PASSWORD=")][0]


def _load_keys() -> None:
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v)
    os.environ.setdefault("SILICONFLOW_API_KEY", os.environ.get("DEEPSEEK_API_KEY", ""))


async def _llm_query(client, stem: str, mode: str) -> str:
    import httpx

    instruction = (
        "把一道数学题转写成高中数学老师在题库里的自然找题query。要求：≤20字；"
        "写考查的知识点与题型意图（如『椭圆离心率 计算题』）；"
        "禁止照抄题干中的具体数字与完整句式；只输出query本身。"
        if mode == "deid" else
        "把一道数学题转写成高中数学老师在题库里的自然找题query。要求：≤20字；"
        "写考查的知识点与题型意图，并保留题中最有辨识度的一个数值或条件"
        "（教师找题通常带具体数字，如『椭圆 a=2 求离心率』）；"
        "不要整句照抄题干；只输出query本身。")
    r = await client.post(
        "https://api.siliconflow.cn/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['SILICONFLOW_API_KEY']}"},
        json={"model": "deepseek-ai/DeepSeek-V3.2", "temperature": 0.2,
              "messages": [{"role": "system", "content": instruction},
                           {"role": "user", "content": stem[:300]}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip().strip('"「」')


async def main_async(mode: str = "realistic") -> dict:
    _load_keys()
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.rag.embedder import Embedder
    from app.rag.retriever import Retriever

    engine = create_async_engine(
        f"postgresql+psycopg://{DB_USER}:{_db_password()}@{DB_HOST}:{DB_PORT}/{DB_NAME}")
    sf = async_sessionmaker(engine, expire_on_commit=False)
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT stem_hash, stem_latex, q_type, year FROM quiz_questions "
            "ORDER BY md5(stem_hash) LIMIT :n"), {"n": N_QUERIES * 3})).mappings().all()
    # 分层：choice/fill/solve 各取约 1/3
    by_type: dict[str, list] = {"choice": [], "fill": [], "solve": []}
    for r in rows:
        if r["q_type"] in by_type and len(by_type[r["q_type"]]) < N_QUERIES // 3 + 2:
            by_type[r["q_type"]].append(r)
    sample = (by_type["choice"][:17] + by_type["fill"][:17] + by_type["solve"][:16])[:N_QUERIES]

    import httpx

    async with httpx.AsyncClient(timeout=120) as client:
        pairs = []
        for r in sample:
            q, fallback = None, False
            for attempt in range(3):
                try:
                    q = await _llm_query(client, r["stem_latex"], mode)
                    break
                except Exception:
                    if attempt == 2:
                        q, fallback = r["stem_latex"][:60], True
                    else:
                        await asyncio.sleep(3 * (attempt + 1))
            entry = {"query": q, "stem_hash": r["stem_hash"], "q_type": r["q_type"]}
            if fallback:
                entry["query_fallback"] = True
            pairs.append(entry)

    retriever = Retriever(Embedder(), sf)
    hits, latencies, details = 0, [], []
    for p in pairs:
        t0 = time.perf_counter()
        chunks = await retriever.retrieve(p["query"], collections=["quiz_bank"], top_k=5)
        latencies.append((time.perf_counter() - t0) * 1000)
        got = [c.metadata.get("stem_hash") for c in chunks]
        hit = p["stem_hash"] in got
        hits += hit
        details.append({"query": p["query"], "hit": hit, "rank": got.index(p["stem_hash"]) + 1 if hit else None,
                        "top1": chunks[0].locator() if chunks else None})
    await engine.dispose()

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "n_queries": len(pairs), "mode": mode, "hit_at_5": round(hits / len(pairs), 3),
        "target": ">= 0.7（B2 §6 M4）", "pass": hits / len(pairs) >= 0.7,
        "latency_ms_p50": round(statistics.median(latencies)),
        "latency_ms_mean": round(statistics.mean(latencies)),
        "misses": [d for d in details if not d["hit"]][:10],
    }
    out = Path(__file__).resolve().parent / "retrieval_report.json"
    out.write_text(json.dumps({**report, "details": details}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    return report


if __name__ == "__main__":
    _mode = sys.argv[1] if len(sys.argv) > 1 else "realistic"
    rep = asyncio.run(main_async(_mode),
                      loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    print(json.dumps({k: v for k, v in rep.items() if k != "misses"},
                     ensure_ascii=False, indent=2))
    print("misses:", json.dumps(rep["misses"][:5], ensure_ascii=False))
