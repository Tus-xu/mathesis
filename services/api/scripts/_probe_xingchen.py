"""星辰 8 工作流凭证实测（P0-1）

对每个 flow：先试后端现行格式 Bearer key:secret，401 再试 keyonly+X-Appid+X-Secret。
"""

import asyncio
import time

import httpx

URL = "https://xingchen-api.xf-yun.com/workflow/v1/chat/completions"

APP_A_KEY = "31a1cee598de3c03a5c8153f1c9b9c00"
APP_A_SECRET = "MjcwY2ZhZjgwMWJjZDkwZTBkZjA0YjE0"
APP_B_KEY = "df6c2a7b3f5bd4c3c688c55ae7f8fb9d"
APP_B_SECRET = "MjdlNWIyYjliNDE3MTQ3YTEwZTk5YTdj"

FLOWS = [
    ("wf_intent_router", "7489573912004284416", APP_A_KEY, APP_A_SECRET,
     {"AGENT_USER_INPUT": "帮我出一道导数题"}),
    ("wf_speech_to_latex", "7489631049277669376", APP_A_KEY, APP_A_SECRET,
     {"AGENT_USER_INPUT": "x等于负b加减根号下b方减4ac除以2a"}),
    ("wf_smart_quiz", "7490683865815568384", APP_A_KEY, APP_A_SECRET,
     {"AGENT_USER_INPUT": "出一道导数中等难度选择题", "grade_hint": "G2"}),
    ("wf_error_analysis", "7490747043280199680", APP_A_KEY, APP_A_SECRET,
     {"AGENT_USER_INPUT": "分析错因", "question_text": "已知f(x)=x^2，求f(2)。", "student_answer": "f(2)=2", "context_kp": "M-G2-DER-005"}),
    ("wf_course_preprocess", "7490777009261281280", APP_A_KEY, APP_A_SECRET,
     {"kp_hint": '["M-G3-DER-001"]'}),
    ("wf_socratic_cha", "7490831093411885056", APP_B_KEY, APP_B_SECRET,
     {"AGENT_USER_INPUT": "什么是集合"}),
    ("wf_doc_understand_V2", "7490845592819200000", APP_B_KEY, APP_B_SECRET,
     {"AGENT_USER_INPUT": "提取图中题目", "image_url": "https://img.alicdn.com/imgextra/i4/O1CN013S5fVb1Z0G7P7mG2E_!!6000000003132-2-tps-800-600.png", "task": "extract_question", "grade_hint": "G3"}),
    ("wf_solution_pregrade", "7494038049656209409", APP_A_KEY, APP_A_SECRET,
     {"question": "已知f(x)=x^2，求f(2)。", "reference": "f(2)=2^2=4。", "student_answer": "f(2)=4", "max_score": 5}),
]


async def probe(name, flow_id, key, secret, params, mode):
    headers = {"Content-Type": "application/json"}
    if mode == "colon":
        headers["Authorization"] = f"Bearer {key}:{secret}"
    else:
        headers["Authorization"] = f"Bearer {key}"
        headers["X-Appid"] = "094aafcd"
        headers["X-Secret"] = secret
    body = {"flow_id": flow_id, "uid": f"probe-{int(time.time())}", "stream": False, "parameters": params}
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=90) as c:
            r = await c.post(URL, headers=headers, json=body)
        return r.status_code, r.text, time.time() - t0
    except Exception as e:
        return 0, f"{type(e).__name__}: {str(e)[:80]}", time.time() - t0


async def main():
    for name, flow_id, key, secret, params in FLOWS:
        st, text, dt = await probe(name, flow_id, key, secret, params, "colon")
        mode = "colon"
        if st == 401:
            st2, text2, dt2 = await probe(name, flow_id, key, secret, params, "keyonly")
            st, text, dt, mode = st2, text2, dt2, "keyonly"
        ok = (st == 200)
        print(f"[{'OK' if ok else 'FAIL'}] {name} [{mode}] status={st} {dt:.1f}s")
        print("   ", text[:260].replace("\n", " "))

asyncio.run(main())
