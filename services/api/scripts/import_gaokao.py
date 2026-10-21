"""GAOKAO 844 题知识库批量入库脚本（迭代05 补齐 B-P1-14，SSOT §5.8 / ADR-024）

数据供给链第一环：批量 embed + chunks 写入，修复 F1 底稿/F2 原型/RAG 检索三处空转。

用法：
    cd services/api
    python -m scripts.import_gaokao --jsonl <path/to/gaokao_844.jsonl> --batch-id 20260804-gaokao-01

jsonl 每行一题（JSON）：
    {
      "content": "题干+答案+解析全文（公式用 $...$ LaTeX）",
      "content_type": "question",              # 可选，默认 question
      "kp_codes": ["MATH-G1-TRIG-001"],        # 可选，须存在于 knowledge_points
      "doc_title": "GAOKAO 844 题集"            # 可选，默认取 batch_id
    }

入库纪律（与 /api/kb/docs/import 一致，SSOT §5.8）：
- 公式配对检查（$ 偶数、$$ 成对）100% 通过才入库（ADR-024：禁止在公式内部截断的产物）
- kp_codes 必须存在于 knowledge_points 表
- embedding 为 NULL 不允许入库（红线）；未预生成时在线批量生成（32/批）
- batch_id 幂等：已存在则跳过（--force 先删旧批次再重导）

需要 PostgreSQL + Embedding 服务（BGE-M3 :8080）运行中。
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 确保 app 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from app.gateway.kb_router import _VALID_CONTENT_TYPES, _latex_paired
from app.models.chunk import Chunk
from app.models.database import async_session_factory, init_db
from app.models.knowledge_doc import KnowledgeDoc
from app.models.knowledge_point import KnowledgePoint
from app.providers.embedding import EmbeddingProvider, resolve_embedding_config

EMBED_BATCH = 32


def load_and_validate(jsonl_path: Path) -> tuple[list[dict], list[str]]:
    """逐行解析 + 本地校验（不查库的部分）"""
    items: list[dict] = []
    errors: list[str] = []
    for idx, line in enumerate(jsonl_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"第 {idx} 行 JSON 非法: {e}")
            continue
        content = (item.get("content") or "").strip()
        if not content:
            errors.append(f"第 {idx} 行 content 为空")
            continue
        content_type = item.get("content_type") or "question"
        if content_type not in _VALID_CONTENT_TYPES:
            errors.append(f"第 {idx} 行 content_type 非法: {content_type}")
            continue
        if not _latex_paired(content):
            errors.append(f"第 {idx} 行公式配对检查未通过（$/$$ 不配对，ADR-024）")
            continue
        item["content"] = content
        item["content_type"] = content_type
        items.append(item)
    return items, errors


async def run(jsonl_path: Path, batch_id: str, doc_title: str, force: bool) -> int:
    await init_db()

    items, errors = load_and_validate(jsonl_path)
    if errors:
        print(f"[FAIL] 本地校验未通过（整批退回制），共 {len(errors)} 处错误，前 10 条：")
        for e in errors[:10]:
            print(f"  - {e}")
        return 1
    if not items:
        print("[FAIL] jsonl 无有效数据")
        return 1
    print(f"[OK] 本地校验通过：{len(items)} 条")

    async with async_session_factory() as db:
        # 幂等检查
        existing = await db.execute(
            select(KnowledgeDoc).where(
                KnowledgeDoc.meta_["batch_id"].astext == batch_id,
                KnowledgeDoc.deleted_at.is_(None),
            )
        )
        old_doc = existing.scalar_one_or_none()
        if old_doc is not None:
            if not force:
                print(
                    f"[SKIP] batch_id={batch_id} 已存在（doc_id={old_doc.id}）；如需重导加 --force"
                )
                return 0
            await db.execute(delete(Chunk).where(Chunk.doc_id == old_doc.id))
            await db.execute(delete(KnowledgeDoc).where(KnowledgeDoc.id == old_doc.id))
            print(f"[INFO] --force：已删除旧批次 doc_id={old_doc.id}")

        # kp_codes 存在性校验 + 转 kp_ids
        all_codes = list({c for item in items for c in (item.get("kp_codes") or [])})
        kp_map = {}
        if all_codes:
            rows = await db.execute(
                select(KnowledgePoint.id, KnowledgePoint.code).where(
                    KnowledgePoint.code.in_(all_codes)
                )
            )
            kp_map = {code: kp_id for kp_id, code in rows.all()}
        unknown = [c for c in all_codes if c not in kp_map]
        if unknown:
            print(
                f"[FAIL] kp_codes 不存在于 knowledge_points（整批退回）: {', '.join(unknown[:10])}"
            )
            return 1

        # embedding：预生成优先，否则在线批量生成（红线：NULL 不入库）
        need_embed = [i for i, item in enumerate(items) if not item.get("embedding")]
        if need_embed:
            print(
                f"[INFO] {len(need_embed)} 条无预生成 embedding，在线批量生成（{EMBED_BATCH}/批）…"
            )
            # embedding 提供商可配置（与 /api/kb/docs/import 端点一致）：
            # system_configs["embedding"] 优先 → env 兜底；库内无配置时自动回退本地 BGE-M3
            embedder = EmbeddingProvider(await resolve_embedding_config(db))
            for start in range(0, len(need_embed), EMBED_BATCH):
                batch_idx = need_embed[start : start + EMBED_BATCH]
                vectors = await embedder.embed([items[i]["content"] for i in batch_idx])
                if not vectors or len(vectors) != len(batch_idx):
                    print(
                        f"[FAIL] embedding 服务返回异常（批 {start // EMBED_BATCH + 1}），整批退回"
                    )
                    return 1
                for i, vec in zip(batch_idx, vectors, strict=True):
                    items[i]["embedding"] = vec
                print(f"  …已生成 {min(start + EMBED_BATCH, len(need_embed))}/{len(need_embed)}")

        null_emb = [i for i, item in enumerate(items) if not item.get("embedding")]
        if null_emb:
            print(f"[FAIL] {len(null_emb)} 条 embedding 为 NULL（红线拒入，整批退回）")
            return 1

        # 落库
        doc = KnowledgeDoc(
            title=doc_title[:255],
            source_type="question",
            status="ready",
            meta_={"batch_id": batch_id, "chunk_count": len(items), "source": "gaokao_844"},
        )
        db.add(doc)
        await db.flush()
        for idx, item in enumerate(items):
            db.add(
                Chunk(
                    doc_id=doc.id,
                    content=item["content"],
                    embedding=item["embedding"],
                    kp_ids=[kp_map[c] for c in (item.get("kp_codes") or [])],
                    chunk_index=idx,
                )
            )
        await db.commit()

        print(f"[DONE] 入库成功：doc_id={doc.id}，chunks={len(items)}，batch_id={batch_id}")
        print(
            "  验证建议：调用 POST /api/kb/retrieve 或 /tools/retrieve 抽查召回质量（目标 Recall@5 ≥85%）"
        )
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="GAOKAO 844 题知识库批量入库（SSOT §5.8）")
    parser.add_argument("--jsonl", required=True, help="chunks jsonl 文件路径（每行一题）")
    parser.add_argument("--batch-id", required=True, help="批次编号，如 20260804-gaokao-01")
    parser.add_argument("--doc-title", default="GAOKAO 844 题集", help="文档标题")
    parser.add_argument("--force", action="store_true", help="batch_id 已存在时先删旧批次再重导")
    args = parser.parse_args()

    jsonl_path = Path(args.jsonl)
    if not jsonl_path.exists():
        print(f"[FAIL] 文件不存在: {jsonl_path}")
        sys.exit(1)

    rc = asyncio.run(run(jsonl_path, args.batch_id, args.doc_title, args.force))
    sys.exit(rc)


if __name__ == "__main__":
    main()
