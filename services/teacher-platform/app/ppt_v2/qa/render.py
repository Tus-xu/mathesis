"""QA 专用逐页渲染（T24）：在 8100 侧用 PowerPoint COM 独立渲染 PPTX 为 PNG。

为什么不由 8200 取页图：R21 的管道把 PAGE_PNG 登记为引用（object_key 指向 8201 的 ref），
并没有把字节复制进 8200 的 artifact store，因此 8200 的 content 端点对页图返回 404。
更重要的是：**QA 的渲染器与生成器分离**本来就是本轮的验收要求——独立渲染比复用生成侧
的页图更能证明质检不是自说自话。

不可用（无 COM / 非 Windows）时返回空 dict，由调用方如实记录"QA 不可用"，不伪造页图。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import tempfile
import threading
from pathlib import Path

_RENDER_LOCK = threading.Lock()

# PowerPoint 是单实例桌面应用：跨进程并发 Open 会直接失败
# （实测：8100 的独立 QA 渲染与 8201 的 PDF 导出同时进行 → "Presentations.Open : Failed"）。
# 用文件锁做跨进程串行化，而不是只在本进程内加锁。
_COM_LOCK_PATH = Path(tempfile.gettempdir()) / "t24-powerpoint-com.lock"


@contextlib.contextmanager
def com_guard():
    if os.name != "nt":
        yield
        return
    import msvcrt
    import time

    fh = open(_COM_LOCK_PATH, "a+b")  # noqa: SIM115 - 需在整个 COM 会话期间持有
    try:
        for _ in range(180):
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                fh.seek(0)
                time.sleep(1.0)
        else:
            raise TimeoutError("等待 PowerPoint COM 跨进程锁超时（>180s）")
        yield
    finally:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            fh.close()


def renderer_available() -> bool:
    if os.name != "nt":
        return False
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        return False
    return True


def _export_sync(pptx_path: Path, out_dir: Path, *, width: int = 1280, only_slide: int | None = None) -> None:
    import pythoncom
    import win32com.client as wc

    pythoncom.CoInitialize()
    app = None
    pres = None
    try:
        app = wc.DispatchEx("PowerPoint.Application")
        app.DisplayAlerts = 1
        pres = app.Presentations.Open(str(pptx_path), ReadOnly=True, Untitled=False, WithWindow=False)
        count = pres.Slides.Count
        # only_slide：单页重试只导出目标页。整册渲染会让一次"局部重试"变成分钟级请求。
        pages = [only_slide] if only_slide else list(range(1, count + 1))
        for i in pages:
            if not 1 <= i <= count:
                continue
            pres.Slides(i).Export(str(out_dir / f"page-{i:02d}.png"), "PNG", width, int(width * 720 / 1280))
    finally:
        if pres is not None:
            pres.Close()
        if app is not None:
            app.Quit()
        pythoncom.CoUninitialize()


def _render_sync(pptx_bytes: bytes, only_slide: int | None = None) -> dict[int, bytes]:
    out: dict[int, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="t24-qa-render-") as tmp:
        tmp_path = Path(tmp)
        pptx = tmp_path / "deck.pptx"
        pptx.write_bytes(pptx_bytes)
        pages = tmp_path / "pages"
        pages.mkdir()
        # COM 是单实例进程级资源：并发渲染会互相打断；跨进程亦需串行
        with _RENDER_LOCK, com_guard():
            _export_sync(pptx, pages, only_slide=only_slide)
        for png in sorted(pages.glob("page-*.png")):
            out[int(png.stem.split("-")[1])] = png.read_bytes()
    return out


async def render_pages(
    pptx_bytes: bytes, *, page_no: int | None = None, timeout_s: float = 420.0
) -> dict[int, bytes]:
    """渲染全部页；给 page_no 时只渲染该页（单页重试用）。"""
    if not renderer_available():
        return {}
    return await asyncio.wait_for(asyncio.to_thread(_render_sync, pptx_bytes, page_no), timeout=timeout_s)
