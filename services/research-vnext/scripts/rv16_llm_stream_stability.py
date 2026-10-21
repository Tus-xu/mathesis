"""RV16 · ≥60 次**真实 LLM 流式**稳定性实测（直连 8300，网关签名 AccessContext）。

## 为什么需要它

第 1 轮的 60 次稳定性样本里，**只有 10 次是真实 LLM 流式**（`GET /sessions/{id}/stream`
产出 `event: token`），另外 50 次是 welcome/projects/literature 等 CRUD —— 后者几乎不可能
失败，样本在统计上站不住。本脚本把「真实 LLM 流式」这一类的样本量提到 **N≥60**，
并给出：成功率、失败率、首 token 延迟分布、以及**失败率的 95% 单侧置信上界**
（Clopper-Pearson 精确法）。

> 结论口径：只有在 **N≥59 次全成功**（0 失败）时，失败率的 95% 单侧上界才 ≤5%。
> 因此本脚本默认 `--n 60`。

## 依赖（环境恢复后）

- 8300 已启动，且 `.env`（`services/research-vnext/.env` 或仓库根 `.env`）含
  `RESEARCH_GATEWAY_HMAC_SECRET`；
- 一个**能触发真实 LLM 轮次**的会话 id：写入 `.rv16scratch/sid.txt`（或 `--sid`）。
  （与第 1 轮同一机制：该会话无待处理 user message 时，每次 stream 触发一次真实 LLM 轮次。）

## 运行

    cd services/research-vnext
    python scripts/rv16_llm_stream_stability.py --n 60
    # 可选：--base http://127.0.0.1:8300/api/research-vnext
    #       --actor <uuid> --tenant <uuid> --timeout 120

输出：stdout 摘要 + `.rv16scratch/llm_stability.out`（逐次明细）+
      `RV16/evidence/STABILITY-RUN-LLM.out`（摘要，供证据引用）。

> 本脚本仅用标准库（urllib），无需额外依赖；**不含**任何密钥，密钥只从 .env 读取。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import base64
import hashlib
import hmac
from pathlib import Path
from statistics import mean, median

SERVICE_ROOT = Path(__file__).resolve().parents[1]  # services/research-vnext
REPO_ROOT = SERVICE_ROOT.parents[1]  # 仓库根（zxsy-math-arena）
SCRATCH = SERVICE_ROOT / ".rv16scratch"
EVIDENCE = REPO_ROOT / "RV16" / "evidence"

DEFAULT_ACTOR = "d1f1756e-6e5a-4a3f-9b0e-52f0a1b2c3d4"
DEFAULT_TENANT = "a1a2a3a4-b1b2-4c1c-8d1d-000000000001"


def _read_env_key(*names: str) -> str:
    """从服务 .env / 仓库根 .env 读取首个命中的键（值不落日志）。"""
    for env_file in (SERVICE_ROOT / ".env", REPO_ROOT / ".env"):
        if not env_file.exists():
            continue
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key.strip() in names:
                return value.strip().strip('"').strip("'")
    return ""


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign_ctx(secret: str, actor: str, tenant: str) -> tuple[str, str]:
    ctx = {
        "actor_id": actor,
        "tenant_id": tenant,
        "workspace": "research",
        "domain": "research-vnext",
        "capabilities": ["workspace.research.enter", "research.session.write"],
        "resource_scopes": [],
        "security_version": 1,
        "policy_version": "p1",
        "trace_id": "0" * 32,
        "authority": "api",
    }
    payload = _b64url(json.dumps(ctx, separators=(",", ":"), sort_keys=True).encode())
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload, sig


def _load_ctx(secret: str, actor: str, tenant: str) -> tuple[str, str]:
    ctx_file = SCRATCH / "ctx.txt"
    if ctx_file.exists():
        lines = [ln.strip() for ln in ctx_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if len(lines) >= 2:
            return lines[0], lines[1]
    return _sign_ctx(secret, actor, tenant)


def _stream_once(base: str, sid: str, ctx: str, sig: str, timeout: float) -> tuple[bool, float | None, int]:
    """单次流式调用：返回 (是否见到 token, 首 token 秒, 字节数)。"""
    url = f"{base}/sessions/{sid}/stream"
    req = urllib.request.Request(
        url,
        headers={
            "X-Research-Access-Context": ctx,
            "X-Research-Access-Sig": sig,
            "Accept": "text/event-stream",
        },
    )
    start = time.perf_counter()
    first_token: float | None = None
    nbytes = 0
    try:
        # 本地回环：禁用代理
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as resp:
            for raw in resp:
                nbytes += len(raw)
                if first_token is None and b"event: token" in raw:
                    first_token = time.perf_counter() - start
                    # 拿到首 token 即算成功（后续继续读到流结束以计入总量）
        return first_token is not None, first_token, nbytes
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
        return False, first_token, nbytes


def _cp_upper(k: int, n: int, alpha: float = 0.05) -> float:
    """失败数 k / 样本 n 的 95% 单侧（Clopper-Pearson 精确）上界。"""
    if n == 0:
        return 1.0
    if k >= n:
        return 1.0

    def cdf(p: float) -> float:
        # P(X <= k | n, p)
        total = 0.0
        for i in range(0, k + 1):
            total += math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
        return total

    lo, hi = k / n, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if cdf(mid) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8300/api/research-vnext")
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--actor", default=os.getenv("RV16_ACTOR", DEFAULT_ACTOR))
    ap.add_argument("--tenant", default=os.getenv("RV16_TENANT", DEFAULT_TENANT))
    ap.add_argument("--sid", default="")
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    secret = _read_env_key("RESEARCH_GATEWAY_HMAC_SECRET")
    if not secret:
        print("FATAL: 未找到 RESEARCH_GATEWAY_HMAC_SECRET（服务 .env / 仓库根 .env）")
        return 2

    sid = args.sid or os.getenv("RV16_SID", "")
    if not sid:
        sid_file = SCRATCH / "sid.txt"
        if sid_file.exists():
            sid = sid_file.read_text(encoding="utf-8").strip()
    if not sid:
        print("FATAL: 未指定会话 id（--sid / RV16_SID / .rv16scratch/sid.txt）")
        return 2

    ctx, sig = _load_ctx(secret, args.actor, args.tenant)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    detail_path = SCRATCH / "llm_stability.out"

    print("=" * 72)
    print(f"RV16 真实 LLM 流式稳定性实测 · n={args.n} sid={sid} base={args.base}")
    print("=" * 72)

    ok = 0
    first_tokens: list[float] = []
    lines: list[str] = []
    t0 = time.perf_counter()
    for i in range(1, args.n + 1):
        success, ft, nbytes = _stream_once(args.base, sid, ctx, sig, args.timeout)
        if success:
            ok += 1
            if ft is not None:
                first_tokens.append(ft)
        line = f"call {i:03d} {'OK ' if success else 'FAIL'} first_token={ft if ft is None else round(ft, 3)}s bytes={nbytes}"
        print(line)
        lines.append(line)

    fail = args.n - ok
    fail_rate = fail / args.n
    upper = _cp_upper(fail, args.n)
    total_s = time.perf_counter() - t0

    summary = [
        "=== SUMMARY ===",
        f"n={args.n} success={ok} fail={fail} fail_rate={fail_rate:.3%}",
        f"failure_rate_95pct_upper_bound={upper:.3%}",
        f"claim_fail_rate<=5%: {'YES' if upper <= 0.05 else 'NO'}",
        f"first_token_s: min={min(first_tokens):.3f} median={median(first_tokens):.3f} mean={mean(first_tokens):.3f} max={max(first_tokens):.3f}"
        if first_tokens
        else "first_token_s: (none)",
        f"wall_time_s={total_s:.1f}",
    ]
    for ln in summary:
        print(ln)

    detail_path.write_text("\n".join(lines) + "\n" + "\n".join(summary) + "\n", encoding="utf-8")
    (EVIDENCE / "STABILITY-RUN-LLM.out").write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(f"\n[OK] 明细 -> {detail_path}\n[OK] 摘要 -> {EVIDENCE / 'STABILITY-RUN-LLM.out'}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
