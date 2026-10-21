"""测试残留数据清理脚本

删除 knowledge_points 中所有非 MATH- 前缀的自动化测试残留节点
（正式 KP 均为 MATH- 前缀；残留家族包括 e2e_ / wp_ / radar_ / lk_ / t_ / TST
以及 test_mock_exam 的 FM/FT/FL/PD/DC/DA/TP 随机前缀模块，如 DA0b07-M2-002）。
保险丝：模式一旦命中任何 MATH- 前缀正式 KP 即拒绝执行。
排除项：app 级兜底码 custom（对话出题无归属）与 function（smart_quiz 兜底）
是业务数据而非测试残留，不匹配、不删除。

以及引用这些 kp 的关联数据：

- mastery_records：按 kp_id 关联删除
- error_records：按 kp_code 匹配删除（硬删，测试残留无需保留软删墓碑）
- mastery_snapshots：按 kp_code 匹配删除
- user_profiles.weak_points：JSONB 数组，只移除含这些 code 的元素
  （兼容两种历史形态：str(kp_code) 或 dict，dict 仅按 code 键判定，
  避免宽模式误伤 name 等中文字段）
- chunks.kp_ids：UUID 数组，仅剔除这些 kp 的 id（不删 chunk 本身）

daily_questions 测试残留按任务要求不动。

用法：
    cd services/api
    python -m scripts.cleanup_test_data          # 干跑：只 SELECT 打印将删计数
    python -m scripts.cleanup_test_data --yes    # 真正 DELETE

数据库连接与 app 一致：app.config.settings 读取仓库根目录 .env 的 DATABASE_URL。
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

# 确保 app 可导入
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402

# 残留 kp_code 模式：非 MATH- 前缀，且非 app 兜底码 custom / function
# （Postgres ARE 支持前瞻约束；(?!...) 内为整体前缀/精确匹配交替）
KP_PATTERN = r"^(?!MATH-|custom$|function$)"
_KP_RE = re.compile(KP_PATTERN)

# -------------------- SQL --------------------

_SQL_COUNT_KP = text("SELECT count(*) FROM knowledge_points WHERE code ~ :pat")
_SQL_COUNT_KP_MATH = text(
    "SELECT count(*) FROM knowledge_points WHERE code ~ :pat AND code LIKE 'MATH-%'"
)
_SQL_COUNT_KP_SENTINEL = text(
    "SELECT count(*) FROM knowledge_points WHERE code ~ :pat AND code IN ('custom', 'function')"
)
_SQL_COUNT_KP_SAMPLE = text(
    "SELECT substring(code from '^[A-Za-z_]+[0-9a-f]*') AS pfx, count(*) "
    "FROM knowledge_points WHERE code ~ :pat GROUP BY 1 ORDER BY 2 DESC LIMIT 15"
)
_SQL_COUNT_MASTERY = text(
    "SELECT count(*) FROM mastery_records mr "
    "JOIN knowledge_points kp ON kp.id = mr.kp_id WHERE kp.code ~ :pat"
)
_SQL_COUNT_ERROR = text("SELECT count(*) FROM error_records WHERE kp_code ~ :pat")
_SQL_COUNT_SNAPSHOT = text("SELECT count(*) FROM mastery_snapshots WHERE kp_code ~ :pat")
_SQL_COUNT_CHUNKS = text(
    "SELECT count(*) FROM chunks WHERE kp_ids && ("
    "  SELECT COALESCE(array_agg(id), '{}') FROM knowledge_points WHERE code ~ :pat)"
)
_SQL_AFFECTED_PROFILES = text(
    "SELECT user_id, weak_points FROM user_profiles WHERE EXISTS ("
    "  SELECT 1 FROM jsonb_array_elements(weak_points) e WHERE ("
    "    CASE WHEN jsonb_typeof(e) = 'string' THEN e #>> '{}'"
    "         ELSE COALESCE(e->>'code', '') END"
    "  ) ~ :pat)"
)

_SQL_DEL_MASTERY = text(
    "DELETE FROM mastery_records mr USING knowledge_points kp "
    "WHERE kp.id = mr.kp_id AND kp.code ~ :pat"
)
_SQL_DEL_ERROR = text("DELETE FROM error_records WHERE kp_code ~ :pat")
_SQL_DEL_SNAPSHOT = text("DELETE FROM mastery_snapshots WHERE kp_code ~ :pat")
_SQL_UPDATE_CHUNKS = text(
    "UPDATE chunks SET kp_ids = ARRAY("
    "  SELECT unnest(kp_ids) EXCEPT"
    "  SELECT id FROM knowledge_points WHERE code ~ :pat) "
    "WHERE kp_ids && ("
    "  SELECT COALESCE(array_agg(id), '{}') FROM knowledge_points WHERE code ~ :pat)"
)
_SQL_DEL_KP = text("DELETE FROM knowledge_points WHERE code ~ :pat")
_SQL_UPDATE_WEAK = text("UPDATE user_profiles SET weak_points = CAST(:wp AS jsonb) WHERE user_id = :uid")


def _element_is_residue(el: object) -> bool:
    """weak_points 元素是否含残留 kp_code（str 命中，或 dict 的 code 键命中）。

    dict 只看 code 键：宽模式下 name 等中文字段也会匹配“非 MATH-”，不能作为判定依据。
    """
    if isinstance(el, str):
        return bool(_KP_RE.match(el))
    if isinstance(el, dict):
        code = el.get("code")
        return isinstance(code, str) and bool(_KP_RE.match(code))
    return False


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="清理非 MATH- 前缀（排除兜底码 custom/function）的测试残留数据"
    )
    parser.add_argument("--yes", action="store_true", help="真正执行 DELETE（缺省仅打印计数）")
    args = parser.parse_args()

    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            params = {"pat": KP_PATTERN}

            # ---------- 1. 干跑计数 ----------
            kp_cnt = (await conn.execute(_SQL_COUNT_KP, params)).scalar_one()
            kp_math = (await conn.execute(_SQL_COUNT_KP_MATH, params)).scalar_one()
            kp_sentinel = (await conn.execute(_SQL_COUNT_KP_SENTINEL, params)).scalar_one()
            mastery_cnt = (await conn.execute(_SQL_COUNT_MASTERY, params)).scalar_one()
            error_cnt = (await conn.execute(_SQL_COUNT_ERROR, params)).scalar_one()
            snapshot_cnt = (await conn.execute(_SQL_COUNT_SNAPSHOT, params)).scalar_one()
            chunk_cnt = (await conn.execute(_SQL_COUNT_CHUNKS, params)).scalar_one()
            profiles = (await conn.execute(_SQL_AFFECTED_PROFILES, params)).mappings().all()
            weak_removed = sum(
                1 for row in profiles for el in (row["weak_points"] or []) if _element_is_residue(el)
            )

            print("== 将删除/清理的数据（模式：非 MATH- 前缀，排除 custom/function）==")
            print(f"  knowledge_points           : {kp_cnt}")
            for row in (await conn.execute(_SQL_COUNT_KP_SAMPLE, params)).mappings():
                print(f"      {row['pfx']:<14} × {row['count']}")
            print(f"  mastery_records (关联)     : {mastery_cnt}")
            print(f"  error_records (kp_code)    : {error_cnt}")
            print(f"  mastery_snapshots (kp_code): {snapshot_cnt}")
            print(f"  chunks (剔除 kp_ids 元素)  : {chunk_cnt}")
            print(f"  user_profiles 涉及         : {len(profiles)} 个档案，移除 weak_points 元素 {weak_removed} 个")
            print("  daily_questions            : 不动（任务要求）")
            if kp_math:
                print(f"!! 中止：命中 {kp_math} 条 MATH- 前缀正式 KP，模式有误，拒绝执行")
                return
            if kp_sentinel:
                print(f"!! 中止：命中 {kp_sentinel} 条 custom/function 兜底码，模式有误，拒绝执行")
                return

            if not args.yes:
                print("\n干跑模式：未做任何修改。确认后加 --yes 真正删除。")
                return

            # ---------- 2. 真正删除（同一事务）----------
            r = await conn.execute(_SQL_DEL_MASTERY, params)
            print(f"已删 mastery_records  : {r.rowcount}")
            r = await conn.execute(_SQL_DEL_ERROR, params)
            print(f"已删 error_records    : {r.rowcount}")
            r = await conn.execute(_SQL_DEL_SNAPSHOT, params)
            print(f"已删 mastery_snapshots: {r.rowcount}")
            r = await conn.execute(_SQL_UPDATE_CHUNKS, params)
            print(f"已更新 chunks         : {r.rowcount} 行（剔除残留 kp_ids 元素）")
            updated = 0
            for row in profiles:
                kept = [el for el in (row["weak_points"] or []) if not _element_is_residue(el)]
                await conn.execute(_SQL_UPDATE_WEAK, {"wp": json.dumps(kept), "uid": row["user_id"]})
                updated += 1
            print(f"已更新 user_profiles  : {updated} 个档案的 weak_points")
            r = await conn.execute(_SQL_DEL_KP, params)
            print(f"已删 knowledge_points : {r.rowcount}")
            print("完成。")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
