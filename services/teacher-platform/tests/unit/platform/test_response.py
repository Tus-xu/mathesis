"""响应壳契约单测：ok/fail 信封与 client.js 语义对齐（{code,message,data,request_id}）。"""
from __future__ import annotations

from app.core.errors import ErrorCode
from app.core.response import fail, ok


class TestOkEnvelope:
    def test_ok_shape_matches_client_contract(self):
        envelope = ok({"id": "x"}, request_id="rid-1")
        # client.js: code===0 成功，读 data / request_id
        assert envelope["code"] == 0
        assert envelope["message"] == "ok"
        assert envelope["data"] == {"id": "x"}
        assert envelope["request_id"] == "rid-1"
        assert set(envelope.keys()) == {"code", "message", "data", "request_id"}

    def test_ok_default_data_null(self):
        envelope = ok()
        assert envelope["data"] is None


class TestFailEnvelope:
    def test_fail_code_message_data_null(self):
        envelope = fail(ErrorCode.ROLE_DENIED, "role_denied", request_id="rid-2", error_key="role_denied")
        assert envelope["code"] == 40301
        assert envelope["message"] == "role_denied"
        assert envelope["data"] is None
        assert envelope["error_key"] == "role_denied"

    def test_fail_without_error_key_omits_field(self):
        envelope = fail(ErrorCode.NOT_FOUND, "not found", request_id="rid-3")
        assert "error_key" not in envelope
        assert envelope["code"] == 40400

    def test_error_code_values_frozen(self):
        # 契约冻结：40301 role_denied 等错误码值不许变（B5 契约测试依赖）
        assert ErrorCode.ROLE_DENIED.value == 40301
        assert ErrorCode.UNAUTHORIZED.value == 40100
        assert ErrorCode.OK.value == 0

    def test_domain_not_found_codes(self):
        # mock 契约：40401 知识点 / 40402 题目/作业（B5 test_response_shell 断言）
        assert ErrorCode.NOT_FOUND_RESOURCE.value == 40401
        assert ErrorCode.NOT_FOUND_ITEM.value == 40402


class TestTasksContractShape:
    """/tasks 契约形状 {items, running}（对齐 B5 test_tasks_shape_running_count）。"""

    def test_status_map_matches_contract_enums(self):
        from app.api.platform import _TASK_STATUS_MAP

        assert set(_TASK_STATUS_MAP.values()) <= {"queued", "running", "succeeded", "failed"}

    def test_contract_envelope_via_client(self):
        from fastapi.testclient import TestClient

        from app.core.security import create_access_token
        from main import app

        client = TestClient(app)
        token = create_access_token(user_id="00000000-0000-0000-0000-000000000001", name="x", role="teacher")
        r = client.get("/api/teacher-v3/tasks", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert body["code"] == 0
        assert isinstance(body["data"]["items"], list)
        assert isinstance(body["data"]["running"], int)

    def test_non_uuid_sub_tolerant(self):
        from fastapi.testclient import TestClient

        from app.core.security import create_access_token
        from main import app

        client = TestClient(app)
        token = create_access_token(user_id="seed-teacher-zhanglaoshi", name="x", role="teacher")
        r = client.get("/api/teacher-v3/tasks", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["data"] == {"items": [], "running": 0}
