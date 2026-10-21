"""RV16 · 创建**真实** researcher 测试账号（走身份表 + 真实登录，禁止手改 JWT）。

## 背景

fe-engineer 需要一枚 `active_role == "researcher"` 的真实账号来联调科研工作台。
本脚本通过**公开 HTTP 接口**创建账号（`users` / `role_bindings` / `auth_sessions`
真实落库），再用**真实登录**拿到会话令牌，最后用 `/api/auth/me` 自证角色。

## 关键事实（读代码得出，非臆测）

- `POST /api/auth/register/teacher` 已**停用**（410 `AUTH_LEGACY_TEACHER_REGISTRATION_RETIRED`），
  不能再走这条老路。
- 当前 `.env` 未设置 `AUTH_PROFESSIONAL_REVIEW_ENABLED` ⇒ 取默认 `False`（**演示模式**）。
  `IdentityService.login_sms(..., review_enabled=False)` 在专业身份下会**自动批准**
  绑定（`status="approved"` + 审计 `role_review.bypassed`）。因此：
  - **演示模式**：`POST /api/auth/login/sms` 带 `preferred_role="researcher"` 一步到位；
  - **生产模式**（review 开启）：需 `POST /api/auth/profile/role-applications` 提交申请，
    再由管理员 `POST /api/admin/identity/applications/{id}/approve` 审核（本脚本会
    自动探测模式并走对应分支）。

## 运行

    cd services/api
    .venv/Scripts/python.exe scripts/rv16_create_researcher_account.py

    # 可选环境变量：
    #   RV16_API_BASE   (默认 http://127.0.0.1:8000)
    #   RV16_PHONE      (默认 13900000016)
    #   RV16_ORG        (默认 "RV16 科研测试单位")
    #   RV16_ADMIN_PHONE(生产模式审核用；默认取 .env ADMIN_PHONES 首个)

成功时写 `RV16/evidence/TEST-ACCOUNT.md`（**只含账号与结论，绝不含令牌/密钥**），退出码 0。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

SERVICE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = SERVICE_ROOT.parents[1]

API_BASE = os.getenv("RV16_API_BASE", "http://127.0.0.1:8000").rstrip("/")
PHONE = os.getenv("RV16_PHONE", "13900000016")
ORG = os.getenv("RV16_ORG", "RV16 科研测试单位")
RESEARCH_DIRECTION = os.getenv("RV16_RESEARCH_DIRECTION", "数值 PDE / 有限体积法")
DEV_SMS_CODE = os.getenv("RV16_DEV_SMS_CODE", "123456")

EVIDENCE_DIR = REPO_ROOT / "RV16" / "evidence"
EVIDENCE_MD = EVIDENCE_DIR / "TEST-ACCOUNT.md"

# 本地回环不走系统代理（开发机常配 http_proxy，会把 127.0.0.1 也劫持）。
_CLIENT = httpx.Client(base_url=API_BASE, timeout=30.0, trust_env=False)


def _ok(resp: httpx.Response) -> dict:
    resp.raise_for_status()
    body = resp.json()
    if body.get("code") not in (0, None):
        raise RuntimeError(f"业务错误 code={body.get('code')} message={body.get('message')}")
    return body.get("data") or {}


def step(msg: str) -> None:
    print(f"\n[STEP] {msg}")


def check(msg: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {msg}" + (f" | {detail}" if detail else ""))
    if not cond:
        raise SystemExit(f"自证失败：{msg}")


def main() -> int:
    print("=" * 72)
    print(f"RV16 researcher 账号创建 · base={API_BASE} phone={PHONE}")
    print("=" * 72)

    # 0) 健康探针
    step("GET /api/health")
    try:
        r = _CLIENT.get("/api/health")
        print(f"  health -> HTTP {r.status_code}")
    except httpx.HTTPError as exc:
        raise SystemExit(
            f"8000 不可达（{exc}）。请先启动后端与 PostgreSQL（见 start-services/start-all.ps1）。"
        ) from exc

    # 1) 申请短信挑战码
    step("POST /api/auth/challenges/sms")
    ch = _ok(
        _CLIENT.post(
            "/api/auth/challenges/sms", json={"phone": PHONE, "purpose": "login"}
        )
    )
    challenge_id = ch.get("challenge_id")
    code = ch.get("demo_code") or DEV_SMS_CODE
    check("拿到 challenge_id", bool(challenge_id), f"id={challenge_id} account_exists={ch.get('account_exists')}")

    # 2) 短信登录（新号自动建号 + 演示模式自动批准 researcher）
    step('POST /api/auth/login/sms (preferred_role="researcher")')
    login = _ok(
        _CLIENT.post(
            "/api/auth/login/sms",
            json={
                "phone": PHONE,
                "challenge_id": challenge_id,
                "code": code,
                "remember": False,
                "preferred_role": "researcher",
            },
        )
    )
    token = login.get("access_token") or login.get("token")
    user = login.get("user") or {}
    check("拿到访问令牌", bool(token))
    roles = {r.get("role"): r.get("status") for r in (user.get("roles") or [])}
    print(f"  active_role={user.get('active_role')!r} identity_status={login.get('identity_status')!r}")
    print(f"  roles={roles}")

    active_role = user.get("active_role")
    review_mode = "demo(auto-approve)" if active_role == "researcher" else "production(pending)"

    # 3) 生产模式兜底：提交申请（若演示模式已批准则跳过）
    if active_role != "researcher":
        step("POST /api/auth/profile/role-applications（生产模式：提交审核）")
        app = _ok(
            _CLIENT.post(
                "/api/auth/profile/role-applications",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "role": "researcher",
                    "organization_name": ORG,
                    "research_direction": RESEARCH_DIRECTION,
                },
            )
        )
        print(f"  application={app}")
        print(
            "  ⚠ 生产模式需管理员审核后角色方生效：\n"
            f"     POST /api/admin/identity/applications/{app.get('id')}/approve  (admin 会话，需 10 分钟内 reauth)"
        )
        check(
            "生产模式下已提交 researcher 申请（待管理员批准）",
            app.get("status") in {"pending", "needs_more_info"},
        )

    # 4) 真实登录自证：/api/auth/me
    step("GET /api/auth/me（Bearer）")
    me = _ok(_CLIENT.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}))
    check("me.active_role == researcher", me.get("active_role") == "researcher", f"got={me.get('active_role')!r}")
    researcher_binding = next(
        (r for r in (me.get("roles") or []) if r.get("role") == "researcher"), None
    )
    check(
        "researcher 绑定 status == approved",
        bool(researcher_binding) and researcher_binding.get("status") == "approved",
        f"binding={researcher_binding}",
    )
    print(f"  user_id={me.get('id')} nickname={me.get('nickname')!r}")

    # 5) 落证据（绝不含令牌）
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    md = f"""# RV16 · researcher 测试账号（真实身份表 + 真实登录）

