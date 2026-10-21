"""tel 热修·生产库清理：CMM 演示题退出 student 供给（2026-10-12）。

事故：565 道内容横跨算法初步/组合数学/逻辑的 CMM 演示题被 hybrid 批量标注成
「1.1 集合的概念」（MATH-PEP-BIXI1-01CC322413），教师发布学前作业时把
"输入语句"（旧课标算法初步，与集合毫无关系）发给了学生。

根因：import_cmm_math.py 的导入契约是 scope='pending_qa'（抽样答案正确率
≥97% 门禁通过后才放行 'student'），但生产库中 7874 道 CMM 题全部被放行为
'student'（违约为数据未复现；推测是某次批量放行/标注脚本所至）——
且其 hybrid 标注与题面严重错配（每题平均 2.4 个 kp_codes，混用新旧码制）。

处置（与用户红线一致：有问题的教材/题目不能进入供给；数据不删，物理保留）：
- source_batch='cmm_math_v1' 的行 scope: student → pending_qa（回到导入契约状态）；
- 供题链（supply_questions）已加演示批次黑名单双保险（app/skills/question_supply.py）；
- gaokao 真题批次（gaokao_mm_v1，982 题，过质检）不受影响，仍是供给主力。

幂等：重复执行第二次影响 0 行。只 UPDATE scope，不动其它任何字段。

运行（162 容器内）：python scripts/fix_scope_cmm_demo.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import text  # noqa: E402

from app.models.database import async_session_factory  # noqa: E402

DEMO_BATCHES = ("cmm_math_v1",)


async def main() -> int:
    async with async_session_factory() as db:
        before = (
            await db.execute(
                text(
                    "SELECT count(*) FROM question_bank "
                    "WHERE source_batch = ANY(:b) AND scope = 'student' AND deleted_at IS NULL"
                ),
                {"b": list(DEMO_BATCHES)},
            )
        ).scalar_one()
        print(f"待回退（demo 批次且 scope=student）：{before} 行")
        if before:
            result = await db.execute(
                text(
                    "UPDATE question_bank SET scope = 'pending_qa', updated_at = now() "
                    "WHERE source_batch = ANY(:b) AND scope = 'student' AND deleted_at IS NULL"
                ),
                {"b": list(DEMO_BATCHES)},
            )
            await db.commit()
            print(f"已回退 {result.rowcount} 行 → scope='pending_qa'（数据保留，仅退出学生供给）")
        else:
            print("无待回退行（幂等：已处理过）")
        # 供给主力健康检查
        stats = (
            await db.execute(
                text(
                    "SELECT scope, count(*) FROM question_bank "
                    "WHERE deleted_at IS NULL GROUP BY scope ORDER BY 2 DESC"
                )
            )
        ).all()
        print("清理后题库 scope 分布：", {str(r.scope): int(r[1]) for r in stats})
        remain = (
            await db.execute(
                text(
                    "SELECT count(*) FROM question_bank "
                    "WHERE scope='student' AND source_batch = ANY(:b) AND deleted_at IS NULL"
                ),
                {"b": list(DEMO_BATCHES)},
            )
        ).scalar_one()
        print(f"student scope 中残留 demo 题数（应为 0）：{remain}")
        return 0 if int(remain) == 0 else 1


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
