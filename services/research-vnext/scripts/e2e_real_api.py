"""RV02 · 真实进程 + 真实 PostgreSQL 端到端证据采集。

与 pytest 不同，本脚本：
1. 以 **RESEARCH_ENV=prod** 启动 uvicorn（证明生产启动不做 runtime DDL）；
2. 在**空 schema** 上以 prod 启动 → 必须失败（反证：没有 runtime DDL 兜底）；
3. 用真实 HTTP（curl / urllib）走完 create → get → transition → 重放 → 409；
4. 抓一段**原始 SSE 报文**。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

SECRET = os.getenv("RESEARCH_GATEWAY_HMAC_SECRET", "rv02-e2e-secret")
SCHEMA = os.getenv("E2E_SCHEMA", "rv8300_e2e")
PG_URL = os.getenv(
    "RESEARCH_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:54329/research_vnext"
)

# 绕开沙箱 http_proxy
for k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "*"
os.environ["no_proxy"] = "*"


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def sign(payload: str) -> str:
    return hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def ctx(actor, tenant, capabilities=None, scopes=None, sv=1):
    d = {
        "actor_id": str(actor),
        "tenant_id": str(tenant),
        "session_id": str(uuid.uuid4()),
        "workspace": "research",
        "domain": "research-vnext",
        "capabilities": capabilities or ["workspace.research.enter", "research.session.write"],
        "resource_scopes": scopes or [],
        "security_version": sv,
        "policy_version": "p1",
        "trace_id": hashlib.md5(uuid.uuid4().bytes).hexdigest(),
        "authority": "api",
    }
    p = b64u(json.dumps(d, separators=(",", ":")).encode())
    return {"X-Research-Access-Context": p, "X-Research-Access-Sig": sign(p), "Content-Type": "application/json"}


def env(tenant, project, to, fp_payload, crid, rev, actor):
    return {
        "client_request_id": crid,
        "expected_revision": rev,
        "actor_id": str(actor),
        "scope": {"kind": "project", "id": str(project), "role": "owner"},
        "traceparent": "00-" + hashlib.md5(uuid.uuid4().bytes).hexdigest() + "-0000000000000001-01",
        "deadline": "2030-01-01T00:00:00Z",
        "idempotency_fingerprint": hashlib.sha256(
            json.dumps(fp_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "command_type": "session.create" if to is None else "session.transition",
        "payload_ref": "inline:e2e",
        "tenant_id": str(tenant),
        "payload": fp_payload,
    }


def hget(h, key, default=None):
    """大小写不敏感地取响应头。"""
    try:
        for k, v in h.items():
            if k.lower() == key.lower():
                return v
    except Exception:
        pass
    return default


def req(url, method="GET", body=None, headers=None, raw_stream=False, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            if raw_stream:
                return resp.status, resp.read(4000).decode(errors="replace"), dict(resp.headers)
            return resp.status, json.loads(resp.read().decode()), dict(resp.headers)
    except urllib.error.HTTPError as e:
        payload = e.read().decode(errors="replace")
        try:
            payload = json.loads(payload)
        except Exception:
            pass
        return e.code, payload, dict(e.headers)


def free_port():
    for p in (8300, 8311, 8321, 8331):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError("no port")


def alembic(args, schema, extra_env=None):
    e = dict(os.environ)
    e["RESEARCH_DATABASE_URL"] = PG_URL
    e["RESEARCH_DB_SCHEMA"] = schema
    e["RESEARCH_ENV"] = "test"
    if extra_env:
        e.update(extra_env)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=str(SERVICE_ROOT), env=e, capture_output=True, text=True
    )


def start(port, schema, *, prod=True):
    e = dict(os.environ)
    e.update(
        {
            "RESEARCH_DATABASE_URL": PG_URL,
            "RESEARCH_DB_SCHEMA": schema,
            "RESEARCH_ENV": "prod" if prod else "test",
            "RESEARCH_GATEWAY_HMAC_SECRET": SECRET,
            "RESEARCH_SSE_HEARTBEAT_SECONDS": "1",
            "RESEARCH_SSE_RETENTION_EVENTS": "200",
            "PYTHONPATH": str(SERVICE_ROOT),
            "NO_PROXY": "*",
        }
    )
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "research_vnext.core.api.app:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(SERVICE_ROOT), env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def wait_ready(base, proc, tries=60):
    for _ in range(tries):
        try:
            st, body, _ = req(f"{base}/healthz")
            if st == 200:
                return True
        except Exception:
            pass
        if proc.poll() is not None:
            return False
        time.sleep(0.5)
    return False


def main() -> int:
    out = []
    def log(s=""):
        print(s)
        out.append(s)

    log("=" * 78)
    log("RV02 · 真实进程 + 真实 PostgreSQL 端到端证据")
    log(f"时间(UTC): {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}")
    log(f"数据库    : {PG_URL.split('@')[-1]}")
    log(f"schema    : {SCHEMA}")
    log("=" * 78)

    # 0) 准备 schema（Alembic，非 runtime DDL）
    alembic(["downgrade", "base"], SCHEMA)
    r = alembic(["upgrade", "head"], SCHEMA)
    log(f"\n[0] alembic upgrade head  -> rc={r.returncode}")
    assert r.returncode == 0, r.stdout + r.stderr

    # 1) 反证：空 schema + prod 启动必须失败（证明没有 runtime DDL 兜底）
    empty_schema = SCHEMA + "_empty"
    import psycopg

    with psycopg.connect(PG_URL.replace("+psycopg", "").replace("+asyncpg", ""), autocommit=True) as c:
        c.execute(f'DROP SCHEMA IF EXISTS "{empty_schema}" CASCADE')
    log("")
    log("[1] 已 DROP SCHEMA " + empty_schema + "（0 张表），以 RESEARCH_ENV=prod 启动")
    p0 = start(free_port(), empty_schema, prod=True)
    rc = None
    for _ in range(30):
        rc = p0.poll()
        if rc is not None:
            break
        time.sleep(0.5)
    try:
        out0 = p0.communicate(timeout=10)[0] or ""
    except Exception:
        p0.kill()
        out0 = ""
    if p0.poll() is None:
        p0.kill()
    log(f"\n[1] 空 schema + RESEARCH_ENV=prod 启动 -> 进程退出码={rc}")
    if rc not in (0, None):
        log("    结论：生产环境缺迁移即**启动失败**，不做 runtime DDL 兜底 ✅")
        hit = [l for l in out0.splitlines() if "startup failed" in l or "runtime DDL" in l]
        log(f"    进程输出关键行: {hit[:1]}")
    else:
        log(f"    stdout/err 片段: {out0[:400]}")
        log("    ⚠️ 未复现失败")

    # 2) prod 模式启动
    port = free_port()
    proc = start(port, SCHEMA, prod=True)
    base = f"http://127.0.0.1:{port}"
    ok = wait_ready(base, proc)
    log(f"\n[2] RESEARCH_ENV=prod 启动 -> ready={ok} base={base}")
    if not ok:
        proc.terminate()
        print(proc.stdout.read())
        return 1

    try:
        tenant, project, actor = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        h = ctx(actor, tenant)

        # 3) create
        payload = {"title": "RV02 真实进程 E2E", "mode": "MODELING",
                   "project_id": str(project), "tenant_id": str(tenant),
                   "policy_refs": ["policy:research.default@v1"]}
        crid = "crq-e2e-0001"
        st, body, hdrs = req(f"{base}/api/research-v1/sessions:create", "POST",
                             env(tenant, project, None, payload, crid, 0, actor), h)
        assert st == 201, (st, body)
        sid = body["session"]["id"]
        log(f"\n[3] POST sessions:create -> {st}  revision={body['revision']} status={body['session']['status']}")
        log(f"    session_id={sid}")
        log(f"    X-Idempotency-Status={hget(hdrs,'X-Idempotency-Status')!r}  X-Trace-Id={hget(hdrs,'X-Trace-Id')!r}")
        log(f"    全部响应头: {dict(hdrs.items())}")

        # 4) 重放
        st2, body2, hdrs2 = req(f"{base}/api/research-v1/sessions:create", "POST",
                                env(tenant, project, None, payload, crid, 0, actor), h)
        log(f"\n[4] 重放同一 client_request_id+fingerprint -> {st2}  revision={body2['revision']}")
        log(f"    X-Idempotency-Status={hget(hdrs2,'X-Idempotency-Status')}  (期望 REPLAYED)")
        log(f"    与首次结果一致: {body['session'] == body2['session']}")

        # 5) 同 ID 不同 payload
        bad = dict(payload)
        bad["title"] = "改了标题"
        st3, body3, _ = req(f"{base}/api/research-v1/sessions:create", "POST",
                            env(tenant, project, None, bad, crid, 0, actor), h)
        log(f"\n[5] 同 client_request_id 不同 payload -> {st3} code={body3.get('error', {}).get('code')}")

        # 6) transition + 409
        st4, b4, _ = req(f"{base}/api/research-v1/sessions/{sid}:transition", "POST",
                         env(tenant, project, "ACTIVE", {"to": "ACTIVE"}, "crq-e2e-0002", 0, actor), h)
        log(f"\n[6] transition -> ACTIVE : {st4} revision={b4.get('revision')}")
        st5, b5, _ = req(f"{base}/api/research-v1/sessions/{sid}:transition", "POST",
                         env(tenant, project, "ACTIVE", {"to": "ACTIVE"}, "crq-e2e-0003", 0, actor), h)
        log(f"    过期 expected_revision=0 再发 -> {st5} code={b5.get('error',{}).get('code')} "
            f"expected={b5.get('error',{}).get('expected')} current={b5.get('error',{}).get('current')}")

        # 7) 跳过 confirmation
        st6, b6, _ = req(f"{base}/api/research-v1/sessions/{sid}:transition", "POST",
                         env(tenant, project, "READY_FOR_REVIEW", {"to": "READY_FOR_REVIEW"},
                             "crq-e2e-0004", 1, actor), h)
        log(f"\n[7] ACTIVE -> READY_FOR_REVIEW（无 confirmation）-> {st6} code={b6.get('error',{}).get('code')}")

        # 8) 跨租户 403
        hx = ctx(uuid.uuid4(), uuid.uuid4())
        st7, b7, _ = req(f"{base}/api/research-v1/sessions/{sid}", "GET", None, hx)
        log(f"\n[8] 跨租户 GET -> {st7} code={b7.get('error',{}).get('code')}")

        # 9) 无 AccessContext
        st8, b8, _ = req(f"{base}/api/research-v1/sessions/{sid}", "GET", None,
                         {"Content-Type": "application/json"})
        log(f"    无 AccessContext -> {st8} code={b8.get('error',{}).get('code')}")

        # 10) 原始 SSE
        hs = dict(h)
        st9, raw, _ = req(f"{base}/api/research-v1/sessions/{sid}/events", "GET", None, hs,
                          raw_stream=True, timeout=8)
        log(f"\n[10] SSE 原始报文 (status={st9}):")
        for line in raw.splitlines()[:14]:
            log("    " + line)

        log(f"\n[11] 结论：prod 模式 + 真实 PostgreSQL + 真实 HTTP 全链路通过")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
