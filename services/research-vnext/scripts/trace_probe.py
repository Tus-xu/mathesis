"""RV02 · trace 全链路串联证据采集。

证明同一个 trace_id 贯穿：
Browser/Gateway(traceparent) → Core(响应头 X-Trace-Id) → DB(domain_event)
→ outbox(outbox_event)，并在日志中按结构化字段输出。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_ROOT))

for k in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = "*"

SECRET = "trace-probe"
SCHEMA = os.getenv("PROBE_SCHEMA", "rv8300_trace")
PG = os.getenv(
    "RESEARCH_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:54329/research_vnext"
)
PORT = int(os.getenv("PROBE_PORT", "8315"))

E = dict(os.environ)
E.update(
    {
        "RESEARCH_DATABASE_URL": PG,
        "RESEARCH_DB_SCHEMA": SCHEMA,
        "RESEARCH_ENV": "prod",
        "RESEARCH_GATEWAY_HMAC_SECRET": SECRET,
        "PYTHONPATH": str(SERVICE_ROOT),
        "NO_PROXY": "*",
    }
)


def alembic(*args):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=str(SERVICE_ROOT), env=E,
        capture_output=True, text=True,
    )


def main() -> int:
    alembic("downgrade", "base")
    r = alembic("upgrade", "head")
    print(f"[setup] alembic upgrade head rc={r.returncode}")

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "research_vnext.core.api.app:app",
         "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(SERVICE_ROOT), env=E, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{PORT}"
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/healthz", timeout=2)
            break
        except Exception:
            time.sleep(0.5)

    try:
        tid = hashlib.md5(uuid.uuid4().bytes).hexdigest()
        tp = f"00-{tid}-0000000000000001-01"
        actor, tenant, project = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        ctx = {
            "actor_id": str(actor), "tenant_id": str(tenant), "session_id": str(uuid.uuid4()),
            "workspace": "research", "domain": "research-vnext",
            "capabilities": ["workspace.research.enter", "research.session.write"],
            "resource_scopes": [], "security_version": 1, "policy_version": "p1",
            "trace_id": tid, "authority": "api",
        }
        pl = base64.urlsafe_b64encode(json.dumps(ctx, separators=(",", ":")).encode()).decode().rstrip("=")
        sig = hmac.new(SECRET.encode(), pl.encode(), hashlib.sha256).hexdigest()

        payload = {
            "title": "trace probe", "mode": "MODELING", "project_id": str(project),
            "tenant_id": str(tenant), "policy_refs": ["policy:research.default@v1"],
        }
        body = {
            "client_request_id": "crq-trace-0001", "expected_revision": 0, "actor_id": str(actor),
            "scope": {"kind": "project", "id": str(project), "role": "owner"},
            "traceparent": tp, "deadline": "2030-01-01T00:00:00Z",
            "idempotency_fingerprint": hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "command_type": "session.create", "payload_ref": "inline:t",
            "tenant_id": str(tenant), "payload": payload,
        }
        headers = {
            "X-Research-Access-Context": pl, "X-Research-Access-Sig": sig,
            "Content-Type": "application/json", "traceparent": tp,
        }

        req = urllib.request.Request(
            base + "/api/research-v1/sessions:create",
            data=json.dumps(body).encode(), method="POST", headers=headers,
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            out = json.loads(resp.read().decode())
            hdr_trace = resp.headers.get("X-Trace-Id")
        sid = out["session"]["id"]

        import psycopg

        sync = PG.replace("+psycopg", "").replace("+asyncpg", "")
        with psycopg.connect(sync) as c:
            de = c.execute(
                f'select event_id, event_type, trace_id from "{SCHEMA}".domain_event where session_id=%s',
                (sid,),
            ).fetchall()
            ob = c.execute(
                f'select event_id, status, trace_id, published_at, attempts '
                f'from "{SCHEMA}".outbox_event where session_id=%s', (sid,),
            ).fetchall()

        print("=" * 74)
        print("RV02 · TRACE 全链路串联证据")
        print("=" * 74)
        print(f"[1] Browser/Gateway  traceparent : {tp}")
        print(f"[2] Core 响应头      X-Trace-Id  : {hdr_trace}")
        print(f"[3] session_id                   : {sid}")
        print(f"[4] DB   domain_event.trace_id   : {[r[2] for r in de]}")
        print(f"[5] DB   outbox_event.trace_id   : {[r[2] for r in ob]}")
        print(f"    outbox status / published_at : {[r[1] for r in ob]} / {[r[3] for r in ob]}")
        ok = bool(de) and bool(ob) and all(r[2] == tid for r in de + ob) and hdr_trace == tid
        print(f"[6] 全链路同一 trace_id          : {ok}")
        print("=" * 74)

        # 脱敏自检
        from research_vnext.core.observability.redaction import redact, redact_mapping

        sample = f"Authorization: Bearer eyJhbGciOi.abcdefgh / key ak-{'a'*32} / phone 13800138000"
        print("[7] 脱敏前 :", sample)
        print("    脱敏后 :", redact(sample))
        print("[8] 结构脱敏:", json.dumps(redact_mapping({"token": "Bearer " + "b" * 30}), ensure_ascii=False))
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
