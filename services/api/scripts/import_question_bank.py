"""结构化题库批量导入脚本（"题库优先"供给链第一环）

用法：
    cd services/api
    python -m scripts.import_question_bank --file <path/to/bank.jsonl> [--dry-run]

jsonl 每行一题（JSON，字段宽容缺省）：
    {
      "stem": "题干（必填，公式用 $...$ LaTeX）",
      "q_type": "choice|blank|solution",     # 缺省推断：有 options → choice，否则 blank
      "options": {"A": "A. ...", ...},        # 可空；list 形式 ["A. ...", ...] 自动归一化为 dict
      "answer": "标准答案（必填）",
      "analysis": "解析（可空）",
      "difficulty": "easy|medium|hard",       # 缺省 medium
      "kp_codes": ["MATH-G1-FUNC-104"],       # 缺省 []（建议存在于 knowledge_points）
      "source": "2023新课标I卷",              # 可空
      "year": 2023,                           # 可空
      "is_real_exam": true                    # 缺省 false
    }

入库纪律：
- stem/answer 必填、题型/难度须为枚举值：非法行跳过并汇总报告（宽容缺省，不整批退回）
- hash（规范化题干 sha256）去重：文件内 + 库内已有一律跳过（幂等可重跑）
- embedding best-effort：在线批量生成（32/批），任何失败落 NULL 继续（不阻塞入库，
  与 import_gaokao 的"NULL 红线"不同——题库检索主路径是 kp/题型/难度过滤，不依赖向量）
- --dry-run：只解析校验 + 报告，不连库不写库
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 确保 app 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.models.database import async_session_factory, init_db
from app.models.question_bank import BANK_DIFFICULTIES, BANK_Q_TYPES, QuestionBank, stem_hash

EMBED_BATCH = 32
# 单次 hash 存在性查询的 IN 批量
_HASH_PROBE_BATCH = 500


def normalize_item(raw: dict, line_no: int) -> tuple[dict | None, str | None]:
    """单行归一化：合法返回 (item, None)；非法返回 (None, 原因)（宽容缺省，必填仅 stem/answer）"""
    stem = str(raw.get("stem") or "").strip()
    if not stem:
        return None, f"第 {line_no} 行 stem 为空"
    answer = str(raw.get("answer") or "").strip()
    if not answer:
        return None, f"第 {line_no} 行 answer 为空"

    # options 宽容归一化：list ["A. ..."] → dict {"A": "A. ..."}；空值 → None
    options = raw.get("options")
    if isinstance(options, list):
        options = {chr(ord("A") + i): str(o) for i, o in enumerate(options)} or None
    elif options is not None and not isinstance(options, dict):
        return None, f"第 {line_no} 行 options 类型非法（须为 dict/list）"

    q_type = str(raw.get("q_type") or "").strip() or ("choice" if options else "blank")
    if q_type not in BANK_Q_TYPES:
        return None, f"第 {line_no} 行 q_type 非法: {q_type}（仅 {BANK_Q_TYPES}）"
    if q_type == "choice" and not options:
        return None, f"第 {line_no} 行选择题缺 options"

    difficulty = str(raw.get("difficulty") or "").strip() or "medium"
    if difficulty not in BANK_DIFFICULTIES:
        return None, f"第 {line_no} 行 difficulty 非法: {difficulty}（仅 {BANK_DIFFICULTIES}）"

    kp_codes = raw.get("kp_codes") or []
    if not isinstance(kp_codes, list):
        return None, f"第 {line_no} 行 kp_codes 类型非法（须为数组）"
    kp_codes = [str(c).strip() for c in kp_codes if str(c).strip()]

    year = raw.get("year")
    if year is not None:
        try:
            year = int(year)
        except (TypeError, ValueError):
            return None, f"第 {line_no} 行 year 非法: {raw.get('year')}"

    analysis = raw.get("analysis")
    source = raw.get("source")
    # 新字段透传（宽容缺省，方案 §4.2）
    scope = str(raw.get("scope") or "student").strip()
    if scope not in ("student", "teacher", "research"):
        scope = "student"
    return {
        "stem": stem,
        "q_type": q_type,
        "options": options,
        "answer": answer,
        "analysis": str(analysis).strip() or None if analysis is not None else None,
        "difficulty": difficulty,
        "kp_codes": kp_codes,
        "source": str(source).strip() or None if source is not None else None,
        "year": year,
        "is_real_exam": bool(raw.get("is_real_exam")),
        "scope": scope,
        "source_batch": str(raw.get("source_batch") or "").strip() or None,
        "is_competition": bool(raw.get("is_competition")),
        "out_of_syllabus": bool(raw.get("out_of_syllabus")),
        "kp_status": str(raw.get("kp_status") or "ok").strip() or None,
        "kp_confidence": str(raw.get("kp_confidence") or "").strip() or None,
        "kp_granular": str(raw.get("kp_granular") or "").strip() or None,
        "kp_source": str(raw.get("kp_source") or "").strip() or None,
        "annotate_meta": raw.get("annotate_meta") if isinstance(raw.get("annotate_meta"), dict) else None,
        "image": raw.get("image") if isinstance(raw.get("image"), list) else [],
        "hash": stem_hash(stem),
        "embedding": raw.get("embedding"),  # 预生成向量优先；无则在线 best-effort
    }, None


def load_items(jsonl_path: Path) -> dict:
    """逐行解析 + 归一化 + 文件内 hash 去重（不查库）

    返回 {"items", "errors", "total", "dup_in_file"}：total 为有效 JSON 行数。
    """
    items: list[dict] = []
    errors: list[str] = []
    total = 0
    dup_in_file = 0
    seen: set[str] = set()
    for idx, line in enumerate(jsonl_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"第 {idx} 行 JSON 非法: {e}")
            continue
        if not isinstance(raw, dict):
            errors.append(f"第 {idx} 行须为 JSON 对象")
            continue
        total += 1
        item, error = normalize_item(raw, idx)
        if error:
            errors.append(error)
            continue
        if item["hash"] in seen:
            dup_in_file += 1
            continue
        seen.add(item["hash"])
        items.append(item)
    return {"items": items, "errors": errors, "total": total, "dup_in_file": dup_in_file}


async def _embed_best_effort(db, items: list[dict]) -> int:
    """在线批量 embedding（32/批）；任何异常 → 告警并保留 NULL（best-effort，不阻塞入库）。

    返回成功生成条数。预生成 embedding 的行不重复生成。
    """
    need = [i for i, item in enumerate(items) if not item.get("embedding")]
    if not need:
        return 0
    try:
        from app.providers.embedding import EmbeddingProvider, resolve_embedding_config

        embedder = EmbeddingProvider(await resolve_embedding_config(db))
        done = 0
        for start in range(0, len(need), EMBED_BATCH):
            batch_idx = need[start : start + EMBED_BATCH]
            vectors = await embedder.embed([items[i]["stem"][:1000] for i in batch_idx])
            if not vectors or len(vectors) != len(batch_idx):
                raise RuntimeError("embedding 服务返回条数异常")
            for i, vec in zip(batch_idx, vectors, strict=True):
                items[i]["embedding"] = vec
            done += len(batch_idx)
            print(f"  …embedding 已生成 {done}/{len(need)}")
        return done
    except Exception as e:
        # best-effort：失败行保留 NULL 继续入库（题库检索主路径不依赖向量）
        print(f"[WARN] embedding 生成失败，落 NULL 继续入库: {type(e).__name__}: {str(e)[:120]}")
        return 0


async def run(jsonl_path: Path, dry_run: bool) -> int:
    report = load_items(jsonl_path)
    items, errors = report["items"], report["errors"]
    print(
        f"[OK] 解析完成：有效 {len(items)} 条 / 非法 {len(errors)} 条 "
        f"/ 文件内重复 {report['dup_in_file']} 条（共 {report['total']} 行）"
    )
    if errors:
        print("[WARN] 非法行前 10 条（已跳过）：")
        for e in errors[:10]:
            print(f"  - {e}")
    if not items:
        print("[FAIL] 无有效数据")
        return 1
    if dry_run:
        by_type = {}
        for item in items:
            by_type[item["q_type"]] = by_type.get(item["q_type"], 0) + 1
        print(f"[DRY-RUN] 不写库。题型分布: {by_type}")
        print(f"[DRY-RUN] 首条样例: stem={items[0]['stem'][:60]}… hash={items[0]['hash'][:12]}…")
        return 0

    await init_db()
    async with async_session_factory() as db:
        # 库内 hash 去重（分批 IN 探测）
        existing: set[str] = set()
        hashes = [item["hash"] for item in items]
        for start in range(0, len(hashes), _HASH_PROBE_BATCH):
            rows = await db.execute(
                select(QuestionBank.hash).where(
                    QuestionBank.hash.in_(hashes[start : start + _HASH_PROBE_BATCH])
                )
            )
            existing.update(rows.scalars().all())
        fresh = [item for item in items if item["hash"] not in existing]
        skipped = len(items) - len(fresh)
        if skipped:
            print(f"[SKIP] 库内已存在 {skipped} 条（hash 去重，幂等跳过）")
        if not fresh:
            print("[DONE] 无需入库（全部已存在）")
            return 0

        # kp_codes 存在性提示（只告警不拦截：题库允许先入库后补知识点）
        all_codes = list({c for item in fresh for c in item["kp_codes"]})
        if all_codes:
            from app.models.knowledge_point import KnowledgePoint

            found = set(
                (
                    await db.execute(
                        select(KnowledgePoint.code).where(KnowledgePoint.code.in_(all_codes))
                    )
                ).scalars().all()
            )
            unknown = [c for c in all_codes if c not in found]
            if unknown:
                print(f"[WARN] {len(unknown)} 个 kp_code 不在 knowledge_points（不影响入库）: {unknown[:5]}")

        # embedding best-effort（失败落 NULL 继续）
        embedded = await _embed_best_effort(db, fresh)

        for item in fresh:
            db.add(
                QuestionBank(
                    stem=item["stem"],
                    q_type=item["q_type"],
                    options=item["options"],
                    answer=item["answer"],
                    analysis=item["analysis"],
                    difficulty=item["difficulty"],
                    kp_codes=item["kp_codes"],
                    source=item["source"],
                    year=item["year"],
                    is_real_exam=item["is_real_exam"],
                    embedding=item["embedding"],
                    hash=item["hash"],
                    scope=item.get("scope", "student"),
                    source_batch=item.get("source_batch"),
                    is_competition=item.get("is_competition", False),
                    out_of_syllabus=item.get("out_of_syllabus", False),
                    kp_status=item.get("kp_status"),
                    kp_confidence=item.get("kp_confidence"),
                    kp_granular=item.get("kp_granular"),
                    kp_source=item.get("kp_source"),
                    annotate_meta=item.get("annotate_meta"),
                    image=item.get("image", []),
                )
            )
        await db.commit()
        print(
            f"[DONE] 入库成功：新增 {len(fresh)} 条（embedding 成功 {embedded} 条，"
            f"跳过已存在 {skipped} 条）"
        )
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="结构化题库批量导入（题库优先供给链）")
    parser.add_argument("--file", required=True, help="题库 jsonl 文件路径（每行一题）")
    parser.add_argument("--dry-run", action="store_true", help="只解析校验+报告，不写库")
    args = parser.parse_args()

    jsonl_path = Path(args.file)
    if not jsonl_path.exists():
        print(f"[FAIL] 文件不存在: {jsonl_path}")
        sys.exit(1)

    rc = asyncio.run(run(jsonl_path, args.dry_run))
    sys.exit(rc)


if __name__ == "__main__":
    main()
