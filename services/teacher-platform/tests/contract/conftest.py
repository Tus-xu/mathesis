"""B5 契约测试公共夹具（移植母本：D:\\frontend\\test\\teacher\\v3MockContract.test.ts）。

两档跑法（B5 规范 M1 / 02-ARCHITECTURE §18.5 红线 5）：
- **ASGI 直连**（默认）：httpx.AsyncClient(ASGITransport) 起 `main.create_app()`，
  覆盖纯应用层断言（响应壳/鉴权矩阵/路由存在性）；
- **compose 全栈**（marker `compose`）：TEACHER_CONTRACT_BASE_URL 指向真实 8100 容器，
  SSE 序列/幂等/课堂用例需要 DB+Valkey，禁进程内模拟。

`main.py` 未实现时 fail 并注明归因——上游未实现导致红是预期（先红后绿是常态），
但必须红得可读，禁止静默 skip 掩盖契约缺口。

marker 注册：conftest 里 `pytest_configure` 声明（pyproject 归 B3 所有权，B5 不改）。
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx
import pytest

# compose 全栈基准地址；未设置 = ASGI 直连档
CONTRACT_BASE_URL = os.environ.get("TEACHER_CONTRACT_BASE_URL", "").rstrip("/")

FRONTEND_API_TS = os.environ.get(
    "FRONTEND_TEACHER_API", r"D:\frontend\src\api\teacherV3.ts"
)

# 教师端服务端口定案（02-ARCHITECTURE §1.3）
TEACHER_PORT = 8100


def _load_app():
    """惰性导入应用工厂。main.py 未落地 → fail（可读的红），不 skip。"""
    try:
        from main import create_app  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            "[契约红·预期] 后端应用工厂尚未实现（M1-B3）：`from main import create_app` 失败："
            f"{exc!r}。骨架健康达标（/healthz + login 路由存在）后本套件自动转绿。"
        )
    return create_app()


@pytest.fixture(scope="session")
def app():
    if CONTRACT_BASE_URL:
        pytest.skip(f"compose 档（TEACHER_CONTRACT_BASE_URL={CONTRACT_BASE_URL}），ASGI 夹具不适用")
    return _load_app()


@pytest.fixture(scope="session")
def any_client(app):
    """ASGI 直连客户端（session 级；由 pytest 收尾时统一 aclose）。"""
    transport = httpx.ASGITransport(app=app)
    return PrefixingAsyncClient(transport=transport, base_url=f"http://teacher-api.test:{TEACHER_PORT}")


class PrefixingAsyncClient(httpx.AsyncClient):
    """把契约测试路径 /teacher-v3/* 映射到真实线缆路径 /api/teacher-v3/*。

    口径依据：前端统一 `BASE='/api'` + teacher-v3 相对路径，vite 代理
    `'/api/teacher-v3': {target: 8100}` **不剥前缀**（D:\\frontend\\vite.config.js:38），
    故后端挂载即 `/api/teacher-v3`（app/api/routes.py）。inventory 中的路径为
    mock 相对口径（/teacher-v3/*），由本类与导出器统一换算。
    """

    def build_request(self, method: str, url: httpx.URL | str, *args, **kwargs) -> httpx.Request:  # type: ignore[override]
        request = super().build_request(method, url, *args, **kwargs)
        path = request.url.path
        if path.startswith("/teacher-v3"):
            request.url = request.url.copy_with(path="/api" + path)
        return request


class PrefixingSyncClient(httpx.Client):
    """compose 档同步版（同口径换算）。"""

    def build_request(self, method: str, url: httpx.URL | str, *args, **kwargs) -> httpx.Request:  # type: ignore[override]
        request = super().build_request(method, url, *args, **kwargs)
        path = request.url.path
        if path.startswith("/teacher-v3"):
            request.url = request.url.copy_with(path="/api" + path)
        return request


client = any_client  # 同义别名：测试代码统一用 client


# ---------------- token 夹具（负向矩阵可合成；正向课堂 token 必须 M2-A 起走真实 join） ----------------

SEED_TEACHER_ID = "seed-teacher-zhanglaoshi"
SEED_SCHOOL_ID = "seed-school-001"


def _make_token(role: str, **extra: Any) -> str:
    """用与后端一致的 HS256 密钥签发测试 JWT（claim 契约：{sub,name,role,school_id}）。"""
    import jwt

    from app.core.config import get_settings

    settings = get_settings()
    now = int(time.time())
    claims = {
        "sub": extra.pop("sub", SEED_TEACHER_ID),
        "name": extra.pop("name", "张老师"),
        "role": role,
        "school_id": extra.pop("school_id", SEED_SCHOOL_ID),
        "iat": now,
        "exp": now + 3600,
        **extra,
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


@pytest.fixture(scope="session")
def teacher_token() -> str:
    return _make_token("teacher")


@pytest.fixture(scope="session")
def student_token() -> str:
    """学生端角色 token（学生平台账号体系）——调教师端点必须 40301。"""
    return _make_token("student", sub="student-role-user", name="学生角色", school_id=SEED_SCHOOL_ID)


@pytest.fixture(scope="session")
def classroom_student_token() -> str:
    """合成课堂作用域 token（负向矩阵专用）：绑定 session+participant，role 非教师。

    正向断言（学生端点可用）禁用本夹具——必须走 `POST /classroom/join` 真实签发（M2-A）。
    """
    return _make_token(
        "classroom_student",
        sub="anon-participant-1",
        name="王小明",
        school_id=None,
        session_id="00000000-0000-0000-0000-000000000001",
        participant_id="00000000-0000-0000-0000-000000000002",
    )


@pytest.fixture(scope="session")
def http_base() -> str:
    """compose 全栈基准地址（marker=compose 用例专用）。未设置 → fail 并注明跑法。"""
    if not CONTRACT_BASE_URL:
        pytest.fail(
            "[compose 用例] 需要 docker compose 全栈：先 `docker compose up -d`，"
            "再以 TEACHER_CONTRACT_BASE_URL=http://127.0.0.1:8101 pytest -m compose 运行（教师端现驻 8101，DEF-002）"
            "（基础设施禁进程内模拟，红线 5）。"
        )
    return CONTRACT_BASE_URL


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------- SSE 解析（对齐前端 parseSse：event:/data:/id:/retry:） ----------------


class SseEvent:
    __slots__ = ("event", "data", "id", "raw")

    def __init__(self, event: str, data: Any, id_: str | None, raw: str):
        self.event = event
        self.data = data
        self.id = id_
        self.raw = raw

    def __repr__(self) -> str:  # 调试可读
        return f"<Sse {self.event} id={self.id} data={str(self.data)[:80]}>"


def parse_sse_text(text: str) -> list[SseEvent]:
    """把 SSE 文本流解析为事件列表（与前端 v3MockContract.parseSse 同语义 + id/retry 捕获）。"""
    events: list[SseEvent] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        ev_name: str | None = None
        data_parts: list[str] = []
        id_: str | None = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                ev_name = line[7:]
            elif line.startswith("data: "):
                data_parts.append(line[6:])
            elif line.startswith("id: "):
                id_ = line[4:]
            # retry:/注释行（: ping）忽略——但 retry 值单独断言
        if ev_name is None and not data_parts:
            continue
        if ev_name is None:
            continue
        raw_data = "\n".join(data_parts)
        try:
            data: Any = json.loads(raw_data) if raw_data else None
        except json.JSONDecodeError:
            data = {"raw": raw_data}
        events.append(SseEvent(ev_name, data, id_, block))
    return events


async def collect_sse(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    timeout: float = 60.0,
    max_bytes: int = 2_000_000,
) -> tuple[int, httpx.Headers, str]:
    """收集完整 SSE 流，返回 (status, headers, raw_text)。断言 content-type 由调用方负责。"""
    buf: list[str] = []
    async with client.stream(
        method, url, headers=headers, json=json_body, timeout=timeout
    ) as resp:
        status = resp.status_code
        headers_out = resp.headers
        async for chunk in resp.aiter_text():
            buf.append(chunk)
            if sum(len(c) for c in buf) > max_bytes:
                break
    return status, headers_out, "".join(buf)


def assert_sse_stream_headers(headers: httpx.Headers) -> None:
    """SSE 通用帧契约（笔记 §1.6 #49）。"""
    ct = headers.get("content-type", "")
    assert ct.startswith("text/event-stream"), f"content-type 应为 text/event-stream，实际 {ct!r}"
    assert headers.get("cache-control") == "no-cache"
    assert headers.get("x-accel-buffering") == "no"


def seq_of(events: list[SseEvent]) -> list[str]:
    return [e.event for e in events]


# ---------------- pytest marker 注册 ----------------


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "compose: 需要 docker compose 全栈（DB+Valkey+MinIO）真实容器的用例；"
        "跑法：TEACHER_CONTRACT_BASE_URL=http://127.0.0.1:8100 pytest -m compose",
    )
