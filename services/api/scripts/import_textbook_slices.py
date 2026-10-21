"""教材预切片真实入库（学生端 / 通用 API 侧，chunks 1024 维）

数据源：`D:\\知识库\\AC切片\\processed\\<batch_id>\\{manifest.json, chunks.jsonl, images/}`
——与教师端 `services/teacher-platform/app/textbook_v2` 同源的**授权预切片**，
本脚本不重新切片、不读 PDF、不解析原书，只做格式转换与入库。

**维度铁律（不得违反）**：
- 本脚本写入 `services/api` 的 `knowledge_docs` + `chunks`，embedding = **1024** 维；
- 教师端 `textbook_chunks` 是 **768** 维，**禁止交叉写索引**；
- 因此本脚本**绝不 import 任何 `app/textbook_v2/**` 或 `app/teacher_prep/**` 模块，
  只复用切片的文件格式约定（manifest + chunks.jsonl）。

用法：
    cd services/api
    # 先干跑：只解析 + 校验 + 统计，不连库、不写库
    .venv/Scripts/python.exe -m scripts.import_textbook_slices --dry-run

    # 指定批次 / 只看人教A版
    .venv/Scripts/python.exe -m scripts.import_textbook_slices --dry-run --book-id bixi1,xbixi1
    .venv/Scripts/python.exe -m scripts.import_textbook_slices --batches 20260726-A-bixi1-01

    # 真实入库（需 PostgreSQL :54329 + Embedding 服务在线）
    .venv/Scripts/python.exe -m scripts.import_textbook_slices
    .venv/Scripts/python.exe -m scripts.import_textbook_slices --force   # 先删旧批次再重导

入库纪律（与 `/api/kb/docs/import` 及 `import_gaokao.py` 一致，SSOT §5.8 / ADR-024）：
- 公式配对检查（$ 偶数、$$ 成对）不通过 → 该切片**拒入**并计数，不静默跳过；
- embedding 为 NULL 不入库（红线）；
- `batch_id` 幂等：已存在则跳过（`--force` 例外）；
- 切片出处（册别/章节/小节/chunk_id）写入 `Chunk.meta_`，支撑教材溯源与 Evidence。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.gateway.kb_router import _VALID_CONTENT_TYPES, _latex_paired  # noqa: E402

# PLAN §7.1：原 D:\知识库\AC切片 已不在线（R4 §1.1 实测），缺省改授权镜像；
# TEXTBOOK_SLICE_ROOTS env 首个存在的 processed 目录优先（与教师端同名 env 同义）。
_DEFAULT_ROOT_CANDIDATES = (
    Path(r"D:\ppt-rescue-control\20260909-R05\教材切片\AC切片镜像\processed"),
)


def _default_root() -> Path:
    import os

    raw = os.environ.get("TEXTBOOK_SLICE_ROOTS", "").strip()
    for part in raw.split(os.pathsep) if raw else []:
        candidate = Path(part.strip()) if part.strip() else None
        if candidate is not None and candidate.is_dir():
            return candidate
    for candidate in _DEFAULT_ROOT_CANDIDATES:
        if candidate.is_dir():
            return candidate
    return _DEFAULT_ROOT_CANDIDATES[0]


DEFAULT_ROOT = _default_root()
EMBED_BATCH = 32
# 用于生成向量的文本上限：教材切片平均约 1200 字符，个别长切片在 CPU 上的
# BGE-M3 推理会显著变慢（实测 32 条 ×512 字符约 18s，长文本单批可超 180s 客户端超时）。
# 截断只作用于 embedding，**入库 content 始终完整**；教材切片的前若干句已足以表达
# 该节的语义，截断对检索召回的影响远小于整批超时失败。
EMBED_MAX_CHARS = 1000

# AC 切片 book_type → 本库 content_type（须落在 kb_router 的七枚举内）
_BOOK_TYPE_MAP = {
    "textbook": "textbook",
    "teacher_book": "lesson_plan",
    "standard": "standard",
    "training": "method_card",
}
_DEFAULT_CONTENT_TYPE = "textbook"


def _discover_batches(root: Path, only: list[str] | None) -> list[Path]:
    """列出待导入批次目录（含 manifest.json + chunks.jsonl 才算合法）。"""
    if not root.is_dir():
        raise SystemExit(f"切片根目录不存在: {root}")
    out: list[Path] = []
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if only and d.name not in only:
            continue
        if (d / "manifest.json").is_file() and (d / "chunks.jsonl").is_file():
            out.append(d)
    return out


def _read_manifest(d: Path) -> dict:
    return json.loads((d / "manifest.json").read_text(encoding="utf-8"))


def _read_chunks(d: Path) -> list[dict]:
    rows: list[dict] = []
    for ln in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            rows.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return rows


def _content_type_for(book_type: str) -> str:
    ct = _BOOK_TYPE_MAP.get((book_type or "").strip(), _DEFAULT_CONTENT_TYPE)
    return ct if ct in _VALID_CONTENT_TYPES else _DEFAULT_CONTENT_TYPE


def _clean_content(text: str) -> str:
    """轻度清洗：去首尾空白、压缩 3+ 连续空行（不动公式、不重排段落）。"""
    if not text:
        return ""
    lines = [ln.rstrip() for ln in text.strip().splitlines()]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if ln.strip():
            blank = 0
            out.append(ln)
        else:
            blank += 1
            if blank <= 1:
                out.append("")
    return "\n".join(out).strip()


def _plan_batch(d: Path, book_ids: list[str] | None) -> tuple[dict, list[dict], dict]:
    """解析一个批次 → (manifest, 待入库切片列表, 统计)。返回空列表表示该批次被过滤。"""
    man = _read_manifest(d)
    if book_ids and man.get("book_id") not in book_ids:
        return man, [], {"skipped_by_filter": True}

    book_name = man.get("book_name") or man.get("book_id") or d.name
    volume = man.get("volume") or ""
    doc_title = f"{book_name}·{volume}" if volume else str(book_name)
    content_type = _content_type_for(man.get("book_type", ""))

    stats: Counter = Counter()
    planned: list[dict] = []
    seen: set[str] = set()

    for idx, row in enumerate(_read_chunks(d)):
        stats["read"] += 1
        content = _clean_content(row.get("content") or "")
        if not content:
            stats["empty"] += 1
            continue
        # 同批次内内容去重（切片源可能含重复段落）
        digest = str(hash(content))
        if digest in seen:
            stats["dup"] += 1
            continue
        seen.add(digest)
        if not _latex_paired(content):
            stats["latex_unpaired"] += 1
            continue
        planned.append(
            {
                "content": content,
                "content_type": content_type,
                "doc_title": doc_title,
                "chunk_index": idx,
                "meta": {
                    "batch_id": man.get("batch_id") or d.name,
                    "book_id": man.get("book_id"),
                    "book_name": book_name,
                    "volume": volume,
                    "section": row.get("section") or "",
                    "subsection": row.get("subsection") or "",
                    "source_chunk_id": row.get("chunk_id") or "",
                    "char_count": row.get("char_count") or len(content),
                    # 溯源标记：与 AI 生成内容区分，供 Evidence / SourceLocator 消费
                    "origin": "ac_slice",
                    "rights": "authorized",
                },
            }
        )
        stats["planned"] += 1
    return man, planned, dict(stats)


async def _kp_lookup(db, sections: set[str]) -> dict[str, str]:
    """章节名 → 知识点 code 的确定性映射（最长名命中，零幻觉）。

    复用 `knowledge_points` 的 name/aliases 做子串匹配——与
    `gateway/v1_compat_router.py::kp_guess` 同一口径，保证接口与入库一致。
    """
    from sqlalchemy import select

    from app.models.knowledge_point import KnowledgePoint

    if not sections:
        return {}
    rows = (
        await db.execute(select(KnowledgePoint.code, KnowledgePoint.name, KnowledgePoint.aliases))
    ).all()
    mapping: dict[str, str] = {}
    for sec in sections:
        if not sec:
            continue
        best_len, best_code = 0, ""
        for code, name, aliases in rows:
            for cand in [name, *(aliases or [])]:
                cand = (cand or "").strip()
                if len(cand) < 2 or cand not in sec:
                    continue
                if len(cand) > best_len:
                    best_len, best_code = len(cand), code
        if best_code:
            mapping[sec] = best_code
    return mapping


async def _persist(all_batches: list[tuple[dict, list[dict]]], force: bool) -> None:
    from sqlalchemy import select

    from app.models.chunk import Chunk
    from app.models.database import async_session_factory
    from app.models.knowledge_doc import KnowledgeDoc
    from app.models.knowledge_point import KnowledgePoint
    from app.providers.embedding import EmbeddingProvider, resolve_embedding_config

    async with async_session_factory() as db:
        embedder = EmbeddingProvider(await resolve_embedding_config(db))
        total_in = 0
        for man, planned in all_batches:
            batch_id = man.get("batch_id") or "unknown"
            existing = (
                await db.execute(
                    select(KnowledgeDoc).where(
                        KnowledgeDoc.meta_["batch_id"].astext == batch_id,
                        KnowledgeDoc.deleted_at.is_(None),
                    )
                )
            ).scalars().first()
            if existing is not None:
                if force:
                    await db.delete(existing)
                    await db.flush()
                else:
                    print(f"  [skip] 批次已存在（幂等）: {batch_id}")
                    continue

            kp_map = await _kp_lookup(db, {p["meta"]["section"] for p in planned})
            kp_ids_by_code: dict[str, list] = {}
            if kp_map:
                codes = set(kp_map.values())
                rows = (
                    await db.execute(
                        select(KnowledgePoint.id, KnowledgePoint.code).where(
                            KnowledgePoint.code.in_(codes)
                        )
                    )
                ).all()
                kp_ids_by_code = {code: kid for kid, code in rows}

            # embedding 批量生成（红线：任一条为 NULL 则整批拒入）
            # 注意：送入向量化的文本按 EMBED_MAX_CHARS 截断（仅影响向量，不影响入库原文）
            vectors: list[list[float]] = []
            for start in range(0, len(planned), EMBED_BATCH):
                part = planned[start : start + EMBED_BATCH]
                try:
                    got = await embedder.embed(
                        [p["content"][:EMBED_MAX_CHARS] for p in part]
                    )
                except Exception as e:  # noqa: BLE001
                    print(f"  [fail] {batch_id} embedding 失败: {str(e)[:120]}")
                    got = []
                vectors.extend(got)
            if len(vectors) != len(planned) or any(not v for v in vectors):
                print(f"  [fail] {batch_id} embedding 不完整，整批拒入（红线）")
                continue

            doc = KnowledgeDoc(
                title=(planned[0]["doc_title"] if planned else batch_id)[:255],
                source_type=planned[0]["content_type"] if planned else "textbook",
                file_uri=str(DEFAULT_ROOT / batch_id)[:512],
                uploader_id=None,
                status="ready",
                meta_={
                    "batch_id": batch_id,
                    "book_id": man.get("book_id"),
                    "book_name": man.get("book_name"),
                    "chunk_count": len(planned),
                    "origin": "ac_slice",
                    "manifest": {k: man.get(k) for k in ("volume", "book_type", "created_at")},
                },
            )
            db.add(doc)
            await db.flush()
            for p, vec in zip(planned, vectors, strict=True):
                kp_code = kp_map.get(p["meta"]["section"])
                db.add(
                    Chunk(
                        doc_id=doc.id,
                        content=p["content"],
                        embedding=vec,
                        kp_ids=[kp_ids_by_code[kp_code]] if kp_code in kp_ids_by_code else [],
                        meta_=p["meta"],
                        chunk_index=p["chunk_index"],
                    )
                )
            await db.commit()
            total_in += len(planned)
            print(f"  [ok]   {batch_id}: {len(planned)} 切片（doc={doc.id}）")
        print(f"\n入库完成，共写入 {total_in} 个切片")


def main() -> None:
    ap = argparse.ArgumentParser(description="教材预切片入库（AC切片 → chunks 1024 维）")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="切片根目录（默认 D:\\知识库\\AC切片\\processed）")
    ap.add_argument("--batches", default="", help="只导入指定批次，逗号分隔")
    ap.add_argument("--book-id", dest="book_ids", default="", help="只导入指定书目 book_id，逗号分隔")
    ap.add_argument("--dry-run", action="store_true", help="只解析+校验+统计，不连库不写库")
    ap.add_argument("--force", action="store_true", help="批次已存在时先删旧批次再重导")
    args = ap.parse_args()

    root = Path(args.root)
    only = [b.strip() for b in args.batches.split(",") if b.strip()] or None
    book_ids = [b.strip() for b in args.book_ids.split(",") if b.strip()] or None

    batches = _discover_batches(root, only)
    if not batches:
        raise SystemExit(f"未发现合法批次（需 manifest.json + chunks.jsonl）: {root}")

    print(f"切片根目录: {root}")
    print(f"发现批次: {len(batches)}" + ("（已按 --batches 过滤）" if only else ""))
    print()

    all_planned: list[tuple[dict, list[dict]]] = []
    total: Counter = Counter()
    for d in batches:
        man, planned, stats = _plan_batch(d, book_ids)
        if stats.get("skipped_by_filter"):
            continue
        all_planned.append((man, planned))
        for k, v in stats.items():
            if k != "skipped_by_filter":
                total[k] += v
        flag = "DRY" if args.dry_run else "RUN"
        print(
            f"[{flag}] {d.name:<26} {man.get('book_name', '')[:28]:<30} "
            f"read={stats.get('read', 0):>4} ok={stats.get('planned', 0):>4} "
            f"dup={stats.get('dup', 0):>3} empty={stats.get('empty', 0):>3} "
            f"latex={stats.get('latex_unpaired', 0):>3}"
        )

    print()
    print("=" * 78)
    print(f"批次 {len(all_planned)}  读取 {total['read']}  计划入库 {total['planned']}")
    print(f"去重 {total['dup']}  空内容 {total['empty']}  公式不配对拒入 {total['latex_unpaired']}")
    print("=" * 78)

    if args.dry_run:
        print("\n[dry-run] 未连接数据库、未写入任何数据。")
        print("确认无误后去掉 --dry-run 执行真实入库（需 PostgreSQL + Embedding 服务在线）。")
        return

    asyncio.run(_persist(all_planned, args.force))


if __name__ == "__main__":
    main()
