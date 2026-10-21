# -*- coding: utf-8 -*-
"""阶段3 晨间修复：Bench-Updates 2023/2024 卷来源修复 + 多选题补导。

背景（用户实测）：
- 2023/2024 卷来源被数据集脏 category 污染（2 行解析文本被错当 category）+ 冗余后缀
  （"…新高考"），导致真题卷分组破碎/不完整；
- 新高考多选题（2024 共 5 道）被单字母答案契约在导入时丢弃 → 卷子缺题、学生无多选题可练。

动作：
1) 来源修复：按题干回对 Updates 文件，source 归一为 "2023新课标Ⅰ卷" 等（去掉脏 category
   与"新高考"冗余后缀）；garbage-category 的 2 行按其解析结论归卷。
2) 多选补导：answer ∈ [A-D]{2,4} 的题以 q_type=choice + 归一排序多字母答案入库
   （meta multi_select=true；判分 _match_choice 多选归一精确比对；UI 支持多选拼字母）。
3) 幂等：stem_hash 去重，重跑跳过已入库。

运行：cd services/api && C:/python13/python scripts/import_gaokao_multichoice.py [--dry-run]
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert  # noqa: E402

from app.config import settings  # noqa: E402
from app.models.database import async_session_factory  # noqa: E402
from app.models.question_bank import QuestionBank, stem_hash  # noqa: E402

UPDATES = Path(r"D:/知识库/GAOKAO-Bench-Updates-main/Data")
FILES = [
    (UPDATES / "GAOKAO-Bench-2023" / "2023_Math_MCQs.json", 2023),
    (UPDATES / "GAOKAO-Bench-2024" / "2024_Math_MCQs.json", 2024),
]
BATCH = "gaokao_multichoice_v1"
_OPTION_LINE_RE = re.compile(r"^([A-D])[.、．]\s*(.*)$")

# category 归一 → 规范卷名
_CAT_MAP = {
    "全国甲卷理科": "全国甲卷（理科）",
    "全国甲卷文科": "全国甲卷（文科）",
    "全国乙卷理科": "全国乙卷（理科）",
    "全国乙卷文科": "全国乙卷（文科）",
    "新课标Ⅰ卷": "新课标Ⅰ卷",
    "新课标Ⅱ卷": "新课标Ⅱ卷",
    "新课标I": "新课标Ⅰ卷",
    "新课标 I": "新课标Ⅰ卷",
    "新课标 I ": "新课标Ⅰ卷",
    "新课标II": "新课标Ⅱ卷",
    "新课标 II": "新课标Ⅱ卷",
}


def split_options(question: str) -> tuple[str, dict[str, str]]:
    lines = question.splitlines()
    stem_lines, options = [], {}
    for ln in lines:
        m = _OPTION_LINE_RE.match(ln.strip())
        if m and len(options) < 4:
            options[m.group(1)] = m.group(2).strip()
        else:
            stem_lines.append(ln)
    if len(options) != 4:
        return question.strip(), {}
    stem = "\n".join(stem_lines).rstrip()
    stem = re.sub(r"[（(]\s*[）)]\s*$", "", stem).rstrip()
    return stem, options


def parse_item(ex: dict, year: int) -> dict | None:
    question = str(ex.get("question") or "").strip()
    if not question:
        return None
    raw = "".join(ex.get("answer") or [])
    answer = re.sub(r"[^A-D]", "", raw.upper())
    if len(answer) < 2 or len(set(answer)) != len(answer):  # 只收多选（单选已入库）
        return None
    stem, options = split_options(question)
    if len(options) != 4:
        return None
    cat = str(ex.get("category") or "").strip()
    # 解析文本被错当 category 的脏行：按其解析结论归卷
    if "全国新高考Ⅱ卷" in cat or "新高考Ⅱ卷" in cat:
        vol = "新课标Ⅱ卷"
    elif "全国甲卷文科" in cat:
        vol = "全国甲卷（文科）"
    elif "全国甲卷理科" in cat:
        vol = "全国甲卷（理科）"
    elif "全国乙卷理科" in cat:
        vol = "全国乙卷（理科）"
    elif "全国乙卷文科" in cat:
        vol = "全国乙卷（文科）"
    else:
        vol = _CAT_MAP.get(cat, "")
    if not vol:
        return None
    source = f"{year}{vol}"
    analysis = str(ex.get("analysis") or "").strip() or None
    meta = {"multi_select": True, "score": ex.get("score") or None, "license": "Apache-2.0",
            "dataset": "GAOKAO-Bench-Updates", "origin_no": None}
    m = re.match(r"\s*(\d{1,2})\s*[.、．]", stem)
    if m:
        meta["origin_no"] = int(m.group(1))
    full_text = stem + "\n" + "\n".join(f"{k}. {v}" for k, v in options.items())
    return {
        "stem": stem,
        "q_type": "choice",
        "options": options,
        "answer": answer,
        "analysis": analysis,
        "difficulty": "medium",
        "kp_codes": [],
        "source": source[:100],
        "year": year,
        "is_real_exam": True,
        "image": [],
        "figure_params": None,
        "is_competition": False,
        "out_of_syllabus": False,
        "source_batch": BATCH,
        "scope": "student",
        "kp_source": "pending",
        "annotate_meta": meta,
        "hash": stem_hash(full_text),
    }


def source_fix_map() -> dict[str, str]:
    """现存脏/冗余来源 → 规范来源（按前缀匹配）。"""
    fixes = {}
    for year in (2023, 2024):
        for raw, canon in _CAT_MAP.items():
            fixes[f"{year}{raw}新高考"] = f"{year}{canon}"
            fixes[f"{year}{raw}"] = f"{year}{canon}"
    return fixes


async def main(dry_run: bool) -> None:
    items = []
    for fp, year in FILES:
        data = json.loads(fp.read_text(encoding="utf-8"))
        for ex in data.get("example", []):
            row = parse_item(ex, year)
            if row:
                row["annotate_meta"]["origin_category"] = str(ex.get("category") or "")[:60]
                items.append(row)
    print(f"多选题解析：{len(items)} 道（2024 新课标 5 + 兜底其它）")
    for r in items[:6]:
        print("  ", r["source"], "| 答案", r["answer"], "|", r["stem"][:40].replace("\n", " "))

    fixes = source_fix_map()
    async with async_session_factory() as db:
        # 1) 来源修复：现存 2023/2024 行按前缀归一
        rows = (await db.execute(
            select(QuestionBank).where(
                QuestionBank.deleted_at.is_(None),
                QuestionBank.scope == "student",
                QuestionBank.year.in_([2023, 2024]),
                QuestionBank.is_real_exam.is_(True),
            )
        )).scalars().all()
        fixed = 0
        for row in rows:
            canon = fixes.get(row.source or "")
            if canon and canon != row.source:
                row.source = canon
                fixed += 1
        await db.commit()
        print(f"来源修复：{fixed} 行归一为规范卷名")

        # 2) 多选补导
        inserted = 0
        for row in items:
            stmt = insert(QuestionBank.__table__).values(**row).on_conflict_do_nothing(
                index_elements=[QuestionBank.__table__.c.hash]
            )
            res = await db.execute(stmt)
            inserted += res.rowcount or 0
        await db.commit()
        print(f"多选入库：新插 {inserted} 道（重复跳过 {len(items) - inserted}）")
        if dry_run:
            await db.rollback()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main(dry_run="--dry-run" in sys.argv))
