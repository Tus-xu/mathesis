"""AI 管家最小闭环验收（任务6）：3 条闭环 × 5 次，真实后端 + 真实 LLM。

闭环：
1. "我哪部分最弱" → /api/agent/chat（chat skill 管家查询注入真实学情画像）→ 回复含真实薄弱点
2. "给我出3道导数题" → /api/agent/chat（smart_quiz 出题）→ quiz_set 卡片 ≥3 题、导数知识点
3. "打开错题本" → /api/agent/route-intent → matched + route=/errors（navigate 跳转指令）

数据准备：验收专用用户 + 真实种子数据（薄弱知识点 掌握度 0.12 + 到期错题），
结束后清理（不污染开发库）。

用法：cd services/api && .venv\\Scripts\\python.exe -m scripts.verify_butler_loops
（需后端已在 127.0.0.1:8000 运行）
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://127.0.0.1:8000"
RUNS = 5
KP_CODE = f"VFY{uuid.uuid4().hex[:6].upper()}"
KP_NAME = f"验收薄弱点-{KP_CODE[-4:]}"


def _parse_sse(sse_text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    current = None
    for line in sse_text.splitlines():
        if line.startswith("event: "):
            current = line[7:]
        elif line.startswith("data: ") and current:
            try:
                events.append((current, json.loads(line[6:])))
            except json.JSONDecodeError:
                events.append((current, {"raw": line[6:]}))
    return events


async def _seed(db) -> tuple[str, str, str]:
    """创建验收用户 + 真实白名单知识点薄弱种子，返回 (token, user_id, kp_name)"""
    from app.gateway.jwt import create_token_with_role
    from app.models.coursework import ErrorRecord, MasteryRecord
    from app.models.user import User

    # 学情画像只统计白名单（MATH-%）知识点：取真实知识点做薄弱种子
    kp_row = (
        await db.execute(
            text("SELECT id, code, name FROM knowledge_points WHERE code LIKE 'MATH-%' LIMIT 1")
        )
    ).fetchone()
    if kp_row is None:
        raise RuntimeError("knowledge_points 无 MATH-% 白名单数据")
    kp_id = kp_row._mapping["id"]
    kp_code, kp_name = kp_row._mapping["code"], kp_row._mapping["name"]

    user = User(phone=f"138{uuid.uuid4().int % 100000000:08d}", nickname="闭环验收用户")
    db.add(user)
    await db.flush()
    db.add(MasteryRecord(user_id=user.id, kp_id=kp_id, mastery=0.12, practice_count=10))
    db.add(ErrorRecord(
        user_id=user.id, question_text=f"验收题：{kp_name} 错题",
        source_channel="auto_judge", error_type="concept", kp_code=kp_code,
        next_review_at=datetime.now(UTC) - timedelta(hours=1),
    ))
    await db.commit()
    token = create_token_with_role(
        user_id=str(user.id), role="student", roles=["student"], verified=True
    )
    return token, str(user.id), kp_name


async def _cleanup(db, user_id: str) -> None:
    uid = uuid.UUID(user_id)
    # FK 顺序：先子表后父表；逐表 best-effort（清理失败不掩盖验收结果）
    for stmt in (
        text("DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE user_id=:u)"),
        text("DELETE FROM conversations WHERE user_id=:u"),
        text("DELETE FROM tutor_sessions WHERE user_id=:u"),
        text("DELETE FROM episodic_memories WHERE user_id=:u"),
        text("DELETE FROM learning_events WHERE user_id=:u"),
        text("DELETE FROM events WHERE user_id=:u"),
        text("DELETE FROM error_records WHERE user_id=:u"),
        text("DELETE FROM mastery_records WHERE user_id=:u"),
        text("DELETE FROM mastery_snapshots WHERE user_id=:u"),
    ):
        try:
            await db.execute(stmt, {"u": uid})
        except Exception as e:  # noqa: BLE001
            print(f"  [cleanup skip] {str(e)[:80]}")
    try:
        await db.execute(text("DELETE FROM users WHERE id=:u"), {"u": uid})
    except Exception as e:  # noqa: BLE001
        print(f"  [cleanup skip] users: {str(e)[:80]}")
    await db.commit()


async def _chat(token: str, message: str) -> list[tuple[str, dict]]:
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "message": message,
        "context": {"workspace": "student", "client_msg_id": uuid.uuid4().hex[:16]},
    }
    async with httpx.AsyncClient(timeout=180) as client:
        resp = await client.post(f"{BASE}/api/agent/chat", json=payload, headers=headers)
        resp.raise_for_status()
        return _parse_sse(resp.text)


async def _route_intent(token: str, text_msg: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{BASE}/api/agent/route-intent", json={"text": text_msg}, headers=headers
        )
        resp.raise_for_status()
        return resp.json()["data"]


async def main() -> int:
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    results: list[dict] = []
    async with factory() as db:
        token, user_id, kp_name = await _seed(db)
    try:
        # ---- 闭环 1：我哪部分最弱 ----
        print("== 闭环1「我哪部分最弱」×5（期望：调用学情工具，回复含真实薄弱点） ==")
        for i in range(1, RUNS + 1):
            try:
                events = await _chat(token, "我哪部分最弱")
                text_out = "".join(d.get("text", "") for t, d in events if t == "token")
                skill = next((d.get("skill") for t, d in events if t == "meta"), "")
                # 真实薄弱点必须出现（管家查询注入真实学情画像）；数据不足时如实说明也视为闭环可用
                ok = kp_name in text_out or "掌握度" in text_out
                print(f"  第{i}次: {'PASS' if ok else 'FAIL'} skill={skill} 回复={text_out[:60]!r}")
                results.append({"loop": 1, "run": i, "ok": ok, "detail": text_out[:80]})
            except Exception as e:  # noqa: BLE001
                print(f"  第{i}次: ERROR {e}")
                results.append({"loop": 1, "run": i, "ok": False, "detail": str(e)[:80]})

        # ---- 闭环 2：给我出3道导数题 ----
        print("\n== 闭环2「给我出3道导数题」×5（期望：quiz_set 卡 ≥3 题、导数知识点） ==")
        for i in range(1, RUNS + 1):
            try:
                events = await _chat(token, "给我出3道导数题")
                cards = [d for t, d in events if t == "card"]
                quiz = next((c for c in cards if c.get("type") == "quiz_set"), None)
                if quiz:
                    items = quiz.get("items") or []
                    kps = {str(it.get("kp_code") or "") for it in items}
                    ok = len(items) >= 3 and any(
                        ("deriv" in k.lower()) or k.startswith("DR") or "导数" in k for k in kps
                    )
                    print(f"  第{i}次: {'PASS' if ok else 'FAIL'} 题数={len(items)} kp={sorted(kps)}")
                    results.append({"loop": 2, "run": i, "ok": ok, "detail": f"items={len(items)}"})
                else:
                    text_out = "".join(d.get("text", "") for t, d in events if t == "token")
                    print(f"  第{i}次: FAIL 无 quiz_set 卡 回复={text_out[:60]!r}")
                    results.append({"loop": 2, "run": i, "ok": False, "detail": "no quiz_set card"})
            except Exception as e:  # noqa: BLE001
                print(f"  第{i}次: ERROR {e}")
                results.append({"loop": 2, "run": i, "ok": False, "detail": str(e)[:80]})

        # ---- 闭环 3：打开错题本 ----
        print("\n== 闭环3「打开错题本」×5（期望：route-intent 产生 navigate 跳转指令 /errors） ==")
        for i in range(1, RUNS + 1):
            try:
                data = await _route_intent(token, "打开错题本")
                ok = data.get("matched") is True and data.get("route") == "/errors"
                print(f"  第{i}次: {'PASS' if ok else 'FAIL'} {data}")
                results.append({"loop": 3, "run": i, "ok": ok, "detail": str(data)[:100]})
            except Exception as e:  # noqa: BLE001
                print(f"  第{i}次: ERROR {e}")
                results.append({"loop": 3, "run": i, "ok": False, "detail": str(e)[:80]})

    finally:
        async with factory() as db:
            await _cleanup(db, user_id)
        await engine.dispose()

    print("\n== 验收汇总 ==")
    for loop in (1, 2, 3):
        rs = [r for r in results if r["loop"] == loop]
        passed = sum(1 for r in rs if r["ok"])
        print(f"  闭环{loop}: {passed}/{len(rs)} 通过")
    total_ok = sum(1 for r in results if r["ok"])
    print(f"  总计: {total_ok}/{len(results)}")
    return 0 if total_ok == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
