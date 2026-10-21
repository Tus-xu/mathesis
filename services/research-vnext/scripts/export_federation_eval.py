"""RV06 · 导出固定检索评测集（真实 Teacher direct-chunk + 真实 Crossref metadata）。

设计意图（任务书验收：Recall/nDCG/可回源率，且**不得硬编码查询特例**）：

- 评测集**一次性生成后落盘**（`tests/fixtures/federation/eval/queries.jsonl`），
  评测测试只读文件 —— 指标因此可复现、可跨轮次对比；
- 查询文本取自**真实** section 标题（去掉编号前缀），相关性判据取自**真实** chunk id；
- 查询集里不写任何"某查询应返回某条结果"的特例逻辑：判据是 `relevant` 里的 locator，
  评分代码只做集合运算。

用法（需 Teacher 只读库可达）：
    python scripts/export_federation_eval.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

TEACHER_ENV = SERVICE_ROOT.parent / "teacher-platform" / ".env"
OUT_PATH = SERVICE_ROOT / "tests" / "fixtures" / "federation" / "eval" / "queries.jsonl"

#: 真实 Crossref 记录（RV04 实测抓取；DOI 与标题均来自真实响应）。
LITERATURE_CASES: list[dict] = [
    {
        "query_id": "lit-01",
        "dimension": "LITERATURE",
        "query_text": "deep learning",
        "relevant": ["doi:10.1038/nature14539"],
    },
    {
        "query_id": "lit-02",
        "dimension": "LITERATURE",
        "query_text": "transformer attention",
        "relevant": ["doi:10.48550/arxiv.1706.03762"],
    },
]

#: 项目资产：文件名固定，判据用文件名（不是随机 id，保证跨运行可比）。
ASSET_CASES: list[dict] = [
    {
        "query_id": "pa-01",
        "dimension": "PROJECT_ASSET",
        "query_text": "函数与导数专题",
        "relevant": ["filename:函数与导数专题.pdf"],
    },
    {
        "query_id": "pa-02",
        "dimension": "PROJECT_ASSET",
        "query_text": "立体几何",
        "relevant": ["filename:立体几何复习讲义.pdf"],
    },
]

SECTION_LIMIT = 8
MIN_CHUNKS_PER_SECTION = 3


def _teacher_dsn() -> str:
    explicit = os.getenv("RESEARCH_FOUNDATIONAL_DATABASE_URL")
    if explicit:
        return re.sub(r"^postgresql\+\w+://", "postgresql+psycopg://", explicit)
    for line in TEACHER_ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\s*DATABASE_URL\s*=\s*(.*)$", line)
        if m:
            raw = m.group(1).strip().strip('"').strip("'")
            return re.sub(r"^postgresql\+\w+://", "postgresql+psycopg://", raw)
    raise SystemExit("找不到 Teacher DATABASE_URL；请设置 RESEARCH_FOUNDATIONAL_DATABASE_URL")


def _strip_number_prefix(title: str) -> str:
    return re.sub(r"^[\s\d.、]+", "", title or "").strip()


def main() -> int:
    from sqlalchemy import text as sa_text

    from research_vnext.core.federation.providers.foundational_math import (
        build_readonly_engine,
    )

    dsn = _teacher_dsn()
    cases: list[dict] = []
    engine = build_readonly_engine(dsn)

    with engine.connect() as conn:
        rows = list(
            conn.execute(
                sa_text(
                    """
                    SELECT s.id, s.textbook_id, s.title
                    FROM textbook_sections AS s
                    JOIN textbook_sources AS src ON src.id = s.textbook_id
                    WHERE src.rights_status IN ('STAGED', 'ACTIVE')
                      AND s.title IS NOT NULL
                      AND s.title <> '未分节'
                    ORDER BY s.title, s.id
                    """
                )
            )
        )

        # 按**归一化标题**分组：同名章节常见于多册教材（如"数量关系"），
        # 用户问的是"那一节"而不是"某一册里的那一节"，因此判据要覆盖全部同名节。
        # 只认单册会让指标变成"碰巧选中了哪一册"的函数。
        grouped: dict[str, list[tuple[Any, Any]]] = {}
        for sec_id, src_id, title in rows:
            key = _strip_number_prefix(title)
            if len(key) < 3:
                continue
            grouped.setdefault(key, []).append((sec_id, src_id))

        picked: list[tuple[str, list[tuple[Any, Any]]]] = []
        # 查询要像"人真的会问的话"：短标题、不含 LaTeX 记号。
        # 若按长度降序选，会拿到 `探究 A 对 $y=A\\sin(\\omega x+\\varphi)$ 的图象的影响`
        # 这类公式标题 —— 那不是查询，是排版残留。
        candidates = [
            t for t in grouped if 3 <= len(t) <= 16 and "$" not in t and "\\" not in t
        ]
        for query_text in sorted(candidates, key=lambda t: (len(t), t)):
            if len(picked) >= SECTION_LIMIT:
                break
            picked.append((query_text, grouped[query_text]))

        for query_text, members in picked:
            relevant: list[str] = []
            for sec_id, src_id in members:
                chunk_ids = [
                    row[0]
                    for row in conn.execute(
                        sa_text(
                            "SELECT id FROM textbook_chunks"
                            " WHERE section_id = :sid ORDER BY id"
                        ),
                        {"sid": sec_id},
                    )
                ]
                relevant.extend(f"textbook:{src_id}:{sec_id}:{cid}" for cid in chunk_ids)
            if len(relevant) < MIN_CHUNKS_PER_SECTION:
                continue
            cases.append(
                {
                    "dimension": "FOUNDATIONAL_MATH",
                    "query_text": query_text,
                    "relevant": relevant,
                    # 该标题在语料中出现的节数（同名章节消歧的难度指示）
                    "same_title_sections": len(members),
                    "judged_source": "teacher_platform.textbook_chunks",
                }
            )

    # 统一编号：跳过组不产生空号
    for idx, case in enumerate(cases, start=1):
        case["query_id"] = f"fm-{idx:02d}"

    cases.extend(LITERATURE_CASES)
    cases.extend(ASSET_CASES)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        for case in cases:
            fh.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")

    by_dim: dict[str, int] = {}
    for c in cases:
        by_dim[c["dimension"]] = by_dim.get(c["dimension"], 0) + 1
    print(f"wrote {len(cases)} cases → {OUT_PATH}")
    print("by dimension:", by_dim)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
