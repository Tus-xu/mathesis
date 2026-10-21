# coding: utf-8
"""WP-E / E-3 回归：导出调度失败必须响亮（记日志 + 任务置 failed），不得静默吞。

修复前：app/domains/contents/router.py 的导出调度块为
    try: asyncio.create_task(...)
    except Exception: pass
调度层失败（引擎 import 异常/参数求值异常）→ 任务永远停在 pending、无日志、无错误返回，
教师侧只看到一直「导出中」（P1）。

响应契约保持不变：HTTP 200 + {task_id, job_id}（contract: test_response_shell.py::test_deck_export_returns_task_id）。
"""
from __future__ import annotations

import uuid

import pytest

from app.domains.contents import router as cr


class _FakeJob:
    id = uuid.uuid4()
    deck_id = uuid.uuid4()


class _FakeSession:
    async def commit(self) -> None:
        return None


class _Req:
    class state:  # noqa: N801
        request_id = "req-e3-test"


class _User:
    id = str(uuid.uuid4())


@pytest.fixture
def wired(monkeypatch):
    """替换 endpoint 的外部依赖（DB/TaskService/归属校验），保留被修的调度块原样。

    TaskService 在 endpoint 内以局部 import 引入（from ...tasks.service import TaskService），
    故只能改其来源模块上的静态方法，不能改 contents.router 的属性。
    """
    from app.tasks.service import TaskService

    finish_calls: list[tuple[str, str | None]] = []
    task_id = str(uuid.uuid4())

    async def _register_export(session, deck_id, *, fmt):
        return _FakeJob()

    async def _assert_deck_owner(session, deck_id, user):
        return None

    async def _submit(task_type, payload, user_id):
        return {"id": task_id, "status": "pending", "type": task_type}

    async def _finish(tid, status, *, result=None, error=None):
        finish_calls.append((status, error))

    monkeypatch.setattr(cr.ct, "register_export", _register_export)
    monkeypatch.setattr(cr, "_assert_deck_owner", _assert_deck_owner)
    monkeypatch.setattr(TaskService, "submit", staticmethod(_submit))
    monkeypatch.setattr(TaskService, "finish", staticmethod(_finish))
    return {"task_id": task_id, "finish_calls": finish_calls}


async def test_export_scheduling_failure_is_logged_and_task_marked_failed(wired, monkeypatch):
    """修复前：调度失败被 `except: pass` 吞掉 → finish 未被调用、零日志。"""
    def _boom(coro, **kwargs):
        coro.close()
        raise RuntimeError("模拟调度失败：导出引擎 import 异常")

    monkeypatch.setattr(cr.asyncio, "create_task", _boom)

    # structlog 的 get_logger() 在本仓不向 stdlib logging 传播（实测 caplog 抓不到），
    # 故用 logger 替身直接断言「确实记了 ERROR」。
    logged: list[tuple] = []

    class _LoggerSpy:
        def exception(self, msg, *args, **kwargs):
            logged.append((msg, args, kwargs))

        def __getattr__(self, name):  # 其它级别透传为 no-op
            def _noop(*args, **kwargs):
                logged.append((name, args, kwargs))
            return _noop

    monkeypatch.setattr(cr, "logger", _LoggerSpy())

    result = await cr.export_deck(_Req(), str(uuid.uuid4()), {"format": "pptx"}, _User(), _FakeSession())

    # 1) 响应契约不变
    assert result["data"] == {"task_id": wired["task_id"], "job_id": str(_FakeJob.id)}
    # 2) 任务被显式置 failed（此前永远 pending）
    assert len(wired["finish_calls"]) == 1
    status, error = wired["finish_calls"][0]
    assert status == "failed"
    assert error and "模拟调度失败" in error
    # 3) 确实记了 ERROR 日志，且带 task_id（此前零日志）
    assert logged, "调度失败必须留日志"
    assert "导出任务调度失败" in str(logged[0][0])
    assert logged[0][2].get("task_id") == wired["task_id"]


async def test_export_scheduling_success_path_unchanged(wired, monkeypatch):
    """成功路径零行为变化：create_task 正常调度，不做任何 failed 回写。"""
    scheduled: list = []

    class _Tracked:
        def __init__(self, coro):
            self._coro = coro
            self._coro.close()  # 不在测试里真跑导出引擎
            scheduled.append(self)

        def add_done_callback(self, cb):
            self._cb = cb

    monkeypatch.setattr(cr.asyncio, "create_task", lambda coro, **kw: _Tracked(coro))

    result = await cr.export_deck(_Req(), str(uuid.uuid4()), {"format": "pptx"}, _User(), _FakeSession())

    assert result["data"] == {"task_id": wired["task_id"], "job_id": str(_FakeJob.id)}
    assert len(scheduled) == 1, "成功路径必须调度一次导出任务"
    assert wired["finish_calls"] == [], "成功路径不得回写 failed"
