"""AI 调用审计（providers/audit.py）

每次模型调用落 ai_calls 表：scene/provider/model/token/延迟/状态（含 fallback）。
设计纪律（手册 §7.1）：
- 成功也落，scene 必填 —— 成本核算与"星火使用证明"的数据源；
- 写库失败只记日志，绝不影响主链路；
- 流式调用在流结束后写（不阻塞 token 输出 >5ms，§10.1）。
"""

import asyncio

import structlog

logger = structlog.get_logger()


def log_ai_call(
    *,
    request_id: str,
    scene: str,
    provider: str,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int = 0,
    status: str,
    error: str | None = None,
) -> None:
    """异步落一条 ai_calls（fire-and-forget，失败静默）。"""
    try:
        task = asyncio.create_task(
            _write_ai_call(
                request_id=request_id,
                scene=scene,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                status=status,
                error=error,
            )
        )
        task.add_done_callback(_swallow_task_error)
    except RuntimeError:
        # 无运行中的事件循环（如脚本同步上下文）——直接放弃，不阻塞
        pass


def _swallow_task_error(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("audit.ai_call_write_failed", error=str(exc)[:200])


async def _write_ai_call(
    *,
    request_id: str,
    scene: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    status: str,
    error: str | None,
) -> None:
    from app.models.ai_call import AICall
    from app.models.database import async_session_factory

    async with async_session_factory() as session:
        try:
            session.add(
                AICall(
                    request_id=request_id,
                    scene=scene,
                    provider=provider,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    status=status,
                    error=error,
                )
            )
            await session.commit()
        except Exception as e:
            await session.rollback()
            logger.warning("audit.ai_call_db_error", error=str(e)[:200])
