"""直接探测 wf_doc_understand（data URI 直传）——诊断图片可读性"""

import asyncio
import base64
import json
import re
import sys
from pathlib import Path

import httpx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 本文件位于 services/api/scripts/，上溯 3 级（scripts → api → services → 仓库根）。
REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_PATH = REPO_ROOT / ".env"
TMP_DIR = REPO_ROOT / ".tmp"

with open(ENV_PATH, encoding="utf-8") as _env_f:
    env = _env_f.read()


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
        with open(TMP_DIR / name, "rb") as _img_f:
            data = _img_f.read()
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
