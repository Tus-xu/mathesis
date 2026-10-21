"""P-Q1 题库导入器：GAOKAO-Bench 真题 → question_bank（台账 §13.3）

数据源（本地已有，Apache 2.0）：
- D:/知识库/GAOKAO-Bench-main/data/{Objective,Subjective}_Questions/2010-2022_Math_*.json
- D:/知识库/GAOKAO-Bench-Updates-main/Data/GAOKAO-Bench-{2023,2024}/（数学 MCQs）

导入纪律：
- choice/blank/solution 三题型映射；选择题选项从题干拆出（{"A": 文本}）；
- hash 幂等（stem_hash 唯一约束，on_conflict_do_nothing 可重跑）；
- 含"如图"的题照常入库（题目文字自洽）但 annotate_meta 标记 needs_figure；
- 知识点标注留空（kp_source='pending'），后续批量打标；
- embedding 留空（best-effort，检索走 trgm/题干精确路径）。

运行：cd services/api && python scripts/import_gaokao_bench.py [--dry-run]
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy.dialects.postgresql import insert  # noqa: E402

from app.config import settings  # noqa: E402
from app.models.database import async_session_factory  # noqa: E402
from app.models.question_bank import QuestionBank, stem_hash  # noqa: E402

BENCH = Path(r"D:/知识库/GAOKAO-Bench-main/data")
UPDATES = Path(r"D:/知识库/GAOKAO-Bench-Updates-main/Data")
BATCH = "gaokao_bench_v1"

OBJECTIVE_FILES = [
    (BENCH / "Objective_Questions" / "2010-2022_Math_I_MCQs.json", "Math I"),
    (BENCH / "Objective_Questions" / "2010-2022_Math_II_MCQs.json", "Math II"),
]
FILL_FILES = [
    (BENCH / "Subjective_Questions" / "2010-2022_Math_I_Fill-in-the-Blank.json", "Math I"),
    (BENCH / "Subjective_Questions" / "2010-2022_Math_II_Fill-in-the-Blank.json", "Math II"),
]
SUBJECTIVE_FILES = [
    (BENCH / "Subjective_Questions" / "2010-2022_Math_I_Open-ended_Questions.json", "Math I"),
    (BENCH / "Subjective_Questions" / "2010-2022_Math_II_Open-ended_Questions.json", "Math II"),
]

_OPTION_LINE_RE = re.compile(r"^([A-D])[.、．]\s*(.*)$")


def split_options(question: str) -> tuple[str, dict[str, str]]:
    """把"题干+行内选项"拆成 (题干, {"A": 文本})；无选项结构时原样返回"""
    lines = question.splitlines()
    stem_lines: list[str] = []
    options: dict[str, str] = {}
    for ln in lines:
        m = _OPTION_LINE_RE.match(ln.strip())
        if m and len(options) < 4:
            options[m.group(1)] = m.group(2).strip()
        else:
            stem_lines.append(ln)
    if len(options) != 4:
        return question.strip(), {}
    # 去掉题干末尾的作答括号残迹（( ) （ ））
    stem = "\n".join(stem_lines).rstrip()
    stem = re.sub(r"[（(]\s*[）)]\s*$", "", stem).rstrip()
    return stem, options


def parse_item(ex: dict, q_type: str, paper: str) -> dict | None:
    question = str(ex.get("question") or "").strip()
    if not question:
        return None
    year = int(ex["year"]) if str(ex.get("year") or "").isdigit() else None
    category = str(ex.get("category") or "").strip("（）() ")
    source = f"{year or ''}{category}{paper}".strip() or "GAOKAO-Bench"
    analysis = str(ex.get("analysis") or "").strip() or None
    score = ex.get("score")
    meta: dict = {"score": score} if score else {}
    if re.search(r"如图|如图所示|下图", question):
        meta["needs_figure"] = True

    if q_type == "choice":
        stem, options = split_options(question)
        if len(options) != 4:
            return None  # 选项结构不完整，宁可不入
        raw_answer = ex.get("answer")
        if isinstance(raw_answer, list):
            raw_answer = raw_answer[0] if raw_answer else ""
        answer = str(raw_answer or "").strip().upper()[:1]
        if answer not in ("A", "B", "C", "D"):
            return None
    else:
        stem = question.strip()
        options = {}
        raw_answer = ex.get("answer")
        answer = (
            "；".join(str(a) for a in raw_answer) if isinstance(raw_answer, list) else str(raw_answer or "")
        ).strip()
        if not answer:
            return None

    full_text = stem + "\n" + "\n".join(f"{k}. {v}" for k, v in options.items())
    return {
        "stem": stem,
        "q_type": q_type,
        "options": options or None,
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
        "annotate_meta": meta or None,
        "hash": stem_hash(full_text),
    }


def collect() -> list[dict]:
    items: list[dict] = []
    for path, paper in OBJECTIVE_FILES:
        data = json.loads(path.read_text(encoding="utf-8"))
        for ex in data.get("example", []):
            row = parse_item(ex, "choice", paper)
            if row:
                items.append(row)
    for path, paper in FILL_FILES:
        data = json.loads(path.read_text(encoding="utf-8"))
        for ex in data.get("example", []):
            row = parse_item(ex, "blank", paper)
            if row:
                items.append(row)
    for path, paper in SUBJECTIVE_FILES:
        data = json.loads(path.read_text(encoding="utf-8"))
        for ex in data.get("example", []):
            row = parse_item(ex, "solution", paper)
            if row:
                items.append(row)
    # Updates（2023/2024 数学选择）
    for year_dir in sorted(UPDATES.glob("GAOKAO-Bench-202*")):
        for path in year_dir.rglob("*Math*MCQ*.json"):
            data = json.loads(path.read_text(encoding="utf-8"))
            paper = "新高考" if "II" in path.name or "2" in path.stem[-12:] else ""
            for ex in data.get("example", []):
                row = parse_item(ex, "choice", paper)
                if row:
                    items.append(row)
    return items


async def main(dry_run: bool = False) -> None:
    items = collect()
    seen: set[str] = set()
    unique: list[dict] = []
    dup = 0
    for row in items:
        if row["hash"] in seen:
            dup += 1
            continue
        seen.add(row["hash"])
        unique.append(row)
    by_type: dict[str, int] = {}
    needs_figure = 0
    for row in unique:
        by_type[row["q_type"]] = by_type.get(row["q_type"], 0) + 1
        needs_figure += 1 if (row["annotate_meta"] or {}).get("needs_figure") else 0
    print(f"解析 {len(items)} 条 → 去重 {dup} → 待入库 {len(unique)} 条")
    print(f"题型分布: {by_type} | 需补图: {needs_figure}")
    if dry_run:
        for row in unique[:3]:
            print("[样例]", row["q_type"], row["source"], "|", row["stem"][:80].replace("\n", " "))
        return
    inserted = 0
    async with async_session_factory() as db:
        for row in unique:
            values = {k: v for k, v in row.items() if k != "hash"}
            values["hash"] = row["hash"]
            stmt = insert(QuestionBank.__table__).values(**values).on_conflict_do_nothing(
                index_elements=[QuestionBank.__table__.c.hash]
            )
            result = await db.execute(stmt)
            inserted += result.rowcount or 0
        await db.commit()
    print(f"入库完成：新插入 {inserted} 条（重复跳过 {len(unique) - inserted}），库：{settings.database_url.split('@')[-1]}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main(dry_run="--dry-run" in sys.argv))
