"""RV09 · 聚焦探针：复现「expected_revision 冲突 → 409 + diff」并打印原始响应体。

为什么要单独一个探针：E2E 的冲突步骤在某轮返回 `detail: {}`，
但集成测试同路径却带全 diff。把**原始响应体**打出来才能区分
"服务端没给" 还是 "脚本解析错了"。

用法：
    python scripts/rv09_probe_conflict.py --base-url http://127.0.0.1:8300
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from scripts.rv09_e2e import E2E, RESEARCH_QUESTION  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8300")
    args = parser.parse_args()

    e = E2E(args.base_url)
    sid = e.new_session("probe-conflict")
    print("session", sid)

    snap = e.send(sid, RESEARCH_QUESTION)
    comp = e.last_component(snap)
    print("component:", comp.get("type"))
    items = e.pending(sid)
    print("pending:", len(items), [i.get("object_ref") for i in items])
    if not items:
        print("NO PENDING — 模型未产出提案，探针无法继续")
        return 2
    pid = items[0]["id"]
    print("pendingAction:", pid, "pa.revision:", items[0].get("revision"))

    rev_now = e.snapshot(sid)["revision"]
    print("snapshot.revision:", rev_now)

    stale = rev_now - 1
    r = e.post(f"/sessions/{sid}/pending-actions/{pid}:accept", {"expected_revision": stale})
    print("HTTP", r.status_code)
    print("BODY:", json.dumps(r.json(), ensure_ascii=False, indent=1)[:2500])
    return 0 if r.status_code == 409 else 1


if __name__ == "__main__":
    raise SystemExit(main())
