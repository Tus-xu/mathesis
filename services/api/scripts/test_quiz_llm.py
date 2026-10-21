"""测试 smart_quiz LLM 调用"""
import asyncio
import sys

sys.stdout.reconfigure(encoding='utf-8')

sys.path.insert(0, '.')
from app.providers.router import get_model_router

QUIZ_PROMPT = (
    "你是数学出题专家。根据指定知识点和难度生成 1 道数学题。\n\n"
    "【输入】\n"
    "- 知识点：{kp_name}\n"
    "- 难度：{difficulty}（easy/medium/hard）\n"
    "- 题型：{q_type}（choice/blank/solution）\n\n"
    "【输出严格 JSON 格式】\n"
    "{{\n"
    '  "q_type": "{q_type}",\n'
    '  "question_text": "题目正文（公式用 $...$ LaTeX）",\n'
    '  "options": ["A. ...", "B. ...", "C. ...", "D. ..."],\n'
    '  "answer": "正确答案",\n'
    '  "answer_analysis": "解题过程",\n'
    '  "kp_codes": ["{kp_code}"],\n'
    '  "difficulty": "{difficulty}",\n'
    '  "sympy_check_code": "from sympy import *\\nx = symbols(\'x\')\\n..."\n'
    "}}\n\n"
    "【出题纪律】\n"
    "- 选择题 4 个选项，干扰项合理\n"
    "- 公式只用 $...$ 分隔符\n"
    "- 只输出 JSON，不要其他文字\n"
)


async def test():
    # 测试 format
    prompt = QUIZ_PROMPT.format(kp_name="三角函数", kp_code="trig", difficulty="medium", q_type="choice")
    print(f"Prompt length: {len(prompt)}")
    print(f"Prompt preview: {prompt[:200]}...")

    # 测试 LLM
    router = get_model_router()
    print("\n调用 LLM...")
    result = await router.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
        max_tokens=1500,
        request_id="test-quiz",
        scene="smart_quiz",
    )
    content = result.get("content", "")
    print(f"\n=== Response ({len(content)} chars) ===")
    print(content[:500])

    # 测试 JSON 解析
    import json
    import re
    try:
        data = json.loads(content)
        print(f"\nJSON parsed OK: q_type={data.get('q_type')}")
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
                print(f"\nJSON extracted OK: q_type={data.get('q_type')}")
            except:
                print("\nJSON extraction FAILED")
        else:
            print("\nNo JSON block found")


if __name__ == "__main__":
    asyncio.run(test())
