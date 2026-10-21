"""难度重打标脚本（P1-4，M2 迭代18）

背景：题库 5727 题里，高考真题(gkb-*) 难度被导入时拍平为 medium；
TAL-SCQ5K(4668) 自带标签几乎全 easy，分布失衡。

方案（题号规则 + LLM 混合）：
- gkb 真题：按"题号 + 题型"规则映射（高考全国卷难度梯度惯例），
  解答题再经 LLM 逐题复核（题号规则对解答题最不可靠）。
- tal-scq5k：无题号信息，全部走 LLM 批量重标（10 题/批）。

写入：difficulty 字段 + annotate_meta.difficulty_relabel 溯源

用法：
    python -m scripts.relabel_difficulty --scope gkb --dry-run
    python -m scripts.relabel_difficulty --scope gkb
    python -m scripts.relabel_difficulty --scope tal --llm-limit 50
    python -m scripts.relabel_difficulty --scope tal
"""

import argparse
import asyncio
import json
import re
import sys
from collections import Counter
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.config import settings
from app.models.database import async_session_factory
from app.models.question_bank import QuestionBank

GKB_BATCHES = ("gkb-2010-2022", "gkb-2023", "gkb-2024")

# 高考全国卷难度梯度惯例（题号 → 难度）
_CHOICE_RULE = {r: "easy" for r in range(1, 7)} | {r: "medium" for r in range(7, 11)} | {r: "hard" for r in range(11, 100)}
_BLANK_RULE = {13: "medium", 14: "medium", 15: "hard", 16: "hard"}
_SOLUTION_RULE = {17: "easy", 18: "medium", 19: "medium", 20: "hard", 21: "hard", 22: "hard", 23: "medium"}

_LLM_BATCH = 10
_LLM_PROMPT = (
    "你是高中数学教研员。请按难度量表给下面{num}道高中数学题各打一个难度标签。\n"
    "难度量表：easy=基础题（概念直接套用/单步计算，送分）；"
    "medium=中档题（需要 2~3 步推理、常规方法组合，高考中档）；"
    "hard=难题（综合性强/多知识点交叉/压轴位置/有思维难点）。\n"
    "题目列表（编号: 题干）：\n{questions}\n"
    "只输出 JSON 数组，格式：[{{\"id\": 1, \"difficulty\": \"easy|medium|hard\", \"reason\": \"一句话理由\"}}]，不要任何多余文字。"
)


def extract_question_no(stem: str) -> int | None:
    """题干开头的题号（如 '10. (5 分) ...' / '5.已知...' / '17. 已知...'）。"""
    m = re.match(r"^\s*(\d{1,2})\s*[\.、．]", stem or "")
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 30 else None


def rule_difficulty(q_type: str, question_no: int | None) -> str | None:
    """题号规则映射；无题号返回 None（交 LLM）。"""
    if question_no is None:
        return None
    if q_type == "choice":
        return _CHOICE_RULE.get(question_no)
    if q_type == "blank":
        return _BLANK_RULE.get(question_no)
    if q_type == "solution":
        return _SOLUTION_RULE.get(question_no)
    return None


async def llm_label_batch(items: list[tuple[int, str]]) -> list[dict] | None:
    """MiMo 批量难度重标（OpenAI 兼容端点，复用 settings 里的 deepseek 通道）。

    items: [(seq, stem)]；失败返回 None（调用方回退规则/原值）。
    """
    if not settings.deepseek_api_key:
        print("[WARN] DEEPSEEK_API_KEY 未配置，跳过 LLM 复核")
        return None
    questions = "\n".join(f"{i}. {stem[:200]}" for i, stem in items)
    body = {
        "model": settings.deepseek_model or "mimo-v2.5-pro",
        "messages": [
            {"role": "user", "content": _LLM_PROMPT.format(num=len(items), questions=questions)}
        ],
        "temperature": 0.1,
    }
    url = (settings.deepseek_base_url or "").rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    url = url + "/chat/completions"
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                r = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
                    json=body,
                )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            content = re.sub(r"```(?:json)?", "", content).strip()
            m = re.search(r"\[.*\]", content, re.S)
            arr = json.loads(m.group(0)) if m else None
            if isinstance(arr, list):
                return arr
        except Exception as e:
            print(f"[WARN] LLM 第 {attempt + 1} 次失败: {type(e).__name__}: {str(e)[:100]}")
    return None


