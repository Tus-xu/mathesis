"""对抗性探针（WP-A FIX-A1 / FIX-A2 红证据采集）。

目的：在**修复前**复现两个缺陷，留下可复现的红证据。
本脚本**不修改**产品代码，只发请求 / 读响应。

用法：
    .venv\\Scripts\\python.exe scripts\\adversarial_probe.py
退出码：0 = 缺陷已复现（红），1 = 未复现。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.getenv("RV_BASE", "http://127.0.0.1:8300")
SECRET = os.getenv("RESEARCH_GATEWAY_HMAC_SECRET", "")
if not SECRET:
    print("FATAL: RESEARCH_GATEWAY_HMAC_SECRET not set", file=sys.stderr)
    raise SystemExit(2)

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
for _k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
urllib.request.install_opener(
    urllib.request.build_opener(urllib.request.ProxyHandler({}))
)

RED: list[str] = []


def hdr(actor: str, tenant: str) -> dict[str, str]:
    d = {
        "actor_id": actor, "tenant_id": tenant, "session_id": str(uuid.uuid4()),
        "workspace": "research", "domain": "research-vnext",
        "capabilities": ["workspace.research.enter", "research.session.write"],
        "resource_scopes": [], "security_version": 1, "policy_version": "p1",
        "trace_id": "f" * 32, "authority": "api",
    }
    p = base64.urlsafe_b64encode(json.dumps(d, separators=(",", ":")).encode()).decode().rstrip("=")
    return {
        "X-Research-Access-Context": p,
        "X-Research-Access-Sig": hmac.new(SECRET.encode(), p.encode(), hashlib.sha256).hexdigest(),
        "Content-Type": "application/json",
    }


def call(path: str, body=None, headers=None, method=None, raw=False, timeout=25):
    data = json.dumps(body).encode() if body is not None else None
    m = method or ("POST" if body is not None else "GET")
    req = urllib.request.Request(BASE + path, data=data, method=m, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            t = r.read().decode(errors="replace")
            return r.status, (t if raw else json.loads(t)), r.headers
    except urllib.error.HTTPError as e:
        t = e.read().decode(errors="replace")
        try:
            return e.code, (t if raw else json.loads(t)), e.headers
        except Exception:
            return e.code, t, e.headers


def hget(h, k, d=None):
    for kk, vv in dict(h).items():
        if kk.lower() == k.lower():
            return vv
    return d


def envelope_fixed_fp(actor, tenant, project, payload, crid, rev, *, fp=None, pref="inline:fixed", ct="session.create"):
    """关键：fingerprint / payload_ref 由调用方**固定**，与 payload 解耦。"""
    return {
        "client_request_id": crid,
        "expected_revision": rev,
        "actor_id": actor,
        "scope": {"kind": "project", "id": project, "role": "owner"},
        "traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01",
        "deadline": "2030-01-01T00:00:00Z",
        "idempotency_fingerprint": fp or ("0" * 64),
        "command_type": ct,
        "payload_ref": pref,
        "tenant_id": tenant,
        "payload": payload,
    }


def probe_a1() -> None:
    print("=" * 78)
    print("PROBE FIX-A1 · idempotency trusts caller-supplied fingerprint")
    print("=" * 78)
    actor, tenant, project = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    h = hdr(actor, tenant)

    # 一个**固定**的 client fingerprint，与 payload 无关
    FIXED_FP = "f" * 64
    crid = "crq-adv-a1-" + uuid.uuid4().hex[:8]

    P1 = {"title": "adv-A1-original", "mode": "MODELING", "project_id": project,
          "tenant_id": tenant, "policy_refs": ["policy:research.default@v1"]}
    # P2 与 P1 **语义不同**（不同 title），但 fingerprint/payload_ref 保持不变
    P2 = dict(P1)
    P2["title"] = "adv-A1-MUTATED-different-semantics"

    e1 = envelope_fixed_fp(actor, tenant, project, P1, crid, 0, fp=FIXED_FP)
    st1, b1, _ = call("/api/research-v1/sessions:create", e1, h)
    sid = b1.get("session", {}).get("id")
    print(f"[1] create  crid={crid} fp=FIXED payload=P1(title={P1['title']!r})")
    print(f"    -> HTTP {st1}  title={b1.get('session', {}).get('title')!r}  session={sid}")

    e2 = envelope_fixed_fp(actor, tenant, project, P2, crid, 0, fp=FIXED_FP)
    st2, b2, h2 = call("/api/research-v1/sessions:create", e2, h)
    print(f"[2] replay  crid={crid} fp=FIXED payload=P2(title={P2['title']!r})  <-- 语义已变")
    print(f"    -> HTTP {st2}  X-Idempotency-Status={hget(h2,'X-Idempotency-Status')!r}")
    print(f"    -> returned title={b2.get('session', {}).get('title')!r}")

    violated = (
        st2 == 200
        and hget(h2, "X-Idempotency-Status") == "REPLAYED"
        and b2.get("session", {}).get("title") == P1["title"]
    )
    if violated:
        print("    >>> DEFECT REPRODUCED: P2 was silently discarded, caller told REPLAYED (200).")
        RED.append("FIX-A1 create-path")
    else:
        print("    >>> not reproduced on create path")

    # 更严重的形态：状态转换
    actor2, tenant2, project2 = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    h2h = hdr(actor2, tenant2)
    P = {"title": "adv-A1-transition", "mode": "MODELING", "project_id": project2,
         "tenant_id": tenant2, "policy_refs": ["policy:research.default@v1"]}
    c2 = "crq-adv-a1t-" + uuid.uuid4().hex[:8]
    stc, bc, _ = call("/api/research-v1/sessions:create",
                      envelope_fixed_fp(actor2, tenant2, project2, P, c2, 0, fp=FIXED_FP), h2h)
    sid2 = bc["session"]["id"]
    print(f"\n[3] transition session={sid2}")

    tr_fp = "e" * 64
    t1 = envelope_fixed_fp(actor2, tenant2, project2, {"to": "ACTIVE"}, "crq-adv-a1t-1", 0,
                           fp=tr_fp, pref="inline:tr", ct="session.transition")
    t1["scope"] = {"kind": "session", "id": sid2, "role": "owner"}
    s1, r1, hr1 = call(f"/api/research-v1/sessions/{sid2}:transition", t1, h2h)
    print(f"    [3a] to=ACTIVE    -> HTTP {s1} status={r1.get('session',{}).get('status')} rev={r1.get('revision')}")

    # 同一 crid + 同一 fingerprint + 同一 payload_ref，但 payload 改成 CANCELLED
    t2 = envelope_fixed_fp(actor2, tenant2, project2, {"to": "CANCELLED"}, "crq-adv-a1t-1", 0,
                           fp=tr_fp, pref="inline:tr", ct="session.transition")
    t2["scope"] = {"kind": "session", "id": sid2, "role": "owner"}
    s2, r2, hr2 = call(f"/api/research-v1/sessions/{sid2}:transition", t2, h2h)
    print(f"    [3b] to=CANCELLED -> HTTP {s2} X-Idempotency-Status={hget(hr2,'X-Idempotency-Status')!r}")

    st_now, snap, _ = call(f"/api/research-v1/sessions/{sid2}", None, h2h)
    now_status = snap.get("session", {}).get("status")
    print(f"    [3c] server state now: status={now_status} rev={snap.get('revision')}")
    if s2 == 200 and hget(hr2, "X-Idempotency-Status") == "REPLAYED" and now_status != "CANCELLED":
        print("    >>> DEFECT REPRODUCED: CANCELLED intent silently lost while caller saw 200 REPLAYED.")
        RED.append("FIX-A1 transition-path (silent mutation loss)")
    else:
        print("    >>> not reproduced on transition path")


def probe_a2() -> None:
    print("\n" + "=" * 78)
    print("PROBE FIX-A2 · SSE 'id: cursor-<millis>' poisons Last-Event-ID")
    print("=" * 78)
    actor, tenant, project = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    h = hdr(actor, tenant)
    P = {"title": "adv-A2", "mode": "MODELING", "project_id": project,
         "tenant_id": tenant, "policy_refs": ["policy:research.default@v1"]}
    st, b, _ = call("/api/research-v1/sessions:create",
                    envelope_fixed_fp(actor, tenant, project, P, "crq-adv-a2-" + uuid.uuid4().hex[:8], 0,
                                      fp=hashlib.sha256(json.dumps(P, sort_keys=True).encode()).hexdigest()), h)
    sid = b["session"]["id"]
    print(f"session={sid}")

    # 真实 SSE：抓第一帧的 id:
    st, raw, _ = call(f"/api/research-v1/sessions/{sid}/events", None, h, raw=True, timeout=20)
    ids = [l[4:].strip() for l in raw.splitlines() if l.startswith("id: ")]
    print(f"[1] SSE first ids: {ids[:4]}")
    if not ids:
        print("    >>> no ids emitted at all")
        return
    first = ids[0]
    print(f"    first id = {first!r}  (client will echo this as Last-Event-ID)")

    st2, r2, _ = call(f"/api/research-v1/sessions/{sid}:resume", {"last_event_id": first}, h)
    fb = r2.get("fallback")
    print(f"[2] POST :resume last_event_id={first!r} -> HTTP {st2} fallback={fb!r}")
    if fb == "SNAPSHOT_REQUIRED":
        print("    >>> DEFECT REPRODUCED: echoing the stream's OWN first id forces SNAPSHOT_REQUIRED")
        RED.append("FIX-A2 cursor id poisons Last-Event-ID")

    # 用真实事件 id 续传应当正常
    real = [i for i in ids if not i.startswith(("cursor-", "snap-"))]
    if real:
        st3, r3, _ = call(f"/api/research-v1/sessions/{sid}:resume", {"last_event_id": real[0]}, h)
        print(f"[3] POST :resume last_event_id={real[0]!r} (real evt) -> HTTP {st3} fallback={r3.get('fallback')!r}")

    # live SSE with the cursor-shaped Last-Event-ID
    hh = dict(h); hh["Last-Event-ID"] = first
    st4, raw4, _ = call(f"/api/research-v1/sessions/{sid}/events", None, hh, raw=True, timeout=20)
    forced = "snapshot_required" in raw4
    print(f"[4] live SSE with Last-Event-ID={first!r} -> snapshot_required frame: {forced}")
    if forced:
        print("    >>> DEFECT REPRODUCED: every automatic reconnect degrades to full snapshot")
        RED.append("FIX-A2 reconnect degrades to snapshot")


def main() -> int:
    print(f"adversarial probe against {BASE} @ {__import__('time').strftime('%Y-%m-%dT%H:%M:%SZ', __import__('time').gmtime())}")
    probe_a1()
    probe_a2()
    print("\n" + "=" * 78)
    if RED:
        print(f"RED: {len(RED)} defect(s) reproduced (pre-fix expectation)")
        for r in RED:
            print(f"  - {r}")
        return 0
    print("No defects reproduced (post-fix expectation)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