> 生成时间：{datetime.now(UTC).isoformat()}
> 生成方式：`services/api/scripts/rv16_create_researcher_account.py`（公开 HTTP 接口，
> 走 `users` / `role_bindings` / `auth_sessions` 真实落库；**未手改 JWT**）。

## 账号

| 项 | 值 |
| --- | --- |
| 手机号 | `{PHONE}` |
| user_id | `{me.get('id')}` |
| nickname | `{me.get('nickname') or ''}` |
| active_role | `{me.get('active_role')}` |
| 专业审核模式 | `{review_mode}` |
| researcher 绑定 | `status={researcher_binding.get('status') if researcher_binding else 'N/A'}` |

## 获取令牌（fe-engineer 联调用）

```bash
# 1) 申请挑战码（演示模式响应内含 demo_code）
curl --noproxy '*' -s -X POST {API_BASE}/api/auth/challenges/sms \\
  -H 'Content-Type: application/json' \\
  -d '{{"phone":"{PHONE}","purpose":"login"}}'

# 2) 短信登录，取 access_token
curl --noproxy '*' -s -X POST {API_BASE}/api/auth/login/sms \\
  -H 'Content-Type: application/json' \\
  -d '{{"phone":"{PHONE}","challenge_id":"<上一步 challenge_id>","code":"<demo_code 或 123456>","preferred_role":"researcher"}}'

# 3) 自证
curl --noproxy '*' -s {API_BASE}/api/auth/me -H "Authorization: Bearer <access_token>"
```

## 自证结论（本脚本已断言）

- `POST /api/auth/login/sms` → HTTP 200，签发真实会话令牌；
- `GET /api/auth/me` → `active_role == "researcher"`；
- `roles` 含 `researcher` 且 `status == "approved"`。

> 安全：本文件**不含**任何访问令牌、刷新令牌或网关 HMAC 密钥。
"""
    EVIDENCE_MD.write_text(md, encoding="utf-8")
    print(f"\n[OK] 证据已写入 {EVIDENCE_MD}")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    finally:
        _CLIENT.close()
