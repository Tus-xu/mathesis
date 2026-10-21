"""RV16 / GATE80 网关契约离线自检（不依赖 PostgreSQL / 8300 上游）。

## 为什么存在

主理人第 2 轮要求补两项「代码层」证据（可在无 DB 环境下运行）：

- **E1 双前缀**：`/api/research-vnext/*` 与 `/api/research-v1/*` 必须注册同一处理器；
- **E2 伪造头剥除**：客户端自带的 `X-Research-Access-Context` / `X-Research-Access-Sig`
  必须在网关被剥除，只信任网关重签的值。

这两条是**纯函数/路由表**逻辑，可离线断言；端到端 curl（需 8000+8300+PG）另见
`evidence/GATEWAY-PROOF.md`。

## 运行

    cd services/api
    .venv/Scripts/python.exe scripts/verify_rv16_gateway_contract.py

退出码 0 = 全部通过；非 0 = 有失败项（并打印 FAIL 行）。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import uuid
from pathlib import Path

# 服务根目录入 sys.path（包为 app）
SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

# 固定自检密钥（仅进程内，绝不落盘、不打印）。
_SELFCHECK_SECRET = "rv16-offline-selfcheck-secret-not-a-production-value"
os.environ.setdefault("RESEARCH_GATEWAY_HMAC_SECRET", _SELFCHECK_SECRET)

from starlette.datastructures import Headers  # noqa: E402

from app.config import settings  # noqa: E402
from app.domains.identity.types import CurrentIdentity  # noqa: E402
from app.gateway import research_gateway as gw  # noqa: E402

_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(cond), detail))
    flag = "PASS" if cond else "FAIL"
    print(f"[{flag}] {name}" + (f"  | {detail}" if detail else ""))


class _FakeRequest:
    """最小 Request 替身：只需 `.headers`（Starlette Headers 大小写不敏感）。"""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = Headers(headers=headers)


def _unb64u(payload: str) -> dict:
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode()).decode())


def main() -> int:
    print("=" * 72)
    print("RV16 GATE80 网关契约离线自检")
    print("=" * 72)

    # ------------------------------------------------------------------
    # E2 · 伪造头剥除：客户端自带 AccessContext/Sig 一律不可信
    # ------------------------------------------------------------------
    forged_request = _FakeRequest(
        {
            "x-research-access-context": "FORGED-CONTEXT-FROM-CLIENT",
            "x-research-access-sig": "FORGED-SIG-FROM-CLIENT",
            "authorization": "Bearer client-token",
            "accept": "text/event-stream",
            "host": "attacker.example",
            "content-length": "123",
            "connection": "keep-alive",
            "x-request-id": "rid-1",
        }
    )
    gateway_injected = {
        gw.ACCESS_CONTEXT_HEADER: "GATEWAY-CTX",
        gw.ACCESS_SIG_HEADER: "GATEWAY-SIG",
    }
    upstream = gw._build_upstream_headers(forged_request, gateway_injected)
    lower_keys = {k.lower() for k in upstream}

    check(
        "E2.1 伪造 AccessContext 被剥除（仅剩网关值）",
        upstream.get(gw.ACCESS_CONTEXT_HEADER) == "GATEWAY-CTX",
        f"ctx={upstream.get(gw.ACCESS_CONTEXT_HEADER)!r}",
    )
    check(
        "E2.2 伪造 AccessSig 被剥除（仅剩网关值）",
        upstream.get(gw.ACCESS_SIG_HEADER) == "GATEWAY-SIG",
        f"sig={upstream.get(gw.ACCESS_SIG_HEADER)!r}",
    )
    check("E2.3 客户端 host 头被剥除", "host" not in lower_keys)
    check("E2.4 content-length 被剥除（由 httpx 重算）", "content-length" not in lower_keys)
    check("E2.5 hop-by-hop connection 被剥除", "connection" not in lower_keys)
    check(
        "E2.6 白名单头 authorization 透传",
        upstream.get("authorization") == "Bearer client-token",
    )
    check("E2.7 白名单头 accept 透传", upstream.get("accept") == "text/event-stream")

    # ------------------------------------------------------------------
    # E1 · 双前缀：同一处理器注册在两个前缀下
    # ------------------------------------------------------------------
    registered = {r.path for r in gw.router.routes}  # type: ignore[attr-defined]
    check(
        "E1.1 /api/research-vnext/{path} 已注册",
        "/api/research-vnext/{full_path:path}" in registered,
        f"routes={sorted(registered)}",
    )
    check(
        "E1.2 /api/research-v1/{path} 已注册",
        "/api/research-v1/{full_path:path}" in registered,
    )
    check("E1.3 两个前缀路由数量一致（各 1 条）", len(registered) == len(gw._PREFIXES))

    # ------------------------------------------------------------------
    # 签名正确性 + 权威派生（不采信客户端）
    # ------------------------------------------------------------------
    identity = CurrentIdentity(
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        active_role="researcher",
        security_version=7,
    )
    object.__setattr__  # noqa: B018 - 说明：settings 非 frozen，直接赋值即可
    settings.research_gateway_hmac_secret = _SELFCHECK_SECRET

    signed = gw._build_signed_headers(identity, trace_id="t" * 32)
    payload = signed[gw.ACCESS_CONTEXT_HEADER]
    sig = signed[gw.ACCESS_SIG_HEADER]
    expected_sig = hmac.new(
        _SELFCHECK_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()
    check("S1 签名为 HMAC-SHA256(secret, payload)", sig == expected_sig)

    ctx = _unb64u(payload)
    check("S2 actor_id 来自 JWT sub（identity）", ctx["actor_id"] == str(identity.user_id))
    check("S3 security_version 来自 DB 当前值", ctx["security_version"] == 7)
    check("S4 tenant_id 由平台权威派生（非客户端可控）", ctx["tenant_id"] == gw._platform_tenant_id())
    check("S5 workspace/domain 固定为 research", ctx["workspace"] == "research" and ctx["domain"] == "research-vnext")
    check(
        "S6 capabilities 为已批准研究者能力集",
        ctx["capabilities"] == list(gw._RESEARCH_CAPABILITIES),
    )

    # ------------------------------------------------------------------
    # fail-closed：密钥缺失 ⇒ 503 ACCESS_CONTEXT_MISSING（绝不伪造无签名上下文）
    # ------------------------------------------------------------------
    settings.research_gateway_hmac_secret = ""
    try:
        gw._build_signed_headers(identity, trace_id="t" * 32)
        check("F1 密钥缺失时 fail-closed", False, "未抛错（危险）")
    except gw._GatewayError as exc:
        check(
            "F1 密钥缺失时 fail-closed(503/ACCESS_CONTEXT_MISSING)",
            exc.http_status == 503 and exc.code == "ACCESS_CONTEXT_MISSING",
            f"{exc.http_status}/{exc.code}",
        )
    finally:
        settings.research_gateway_hmac_secret = _SELFCHECK_SECRET

    # ------------------------------------------------------------------
    print("-" * 72)
    failed = [name for name, ok, _ in _RESULTS if not ok]
    total = len(_RESULTS)
    print(f"SUMMARY: {total - len(failed)}/{total} passed")
    if failed:
        print("FAILED:")
        for name in failed:
            print(f"  - {name}")
        return 1
    print("RESULT: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
