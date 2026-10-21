"""后端启动壳：Windows 下必须在 uvicorn 创建事件循环之前设置 SelectorEventLoopPolicy，
否则 psycopg 异步连接会因为 ProactorEventLoop 不支持 add_reader 而抛 InterfaceError。
直接 `uvicorn app.main:app` 会先 new_event_loop 再 import app，此时 policy 已经来不及。
"""

from __future__ import annotations

import asyncio
import os
import sys
import selectors

if sys.platform == "win32":
    # 必须在任何 asyncio.* 资源创建之前全局设置（包括 uvicorn 内部 new_event_loop）
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    # 再用显式的 loop_factory 保险（policy 生效后会被默认使用）
    _ = selectors.SelectSelector  # noqa: F401  # 确认模块可导入


def _selector_loop_factory() -> asyncio.AbstractEventLoop:
    """uvicorn 的 loop_factory 形参；替代默认 ProactorEventLoop。
    当设置 WindowsSelectorEventLoopPolicy 后 asyncio.new_event_loop() 已经默认
    返回 SelectorEventLoop，这里显式再保险。"""
    return asyncio.SelectorEventLoop(selectors.SelectSelector())


def _main() -> None:
    import uvicorn

    # stdout 重定向到管道时 Python 按块缓冲（~8KB），排障时日志会"凭空消失"
    # 数分钟——强制行缓冲保证实时可见。
    if not sys.stdout.line_buffering:
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except (AttributeError, OSError):
            pass

    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8000"))
    log_level = os.environ.get("API_LOG_LEVEL", "warning")

    # 方式：通过 Config.get_loop_factory 的路径解析机制，传入 module:attr。
    # 由于本文件就在 sys.path 根（服务启动目录），可用 run_server:_selector_loop_factory。
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        log_level=log_level,
        reload=False,
        loop="run_server:_selector_loop_factory",
    )


if __name__ == "__main__":
    _main()
