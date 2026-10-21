"""Butler daily_plan 性能护栏（阶段 1 Task 2 性能 P0）

根因：daily_plan 曾对 3 张卡片逐条 LLM 润色（3 卡 × title/why/benefit = 9 次）
+ 开场白 1 次 = 单次请求最多 10 次串行模型调用 → 页面 33 秒问题。

护栏：规则数据一次构建，整页最多一次 LLM 润色，禁止卡片级串行调用；
模型失败立即返回规则结果（generate 内部兜底，不抛）。
"""

import uuid
from unittest.mock import AsyncMock

from app.butler import skills


def _fake_profile() -> dict:
    return {
        "streak_days": 3,
        "error_due": 1,
        "weak_points": [{"kp_name": "函数", "mastery": 0.42}],
    }


async def test_daily_plan_calls_llm_at_most_once(monkeypatch):
    """整页 daily_plan 最多 1 次 LLM 调用（原实现最多 10 次）"""
    calls = {"n": 0}

    async def fake_generate(**kwargs):
        calls["n"] += 1
        return kwargs.get("fallback", "")

    monkeypatch.setattr("app.butler.skills.butler_llm.generate", fake_generate)
    monkeypatch.setattr(
        "app.butler.skills.query_due_errors",
        AsyncMock(
            return_value=[
                {
                    "id": "e1",
                    "question_text": "q1",
                    "error_type": "concept",
                    "created_at": "2026-08-18T00:00:00+08:00",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "app.butler.skills.query_weak_points",
        AsyncMock(return_value=[{"kp_code": "MATH-X", "kp_name": "函数", "mastery": 0.42}]),
    )
    monkeypatch.setattr(
        "app.butler.skills.query_profile", AsyncMock(return_value=_fake_profile())
    )

    db = AsyncMock()
    plan = await skills.daily_plan(db, uuid.uuid4())

    assert calls["n"] <= 1, f"daily_plan 应最多 1 次 LLM 调用，实际 {calls['n']} 次"
    # 规则数据必须完整：3 张卡 + 开场白
    assert len(plan["tasks"]) == 3
    for t in plan["tasks"]:
        assert t["title"] and t["why"] and t["benefit"]
        assert t["route"]  # 前端跳转依赖
    assert plan["greeting"]


async def test_daily_plan_llm_failure_returns_rules(monkeypatch):
    """模型失败 → 立即返回规则结果（不抛、不阻塞页面）"""
    async def broken_generate(**kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr("app.butler.skills.butler_llm.generate", broken_generate)
    monkeypatch.setattr(
        "app.butler.skills.query_due_errors", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "app.butler.skills.query_weak_points", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "app.butler.skills.query_profile",
        AsyncMock(
            return_value={
                "streak_days": 0,
                "error_due": 0,
                "weak_points": [],
            }
        ),
    )

    db = AsyncMock()
    plan = await skills.daily_plan(db, uuid.uuid4())

    assert len(plan["tasks"]) == 3
    assert all(t["title"] for t in plan["tasks"])
    assert plan["greeting"]  # 规则兜底开场白
