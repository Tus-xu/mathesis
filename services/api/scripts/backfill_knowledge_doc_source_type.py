"""knowledge_docs.source_type 数据回填（tel F6：PLAN D7/§3.3「加宽回填」的数据半边）。

缺陷：doc 级 source_type 早期被拍扁为 'textbook'（String(16) 无法表示真实 kind），
RAG 文档级过滤 ``KnowledgeDoc.source_type == content_type``（rag.py:524/674）沦为
no-op——socratic/classroom 以 content_type="textbook" 取「教材正文」时，教师用书/
课标/培训/MIT 语料全部混入（socratic_solver:1026-1029 明注「引导底稿不得引用
教师用书/超纲资料」，当前却过滤不到）。

回填映射（唯一依据 = 仓库既有导入惯例 import_textbook_slices._BOOK_TYPE_MAP：
teacher_book→lesson_plan / standard→standard / training→method_card；doc 级必须落
kb_router 七枚举，因为 RAG 过滤是**精确小写比对**、kb 导入边界亦校验七枚举）：

    TEXTBOOK            → textbook
    CURRICULUM_STANDARD → standard
    TEACHER_GUIDE       → lesson_plan
    TRAINING_MATERIAL   → method_card
    ADVANCED_REFERENCE  → （七枚举无对应值，**不回填**、如实报告——MIT 拓展参考
                          的排除由设计上的 chunk 级 source_kinds 过滤承担，
                          rag.py:169；残余背离由 ratchet 显式记录）

规则：仅当该 doc 的全部 chunk 的 meta->>'source_kind' 唯一同类时才回填；
多 kind / 零 chunk 的 doc 一律不动（零 chunk 无语义依据，多 kind 需人工裁决）。
幂等：目标值已是映射结果 → 跳过；默认 dry-run 只报告，``--apply`` 才写库。

用法（cwd = services/api）::

    uv run python scripts/backfill_knowledge_doc_source_type.py            # dry-run
    uv run python scripts/backfill_knowledge_doc_source_type.py --apply    # 写库
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.models.chunk import Chunk  # noqa: E402 — chunks 表（含 meta）
from app.models.database import async_session_factory  # noqa: E402
from app.models.knowledge_doc import KnowledgeDoc  # noqa: E402

#: chunk 级 kind（UPPER_SNAKE）→ doc 级七枚举 content_type（小写）。
#: 依据：import_textbook_slices._BOOK_TYPE_MAP（book_type→content_type 既有惯例）
#: + kb_router._VALID_CONTENT_TYPES 七枚举（RAG 过滤精确小写比对）。
KIND_TO_CONTENT_TYPE: dict[str, str] = {
    "TEXTBOOK": "textbook",
    "CURRICULUM_STANDARD": "standard",
    "TEACHER_GUIDE": "lesson_plan",
    "TRAINING_MATERIAL": "method_card",
    # ADVANCED_REFERENCE 无七枚举对应值——刻意不映射（见模块 docstring）。
}


async def run(*, apply: bool) -> int:
    async with async_session_factory() as session:
        # 不滤软删：审计器（tools/corpus_integrity._DOC_SQL）统计**全部**行——
        # 回填口径必须与审计口径一致（软删行的 doc 级值同样回填，避免审计残留假背离）
        docs = (await session.execute(select(KnowledgeDoc))).scalars().all()
        chunk_rows = (await session.execute(select(Chunk.doc_id, Chunk.meta_))).all()
        kinds_by_doc: dict[str, set[str]] = {}
        for doc_id, meta in chunk_rows:
            kind = str((meta or {}).get("source_kind") or "").strip()
            if kind:
                kinds_by_doc.setdefault(str(doc_id), set()).add(kind)

        changed = 0
        unmapped: list[str] = []
        multi: list[str] = []
        zero: list[str] = []
        for doc in docs:
            kinds = sorted(kinds_by_doc.get(str(doc.id), set()))
            if not kinds:
                zero.append(doc.title)
                continue
            if len(kinds) > 1:
                multi.append(f"{doc.title}: {kinds}")
                continue
            kind = kinds[0]
            target = KIND_TO_CONTENT_TYPE.get(kind)
            if target is None:
                unmapped.append(f"{doc.title}: kind={kind}")
                continue
            if doc.source_type == target:
                continue  # 幂等：已是正确值
            print(f"  -> {doc.title}: {doc.source_type!r} -> {target!r} (kind={kind})")
            doc.source_type = target
            changed += 1

        print(
            f"回填扫描: docs={len(docs)} 待回填={changed} 零chunk={len(zero)} "
            f"多kind={len(multi)} 无映射值={len(unmapped)}（apply={apply}）"
        )
        for line in multi + unmapped:
            print(f"  [KEEP] {line}")
        if apply and changed:
            await session.commit()
            print(f"[APPLIED] 已回填 {changed} 行（doc 级七枚举，RAG content_type 过滤恢复语义）")
        elif changed:
            print(f"[DRY-RUN] 未写库；加 --apply 执行（将回填 {changed} 行）")
        else:
            print("[OK] 无待回填行（幂等：目标状态已达成）")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="knowledge_docs.source_type 数据回填（拍扁'textbook'→七枚举真实值）"
    )
    parser.add_argument("--apply", action="store_true", help="实际写库（缺省 dry-run）")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(apply=args.apply)))


if __name__ == "__main__":
    main()
