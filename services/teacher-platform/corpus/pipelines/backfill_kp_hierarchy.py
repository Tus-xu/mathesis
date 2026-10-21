# coding: utf-8
"""kp_nodes 层级回填（S1-S17 审计 #7 数据层修复，2026-09-07）。

背景：load_to_db._load_kp_nodes 当年硬编码 parent_id=NULL，丢弃了 kp_seed 已解析出的
parent_code/path/prerequisites——2252 个知识点的三级结构（章▸节▸概念/技能）在数据层断裂。

本管线：读 staging/kp_nodes.jsonl（kp_seed.py 对 data_math_full.json 的解析产物，
含 parent_code/path/prerequisites），对**已存在于库中的 code** 幂等回填：
  parent_id ← 按 parent_code 查库；path ← 解析产物；metadata.prerequisites 合并。
只 UPDATE、不 INSERT、不删——不改动 B2 当年圈定的节点集合（语料纪律）。

用法：uv run python -m corpus.pipelines.backfill_kp_hierarchy [--dry-run]
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

STAGING = Path(__file__).resolve().parents[1] / "staging" / "kp_nodes.jsonl"


async def main(dry_run: bool = False) -> int:
    rows = [json.loads(line) for line in STAGING.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"staging rows: {len(rows)}")

    from sqlalchemy import text

    from app.core.config import get_settings
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(get_settings().database_url)
    async with engine.begin() as conn:
        # 库中现有 code → id 映射（只回填已存在节点）
        existing = {
            code: uid for code, uid in (await conn.execute(text("SELECT code, id FROM kp_nodes")))
        }
        print(f"db kp_nodes: {len(existing)}")

        # 父节点解析顺序：先父后子（按 path 深度排序），避免前向引用
        staged = {r["code"]: r for r in rows if r["code"] in existing}

        def depth(r: dict) -> int:
            return len(r.get("path") or [])

        ordered = sorted(staged.values(), key=depth)

        def pg_text_array(items: list) -> str:
            """Python list → PostgreSQL text[] 字面量（kp_nodes.path 列型）。"""
            import json as _json

            return "{" + ",".join(_json.dumps(str(i), ensure_ascii=False) for i in (items or [])) + "}"

        updated_parent = updated_path = missing_parent = 0
        for r in ordered:
            code = r["code"]
            parent_code = r.get("parent_code")
            parent_id = existing.get(parent_code) if parent_code else None
            if parent_code and parent_id is None:
                missing_parent += 1
            meta = json.dumps({"prerequisites": r.get("prerequisites") or []}, ensure_ascii=False)
            if dry_run:
                if parent_code:
                    updated_parent += 1
                continue
            res = await conn.execute(text(
                """
                UPDATE kp_nodes SET
                  parent_id = :parent_id,
                  path = CAST(:path AS text[]),
                  metadata = kp_nodes.metadata || CAST(:meta AS jsonb)
                WHERE code = :code
                """
            ), {
                "parent_id": parent_id,
                "path": [str(p) for p in (r.get("path") or [])],
                "meta": meta,
                "code": code,
            })
            if res.rowcount:
                updated_path += 1
                if parent_code and parent_id:
                    updated_parent += 1

        print(f"updated rows: {updated_path} | parent linked: {updated_parent} | parent_code 不在库中: {missing_parent}")
        if dry_run:
            print("(dry-run，未写库)")
            return 0

        # 验证：层级覆盖率
        total, with_parent = (await conn.execute(text(
            "SELECT COUNT(*), COUNT(parent_id) FROM kp_nodes"
        ))).fetchone()
        roots = (await conn.execute(text(
            "SELECT COUNT(*) FROM kp_nodes WHERE parent_id IS NULL"
        ))).scalar()
        print(f"验证：total={total} with_parent={with_parent} roots={roots}")
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main("--dry-run" in (sys.argv[1:] or []))))
