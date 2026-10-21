"""配图回填脚本（P2-5 图片试点，M2 迭代18）

路线结论（试点实测）：本地无原始试卷 PDF（D:\知识库 293 个 PDF 均为 LaTeX 文档），
"PDF 抽图对齐"路线无源；改为"AI 重绘 SVG"路线——对配图依赖型高考真题
用 MiMo 生成透视线框 SVG，base64 data URI 写入 question_bank.image。
前端 <img src="data:image/svg+xml;base64,..."> 直接渲染（img 上下文内 SVG 脚本不执行，安全）。

用法：
    python -m scripts.backfill_figures --dry-run   # 只统计候选
    python -m scripts.backfill_figures             # 生成并写入
"""

import argparse
import asyncio
import base64
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import settings
from app.models.database import async_session_factory
from app.models.question_bank import QuestionBank

# 配图依赖关键词（题干明确依赖图形）
_FIGURE_RE = "棱柱|棱锥|四棱|三棱|如图|图所示|正方体|长方体"

_SVG_PROMPT = (
    "你是数学插图专家。请为下面的高中数学题生成一幅 SVG 配图：透视线框风格、白色背景、"
    "关键点标注题目中的字母，图宽 400 高 300。只输出 <svg>...</svg> 代码，不要任何多余文字。题目：\n{stem}"
)


async def gen_svg(stem: str) -> str | None:
    """MiMo 生成 SVG；失败/无合法 SVG 返回 None（重试 1 次）。"""
    url = (settings.deepseek_base_url or "").rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    url = url + "/chat/completions"
    body = {
        "model": settings.deepseek_model or "mimo-v2.5-pro",
        "messages": [{"role": "user", "content": _SVG_PROMPT.format(stem=stem[:400])}],
        "temperature": 0.3,
    }
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(
                    url,
                    headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                    json=body,
                )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            m = re.search(r"<svg.*?</svg>", content, re.S)
            svg = m.group(0) if m else None
            if svg and 200 < len(svg) < 30000 and svg.rstrip().endswith("</svg>"):
                return svg
        except Exception as e:
            print(f"  [WARN] 第 {attempt + 1} 次生成失败: {type(e).__name__}: {str(e)[:90]}")
    return None


async def run(dry_run: bool) -> int:
    async with async_session_factory() as s:
        rows = (
            await s.execute(
                select(QuestionBank)
                .where(
                    QuestionBank.source_batch.in_(("gkb-2010-2022", "gkb-2023", "gkb-2024")),
                    QuestionBank.stem.op("~")(_FIGURE_RE),
                    QuestionBank.deleted_at.is_(None),
                )
                .order_by(QuestionBank.id)
            )
        ).scalars().all()
        print(f"[OK] 配图依赖候选: {len(rows)} 题")
        if dry_run:
            for row in rows:
                print("  -", row.stem[:60].replace("\n", " "))
            return 0

        done = 0
        sem = asyncio.Semaphore(2)

        async def one(row: QuestionBank):
            nonlocal done
            async with sem:
                svg = await gen_svg(row.stem)
            if not svg:
                print(f"  [SKIP] {row.id} 生成失败")
                return
            uri = "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")
            meta = dict(row.annotate_meta or {})
            meta["figure_gen"] = {
                "method": "llm_svg",
                "model": settings.deepseek_model or "mimo-v2.5-pro",
                "bytes": len(svg),
            }
            row.image = [uri]
            row.annotate_meta = meta
            done += 1
            print(f"  [OK] {row.id} 已生成配图（{len(svg)} 字节）")

        await asyncio.gather(*(one(r) for r in rows))
        await s.commit()
        print(f"[DONE] 成功写入 {done}/{len(rows)}")
        return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.dry_run)))
