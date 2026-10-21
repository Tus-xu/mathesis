"""全量响应结构探针：定位输出字段"""

import asyncio
import json
import time

import httpx

URL = "https://xingchen-api.xf-yun.com/workflow/v1/chat/completions"


async def probe(name, flow_id, key, secret, params):
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}:{secret}"}
    body = {
        "flow_id": flow_id,
        "uid": f"dump-{int(time.time())}",
        "stream": False,
        "parameters": params,
    }
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(URL, headers=headers, json=body)
    data = r.json()
    print(f"== {name}")
    print(json.dumps(data, ensure_ascii=False)[:1500])


async def main():
    await probe(
        "intent_router",
        "7489573912004284416",
        "31a1cee598de3c03a5c8153f1c9b9c00",
        "MjcwY2ZhZjgwMWJjZDkwZTBkZjA0YjE0",
        {
            "AGENT_USER_INPUT": "帮我出一道导数压轴题",
            "utterance": "帮我出一道导数压轴题",
            "workspace": "student",
            "history_brief": "",
        },
    )
    await probe(
        "socratic_cha",
        "7490831093411885056",
        "df6c2a7b3f5bd4c3c688c55ae7f8fb9d",
        "MjdlNWIyYjliNDE3MTQ3YTEwZTk5YTdj",
        {"AGENT_USER_INPUT": "什么是集合", "question": "什么是集合", "workspace": "student"},
    )


asyncio.run(main())
