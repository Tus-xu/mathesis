"""原始 SSE 帧结构探针：wf_socratic_cha 流式"""

import asyncio

import httpx

URL = "https://xingchen-api.xf-yun.com/workflow/v1/chat/completions"


async def main():
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer df6c2a7b3f5bd4c3c688c55ae7f8fb9d:MjdlNWIyYjliNDE3MTQ3YTEwZTk5YTdj",
    }
    body = {
        "flow_id": "7490831093411885056",
        "uid": "ssedump-1",
        "stream": True,
        "parameters": {
            "AGENT_USER_INPUT": "什么是集合？",
            "question": "什么是集合？",
            "workspace": "student",
        },
    }
    count = 0
    async with (
        httpx.AsyncClient(timeout=90) as c,
        c.stream("POST", URL, headers=headers, json=body) as r,
    ):
        print("HTTP", r.status_code)
        async for line in r.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            count += 1
            print(f"[frame {count}]", payload[:300])
            if count >= 8:
                break


asyncio.run(main())
