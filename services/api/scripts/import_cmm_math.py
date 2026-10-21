"""P-Q2b：CMM-Math 高中题导入（ecnu-icalk/educhat-math，许可证未声明→仅内部演示，台账 §13.3）

数据源：D:/AI对话/_math_question_banks/educhat-math/data/all_data.jsonl（28,069 题）
        + Images/{All,Train,Test}_Images/*.jpg
清洗规则（调研笔记 CMM-Math.md + 总纲阶段2）：
- level ∈ {高一,高二,高三} → 8,521 题；analysis 为空/"null" 删除；题干 <15 字删除；
- solution 字段恒 "null" 弃用；题干 <ImageHere> 占位移除，图片按序挂 image[]（data URI）；
- choice：options 文本行拆 {"A": 文本}，答案字母必须在选项键内，否则宁降级为 blank/solution；
- answer ≤60 字 → blank，否则 → solution（题干即题面、answer 为参考答案）；
- scope='pending_qa'（抽样答案正确率 ≥97% 门禁通过后才放行 'student'）；
- hash 幂等去重；许可证未声明 → annotate_meta 标 purpose=internal_demo。

运行：cd services/api && python scripts/import_cmm_math.py [--dry-run] [--limit N]
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

CMM_ROOT = Path(r"D:/AI对话/_math_question_banks/educhat-math")
DATA_JSONL = CMM_ROOT / "data" / "all_data.jsonl"
IMG_DIRS = [CMM_ROOT / "Images" / d for d in ("All_Images", "Train_Images", "Test_Images")]
BATCH = "cmm_math_v1"
LEVELS = {"高一", "高二", "高三"}

_OPTION_LINE_RE = re.compile(r"^([A-Za-z])[.、．]\s*(.*)$")
_IMG_DIR_CACHE: dict[str, Path | None] = {}


def find_image(name: str) -> Path | None:
    cached = _IMG_DIR_CACHE.get(name)
    if cached is not None:
        return cached if cached.exists() else None
    for d in IMG_DIRS:
        p = d / name
        if p.exists():
            _IMG_DIR_CACHE[name] = d
            return p
    return None


def parse_options(text: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for ln in (text or "").splitlines():
        m = _OPTION_LINE_RE.match(ln.strip())
        if m:
            options[m.group(1).upper()] = m.group(2).strip()
    return options


def parse_item(d: dict) -> dict | None:
    level = str(d.get("level") or "").strip()
    if level not in LEVELS:
        return None
    stem = str(d.get("question") or "").strip()
    if len(stem) < 15:
        return None
    analysis = str(d.get("analysis") or "").strip()
    if not analysis or analysis.lower() == "null":
        return None  # 清洗：无解析不入（总纲阶段2规则）
    answer_raw = str(d.get("answer") or "").strip()
    if not answer_raw or answer_raw.lower() == "null":
        return None

    options = parse_options(str(d.get("options") or ""))
    images: list[str] = []
    for name in d.get("image") or []:
        p = find_image(str(name).strip())
        if not p:
            continue
        uri = load_image_data_uri(p)
        if uri:
            images.append(uri)
    stem = stem.replace("<ImageHere>", " ").strip()

    subject = str(d.get("subject") or "").strip()
    source = f"CMM-Math·高中·{subject}（演示）"[:100]

    if options and answer_raw.upper()[:1] in options:
        q_type = "choice"
        answer = answer_raw.upper()[:1]
    elif len(answer_raw) <= 60:
        q_type, answer = "blank", answer_raw
    else:
        q_type, answer = "solution", answer_raw[:2000]

    meta = {
        "license": "undeclared",
        "purpose": "internal_demo",
        "dataset": "CMM-Math",
        "cmm_id": d.get("id"),
        "level": level,
        "subject": subject,
        "n_images": len(images),
    }
    expect_imgs = len(d.get("image") or [])
    if expect_imgs and len(images) < expect_imgs:
        meta["image_missing"] = expect_imgs - len(images)

    full_text = stem + "\n" + "\n".join(f"{k}. {v}" for k, v in options.items())
    return {
        "stem": stem,
        "q_type": q_type,
        "options": options or None,
        "answer": answer,
        "analysis": analysis[:6000],
        "difficulty": "medium",
        "kp_codes": [],
        "source": source,
        "year": None,
        "is_real_exam": False,
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


def collect(limit: int | None = None) -> tuple[list[dict], dict]:
    items, drop = [], {"level": 0, "short_stem": 0, "no_analysis": 0, "no_answer": 0}
    with DATA_JSONL.open(encoding="utf-8") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            level = str(d.get("level") or "").strip()
            if level not in LEVELS:
                drop["level"] += 1
                continue
            row = parse_item(d)
            if row is None:
                stem = str(d.get("question") or "").strip()
                ans = str(d.get("answer") or "").strip().lower()
                if len(stem) < 15:
                    drop["short_stem"] += 1
                elif not ans or ans == "null":
                    drop["no_answer"] += 1
                else:
                    drop["no_analysis"] += 1
                continue
            items.append(row)
            if limit and len(items) >= limit:
                break
    return items, drop


async def main(dry_run: bool, limit: int | None) -> None:
    items, drop = collect(limit)
    seen: set[str] = set()
    unique, dup = [], 0
    for row in items:
        if row["hash"] in seen:
            dup += 1
            continue
        seen.add(row["hash"])
        unique.append(row)
    by_type: dict[str, int] = {}
    with_img, img_total, img_missing = 0, 0, 0
    for r in unique:
        by_type[r["q_type"]] = by_type.get(r["q_type"], 0) + 1
        if r["image"]:
            with_img += 1
            img_total += len(r["image"])
        if (r["annotate_meta"] or {}).get("image_missing"):
            img_missing += 1
    print(f"清洗丢弃: {drop}")
    print(f"解析 {len(items)} → 批内去重 {dup} → 待入库 {len(unique)}")
    print(f"题型: {by_type} | 带图 {with_img}（{img_total} 张）| 图片缺失行 {img_missing}")
    if dry_run:
        for r in unique[:3]:
            print("[样例]", r["q_type"], r["source"], "| 图", len(r["image"]), "|", r["stem"][:66].replace("\n", " "))
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
    print(f"入库完成：新插入 {inserted}（重复跳过 {len(unique) - inserted}），scope=pending_qa 待门禁放行")


if __name__ == "__main__":
    import asyncio

    _limit = None
    if "--limit" in sys.argv:
        _limit = int(sys.argv[sys.argv.index("--limit") + 1])
    asyncio.run(main(dry_run="--dry-run" in sys.argv, limit=_limit))
