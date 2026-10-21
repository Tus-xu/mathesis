"""直接探测 wf_doc_understand（data URI 直传）——诊断图片可读性"""

import asyncio
import base64
import json
import re
import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

env = open("D:/math-arena/.env", encoding="utf-8").read()


def get(key: str) -> str | None:
    m = re.search(rf"^{key}=(.*)$", env, re.M)
    return m.group(1).strip() if m else None


api_key = get("XINGCHEN_API_KEY")
api_secret = get("XINGCHEN_API_SECRET")
flows = json.loads(get("XINGCHEN_FLOW_IDS") or "{}")
wf = flows.get("wf_doc_understand", {})
flow_id = wf.get("flow_id")
fk = wf.get("api_key", api_key)
fs = wf.get("api_secret", api_secret)


async def main() -> None:
    for name in ("ascii-q.png", "photo-question.png"):
        data = open(f"D:/math-arena/.tmp/{name}", "rb").read()
        data_uri = "data:image/png;base64," + base64.b64encode(data).decode()
        print(f"\n== {name} ({len(data)}B) ==")
        body = {
            "flow_id": flow_id,
            "uid": "probe-doc2",
            "stream": False,
            "parameters": {
                "AGENT_USER_INPUT": "解析题目并提取LaTeX",
                "image_url": data_uri,
                "task": "extract_question",
                "grade_hint": "G3",
            },
        }
        async with httpx.AsyncClient(timeout=150) as c:
            r = await c.post(
                "https://xingchen-api.xf-yun.com/workflow/v1/chat/completions",
                headers={"Authorization": f"Bearer {fk}:{fs}", "Content-Type": "application/json"},
                json=body,
            )
            print("status:", r.status_code)
            txt = r.text
            m = re.search(r'"content"\s*:\s*"(.*?)"\s*,\s*"reasoning_content"', txt, re.DOTALL)
            if m:
                content = m.group(1).encode().decode("unicode_escape")
                print("content:", content[:300])
            else:
                print("raw:", txt[:300])


asyncio.run(main())
