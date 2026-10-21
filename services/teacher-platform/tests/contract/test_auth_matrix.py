"""鉴权矩阵契约（B5 规范 M1）：无 token / 学生 token / 学生课堂 token / 教师 token × 代表端点。

蓝本：MC:67-74（40301）、02-ARCHITECTURE §10（课堂 token 作用域特例）、§13（BOLA）。
正向课堂 token 用例（真实 join 签发）在 M2-A Classroom Basic 落地（conftest 约定禁合成正向）。
"""
from __future__ import annotations

import pytest

from conftest import _auth, _make_token

pytestmark = pytest.mark.usefixtures("app")

# 代表端点：纯教师侧（跨 catalog/decks/写操作/未来课堂教师端点）
TEACHER_ENDPOINTS = [
    ("GET", "/teacher-v3/today", None),
    ("GET", "/teacher-v3/quiz/questions", None),
    ("POST", "/teacher-v3/quiz/questions", {"kp_code": "X", "kp_name": "x", "q_type": "solve", "difficulty": "easy", "stem_latex": "x", "answer": "x"}),
    ("GET", "/teacher-v3/decks", None),
    ("GET", "/teacher-v3/butler/tools", None),
    ("POST", "/teacher-v3/classroom/sessions", {"class_id": "seed-c1"}),  # §12.3 教师端点（M2-A 起真实实现）
]


async def test_no_token_unauthorized(client):
    """#14 — 无 Authorization → 401 + 40100（不是 403：先认证后授权）。"""
    for method, url, body in TEACHER_ENDPOINTS:
        r = await client.request(method, url, json=body)
        assert r.status_code == 401, f"{method} {url} 无 token 应 401，实际 {r.status_code}"
        assert r.json()["code"] == 40100


@pytest.mark.parametrize("role_token", ["student_token", "classroom_student_token"])
async def test_non_teacher_role_denied(client, request, role_token):
    """#13/#15/#16 — 学生角色/学生课堂 token 调教师端点 → 403 + 40301 role_denied。"""
    token = request.getfixturevalue(role_token)
    for method, url, body in TEACHER_ENDPOINTS:
        r = await client.request(method, url, json=body, headers=_auth(token))
        assert r.status_code == 403, f"{method} {url} 以 {role_token} 应 403，实际 {r.status_code}"
        body_json = r.json()
        assert body_json["code"] == 40301, f"{method} {url} 错误码应 40301，实际 {body_json['code']}"
        assert body_json["data"] is None


async def test_teacher_token_passes_gate(client, teacher_token):
    """#17 — 教师 token 通过门禁（业务 404/200 均视为过闸；401/403 = 契约破坏）。"""
    for method, url, body in TEACHER_ENDPOINTS:
        r = await client.request(method, url, json=body, headers=_auth(teacher_token))
        assert r.status_code not in (401, 403), f"{method} {url} 教师 token 被拒：{r.status_code} {r.text[:120]}"


async def test_garbage_token_unauthorized(client):
    """伪 token → 401/40100（JWT 校验拒绝，不得 500）。"""
    r = await client.get("/teacher-v3/today", headers=_auth("not-a-jwt"))
    assert r.status_code == 401
    assert r.json()["code"] in (40100, 40101)


# ---------------- 纯 core 绿证（无 app 依赖：证明测试资产本身可运行） ----------------


def test_jwt_roundtrip_core():
    """security core 往返：签发→解码→claim 保真（B5 资产绿证，不依赖 main.py）。"""
    from app.core.security import create_access_token, decode_access_token

    token = create_access_token(user_id="u-1", name="张老师", role="teacher", school_id="s-1")
    claims = decode_access_token(token)
    assert claims["sub"] == "u-1"
    assert claims["role"] == "teacher"
    assert claims["school_id"] == "s-1"
    assert "exp" in claims and "iat" in claims


def test_jwt_expired_rejected():
    """过期 token → TOKEN_EXPIRED（40101）业务异常，非 500。"""
    import time

    import jwt as pyjwt

    from app.core.config import get_settings
    from app.core.errors import ApiError, ErrorCode

    settings = get_settings()
    now = int(time.time())
    bad = pyjwt.encode({"sub": "u", "role": "teacher", "exp": now - 10, "iat": now - 20}, settings.jwt_secret, algorithm="HS256")
    with pytest.raises(ApiError) as ei:
        from app.core.security import decode_access_token

        decode_access_token(bad)
    assert ei.value.code == ErrorCode.TOKEN_EXPIRED


def test_error_code_default_status():
    """#24 — 错误码→HTTP 状态映射表：40301→403、40402 段→404、40100→401。"""
    from app.core.errors import ErrorCode, _default_status

    assert _default_status(ErrorCode.ROLE_DENIED) == 403
    assert _default_status(ErrorCode.UNAUTHORIZED) == 401
    assert _default_status(ErrorCode.NOT_FOUND) == 404
    assert _default_status(ErrorCode.CONFLICT) == 409


def test_authz_resource_owner_matrix():
    """#18 — assert_resource_owner：跨校拒绝/非属主拒绝/admin 放行/无资源放行。"""
    from app.core.authz import ResourceOwner, assert_resource_owner
    from app.core.errors import ApiError
    from app.core.security import UserToken

    teacher = UserToken("t-1", "张老师", "teacher", "school-A")
    other_school = ResourceOwner(school_id="school-B")
    with pytest.raises(ApiError):
        assert_resource_owner(teacher, other_school)

    not_owner = ResourceOwner(school_id="school-A", allowed_teacher_ids={"t-2"})
    with pytest.raises(ApiError):
        assert_resource_owner(teacher, not_owner)

    ok_owner = ResourceOwner(school_id="school-A", allowed_teacher_ids={"t-1"})
    assert_resource_owner(teacher, ok_owner)  # 不抛

    admin = UserToken("a-1", "管理员", "admin", "school-B")
    assert_resource_owner(admin, other_school)  # admin 跨校放行

    assert_resource_owner(teacher, None)  # 新建资源放行
