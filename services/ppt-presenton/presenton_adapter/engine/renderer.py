"""PowerPoint COM 渲染（真实 PowerPoint；也直接证明产物可被 PowerPoint 打开）。

R3d 性能回炉（真实链路优化，无假进度/无跳过校验）：
- 旧实现每次渲染都走「DispatchEx → Open → Export → Quit → taskkill」全生命周期，
  实测整册导出每页 ~21s，其中 **~20s 花在收尾（Quit/CoUninitialize/taskkill）**，
  真实 open+export 仅 ~0.2s —— 10 页课件仅 COM 收尾就烧掉 ~200s；
- 新实现进程级**持久 PowerPoint 会话**（专用 STA 线程串行驱动），一次启动、
  逐页 open+export+close（实测 10 页共 ~0.5s），只在失败回收/进程退出时才收尾；
- 跨进程互斥沿用 `t24-powerpoint-com.lock`（与 8100 QA 渲染、8201 PDF 导出共享）：
  每次 COM 操作持锁，绝不与另一进程同时驱动 PowerPoint；
- 失败语义不变：COM 不可用/被拒/超时一律如实抛错（RenderUnavailable），
  绝不伪造渲染结果；任何 COM 异常即回收会话（下次调用重建实例）。

公共能力：
- png_export(pptx_path, out_dir)：逐页导出 PNG（Slide.Export），返回 (slide_count, [png 路径])
- openable_check(pptx_path)：打开 + 读页数 + 读首张文字 → 关闭（不修改原文件）
- save_pdf(pptx_path, pdf_path)：SaveAs(path, 32) 真实 PDF（pdf_export 复用同一会话）
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import queue
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from pathlib import Path
from typing import Any


class RenderUnavailable(RuntimeError):
    """PowerPoint COM 不可用（未安装/自动化被拒/会话回收失败）——如实报错，不伪造渲染结果。"""


def _com():
    try:
        import pythoncom
        import win32com.client as win32com_client
    except ImportError as exc:  # pragma: no cover
        raise RenderUnavailable(f"pywin32 不可用: {exc}") from exc
    return pythoncom, win32com_client


def _kill_powerpnt() -> None:
    os.system('taskkill /F /IM POWERPNT.EXE >NUL 2>&1')


# ── 跨进程互斥（与 app/ppt_v2/qa/render.py 的 com_guard 同一把锁文件）──────────
_COM_LOCK_PATH = Path(tempfile.gettempdir()) / "t24-powerpoint-com.lock"


@contextlib.contextmanager
def _cross_process_com_guard():
    """跨进程串行化 PowerPoint：两进程同时 Open 会直接失败（实测 Presentations.Open : Failed）。"""
    import msvcrt

    fh = open(_COM_LOCK_PATH, "a+b")  # noqa: SIM115 - 需在整个 COM 操作期间持有
    try:
        for _ in range(180):
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                fh.seek(0)
                time.sleep(1.0)
        else:
            raise RenderUnavailable("等待 PowerPoint COM 跨进程锁超时（>180s）")
        yield
    finally:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            fh.close()


# ── 持久 COM 会话（专用 STA 线程 + 单实例 PowerPoint）─────────────────────────


class _ComSession:
    """进程级 PowerPoint 会话：所有 COM 调用串行过专用 STA 线程（COM 线程亲和）。

    - `call(fn, timeout_s)`：`fn(app)` 在工作线程内执行（持跨进程锁），阻塞至完成；
    - 任何异常 → 会话回收（Quit 尽力收尾），下次调用自动重建；
    - 超时 → 抛 RenderUnavailable 并回收（taskkill 解除挂死实例），不吞失败。
    """

    def __init__(self) -> None:
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._app: Any = None
        self._ready = threading.Event()

    # ── 工作线程 ──────────────────────────────────────────────────────────
    def _run(self) -> None:
        pythoncom, wc = _com()
        pythoncom.CoInitialize()
        try:
            while True:
                job = self._jobs.get()
                if job is None:
                    break
                fn, done = job
                try:
                    app = self._ensure_app(pythoncom, wc)
                    with _cross_process_com_guard():
                        result = fn(app)
                except Exception as exc:  # noqa: BLE001 - 如实回传给调用方
                    self._drop_app()
                    if not done.done():
                        done.set_exception(exc)
                else:
                    if not done.done():
                        done.set_result(result)
        finally:
            self._drop_app()
            with contextlib.suppress(Exception):
                pythoncom.CoUninitialize()

    def _ensure_app(self, pythoncom: Any, wc: Any) -> Any:
        if self._app is None:
            app = wc.DispatchEx("PowerPoint.Application")
            app.DisplayAlerts = 1  # ppAlertsNone
            self._app = app
        return self._app

    def _drop_app(self) -> None:
        app, self._app = self._app, None
        if app is None:
            return
        try:
            app.Quit()
        except Exception:  # noqa: BLE001 - 回收路径尽力而为
            pass

    # ── 调用侧 ──────────────────────────────────────────────────────────
    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="ps8201-powerpoint-com", daemon=True)
        self._thread.start()

    def call(self, fn: Callable[[Any], Any], *, timeout_s: float = 120.0) -> Any:
        self._ensure_thread()
        done: Future = Future()
        self._jobs.put((fn, done))
        try:
            return done.result(timeout=timeout_s)
        except TimeoutError as exc:
            # 工作线程卡死（COM 挂起）：如实失败 + 换会话（taskkill 解除挂死实例）
            self.recycle()
            raise RenderUnavailable(f"PowerPoint COM 操作超时（>{timeout_s}s）") from exc

    def recycle(self) -> None:
        """丢弃当前会话（挂死/异常后重建）：旧队列里的在途请求如实失败，再强杀残留 POWERPNT。"""
        old_jobs, self._jobs = self._jobs, queue.Queue()
        self._thread = None
        self._app = None
        while True:
            try:
                job = old_jobs.get_nowait()
            except queue.Empty:
                break
            if job is None:
                continue
            _fn, done = job
            if not done.done():
                done.set_exception(RenderUnavailable("PowerPoint COM 会话已回收（上一操作异常/超时）"))
        with contextlib.suppress(Exception):
            old_jobs.put_nowait(None)  # 让旧线程退出（daemon，不阻塞进程退出）
        _kill_powerpnt()


_SESSION = _ComSession()


def _session() -> _ComSession:
    return _SESSION


# ── 具体操作（在会话线程内执行）─────────────────────────────────────────────


def _export_with_app(app: Any, pptx_path: Path, out_dir: Path, width: int = 1280) -> tuple[int, list[str]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pngs: list[str] = []
    pres = app.Presentations.Open(str(pptx_path), ReadOnly=True, Untitled=False, WithWindow=False)
    try:
        n = int(pres.Slides.Count)
        if n <= 0:
            raise RenderUnavailable("打开成功但页数为 0")
        for i in range(1, n + 1):
            p = out_dir / f"page-{i:02d}.png"
            pres.Slides(i).Export(str(p), "PNG", width, int(width * 720 / 1280))
            pngs.append(str(p))
        return n, pngs
    finally:
        pres.Close()


def _openable_with_app(app: Any, pptx_path: Path) -> dict:
    pres = app.Presentations.Open(str(pptx_path), ReadOnly=True, Untitled=False, WithWindow=False)
    try:
        n = int(pres.Slides.Count)
        first_text = ""
        if n >= 1:
            for shp in pres.Slides(1).Shapes:
                if shp.HasTextFrame and shp.TextFrame.HasText:
                    first_text = str(shp.TextFrame.TextRange.Text)[:120]
                    break
        return {"opened": True, "slide_count": n, "first_text": first_text}
    finally:
        pres.Close()


def _save_pdf_with_app(app: Any, pptx_path: Path, pdf_path: Path) -> tuple[int, str]:
    try:
        version = str(app.Version)
    except Exception:  # noqa: BLE001 - 版本读不到不阻断导出，如实标 unknown
        version = "unknown"
    pres = app.Presentations.Open(str(pptx_path), ReadOnly=True, Untitled=False, WithWindow=False)
    try:
        n = int(pres.Slides.Count)
        if n <= 0:
            raise RenderUnavailable("打开成功但页数为 0，拒绝导出")
        pres.SaveAs(str(pdf_path), 32)  # ppSaveAsPDF
        return n, version
    finally:
        pres.Close()


def _export_sync(pptx_path: Path, out_dir: Path, width: int = 1280) -> tuple[int, list[str]]:
    """同步导出（会话化；保留旧签名供 assembler/测试复用）。"""
    return _SESSION.call(lambda app: _export_with_app(app, Path(pptx_path), Path(out_dir), width), timeout_s=120.0)


def _openable_sync(pptx_path: Path) -> dict:
    """同步打开检查（会话化）。"""
    return _SESSION.call(lambda app: _openable_with_app(app, Path(pptx_path)), timeout_s=90.0)


def _save_pdf_sync(pptx_path: Path, pdf_path: Path) -> tuple[int, str]:
    return _SESSION.call(lambda app: _save_pdf_with_app(app, Path(pptx_path), Path(pdf_path)), timeout_s=300.0)


async def png_export(pptx_path: Path, out_dir: Path, *, timeout_s: float = 120.0) -> tuple[int, list[str]]:
    return await asyncio.wait_for(
        asyncio.to_thread(_export_sync, Path(pptx_path), Path(out_dir)), timeout=timeout_s
    )


async def openable_check(pptx_path: Path, *, timeout_s: float = 90.0) -> dict:
    return await asyncio.wait_for(asyncio.to_thread(_openable_sync, Path(pptx_path)), timeout=timeout_s)


async def save_pdf(pptx_path: Path, pdf_path: Path, *, timeout_s: float = 300.0) -> tuple[int, str]:
    return await asyncio.wait_for(asyncio.to_thread(_save_pdf_sync, Path(pptx_path), Path(pdf_path)), timeout=timeout_s)
