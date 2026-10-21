"""课堂生成事件总线单元测试（纯进程内，不依赖 DB/LLM）。

覆盖：
- publish → subscribe 重放缓冲 + 直播队列
- 多订阅者广播
- unsubscribe / clear（clear 后订阅者收到 done 哨兵）
- 日志上限裁剪
"""

import asyncio

from app.domains.classroom.events import (
    clear_session_events,
    publish_session_event,
    subscribe_session_events,
    unsubscribe_session_events,
)


def test_publish_then_subscribe_replays_history():
    publish_session_event("evt-test-1", "status", {"status": "generating"})
    publish_session_event("evt-test-1", "outlines", {"outlines": [1, 2, 3]})
    history, q = subscribe_session_events("evt-test-1")
    try:
        assert [e["type"] for e in history] == ["status", "outlines"]
        # 直播：新事件即时到达
        publish_session_event("evt-test-1", "slide", {"index": 0})
        ev = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            asyncio.wait_for(q.get(), timeout=1)
        )
        assert ev["type"] == "slide" and ev["data"]["index"] == 0
    finally:
        unsubscribe_session_events("evt-test-1", q)
        clear_session_events("evt-test-1")


def test_multiple_subscribers_all_receive():
    publish_session_event("evt-test-2", "status", {"status": "generating"})
    h1, q1 = subscribe_session_events("evt-test-2")
    h2, q2 = subscribe_session_events("evt-test-2")

    async def _run():
        publish_session_event("evt-test-2", "title", {"title": "T"})
        r = await asyncio.gather(
            asyncio.wait_for(q1.get(), timeout=1), asyncio.wait_for(q2.get(), timeout=1)
        )
        return r

    loop = asyncio.new_event_loop()
    try:
        e1, e2 = loop.run_until_complete(_run())
        assert e1 == e2 == {"type": "title", "data": {"title": "T"}}
        assert len(h1) == len(h2) == 1
    finally:
        loop.close()
        unsubscribe_session_events("evt-test-2", q1)
        unsubscribe_session_events("evt-test-2", q2)
        clear_session_events("evt-test-2")


def test_clear_notifies_subscribers_with_done():
    publish_session_event("evt-test-3", "status", {"status": "generating"})
    _, q = subscribe_session_events("evt-test-3")

    async def _run():
        clear_session_events("evt-test-3")
        return await asyncio.wait_for(q.get(), timeout=1)

    loop = asyncio.new_event_loop()
    try:
        ev = loop.run_until_complete(_run())
        assert ev["type"] == "done"
    finally:
        loop.close()
        unsubscribe_session_events("evt-test-3", q)


def test_event_log_trimmed_to_cap():
    from app.domains.classroom.events import _MAX_LOG_EVENTS, _event_log

    sid = "evt-test-4"
    try:
        for i in range(_MAX_LOG_EVENTS + 50):
            publish_session_event(sid, "slide", {"i": i})
        log = _event_log[sid]
        assert len(log) <= _MAX_LOG_EVENTS
        assert log[-1]["data"]["i"] == _MAX_LOG_EVENTS + 49  # 保留最新
    finally:
        clear_session_events(sid)
