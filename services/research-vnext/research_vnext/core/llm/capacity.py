"""LLM 供应商容量控制（**单账号单点**下的承重结构）。

## 为什么这不是"nice-to-have"

`MODEL-INVENTORY.md` §3–§4 记录的实测事实：

- 全部 LLM 能力（对话 / 长文本 / 视觉）都挂在**同一家供应商的同一个账号**上；
- `DEEPSEEK_*` 与 `SPARK_*` 是同一把 key（**有意设计**，不是配置错误）；
- 因此 **不存在任何供应商级容灾**；
- 而平台此前**没有全局限流、没有 provider 并发控制**。

后果是具体的：一次并发突刺会全部打到那个账号的 RPM/TPM 上限，
然后**全平台 LLM 路径一起失败**。所以这里做三层闸，且**都在发出请求之前**生效：

| 层 | 作用 | 超限响应 |
|---|---|---|
| 1. 进程内在飞上限（信号量） | 直接限制同时打开的连接数 | `429 BACKPRESSURE` + `Retry-After` |
| 2. 每用户 / 每会话在飞轮次 | 防止单个用户吃掉整个账号的配额 | `429 BACKPRESSURE` + `Retry-After` |
| 3. 每用户滑动窗口调用数 | 限制单位时间内的总消耗 | `429 BACKPRESSURE` + `Retry-After` |

**明确不做的**：不假装有第二供应商可以回退。没有伪造的 failover，
只有"挡住 + 说清还要等多久"。

进程内信号量只覆盖**本进程**。多副本部署时它退化为"每副本 N 个"，
真正的全局上限需要 Valkey 计数器 —— 那需要跨服务协调，见
`docs/CHANGE-REQUEST.md` 的 CR-14。**这一限制是显式登记在案的，不是遗漏。**
"""

from __future__ import annotations

import threading
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass

from ..config import (
    llm_acquire_timeout_seconds,
    llm_rate_limit_window_seconds,
    max_llm_calls_per_window,
    max_llm_inflight,
    max_llm_turns_per_session,
    max_llm_turns_per_user,
    retry_after_seconds,
)


class LlmCapacityExceeded(Exception):
    """供应商容量已满。**必须**变成 429 + `Retry-After`，不得静默排队。"""

    def __init__(self, reason: str, *, scope: str, retry_after: int | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.scope = scope
        self.retry_after = retry_after or retry_after_seconds()
        self.code = "BACKPRESSURE"
        self.http_status = 429


@dataclass
class _Lease:
    scope_keys: tuple[str, ...]
    released: bool = False


class LlmCapacity:
    """进程内 LLM 容量闸（线程安全）。

    刻意**不**用异步原语：调用点是 async 的，但闸门本身只是计数与时间判断，
    用互斥锁保证"检查 + 占用"原子即可，不必把整个事件循环卷进来。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sem = threading.BoundedSemaphore(max(1, max_llm_inflight()))
        self._inflight_user: dict[str, int] = defaultdict(int)
        self._inflight_session: dict[str, int] = defaultdict(int)
        self._window: dict[str, deque[float]] = defaultdict(deque)

    # ---------------- 观测 ----------------

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "limit_inflight": max_llm_inflight(),
                "inflight_total": self._inflight_total_locked(),
                "inflight_users": dict(self._inflight_user),
                "inflight_sessions": dict(self._inflight_session),
                "window_seconds": llm_rate_limit_window_seconds(),
                "max_calls_per_window": max_llm_calls_per_window(),
            }

    def _inflight_total_locked(self) -> int:
        sem_limit = max(1, max_llm_inflight())
        return sem_limit - self._sem._value  # noqa: SLF001 - 只为观测，不做判定

    # ---------------- 准入 ----------------

    def acquire(
        self,
        *,
        user_id: uuid.UUID | None = None,
        session_id: uuid.UUID | None = None,
        timeout: float | None = None,
    ) -> _Lease:
        """申请一个 LLM 槽位。超限/超时抛 `LlmCapacityExceeded`。

        三道闸**全部在发出请求之前**判定 —— 这正是与"等供应商回 429"的区别：
        后者意味着请求已经占用了账号的配额。
        """
        wait = llm_acquire_timeout_seconds() if timeout is None else timeout
        user_key = str(user_id) if user_id else ""
        sess_key = str(session_id) if session_id else ""

        # 闸 2：per-user / per-session 在飞轮次（先看，避免无谓地抢信号量）
        with self._lock:
            if user_key and self._inflight_user[user_key] >= max_llm_turns_per_user():
                raise LlmCapacityExceeded(
                    f"该用户已有 {self._inflight_user[user_key]} 个在飞 LLM 请求"
                    f"（上限 {max_llm_turns_per_user()}）",
                    scope="llm-user",
                )
            if sess_key and self._inflight_session[sess_key] >= max_llm_turns_per_session():
                raise LlmCapacityExceeded(
                    f"该会话已有 {self._inflight_session[sess_key]} 个在飞 LLM 请求"
                    f"（上限 {max_llm_turns_per_session()}）",
                    scope="llm-session",
                )
            # 闸 3：滑动窗口调用数
            if user_key:
                now = time.monotonic()
                window = self._window[user_key]
                cutoff = now - llm_rate_limit_window_seconds()
                while window and window[0] < cutoff:
                    window.popleft()
                if len(window) >= max_llm_calls_per_window():
                    oldest = window[0]
                    retry_after = max(
                        1, int(llm_rate_limit_window_seconds() - (now - oldest)) + 1
                    )
                    raise LlmCapacityExceeded(
                        f"该用户在 {llm_rate_limit_window_seconds()}s 内已发起 "
                        f"{len(window)} 次 LLM 调用（上限 {max_llm_calls_per_window()}）",
                        scope="llm-user-window",
                        retry_after=retry_after,
                    )

        # 闸 1：进程内在飞上限（带超时，绝不无限排队）
        got = self._sem.acquire(timeout=wait)
        if not got:
            raise LlmCapacityExceeded(
                f"LLM 在飞请求已达上限 {max_llm_inflight()}，"
                f"等待 {wait:.0f}s 仍未获得槽位",
                scope="llm-global",
            )

        with self._lock:
            if user_key:
                self._inflight_user[user_key] += 1
                self._window[user_key].append(time.monotonic())
            if sess_key:
                self._inflight_session[sess_key] += 1
        return _Lease(scope_keys=(user_key, sess_key))

    def release(self, lease: _Lease) -> None:
        if lease is None or lease.released:
            return
        lease.released = True
        user_key, sess_key = lease.scope_keys
        with self._lock:
            if user_key and self._inflight_user.get(user_key):
                self._inflight_user[user_key] -= 1
                if self._inflight_user[user_key] <= 0:
                    self._inflight_user.pop(user_key, None)
            if sess_key and self._inflight_session.get(sess_key):
                self._inflight_session[sess_key] -= 1
                if self._inflight_session[sess_key] <= 0:
                    self._inflight_session.pop(sess_key, None)
        try:
            self._sem.release()
        except ValueError:  # pragma: no cover - 双释放保护
            pass


_capacity: LlmCapacity | None = None


def get_capacity() -> LlmCapacity:
    global _capacity
    if _capacity is None:
        _capacity = LlmCapacity()
    return _capacity


def reset_capacity() -> None:
    """测试用：重建闸门（配置改了之后需要）。"""
    global _capacity
    _capacity = LlmCapacity()
