"""R12 TextbookPage 零新读写守卫（P0）。

背景（DECISIONS.md §3 / D-002）：切片源无原页；textbook_pages 旧表保持 dormant，
不删除、不迁移、不允许任何新写入。page_ref/page_refs/pNN 只允许 legacy 读取。

守卫：
- 运行守卫 `guard_textbook_page_read_only(session)`：session.new/dirty 中出现
  TextbookPage 实例即抛 TextbookPageWriteError（fail closed，不落库）；
- `install_textbook_page_guard`：把守卫挂到 SQLAlchemy before_flush（生产启动可装，
  R12 新 writer 入口已内置调用）；
- 静态守卫见 tests/unit/textbook_v2/test_legacy_guards.py（扫描生产代码禁止构造写入）；
- 逃生舱 `legacy_textbook_page_write_allowed()`：仅测试/旧 fixture 边界 opt-in，
  生产路径禁止使用（静态测试断言）。
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar

from app.textbook_v2.models import TextbookPage

TEXTBOOK_PAGE_WRITE_ERROR = "TextbookPage 已进入 dormant：禁止任何新写入（决策 D-002 / R12）"


class TextbookPageWriteError(RuntimeError):
    """对 textbook_pages 的新写入被守卫拒绝（fail closed）。"""


# 逃生舱标记：默认 False（生产禁止写入）；仅测试 fixture 边界显式开启
_PAGE_WRITE_ALLOWED: ContextVar[bool] = ContextVar("_page_write_allowed", default=False)

# 全局守卫实例（幂等安装；event.remove 需要同一 handler 引用）
_PAGE_GUARD_HANDLER: Callable | None = None


@contextlib.contextmanager
def legacy_textbook_page_write_allowed() -> Iterator[None]:
    """旧 fixture / 测试边界专用逃生舱（生产路径禁止；静态守卫断言其不出现于 app/）。"""
    token = _PAGE_WRITE_ALLOWED.set(True)
    try:
        yield
    finally:
        _PAGE_WRITE_ALLOWED.reset(token)


def guard_textbook_page_read_only(session) -> None:
    """运行守卫：session 待写入/已修改集合中出现 TextbookPage 即拒绝（不落库）。"""
    if _PAGE_WRITE_ALLOWED.get():
        return
    offenders = [obj for obj in session.new if isinstance(obj, TextbookPage)]
    offenders += [obj for obj in session.dirty if isinstance(obj, TextbookPage)]
    if offenders:
        raise TextbookPageWriteError(
            f"{TEXTBOOK_PAGE_WRITE_ERROR}（发现 {len(offenders)} 个待写入 TextbookPage 实例）"
        )


def install_textbook_page_guard(session_maker=None) -> Callable[[], None]:
    """把运行守卫挂到 SQLAlchemy Session 的 before_flush（幂等，返回卸载函数）。

    生产启动可安装；测试验证后须调用返回的卸载函数，避免污染其他用例。
    """
    global _PAGE_GUARD_HANDLER
    if _PAGE_GUARD_HANDLER is not None:
        return _uninstall_textbook_page_guard

    from sqlalchemy import event
    from sqlalchemy.orm import Session

    def _guard(session, _flush_context, _instances) -> None:
        guard_textbook_page_read_only(session)

    _PAGE_GUARD_HANDLER = _guard
    event.listen(Session, "before_flush", _guard)
    return _uninstall_textbook_page_guard


def _uninstall_textbook_page_guard() -> None:
    """卸载全局守卫（测试边界/服务关闭）。"""
    global _PAGE_GUARD_HANDLER
    if _PAGE_GUARD_HANDLER is None:
        return
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    event.remove(Session, "before_flush", _PAGE_GUARD_HANDLER)
    _PAGE_GUARD_HANDLER = None
