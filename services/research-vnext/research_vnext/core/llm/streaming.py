"""RV16 · LLM 流式化封装（超时分层 / 显式重试 / 降级链）。

对齐 KERNEL §2.2 与主理人裁决 X5/X6：

- **超时分层**：连接 10s / 首 token 30s / 短任务总时长 90s / 长任务 300s。
  不得用单一 60s 墙 —— 那正是 `WB_LLM_TIMEOUT` 的成因（整段等待撞墙）。
- **显式重试**：幂等请求自动重试 ≥2 次，指数退避 + 抖动（`num_retries` 的 Router
  默认值保持不动，避免影响既有调用）。
- **降级链（X6）**：主模型 → **同账号第二模型** → 确定性兜底。**禁止跨账号换 key**。
  降级必须标注 `degraded: true` + `degraded_reason`。同账号无第二模型时降级链
  止于确定性兜底；确定性兜底也不可用时，如实返回 `LLM_UNAVAILABLE`，**不得伪装成功**。

本模块只**读** `gateway`，不改变 `acompletion` 的任何既有行为。
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, AsyncIterator

from .. import config
from . import gateway

#: 已产出 token 后不再重试（重试会产生重复文本）；只有"零 token"阶段才退避重试。
_DEGRADED_PRIMARY_TIMEOUT = "primary_timeout"
_DEGRADED_PRIMARY_ERROR = "primary_error"
_DEGRADED_FALLBACK_ERROR = "fallback_error"
_DEGRADED_DETERMINISTIC = "deterministic_fallback"

#: 确定性兜底内容：**结构化骨架 + 明确标注**，绝不伪造结论（KERNEL §2.3）。
_SKELETON = (
    "【降级提示 · degraded】模型当前不可用，以下为服务端生成的**结构化骨架**，"
    "不包含任何模型推断结论，请勿据此下结论：\n"
    "1. 研究问题：________________\n"
    "2. 已知条件 / 未知项：________________\n"
    "3. 拟采用方法：________________\n"
    "4. 需要什么证据才算回答：________________\n"
    "5. 证伪判据：________________\n"
    "请在模型恢复后重试以获取真实回答。"
)


def _first_failure_reason(err: BaseException | None) -> str:
    if isinstance(err, gateway.LlmGateError) and err.code in ("LLM_TIMEOUT", "LLM_EMPTY_CHOICES"):
        return _DEGRADED_PRIMARY_TIMEOUT
    return _DEGRADED_PRIMARY_ERROR


def _deadline_guard(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise gateway.LlmGateError("LLM_TIMEOUT", "总时长超时（超时分层）")


async def _stream_model(
    backend: Any,
    model: str,
    messages: list[dict[str, Any]],
    *,
    first_token_timeout: float,
    total_timeout: float,
) -> AsyncIterator[dict[str, Any]]:
    """单模型 token 流：首 token 独立超时；总时长独立超时。

    首 token 超时是**关键信号**：它区分"模型没在产出"（可重试/降级）与
    "模型产出很慢但仍在产出"（不打断）。
    """
    agen = backend.astream(model=model, messages=messages)
    try:
        try:
            first = await asyncio.wait_for(agen.__anext__(), timeout=first_token_timeout)
        except StopAsyncIteration:
            raise gateway.LlmGateError("LLM_EMPTY_CHOICES", "模型返回空流") from None
        except asyncio.TimeoutError:
            raise gateway.LlmGateError("LLM_TIMEOUT", "首 token 超时（超时分层）") from None
    except BaseException:
        await agen.aclose()
        raise

    deadline = time.monotonic() + total_timeout
    yield first
    try:
        async for ev in agen:
            _deadline_guard(deadline)
            yield ev
    finally:
        await agen.aclose()


async def astream_completion(
    messages: list[dict[str, Any]],
    *,
    task_class: str = "short",
    model: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """流式补全（含重试与降级链）。

    yield 事件（`type` 字段区分）：

    - `{"type":"token","delta":str,"model":str,"finish_reason":str|None}`
    - `{"type":"done","finish_reason":str,"model":str,"degraded":bool,"degraded_reason":str|None}`
    - `{"type":"error","code":str,"message":str,"retryable":bool,"degraded":bool}`
    """
    backend = gateway.get_backend()
    primary = model or backend.model
    chain: list[str] = [primary]
    fb = gateway.llm_model_fallback()
    if fb and fb != primary:
        chain.append(fb)

    retries = gateway.llm_stream_retries()
    base = gateway.llm_retry_backoff_base_s()
    first_to = gateway.llm_first_token_timeout_s()
    total_to = gateway.llm_total_timeout_s(task_class)

    primary_failure: str = _DEGRADED_PRIMARY_ERROR
    last_err: BaseException | None = None

    for idx, target in enumerate(chain):
        for attempt in range(retries + 1):
            emitted = False
            try:
                async for ev in _stream_model(
                    backend, target, messages, first_token_timeout=first_to, total_timeout=total_to
                ):
                    emitted = True
                    yield ev
            except gateway.LlmGateError as e:
                last_err = e
                if idx == 0:
                    primary_failure = _first_failure_reason(e)
                if emitted:
                    # 已产出 token：不重试（避免重复文本），诚实收尾。
                    yield {
                        "type": "error",
                        "code": e.code,
                        "message": e.message,
                        "retryable": True,
                        "degraded": idx > 0,
                    }
                    return
                if attempt < retries:
                    await asyncio.sleep(base * (2**attempt) + random.uniform(0, base))
                    continue
                break
            except Exception as e:  # noqa: BLE001 - 统一收敛成可审计事件
                last_err = e
                if idx == 0:
                    primary_failure = _DEGRADED_PRIMARY_ERROR
                if emitted:
                    yield {
                        "type": "error",
                        "code": "LLM_CALL_FAILED",
                        "message": f"{type(e).__name__}: {e}"[:500],
                        "retryable": True,
                        "degraded": idx > 0,
                    }
                    return
                if attempt < retries:
                    await asyncio.sleep(base * (2**attempt) + random.uniform(0, base))
                    continue
                break
            else:
                # 正常结束
                degraded = idx > 0
                yield {
                    "type": "done",
                    "finish_reason": "stop",
                    "model": target,
                    "degraded": degraded,
                    "degraded_reason": primary_failure if degraded else None,
                }
                return

    # ---- 降级链穷尽 ----
    if config.llm_deterministic_fallback_enabled():
        yield {"type": "token", "delta": _SKELETON, "model": "deterministic", "finish_reason": None}
        yield {
            "type": "done",
            "finish_reason": "stop",
            "model": "deterministic",
            "degraded": True,
            "degraded_reason": _DEGRADED_DETERMINISTIC,
        }
        return

    code = last_err.code if isinstance(last_err, gateway.LlmGateError) else "LLM_UNAVAILABLE"
    yield {
        "type": "error",
        "code": "LLM_UNAVAILABLE" if code != "LLM_NOT_CONFIGURED" else "LLM_NOT_CONFIGURED",
        "message": (getattr(last_err, "message", "") or "模型不可用"),
        "retryable": True,
        "degraded": True,
    }


async def astream_collect(
    messages: list[dict[str, Any]],
    *,
    task_class: str = "short",
    model: str | None = None,
) -> tuple[str, dict[str, Any]]:
    """非 SSE 调用方的便捷封装：聚合 token 流为完整文本 + `done`/`error` 事件。

    返回 `(text, terminal_event)`。`terminal_event["type"]` ∈ `{"done","error"}`。
    """
    parts: list[str] = []
    terminal: dict[str, Any] = {"type": "error", "code": "LLM_UNAVAILABLE", "degraded": True}
    async for ev in astream_completion(messages, task_class=task_class, model=model):
        if ev.get("type") == "token":
            parts.append(ev.get("delta") or "")
        elif ev.get("type") in ("done", "error"):
            terminal = ev
    return "".join(parts), terminal
