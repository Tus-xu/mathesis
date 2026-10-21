"""P-Q2a：GAOKAO-MM 带图真题导入（Apache 2.0，台账 §13.3 / 调研笔记 _notes/GAOKAO-MM.md）

数据源：D:/AI对话/_math_question_banks/GAOKAO-MM/Data/2010-2023_Math_MCQs.json（80 题数学带图选择题）
        + Data/2010-2023_Math_MCQs/*.png（142 张真题原图，题干图与选项图混合，按原序挂 image[]）

导入纪律（与 import_gaokao_bench 同管线）：
- choice 单题型；选项从题干拆出（{"A": 文本}），拆不出 4 选项宁可不入；
- 真图 PNG → 等比缩放 → data URI 入 image[]（题库 image 列既有先例，无静态文件服务）；
- hash 幂等（stem_hash 唯一，on_conflict_do_nothing 可重跑）；
- scope='pending_qa'：过质量门禁（抽样答案正确率 ≥97%）后才放行为 'student'；
- kp_source='pending' 留批量打标；annotate_meta 记 license 与图片来源。

运行：cd services/api && python scripts/import_gaokao_mm.py [--dry-run]
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy.dialects.postgresql import insert  # noqa: E402

from app.config import settings  # noqa: E402
from app.models.database import async_session_factory  # noqa: E402
from app.models.question_bank import QuestionBank, stem_hash  # noqa: E402
from scripts.bank_image_utils import load_image_data_uri  # noqa: E402

GMM_ROOT = Path(r"D:/AI对话/_math_question_banks/GAOKAO-MM")
DATA_JSON = GMM_ROOT / "Data" / "2010-2023_Math_MCQs.json"
IMG_DIR = GMM_ROOT / "Data" / "2010-2023_Math_MCQs"
BATCH = "gaokao_mm_v1"

_OPTION_LINE_RE = re.compile(r"^([A-D])[.、．]\s*(.*)$")
_OPTION_MARKER_RE = re.compile(r"([A-D])[.、．]\s*")


def split_options(question: str) -> tuple[str, dict[str, str], bool]:
    """拆 (题干, {"A": 文本}, 是否图选项题)。

    GAOKAO-MM 选项两种形态：换行/内联文本（"A. 120"）、整段无换行
    （"( )A. $\\frac{5}{4}$B. ..."）。用"A→B→C→D 连续标记段"切分；
    文本里找不到选项且恰好 5 张图（1 题干 + 4 选项图）→ 图选项题。
    """
    markers = [(m.start(), m.group(1)) for m in _OPTION_MARKER_RE.finditer(question)]
    for i in range(len(markers) - 3):
        if [m[1] for m in markers[i:i + 4]] != ["A", "B", "C", "D"]:
            continue
        pos = {m[1]: m[0] for m in markers[i:i + 4]}
        a, b, c, d = pos["A"], pos["B"], pos["C"], pos["D"]
        end = markers[i + 4][0] if i + 4 < len(markers) else len(question)
        seg = lambda s, e: question[s:e].strip()  # noqa: E731
        if max(len(seg(b, c)), len(seg(c, d))) > 160:  # 误命中正文里的字母序号
            continue
        stem = question[:a].rstrip()
        stem = re.sub(r"[（(]\s*[）)]\s*[）)]?$", "", stem).rstrip()
        stem = re.sub(r"[（(]\\quad[）)]\s*$", "", stem).rstrip()
        return stem, {"A": seg(a + 1, b).rstrip(" ，;；"), "B": seg(b + 1, c), "C": seg(c + 1, d), "D": seg(d + 1, end)}, False
    m4 = _OPTION_LINE_RE.match
    lines = question.splitlines()
    stem_lines, options = [], {}
    for ln in lines:
        mm = m4(ln.strip())
        if mm and len(options) < 4:
            options[mm.group(1)] = mm.group(2).strip()
        else:
            stem_lines.append(ln)
    if len(options) == 4:
        stem = "\n".join(stem_lines).rstrip()
        return stem, options, False
    return question.strip(), {}, True


def resolve_picture(pic: str) -> Path | None:
    """picture 相对路径（'../Data/2010-2023_Math_MCQs/x.png'）→ 实际文件。"""
    name = Path(pic).name
    cand = IMG_DIR / name
    if cand.exists():
        return cand
    cand2 = GMM_ROOT / pic.lstrip("./")
    return cand2 if cand2.exists() else None


def parse_item(ex: dict) -> dict | None:
    question = str(ex.get("question") or "").strip()
    if not question:
        return None
    year = int(ex["year"]) if str(ex.get("year") or "").isdigit() else None
    category = str(ex.get("category") or "").strip("（）() ")
    source = f"{year or ''}{category}（带图真题）".strip() or "GAOKAO-MM"

    stem, options, image_options = split_options(question)

    images: list[str] = []
    for pic in ex.get("picture") or []:
        p = resolve_picture(str(pic))
        if not p:
            continue
        uri = load_image_data_uri(p)
        if uri:
            images.append(uri)

    if image_options:
        # 选项为图（图内自带 A-D 字母标记）：options 存诚实标记文本，图按原序挂 image[]
        # （5 图=首图题干图；4 图=全为选项图 A-D，图内字母可自校验顺序）
        if len(images) not in (4, 5):
            return None  # 图数不符宁可不入
        options = {k: f"（选项{k} 为图，见第 {i + 1} 张图）" for i, k in enumerate("ABCD")}
    elif len(options) != 4:
        return None
    raw_answer = ex.get("answer")
    if isinstance(raw_answer, list):
        raw_answer = raw_answer[0] if raw_answer else ""
    answer = str(raw_answer or "").strip().upper()[:1]
    if answer not in ("A", "B", "C", "D"):
        return None

    analysis = str(ex.get("analysis") or "").strip() or None
    meta = {"license": "Apache-2.0", "dataset": "GAOKAO-MM", "n_images": len(images)}
    if image_options:
        meta["option_images"] = True
        meta["qa_skip"] = "image_options"  # 文本盲解不可判，质量门禁抽样时排除
    score = ex.get("score")
    if score:
        meta["score"] = score

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
        "image": images,
        "figure_params": None,
        "is_competition": False,
        "out_of_syllabus": False,
        "source_batch": BATCH,
        "scope": "pending_qa",
        "kp_source": "pending",
        "annotate_meta": meta,
        "hash": stem_hash(full_text),
    }


def collect() -> list[dict]:
    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    rows = []
    for ex in data.get("example", []):
        row = parse_item(ex)
        if row:
            rows.append(row)
    return rows


async def main(dry_run: bool = False) -> None:
    items = collect()
    seen: set[str] = set()
    unique, dup = [], 0
    for row in items:
        if row["hash"] in seen:
            dup += 1
            continue
        seen.add(row["hash"])
        unique.append(row)
    with_img = sum(1 for r in unique if r["image"])
    total_imgs = sum(len(r["image"]) for r in unique)
    no_pic = sum(
        1 for r in unique
        if not r["image"]
    )
    print(f"解析 {len(items)} → 去重 {dup} → 待入库 {len(unique)} | 带图 {with_img}（共 {total_imgs} 张）| 图缺失 {no_pic}")
    if dry_run:
        for row in unique[:3]:
            print("[样例]", row["source"], "| 图", len(row["image"]), "|", row["stem"][:70].replace("\n", " "))
        return
    inserted = 0
    async with async_session_factory() as db:
        for row in unique:
            stmt = insert(QuestionBank.__table__).values(**row).on_conflict_do_nothing(
                index_elements=[QuestionBank.__table__.c.hash]
            )
            res = await db.execute(stmt)
            inserted += res.rowcount or 0
        await db.commit()
    print(f"入库完成：新插入 {inserted}（重复跳过 {len(unique) - inserted}），scope=pending_qa 待门禁放行，库：{settings.database_url.split('@')[-1]}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main(dry_run="--dry-run" in sys.argv))
