"""智学数研 · 线上三端冒烟（真实公网 IP，真实 HTTP）。

覆盖：前端可达 → 登录 → 学生端 / 教师端(8100+8200) / 科研端(8000 网关 → 8300)。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid

HOST = "http://<your-server-ip>"
PHONE, CODE = "13800000000", "123456"
results: list[tuple[str, bool, str]] = []
_tok = ""


def req(method, path, token=None, body=None, raw=None, ctype=None, timeout=120):
    h, data = {}, None
    if raw is not None:
        data, h["Content-Type"] = raw, ctype
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = "Bearer " + token
    r = urllib.request.Request(HOST + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))


def switch(role: str) -> str:
    """切角色。⚠️ 切换后会话角色变化，**必须用返回的新 token**，否则下一个请求 401
    AUTH_SESSION_ROLE_MISMATCH（首版冒烟脚本就踩了这个坑）。"""
    global _tok
    s, t = req("POST", "/api/auth/role/switch", token=_tok, body={"role": role})
    if s != 200:
        print(f"    (switch→{role} HTTP {s} {t[:160]})")
        return ""
    d = json.loads(t).get("data", {})
    _tok = d.get("access_token") or d.get("token") or _tok
    return _tok


print("=" * 74)
print("① 前端可达性（三端同一份 dist）")
print("=" * 74)
s, t = req("GET", "/")
check("GET / 返回 200", s == 200, f"HTTP {s}, {len(t)} bytes")
check("index.html 引用带 hash 的 assets", "/assets/" in t)
for p in ("/research-workspace", "/teacher-v3/prep", "/login"):
    s, t = req("GET", p)
    check(f"SPA 回退 {p}", s == 200, f"HTTP {s}")
s, t = req("GET", "/api/health")
check("GET /api/health（学生端 8000）", s == 200 and '"status":"ok"' in t, f"HTTP {s} {t[:70]}")

print()
print("=" * 74)
print("② 登录")
print("=" * 74)
import time  # noqa: E402

_code_ok = False
for attempt in range(1, 5):
    s, t = req("POST", "/api/auth/sms-code", body={"phone": PHONE})
    if s == 200:
        _code_ok = True
        break
    if "42901" in t or "频率" in t:
        print(f"    (验证码限流，等待 65s 后重试，第 {attempt} 次)")
        time.sleep(65)
        continue
    break
check("POST /api/auth/sms-code", _code_ok, f"HTTP {s}")

s, t = req("POST", "/api/auth/login", body={"phone": PHONE, "code": CODE})
roles: list[str] = []
if s == 200:
    d = json.loads(t).get("data") or {}
    _tok = d.get("token", "")
    roles = [r.get("role") for r in (d.get("user") or {}).get("roles", [])]
check("POST /api/auth/login", bool(_tok), f"roles={roles}" + ("" if _tok else f" resp={t[:160]}"))
if not _tok:
    print("\n登录失败，终止")
    sys.exit(1)

print()
print("=" * 74)
print("③ 学生端")
print("=" * 74)
s, t = req("GET", "/api/auth/me", token=_tok)
check("GET /api/auth/me", s == 200, f"HTTP {s}")

print()
print("=" * 74)
print("④ 科研端（nginx → 8000 网关注入 HMAC → 8300）")
print("=" * 74)
if switch("researcher"):
    s, t = req("GET", "/api/research-vnext/projects", token=_tok)
    check("GET /api/research-vnext/projects", s == 200, f"HTTP {s}")
    pid = ""
    s2, t2 = req("POST", "/api/research-vnext/projects", token=_tok,
                 body={"name": "smoke-" + uuid.uuid4().hex[:6],
                       "client_request_id": "smoke" + uuid.uuid4().hex[:8]})
    if s2 == 201:
        pid = json.loads(t2)["project"]["id"]
    check("POST /projects 建项目", s2 == 201, f"HTTP {s2}")
    if pid:
        s, t = req("GET", f"/api/research-vnext/projects/{pid}/files", token=_tok)
        check("科研端文件树（缺表会 500）", s == 200, f"HTTP {s}")
        s, t = req("POST", f"/api/research-vnext/projects/{pid}/files", token=_tok,
                   body={"path": "smoke/main.tex", "kind": "tex", "content": "\\documentclass{article}\n"})
        check("科研端新建文件（缺表会 500）", s == 201, f"HTTP {s}")
        s, t = req("GET", f"/api/research-vnext/projects/{pid}/papers", token=_tok)
        check("科研端文献列表（缺表会 500）", s == 200, f"HTTP {s}")
        pdf = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
               b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
               b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
               b"trailer<</Root 1 0 R>>\n%%EOF\n")
        bnd = "----smoke" + uuid.uuid4().hex[:8]
        body = (f"--{bnd}\r\n"
                'Content-Disposition: form-data; name="file"; filename="smoke.pdf"\r\n'
                "Content-Type: application/pdf\r\n\r\n").encode() + pdf + f"\r\n--{bnd}--\r\n".encode()
        s, t = req("POST", f"/api/research-vnext/projects/{pid}/papers", token=_tok,
                   raw=body, ctype=f"multipart/form-data; boundary={bnd}")
        check("科研端导入文献（含对象存储）", s == 201, f"HTTP {s} {(t or '')[:110]}")
else:
    check("切换到 researcher", False, "role/switch 失败")

print()
print("=" * 74)
print("⑤ 教师端（nginx → teacher-api:8100 / ppt-studio-v2:8200）")
print("=" * 74)
if switch("teacher"):
    s, t = req("GET", "/api/teacher-v3/prep/sessions", token=_tok)
    check("GET /api/teacher-v3/prep/sessions", s == 200, f"HTTP {s} {(t or '')[:90]}")
else:
    check("切换到 teacher", False, "role/switch 失败")
s, t = req("GET", "/api/ppt-studio-v2/v1/queue-policy")
check("GET /api/ppt-studio-v2/v1/queue-policy（路由确实到 8200）",
      "not_found" not in t and "40400" not in t, f"HTTP {s} {(t or '')[:90]}")

print()
print("=" * 74)
ok_n = sum(1 for _, ok, _ in results if ok)
print(f"结果：{ok_n}/{len(results)} 通过")
for name, ok, detail in results:
    if not ok:
        print(f"  ✗ {name} — {detail}")
print("=" * 74)
