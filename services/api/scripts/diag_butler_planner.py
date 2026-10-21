# -*- coding: utf-8 -*-
"""诊断：学生管家 Planner 的原始模型输出（定位 fallback 根因，一次性脚本）"""
import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.butler.contracts import ActorContext, ActorRole, ButlerRequest
from app.butler.model_adapter import build_planning_prompt
from app.butler.student_task_tools import build_student_task_registry
from app.providers.router import get_model_router_for_user


async def main() -> None:
    registry = build_student_task_registry()
    # 用一个真实学生（无则新建上下文仅用于 prompt 组装，不落库）
    from sqlalchemy import select
    from app.models.user import User
    from app.models.database import background_session_factory

    async with background_session_factory() as db:
        row = (await db.execute(select(User).where(User.phone == "13803941776"))).scalar_one_or_none()
        user_id = row.id

    actor = ActorContext(user_id=user_id, role=ActorRole.STUDENT)
    request = ButlerRequest(
        actor=actor,
        message="出5道集合的练习题",
        scene="student.chat",
        client_request_id="diag-" + str(uuid.uuid4()),
    )
    prompt = build_planning_prompt(request, None, registry)
    print("=== PROMPT 末尾 800 字 ===")
    print(prompt[-800:])
    print("\n=== 模型原始输出（两次） ===")
    router = await get_model_router_for_user(str(user_id), db)
    for i in range(2):
        r = await router.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=4096,
            thinking=False,
            request_id=f"diag-planner-{i}",
            scene="butler.planner.diag",
        )
        print(f"--- 第{i+1}次 ---")
        print(r["content"][:600])
        print()


asyncio.run(main())
