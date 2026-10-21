import asyncio, time, httpx
URL = "https://xingchen-api.xf-yun.com/workflow/v1/chat/completions"
A = ("31a1cee598de3c03a5c8153f1c9b9c00", "MjcwY2ZhZjgwMWJjZDkwZTBkZjA0YjE0")
async def p(name, flow, key, secret, params):
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {key}:{secret}"}
    b = {"flow_id": flow, "uid": f"cross-{int(time.time())}", "stream": False, "parameters": params}
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(URL, headers=h, json=b)
    print(f"{name} status={r.status_code} :: {r.text[:200].replace(chr(10)," ")}")
async def main():
    # A key on B-app flows
    await p("socratic_cha(A-key)", "7490831093411885056", A[0], A[1], {"question": "什么是集合"})
    await p("doc_understand(A-key)", "7490845592819200000", A[0], A[1], {"AGENT_USER_INPUT": "提取图中题目", "image_url": "https://img.alicdn.com/imgextra/i4/O1CN013S5fVb1Z0G7P7mG2E_!!6000000003132-2-tps-800-600.png", "task": "extract_question", "grade_hint": "G3"})
asyncio.run(main())