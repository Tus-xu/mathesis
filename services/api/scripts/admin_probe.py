"""admin 后台 API 实测探针（迭代06）

前置：API 服务已启动；postgres 容器 math-arena-postgres 在跑。
流程：登录测试号 → 直插 admin 角色绑定（等价 ADMIN_PHONES 白名单）→
      复用/重签 token → 逐个打 /api/admin 端点 → 清理测试数据。

用法：./.venv/Scripts/python.exe scripts/admin_probe.py
注意：验证码 60s 频率窗，脚本内已处理"第二发受限时复用首 token"。
"""
import json
import subprocess

import httpx

BASE = "http://127.0.0.1:8000"
PHONE = "13900139007"

c = httpx.Client(timeout=60)

r = c.post(f"{BASE}/api/auth/sms-code", json={"phone": PHONE})
if r.json().get("code") != 0:
    print(f"[x] sms 频率受限，请 60 秒后重跑: {r.json().get('message')}")
    raise SystemExit(1)
tok = c.post(f"{BASE}/api/auth/login", json={"phone": PHONE, "code": "123456"}).json()["data"]["token"]
me = c.get(f"{BASE}/api/auth/me", headers={"Authorization": f"Bearer {tok}"}).json()["data"]
uid = me.get("id") or me.get("user_id")
print(f"[1] user={uid} roles={[rb['role'] for rb in me.get('roles', [])]}")

# 直插 admin 绑定（幂等），等价于 ADMIN_PHONES 白名单的引导效果
sql = (
    f"INSERT INTO role_bindings (user_id, role, verified) "
    f"VALUES ('{uid}', 'admin', true) ON CONFLICT DO NOTHING;"
)
out = subprocess.run(
    ["docker", "exec", "math-arena-postgres", "psql", "-U", "postgres", "-d", "math_arena", "-c", sql],
    capture_output=True, text=True,
)
print(f"[2] grant admin: {(out.stdout or out.stderr).strip()[:120]}")

# 重新登录拿含 admin 的 JWT；频率受限则复用首 token（绑定先于登录存在时已含 admin）
r = c.post(f"{BASE}/api/auth/sms-code", json={"phone": PHONE})
if r.json().get("code") == 0:
    tok = c.post(f"{BASE}/api/auth/login", json={"phone": PHONE, "code": "123456"}).json()["data"]["token"]
else:
    print("    (sms2 频率受限，复用首 token)")
h = {"Authorization": f"Bearer {tok}"}

print("\n[3] GET /api/admin/overview")
d = c.get(f"{BASE}/api/admin/overview", headers=h).json()
print("   ", json.dumps(d.get("data", d), ensure_ascii=False)[:400])

print("\n[4] GET /api/admin/system/model")
d = c.get(f"{BASE}/api/admin/system/model", headers=h).json()
print("   ", json.dumps(d.get("data", d), ensure_ascii=False)[:300])

print("\n[5] GET /api/admin/system/cloud-kb")
d = c.get(f"{BASE}/api/admin/system/cloud-kb", headers=h).json()
print("   ", json.dumps(d.get("data", d), ensure_ascii=False)[:300])

print("\n[6] PUT cloud-kb top_k=8（部分更新）")
d = c.put(f"{BASE}/api/admin/system/cloud-kb", headers=h, json={"top_k": 8}).json()
print("   ", json.dumps(d, ensure_ascii=False)[:150])

print("\n[7] GET /api/admin/workflows")
d = c.get(f"{BASE}/api/admin/workflows", headers=h).json()
wf = d.get("data", {}).get("workflows", [])
print(f"    master={d.get('data', {}).get('master_enabled')}, 共 {len(wf)} 个")
for w in wf:
    print(f"      {w['name']:<24} flow_id={w['flow_id'] or '(未配)':<14} enabled={w['enabled']}")

print("\n[8] PUT workflows/wf_socratic_chat 写入→清除回退")
d = c.put(f"{BASE}/api/admin/workflows/wf_socratic_chat", headers=h, json={"flow_id": "TEST_FLOW_123"}).json()
print("    set ->", d.get("data", {}).get("flow_id"))
d = c.put(f"{BASE}/api/admin/workflows/wf_socratic_chat", headers=h, json={"flow_id": ""}).json()
print("    cleared -> env:", d.get("data", {}).get("flow_id"))

print("\n[9] 越权检查：非 admin 学生访问 /api/admin")
r = c.post(f"{BASE}/api/auth/sms-code", json={"phone": "13900139008"})
if r.json().get("code") == 0:
    tok2 = c.post(f"{BASE}/api/auth/login", json={"phone": "13900139008", "code": "123456"}).json()["data"]["token"]
    resp = c.get(f"{BASE}/api/admin/overview", headers={"Authorization": f"Bearer {tok2}"})
    print(f"    HTTP {resp.status_code} {str(resp.json())[:120]}")
else:
    print("    (频率限制，跳过)")

print("\n[10] POST /api/admin/system/cloud-kb/test（未配真实凭证）")
d = c.post(f"{BASE}/api/admin/system/cloud-kb/test", headers=h).json()
print("   ", json.dumps(d.get("data", d), ensure_ascii=False)[:250])

# 清理：cloud-kb top_k 测试值清除回 env
c.put(f"{BASE}/api/admin/system/cloud-kb", headers=h, json={"top_k": None})
print("\n[cleanup] cloud-kb top_k 已清除回 env 兜底")
print("完成。")
