"""关键接口性能实测（任务10）：P95 响应时间 + 慢查询索引检查。

- 用数据最多的真实用户打 10 个关键 GET 接口 × 30 次，输出 P50/P95/MAX；
- EXPLAIN ANALYZE 检查热点查询（错题列表 / 掌握度聚合）是否走索引；
- 目标：P95 < 200ms（数据接口）。

用法：cd services/api && .venv\\Scripts\\python.exe -m scripts.perf_probe
（后端运行中；不落任何数据，只读。）
"""

from __future__ import annotations

import asyncio
import sys
import time

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
RUNS = 30

ENDPOINTS = [
    ("health（无鉴权）", "GET", "/api/health", False),
    ("错题列表", "GET", "/api/student/error-records", True),
    ("错题复习计划", "GET", "/api/student/error-records/review-plan", True),
    ("学情亮点", "GET", "/api/student/report/highlights", True),
    ("薄弱点", "GET", "/api/student/report/weak-points", True),
    ("训练组推荐", "GET", "/api/student/practice/group-recommend", True),
    ("掌握度汇总", "GET", "/api/student/mastery/summary", True),
    ("知识图谱", "GET", "/api/student/knowledge-graph", True),
    ("会话列表", "GET", "/api/agent/conversations", True),
    ("管家面板", "GET", "/api/butler/dashboard", True),
]


async def main() -> int:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # 数据最多的用户（真实负载形态）
    async with factory() as db:
        top = (
            await db.execute(
                text(
                    "SELECT u.id FROM users u JOIN mastery_records m ON m.user_id = u.id "
                    "GROUP BY u.id ORDER BY SUM(m.practice_count) DESC LIMIT 1"
                )
            )
        ).fetchone()
        uid = str(top._mapping["id"]) if top else None

    from app.gateway.jwt import create_token_with_role

    headers = {}
    if uid:
        headers["Authorization"] = f"Bearer {create_token_with_role(user_id=uid, role='student', roles=['student'], verified=True)}"

    print(f"探测用户: {uid or '（无数据用户，接口按空态返回）'}，每接口 {RUNS} 次\n")

    results: dict[str, list[float]] = {}
    async with httpx.AsyncClient(timeout=60) as client:
        for name, method, path, need_auth in ENDPOINTS:
            lat: list[float] = []
            errors = 0
            for _ in range(RUNS):
                t0 = time.perf_counter()
                try:
                    resp = await client.request(method, f"{BASE}{path}", headers=headers if need_auth else None)
                    if resp.status_code >= 400:
                        errors += 1
                except Exception:
                    errors += 1
                lat.append((time.perf_counter() - t0) * 1000)
                await asyncio.sleep(0.05)  # 轻间隔，避免连发干扰
            lat.sort()
            p50 = lat[int(len(lat) * 0.5)]
            p95 = lat[min(len(lat) - 1, int(len(lat) * 0.95))]
            results[name] = lat
            flag = "✅" if p95 < 200 else ("⚠️" if p95 < 500 else "❌")
            print(
                f"{flag} {name:<12} P50={p50:7.1f}ms  P95={p95:7.1f}ms  MAX={lat[-1]:7.1f}ms  err={errors}"
            )

    # 慢查询检查：热点 SQL EXPLAIN
    print("\n== 热点查询索引检查 ==")
    async with factory() as db:
        for label, sql in (
            ("错题列表（user_id+deleted_at）",
             "EXPLAIN (COSTS OFF) SELECT id FROM error_records WHERE user_id=:u AND deleted_at IS NULL ORDER BY created_at DESC LIMIT 20"),
            ("掌握度聚合（user_id join kp）",
             "EXPLAIN (COSTS OFF) SELECT kp.code, m.mastery, m.practice_count FROM mastery_records m JOIN knowledge_points kp ON kp.id=m.kp_id WHERE m.user_id=:u"),
            ("错题去重唯一索引",
             "SELECT indexname FROM pg_indexes WHERE tablename='error_records' AND indexname LIKE 'uq_error_records%'"),
        ):
            try:
                rows = (await db.execute(text(sql), {"u": uid})).fetchall()
                print(f"{label}:\n  " + "\n  ".join(str(r._mapping if hasattr(r, '_mapping') else r).replace('(', '').replace(')', '').replace("'", '')[:130] for r in rows[:6]))
            except Exception as e:
                print(f"{label}: ERR {str(e)[:100]}")

    await engine.dispose()
    total = sum(len(v) for v in results.values())
    slow = [(k, v) for k, v in results.items() if v[min(len(v) - 1, int(len(v) * 0.95))] >= 200]
    print(f"\n总计 {total} 次请求；P95 ≥200ms 的接口 {len(slow)} 个：{[k for k, _ in slow] or '无'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
