"""TAL-SCQ5K 难度再平衡（断点续跑版，迭代18）

实测（2026-08-15）：MiMo 单批 10 题 ≈ 48s。4668 题 = 467 批。
旧版问题：全量做完才 commit + 无进度输出 + 纯串行 → 跑一晚没结果。
新版：并发 3 + 每批独立会话即时 commit + 已打标行跳过（断点续跑）+ 实时进度。

用法：
    python -m scripts.relabel_tal
    中断后直接重跑即可续跑（annotate_meta.difficulty_relabel 已存在的行自动跳过）。
"""

import asyncio
import json
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import settings
from app.models.database import async_session_factory
from app.models.question_bank import QuestionBank

BATCH = 10
CONCURRENCY = 3

_PROMPT = (
    "你是高中数学教研员。给下面{num}道题各标一个难度（easy=基础/medium=中档/hard=难题）。"
    "只输出 JSON 字符串数组，长度必须等于 {num}，元素只能是 easy/medium/hard，不要任何其他文字。\n{questions}\n"
)


async def _label(batch: list[QuestionBank]) -> list[str] | None:
    """一批题 → 难度数组；失败重试 1 次，仍失败返回 None。"""
    questions = "\n".join(f"{i}. {r.stem[:150]}" for i, r in enumerate(batch, 1))
    url = (settings.deepseek_base_url or "").rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    url = url + "/chat/completions"
    body = {
        "model": settings.deepseek_model or "mimo-v2.5-pro",
        "messages": [{"role": "user", "content": _PROMPT.format(num=len(batch), questions=questions)}],
        "temperature": 0.1,
    }
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=180) as c:
                r = await c.post(
                    url,
                    headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                    json=body,
                )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            m = re.search(r"\[.*\]", content, re.S)
            arr = json.loads(m.group(0)) if m else None
            if isinstance(arr, list) and len(arr) == len(batch):
                return [str(x).strip().lower() for x in arr]
        except Exception as e:
            print(f"  [WARN] 批处理失败({attempt + 1}/2): {type(e).__name__}: {str(e)[:80]}", flush=True)
    return None


async def _persist(batch: list[QuestionBank], labels: list[str] | None) -> int:
    """单批独立会话落库（断点续跑的关键；跨会话须按 id 重取，避免 detached 实例丢写）。"""
    done = 0
    async with async_session_factory() as s:
        rs = await s.execute(select(QuestionBank).where(QuestionBank.id.in_([r.id for r in batch])))
        by_id = {r.id: r for r in rs.scalars().all()}
        for row, label in zip(batch, labels or [None] * len(batch), strict=False):
            cur = by_id.get(row.id)
            if cur is None:
                continue
            meta = dict(cur.annotate_meta or {})
            if "difficulty_relabel" in meta:
                continue  # 已在其他批次/上次运行打过标
            old = cur.difficulty
            new = label if label in ("easy", "medium", "hard") else old
            meta["difficulty_relabel"] = {
                "method": "llm" if new == label else "keep",
                "old": old,
                "new": new,
            }
            cur.difficulty = new
            cur.annotate_meta = meta
            done += 1
        await s.commit()
    return done


async def run() -> int:
    # 每次查询都跳过已打标行（续跑）
    async with async_session_factory() as s:
        rows = (
            await s.execute(
                select(QuestionBank)
                .where(
                    QuestionBank.source_batch == "tal-scq5k",
                    QuestionBank.deleted_at.is_(None),
                )
                .order_by(QuestionBank.id)
            )
        ).scalars().all()
    todo = [r for r in rows if "difficulty_relabel" not in (r.annotate_meta or {})]
    print(f"[START] 共 {len(rows)} 题，待处理 {len(todo)} 题（已标 {len(rows) - len(todo)} 跳过）", flush=True)
    if not todo:
        print("[DONE] 无需处理")
        return 0

    sem = asyncio.Semaphore(CONCURRENCY)
    done_total = 0
    keep_total = 0

    async def one(batch: list[QuestionBank]):
        nonlocal done_total, keep_total
        async with sem:
            labels = await _label(batch)
        if labels is None:
            # 失败不写、不跳过——下次重跑还会再试这批
            print(f"  [SKIP] 一批 {len(batch)} 题失败（未写入，重跑可续）", flush=True)
            return
        done = await _persist(batch, labels)
        keep = sum(1 for lb in labels if lb not in ("easy", "medium", "hard"))
        done_total += done
        keep_total += keep
        print(f"  [OK] +{done} 题（累计 {done_total}/{len(todo)}，降级保留 {keep_total}）", flush=True)

    batches = [todo[i : i + BATCH] for i in range(0, len(todo), BATCH)]
    await asyncio.gather(*(one(b) for b in batches))

    # 汇总
    async with async_session_factory() as s:
        from sqlalchemy import text

        r = await s.execute(text("SELECT difficulty, count(*) FROM question_bank WHERE source_batch='tal-scq5k' GROUP BY 1 ORDER BY 1"))
        print(f"[DONE] 全部批次结束。当前 TAL 分布: {r.all()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