async def run(scope: str, dry_run: bool, llm_limit: int | None, llm_skip: bool) -> int:
    async with async_session_factory() as s:
        if scope == "gkb":
            cond = QuestionBank.source_batch.in_(GKB_BATCHES)
        else:
            cond = QuestionBank.source_batch == "tal-scq5k"
        rows = (await s.execute(select(QuestionBank).where(cond))).scalars().all()
        print(f"[OK] 范围 {scope}: {len(rows)} 题")

        planned: dict[object, str] = {}
        to_llm: list[tuple[QuestionBank, str | None]] = []
        for row in rows:
            if scope == "tal":
                # TAL 题号是数据集内部编号，不能套高考题号规则，全量走 LLM
                planned[row.id] = "llm"
                to_llm.append((row, None))
                continue
            no = extract_question_no(row.stem)
            rule = rule_difficulty(row.q_type, no)
            if rule:
                planned[row.id] = rule
            else:
                planned[row.id] = "llm"
                to_llm.append((row, None))
        # gkb 解答题：即使有规则也交 LLM 复核（题号规则对解答题最不可靠）
        if scope == "gkb" and not llm_skip:
            seen = {id(r) for r, _ in to_llm}
            for row in rows:
                if row.q_type == "solution" and id(row) not in seen:
                    to_llm.append((row, planned.get(row.id)))

        dist = Counter(planned.values())
        print(f"[DRY] 计划分布(rule+待LLM): {dict(dist)}")
        if dry_run:
            cur = Counter(r.difficulty for r in rows)
            print(f"[DRY] 当前分布: {dict(cur)}")
            print(f"[DRY] 需 LLM 复核: {len(to_llm)} 题（解答题/无题号）")
            return 0

        # ---- LLM 批次 ----
        llm_results: dict[object, dict] = {}
        if llm_limit:
            to_llm = to_llm[: llm_limit * _LLM_BATCH]
        for start in range(0, len(to_llm), _LLM_BATCH):
            batch = to_llm[start : start + _LLM_BATCH]
            arr = await llm_label_batch([(i + 1, r.stem) for i, (r, _) in enumerate(batch)])
            if arr is None:
                print(f"[WARN] 批次 {start // _LLM_BATCH + 1} LLM 失败，该批回退规则/原值")
                continue
            for (row, _), res in zip(batch, arr, strict=False):
                d = str(res.get("difficulty") or "").strip().lower()
                if d in ("easy", "medium", "hard"):
                    llm_results[row.id] = {"difficulty": d, "reason": str(res.get("reason", ""))[:200]}
            print(f"  ...LLM 已完成 {min(start + _LLM_BATCH, len(to_llm))}/{len(to_llm)}")

        # ---- 写入 ----
        changed = 0
        for row in rows:
            llm_d = llm_results.get(row.id, {}).get("difficulty")
            new_diff = llm_d or planned.get(row.id)
            if not new_diff or new_diff == "llm":
                continue
            meta = dict(row.annotate_meta or {})
            meta["difficulty_relabel"] = {
                "method": "llm" if row.id in llm_results else "rule",
                "old": row.difficulty,
                "new": new_diff,
                "rule": planned.get(row.id),
                "llm": llm_d,
                "llm_reason": llm_results.get(row.id, {}).get("reason", ""),
            }
            row.difficulty = new_diff
            row.annotate_meta = meta
            changed += 1
        await s.commit()
        cur = Counter(r.difficulty for r in rows)
        print(f"[DONE] 已更新 {changed} 题。当前分布: {dict(cur)}")
        return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--scope", choices=["gkb", "tal"], default="gkb")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--llm-limit", type=int, default=None, help="LLM 最多处理的批数（10题/批）")
    ap.add_argument("--llm-skip", action="store_true", help="完全跳过 LLM（纯规则）")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.scope, args.dry_run, args.llm_limit, args.llm_skip)))
