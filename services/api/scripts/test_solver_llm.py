"""测试 socratic_solver LLM 调用"""
import asyncio

from app.providers.router import get_model_router

SOLVER_PROMPT = (
    "你是数学解题引擎。请完整解出以下题目。\n\n"
    "【输出格式要求】\n"
    "1. 分步边界用 [[STEP]] 标记\n"
    "2. 每一步输出断言和原因\n"
    "3. 最终答案用 \\boxed{} 包裹\n\n"
    "【题目】\nx^2-2x-3=0\n\n"
    "【输出你的完整解题方案】"
)


async def test():
    router = get_model_router()
    print("调用 LLM (非流式)...")
    result = await router.chat(
        messages=[{"role": "user", "content": SOLVER_PROMPT}],
        temperature=0.2,
        max_tokens=2000,
        request_id="test-solver",
        scene="socratic_solver",
    )
    content = result.get("content", "")
    print(f"=== LLM Response ({len(content)} chars) ===")
    print(content[:600])
    print(f"\nHas [[STEP]]: {'[[STEP]]' in content}")
    print(f"Has \\boxed: {'boxed' in content}")

    # 测试 _extract_boxed
    import re
    match = re.search(r"\\boxed\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}", content)
    print(f"Extracted boxed: {match.group(1) if match else 'NONE'}")

    # 测试 _parse_steps
    parts = re.split(r"\[\[STEP\]\]", content)
    steps = [p.strip() for p in parts if p.strip()]
    print(f"Steps count: {len(steps)}")


if __name__ == "__main__":
    asyncio.run(test())
