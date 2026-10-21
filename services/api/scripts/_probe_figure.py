import asyncio
import re
import sys

sys.path.insert(0, ".")
import httpx
from sqlalchemy import text

from app.config import settings
from app.models.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        r = await s.execute(
            text(
                "SELECT count(*) FROM question_bank WHERE source_batch IN ('gkb-2010-2022','gkb-2023','gkb-2024') AND stem ~ '棱柱|棱锥|四棱|三棱|如图|图所示'"
            )
        )
        print("立体几何/配图类高考真题数 =", r.scalar())
        r = await s.execute(
            text(
                "SELECT id, stem FROM question_bank WHERE source_batch IN ('gkb-2010-2022','gkb-2023','gkb-2024') AND stem ~ '三棱柱' ORDER BY random() LIMIT 1"
            )
        )
        row = r.first()
        print("样例题 stem =", row.stem[:200])
        stem = row.stem
    # 用 MiMo 生成 SVG 配图
    url = (settings.deepseek_base_url or "").rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    url = url + "/chat/completions"
    prompt = (
        "你是数学插图专家。请为下面的立体几何题生成一幅 SVG 配图（透视线框风格，标注顶点字母，图宽 400 高 300，白色背景）。"
        "只输出 <svg>...</svg> 代码，不要任何多余文字。题目：\n" + stem[:400]
    )
    body = {
        "model": settings.deepseek_model or "mimo-v2.5-pro",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
    }
    async with httpx.AsyncClient(timeout=120) as c:
        resp = await c.post(
            url, headers={"Authorization": f"Bearer {settings.deepseek_api_key}"}, json=body
        )
    resp.raise_for_status()
    svg = resp.json()["choices"][0]["message"]["content"]
    m = re.search(r"<svg.*?</svg>", svg, re.S)
    svg_clean = m.group(0) if m else svg
    with open("scripts/_probe_figure.svg", "w", encoding="utf-8") as f:
        f.write(svg_clean)
    print("SVG 长度 =", len(svg_clean))
    print("SVG 开头 =", svg_clean[:200].replace("\n", " "))
    print("是否合法闭合 =", svg_clean.rstrip().endswith("</svg>"))


asyncio.run(main())
