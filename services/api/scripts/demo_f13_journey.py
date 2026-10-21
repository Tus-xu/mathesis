"""F13 端到端演示：socratic_solver 全旅程 SSE 事件实录（MockLLM，真实渲染器/真实 DB）。

打印每个事件的类型与摘要，证明 figure 事件随讲解步骤出现、帧数随场景变化、防泄题限帧生效。
用法：cd services/api && .venv\\Scripts\\python.exe -m scripts.demo_f13_journey
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import uuid
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.models.conversation import Conversation
from app.models.user import User
from app.skills.base import SkillContext
from app.skills.socratic_solver.main import SocraticSolverExecutor

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_engine = create_async_engine(settings.database_url, poolclass=NullPool)
_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

QUESTION = "作出函数 $y=x^2-2x-3$ 的图像，并求图像与 $x$ 轴交点的坐标"

FUNCTION_FIG_1 = {
    "type": "function",
    "params": {
        "curves": [{"expr": "x**2-2*x-3", "label": "y=x^2-2x-3"}],
        "x_range": [-3, 5], "y_range": [-5, 6],
        "points": [{"x": 1, "y": -4, "label": "(1,-4)"}],
    },
}
FUNCTION_FIG_2 = {
    "type": "function",
    "params": {
        "curves": [{"expr": "x**2-2*x-3", "label": "y=x^2-2x-3"}],
        "x_range": [-3, 5], "y_range": [-5, 6],
        "points": [{"x": -1, "y": 0, "label": "(-1,0)"}, {"x": 3, "y": 0, "label": "(3,0)"}],
    },
}

PLAN_JSON = json.dumps(
    [
        {"step": 1, "caption": "观察抛物线的开口与顶点", "figure": FUNCTION_FIG_1},
        {"step": 2, "caption": "找到图像与 x 轴的交点", "figure": FUNCTION_FIG_2},
    ],
    ensure_ascii=False,
)

SOLVER_OUT = (
    "[[STEP]]\n断言：$y=x^2-2x-3$ 的图像是开口向上的抛物线，顶点为 $(1,-4)$\n"
    "原因：配方得 $(x-1)^2-4$\n"
    "[[STEP]]\n断言：图像与 $x$ 轴交点横坐标为 $-1$ 与 $3$\n"
    "原因：解方程 $x^2-2x-3=0$ 得 $(x-3)(x+1)=0$\n"
    "[[STEP]]\n断言：交点坐标为 $(-1,0)$ 与 $(3,0)$\n"
    "原因：将横坐标代入 $y=0$\n"
    "最终答案：\\boxed{(-1,0),(3,0)}\n难度：easy"
)

JUDGE_CORRECT = json.dumps(
    {"analysis": "正确", "verdict": "correct", "misconception": None, "feedback_hint": None},
    ensure_ascii=False,
)


class MockLLM:
    intended_provider = "mock"

    def __init__(self, chat_by_scene=None, stream_by_scene=None):
        self.chat_by_scene = {k: list(v) for k, v in (chat_by_scene or {}).items()}
        self.stream_by_scene = {k: list(v) for k, v in (stream_by_scene or {}).items()}

    async def chat(self, messages, **kwargs):
        queue = self.chat_by_scene.get(kwargs.get("scene")) or []
        return {"content": queue.pop(0) if queue else "{}"}

    def chat_stream(self, messages, **kwargs):
        queue = self.stream_by_scene.get(kwargs.get("scene")) or []
        text = queue.pop(0) if queue else ""

        async def _gen():
            if text:
                yield {"token": text}
            yield {"_usage": {}}

        return _gen()


def _dump(events: list[dict], title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")
    for e in events:
        t = e.get("type")
        if t == "token":
            print(f"  [token]  {e['data']['text'][:60]!r}")
        elif t == "figure":
            d = e["data"]
            frames = []
            for fr in d["frames"]:
                svg = base64.b64decode(fr["data_uri"].split(",", 1)[1]).decode()
                has_points = any(k in svg for k in ("(-1,0)", "(3,0)", "(1,-4)"))
                frames.append(f"  └ {fr['label']} ({len(fr['data_uri'])}B, 含答案标注: {has_points})")
            print(f"  [figure] 步骤{d['step_no']}「{d.get('caption','')}」{len(d['frames'])}帧")
            print("\n".join(frames))
        elif t == "card":
            print(f"  [card]   {e['data'].get('card_type')} {e['data'].get('level_name','')}")
        elif t == "status":
            print(f"  [status] {e['data']['text']}")
        elif t == "_result_meta":
            print(f"  [meta]   figures={e['data'].get('figures')} outcome={e['data'].get('outcome')}")
        else:
            print(f"  [{t}]")


@asynccontextmanager
async def _db():
    async with _factory() as session:
        try:
            yield session
        finally:
            await session.rollback()


async def main() -> None:
    llm = MockLLM(
        chat_by_scene={
            "socratic_figure_plan": [PLAN_JSON],
            "socratic_judge": [JUDGE_CORRECT, JUDGE_CORRECT],
        },
        stream_by_scene={
            "socratic_solver": [SOLVER_OUT],
            "socratic_guide": [
                "先看看这条抛物线的开口方向和顶点位置，你觉得它和 x 轴会有几个交点？",
                "答得漂亮！下一步看图找交点，你觉得图像在哪些地方穿过 x 轴？",
            ],
            "socratic_hint": ["看图观察：抛物线最低点在哪里？开口朝哪个方向？"],
            "socratic_summary": ["这道题我们就走完了。以后遇到二次函数求交点，先配方或分解因式，再对照图像验证。"],
        },
    )
    async with _db() as db:
        user = User(phone=f"139{uuid.uuid4().int % 100000000:08d}", nickname="")
        db.add(user)
        await db.flush()  # 先拿 user.id 再建 conv
        conv = Conversation(user_id=user.id, active_role="student", title="F13 演示")
        db.add(conv)
        await db.flush()
        ctx = SkillContext(
            user_id=str(user.id), user_role="student", conversation_id=str(conv.id),
            request_id="demo-f13", db=db, llm=llm,
        )

        async def run(params):
            events = []
            async for ev in SocraticSolverExecutor().run(params, ctx):
                events.append(ev)
            return events

        _dump(await run({"question": QUESTION}), "① 新题：solver → planner → 开场引导（第 1 步构图帧）")
        _dump(await run({"question": "顶点在 (1,-4)，开口向上"}), "② 学生答对第 1 步：完成步全帧 + 下一步构图帧")
        _dump(await run({"tutor_action": "hint", "question": ""}), "③ 主动要提示：当前步构图帧")
        _dump(await run({"tutor_action": "answer", "question": ""}), "④ 直接看答案（一次确认）")
        _dump(await run({"tutor_action": "answer_confirm", "question": ""}), "⑤ 确认揭示：总结 + 全部图形全帧")


if __name__ == "__main__":
    asyncio.run(main())
