"""星辰 8 流原始输出探针（参数按 YAML 契约）"""

import asyncio
import json
import time

import httpx

URL = "https://xingchen-api.xf-yun.com/workflow/v1/chat/completions"
A = ("31a1cee598de3c03a5c8153f1c9b9c00", "MjcwY2ZhZjgwMWJjZDkwZTBkZjA0YjE0")
B = ("df6c2a7b3f5bd4c3c688c55ae7f8fb9d", "MjdlNWIyYjliNDE3MTQ3YTEwZTk5YTdj")

FLOWS = [
    (
        "wf_intent_router",
        "7489573912004284416",
        A,
        {
            "AGENT_USER_INPUT": "帮我出一道导数压轴题",
            "utterance": "帮我出一道导数压轴题",
            "workspace": "student",
            "history_brief": "",
        },
    ),
    (
        "wf_speech_to_latex",
        "7489631049277669376",
        A,
        {
            "AGENT_USER_INPUT": "x等于负b加减根号下b方减4ac除以2a",
            "asr_text": "x等于负b加减根号下b方减4ac除以2a",
            "context_kp": "函数",
        },
    ),
    (
        "wf_smart_quiz",
        "7490683865815568384",
        A,
        {
            "AGENT_USER_INPUT": "出一道导数中等难度选择题",
            "kp_name": "导数",
            "kp_code": "M-G2-DER-005",
            "difficulty": "medium",
            "q_type": "choice",
            "grade_hint": "G2",
            "count": 1,
        },
    ),
    (
        "wf_error_analysis",
        "7490747043280199680",
        A,
        {
            "AGENT_USER_INPUT": "分析错因",
            "question_text": "已知f(x)=x^2，求f(2)。",
            "answer_text": "4",
            "student_answer": "f(2)=2",
            "context_kp": "M-G2-DER-005",
        },
    ),
    (
        "wf_course_preprocess",
        "7490777009261281280",
        A,
        {
            "AGENT_USER_INPUT": "请为这段课程内容生成知识卡片",
            "transcript": "本节课学习导数定义与几何意义。",
            "course_title": "导数的概念",
            "kp_hint": ["M-G3-DER-001"],
        },
    ),
    (
        "wf_socratic_cha",
        "7490831093411885056",
        B,
        {"AGENT_USER_INPUT": "什么是集合", "question": "什么是集合", "workspace": "student"},
    ),
    (
        "wf_doc_understand_V2",
        "7490845592819200000",
        B,
        {
            "AGENT_USER_INPUT": "提取图中题目",
            "image_url": "https://img.alicdn.com/imgextra/i4/O1CN013S5fVb1Z0G7P7mG2E_!!6000000003132-2-tps-800-600.png",
            "task": "extract_question",
            "grade_hint": "G3",
        },
    ),
    (
        "wf_solution_pregrade",
        "7494038049656209409",
        A,
        {
            "AGENT_USER_INPUT": "请批改这道解答题",
            "question": "已知函数f(x)=x^2，求f(2)。",
            "reference": "f(2)=2^2=4。",
            "student_answer": "f(2)=4",
            "max_score": "5",
        },
    ),
]


async def probe(name, flow_id, creds, params):
    key, secret = creds
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}:{secret}"}
    body = {
        "flow_id": flow_id,
        "uid": f"raw-{int(time.time())}",
        "stream": False,
        "parameters": params,
    }
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(URL, headers=headers, json=body)
        dt = time.time() - t0
        data = r.json()
        code = data.get("code", "?")
        content = ""
        choices = data.get("choices", [])
        if choices:
            content = choices[0].get("message", {}).get("content", "")
        print(f"== {name} status={r.status_code} code={code} {dt:.1f}s")
        print("   raw:", json.dumps(content, ensure_ascii=False)[:420])
        return True
    except Exception as e:
        print(f"== {name} ERR {type(e).__name__}: {str(e)[:120]}")
        return False


async def main():
    for name, fid, creds, params in FLOWS:
        await probe(name, fid, creds, params)


asyncio.run(main())
