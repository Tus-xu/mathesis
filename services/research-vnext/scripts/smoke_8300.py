"""WP-A · 8300 生产 go-live 冒烟测试（真实 HTTP，非 TestClient）。

对**已在运行的** 8300 实例发起真实 HTTP 调用，覆盖 WP-A 验收条件 3/4：
  1. /healthz + /readyz
  2. create ResearchSession            (201)
  3. get snapshot
  4. resume
  5. 重复 client_request_id -> 原始结果重放 (X-Idempotency-Status: REPLAYED)
  6. 过期 expected_revision -> 机器可读 409 REVISION_CONFLICT
  7. SSE 连接并至少收到一个带 event id 的事件

AccessContext 通过网关同一套 HMAC-SHA256 机制签名（不是绕过鉴权的 mock）。
密钥只从环境变量读取，绝不打印。

代理注意：本机 WinINET 开着系统代理（127.0.0.1:7897），httpx 在 Windows 上会从
注册表读取它，从而把 127.0.0.1 的请求也代理掉并返回 502。本脚本用
`trust_env=False` + 清空 proxy 环境变量绕开；详见 docs/CHANGE-REQUEST.md CR-1。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = os.getenv("RV_BASE", "http://127.0.0.1:8300")
# 两个都必须可达：v1 = P00 既有契约；vnext = 前端 researchApi.ts 实际调用（A-2 修复）
PREFIXES = ("/api/research-v1", "/api/research-vnext")
SECRET = os.getenv("RESEARCH_GATEWAY_HMAC_SECRET", "")
if not SECRET:
    print("FATAL: RESEARCH_GATEWAY_HMAC_SECRET not set in this shell", file=sys.stderr)
    raise SystemExit(2)

# httpx 在 Windows 会读取 WinINET 注册表代理设置；对本机回环地址必须绕开。
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
for _k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)

# urllib（本脚本用的传输层）不读注册表，但显式挡一次更稳。
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(_opener)

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {label}" + (f" :: {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def sign(payload: str) -> str:
    return hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def access_context(actor: uuid.UUID, tenant: uuid.UUID) -> dict:
    d = {
        "actor_id": str(actor),
        "tenant_id": str(tenant),
        "session_id": str(uuid.uuid4()),
        "workspace": "research",
        "domain": "research-vnext",
        "capabilities": ["workspace.research.enter", "research.session.write"],
        "resource_scopes": [],
        "security_version": 1,
        "policy_version": "p1",
        "trace_id": hashlib.md5(uuid.uuid4().bytes).hexdigest(),
        "authority": "api",
    }
    p = b64u(json.dumps(d, separators=(",", ":")).encode())
    return {
        "X-Research-Access-Context": p,
        "X-Research-Access-Sig": sign(p),
        "Content-Type": "application/json",
    }


def hget(h, key, default=None):
    for k, v in dict(h).items():
        if k.lower() == key.lower():
            return v
    return default


def call(method: str, path: str, body=None, headers=None, timeout=20, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode(errors="replace")
            return r.status, (txt if raw else json.loads(txt)), r.headers
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")
        try:
            return e.code, (txt if raw else json.loads(txt)), e.headers
        except Exception:
            return e.code, txt, e.headers


def envelope(tenant, project, actor, payload, crid, rev, cmd="session.create") -> dict:
    return {
        "client_request_id": crid,
        "expected_revision": rev,
        "actor_id": str(actor),
        "scope": {"kind": "project", "id": str(project), "role": "owner"},
        "traceparent": "00-" + hashlib.md5(uuid.uuid4().bytes).hexdigest() + "-0000000000000001-01",
        "deadline": "2030-01-01T00:00:00Z",
        "idempotency_fingerprint": hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "command_type": cmd,
        "payload_ref": "inline:wpa",
        "tenant_id": str(tenant),
        "payload": payload,
    }


def main() -> int:
    print("=" * 78)
    print("WP-A · 8300 go-live smoke test (real HTTP)")
    print(f"time(UTC) : {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    print(f"base      : {BASE}")
    print("=" * 78)

    # 1) health / readiness ---------------------------------------------------
    print("\n[1] health & readiness")
    st, body, _ = call("GET", "/healthz")
    print(f"  GET /healthz -> {st} {json.dumps(body, ensure_ascii=False)}")
    check("healthz 200 + status ok", st == 200 and body.get("status") == "ok")
    check("healthz reports schema rv8300", body.get("schema") == "rv8300", f"schema={body.get('schema')}")
    check("healthz reports port 8300", body.get("port") == 8300)

    st, body, _ = call("GET", "/readyz")
    print(f"  GET /readyz  -> {st} {json.dumps(body, ensure_ascii=False)}")
    check("readyz 200 + status ready", st == 200 and body.get("status") == "ready")

    # 1b) dual prefix availability (A-2): the frontend prefix must exist too -----
    print("\n[1b] dual API prefix availability")
    # 不存在的路由 = 404；合法前缀在**无签名**下 = 401（fail closed，证明路由已命中）
    for pfx in PREFIXES:
        st, b, _ = call("GET", f"{pfx}/sessions/{uuid.uuid4()}")
        print(f"  GET {pfx}/sessions/<random> (no ctx) -> {st}")
        check(f"{pfx} mounted and fail-closed 401", st == 401,
              f"HTTP {st} body={json.dumps(b, ensure_ascii=False)[:120]}")
    st, _, _ = call("GET", f"/api/research-v2/sessions/{uuid.uuid4()}")
    print(f"  GET /api/research-v2/sessions/<random> -> {st}")
    check("bogus prefix /api/research-v2 is NOT mounted (404)", st == 404, f"HTTP {st}")

    # 2) create ---------------------------------------------------------------
    tenant, project, actor = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    h = access_context(actor, tenant)
    payload = {
        "title": "WP-A go-live smoke",
        "mode": "MODELING",
        "project_id": str(project),
        "tenant_id": str(tenant),
        "policy_refs": ["policy:research.default@v1"],
    }
    crid = "crq-wpa-0001-aaaa"
    print("\n[2] POST /api/research-v1/sessions:create")
    st, body, hdrs = call("POST", "/api/research-v1/sessions:create",
                          envelope(tenant, project, actor, payload, crid, 0), h)
    print(f"  -> {st} revision={body.get('revision')} status={body.get('session', {}).get('status')}")
    print(f"  -> X-Idempotency-Status={hget(hdrs, 'X-Idempotency-Status')!r} X-Trace-Id={hget(hdrs, 'X-Trace-Id')!r}")
    check("create 201", st == 201)
    sid = body.get("session", {}).get("id")
    first_event_id = body.get("last_event_id")
    check("create returned session id", bool(sid), f"session_id={sid}")
    check("create returned last_event_id", bool(first_event_id), f"last_event_id={first_event_id}")
    check("create idempotency EXECUTED", hget(hdrs, "X-Idempotency-Status") == "EXECUTED")
    check("create trace id present", bool(hget(hdrs, "X-Trace-Id")))
    if not sid:
        print("\nFATAL: no session id; aborting")
        return 1

    # 3) snapshot -------------------------------------------------------------
    print(f"\n[3] GET /api/research-v1/sessions/{sid}")
    st, snap, _ = call("GET", f"/api/research-v1/sessions/{sid}", None, h)
    print(f"  -> {st} revision={snap.get('revision')} title={snap.get('session', {}).get('title')!r}")
    print(f"  -> summary_ref={snap.get('summary_ref')!r} trace_hint={snap.get('trace_hint')!r}")
    check("snapshot 200", st == 200)
    check("snapshot revision == 0", snap.get("revision") == 0)
    check("snapshot echoes title", snap.get("session", {}).get("title") == payload["title"])

    # 4) resume ---------------------------------------------------------------
    print(f"\n[4] POST /api/research-v1/sessions/{sid}:resume")
    st, res, _ = call("POST", f"/api/research-v1/sessions/{sid}:resume",
                      {"last_event_id": first_event_id}, h)
    print(f"  -> {st} fallback={res.get('fallback')} events={len(res.get('events', []))} revision={res.get('revision')}")
    check("resume 200", st == 200)
    check("resume fallback NONE (in retention window)", res.get("fallback") == "NONE",
          f"fallback={res.get('fallback')}")
    check("resume returns session snapshot", bool(res.get("session")))

    # 5) idempotent replay ----------------------------------------------------
    print("\n[5] replay same client_request_id + same fingerprint (idempotency)")
    st2, body2, hdrs2 = call("POST", "/api/research-v1/sessions:create",
                             envelope(tenant, project, actor, payload, crid, 0), h)
    print(f"  -> {st2} X-Idempotency-Status={hget(hdrs2, 'X-Idempotency-Status')!r}")
    check("replay 201 (same as first)", st2 == 201)
    check("replay marked REPLAYED", hget(hdrs2, "X-Idempotency-Status") == "REPLAYED",
          f"got {hget(hdrs2, 'X-Idempotency-Status')!r}")
    check("replay returns ORIGINAL session (identical body)",
          body.get("session") == body2.get("session") and body.get("last_event_id") == body2.get("last_event_id"),
          f"same_session_id={body.get('session', {}).get('id') == body2.get('session', {}).get('id')}")

    # 5b) same crid, different payload -> 409 IDEMPOTENCY_CONFLICT ------------
    bad = dict(payload)
    bad["title"] = "mutated title"
    st3, body3, _ = call("POST", "/api/research-v1/sessions:create",
                         envelope(tenant, project, actor, bad, crid, 0), h)
    code3 = (body3.get("error") or {}).get("code") if isinstance(body3, dict) else None
    print(f"\n[5b] same client_request_id + DIFFERENT payload -> {st3} code={code3}")
    check("mutated payload 409 IDEMPOTENCY_CONFLICT",
          st3 == 409 and code3 == "IDEMPOTENCY_CONFLICT", f"{st3}/{code3}")

    # 6) transition, then stale revision -------------------------------------
    print(f"\n[6] transition DRAFT -> ACTIVE (expected_revision=0)")
    st4, b4, h4 = call("POST", f"/api/research-v1/sessions/{sid}:transition",
                       envelope(tenant, project, actor, {"to": "ACTIVE"}, "crq-wpa-0002-bbbb", 0,
                                cmd="session.transition"), h)
    print(f"  -> {st4} revision={b4.get('revision')} status={b4.get('session', {}).get('status')}")
    check("transition 200 -> revision 1", st4 == 200 and b4.get("revision") == 1)

    print(f"\n[6b] STALE expected_revision=0 replay of transition")
    st5, b5, _ = call("POST", f"/api/research-v1/sessions/{sid}:transition",
                      envelope(tenant, project, actor, {"to": "ACTIVE"}, "crq-wpa-0003-cccc", 0,
                               cmd="session.transition"), h)
    err = b5.get("error") if isinstance(b5, dict) else None
    print(f"  -> HTTP {st5}")
    print(f"  -> raw body: {json.dumps(b5, ensure_ascii=False)}")
    check("stale revision -> HTTP 409", st5 == 409)
    check("409 is machine-readable REVISION_CONFLICT", (err or {}).get("code") == "REVISION_CONFLICT",
          f"code={(err or {}).get('code')}")
    check("409 carries expected + current", (err or {}).get("expected") == 0 and (err or {}).get("current") == 1,
          f"expected={(err or {}).get('expected')} current={(err or {}).get('current')}")

    # 7) SSE ------------------------------------------------------------------
    print(f"\n[7] GET /api/research-v1/sessions/{sid}/events  (SSE, raw)")
    st6, raw, hh = call("GET", f"/api/research-v1/sessions/{sid}/events", None, h, timeout=20, raw=True)
    print(f"  -> HTTP {st6} content-type={hget(hh, 'Content-Type')}")
    for line in raw.splitlines()[:20]:
        print(f"     | {line}")
    event_ids = [l[4:].strip() for l in raw.splitlines() if l.startswith("id: ")]
    has_id = any(e and not e.startswith("cursor-") for e in event_ids)
    check("SSE 200", st6 == 200)
    check("SSE content-type is text/event-stream",
          "text/event-stream" in str(hget(hh, "Content-Type")), f"{hget(hh, 'Content-Type')}")
    check("SSE emitted at least one domain event with event id", has_id,
          f"ids={event_ids[:5]}")
    check("SSE sent X-Trace-Id header", bool(hget(hh, "X-Trace-Id")))

    # 8) durability: is the row actually in PostgreSQL (not in-memory)? -------
    print("\n[8] post-run DB state (queried separately via psql)")
    print(f"  session_id for psql verification: {sid}")

    print("\n" + "=" * 78)
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} CHECK(S) FAILED")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("RESULT: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
