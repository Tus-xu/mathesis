"""鉴权契约壳测试（hermetic，无 Docker）：

- login 成功返回 {token,user} 信封；
- 无 token → 401；学生角色 token → 40301 role_denied（依赖解析阶段触发，不触 DB）；
- 请求带 request_id（x-request-id 响应头 + 信封 request_id 一致）。
"""
from __future__ import annotations

import jwt

from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.security import create_access_token
from main import app

client = TestClient(app)


def _login_token() -> str:
    r = client.post("/api/teacher-v3/auth/login", json={"username": "zhanglaoshi", "password": "teacher123"})
    assert r.status_code == 200
    return r.json()["data"]["token"]


class TestLogin:
    def test_login_success_envelope(self):
        r = client.post("/api/teacher-v3/auth/login", json={"username": "zhanglaoshi", "password": "teacher123"})
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert body["data"]["user"]["role"] == "teacher"
        assert body["data"]["token"]
        assert body["request_id"]  # 信封带 request_id（client.js 读取）

    def test_login_wrong_password(self):
        r = client.post("/api/teacher-v3/auth/login", json={"username": "zhanglaoshi", "password": "wrong"})
        assert r.status_code == 401
        body = r.json()
        assert body["code"] == 40100
        assert body["data"] is None


class TestAuthZ:
    def test_missing_token_401(self):
        r = client.get("/api/teacher-v3/tasks")
        assert r.status_code == 401
        assert r.json()["code"] == 40100

    def test_student_role_token_40301(self):
        # 学生角色 token（架构 §10：非 teacher 返回 40301 role_denied）
        settings = get_settings()
        student_token = create_access_token(user_id="stu-1", name="学生", role="student", school_id="s-1")
        r = client.get("/api/teacher-v3/tasks", headers={"Authorization": f"Bearer {student_token}"})
        assert r.status_code == 403
        body = r.json()
        assert body["code"] == 40301
        assert body["error_key"] == "role_denied"

    def test_invalid_token_401(self):
        r = client.get("/api/teacher-v3/tasks", headers={"Authorization": "Bearer not-a-jwt"})
        assert r.status_code == 401
        assert r.json()["code"] == 40100

    def test_expired_token_40101(self):
        settings = get_settings()
        expired = jwt.encode(
            {"sub": "u1", "name": "x", "role": "teacher", "iat": 0, "exp": 1},
            settings.jwt_secret,
            algorithm="HS256",
        )
        r = client.get("/api/teacher-v3/tasks", headers={"Authorization": f"Bearer {expired}"})
        assert r.status_code == 401
        assert r.json()["code"] == 40101


class TestRequestId:
    def test_request_id_header_and_envelope_consistent(self):
        token = _login_token()
        r = client.get("/api/teacher-v3/tasks", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        header_rid = r.headers.get("x-request-id")
        assert header_rid
        assert r.json()["request_id"] == header_rid
