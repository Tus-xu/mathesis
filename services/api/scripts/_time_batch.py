"""计时：10 题一批的难度重标延迟实测"""

import asyncio
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import settings
from app.models.database import async_session_factory
from app.models.question_bank import QuestionBank

_PROMPT = (
    "你是高中数学教研员。给下面{num}道题各标一个难度（只输出 JSON 数组，"
    "元素为难度单词：easy/medium/hard，不要理由、不要其他文字）。\n{questions}\n"
)


async def main():
    async with async_session_factory() as s:
        rows = (
            await s.execute(
                select(QuestionBank)
                .where(QuestionBank.source_batch == "tal-scq5k")
                .limit(10)
            )
        ).scalars().all()
    questions = "\n".join(f"{i}. {r.stem[:150]}" for i, r in enumerate(rows, 1))
    url = settings.deepseek_base_url.rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    url = url + "/chat/completions"
    body = {
        "model": settings.deepseek_model or "mimo-v2.5-pro",
        "messages": [{"role": "user", "content": _PROMPT.format(num=len(rows), questions=questions)}],
        "temperature": 0.1,
    }
    t0 = time.time()
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(url, headers={"Authorization": f"Bearer {settings.deepseek_api_key}"}, json=body)
    dt = time.time() - t0
    print("HTTP", r.status_code, f"{dt:.1f}s")
    try:
        content = r.json()["choices"][0]["message"]["content"]
        print("内容长度:", len(content))
        print("内容:", content[:300])
    except Exception as e:
        print("解析失败:", e, r.text[:300])


asyncio.run(main())
