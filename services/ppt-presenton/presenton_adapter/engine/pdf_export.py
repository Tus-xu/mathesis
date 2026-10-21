"""PPTX → PDF 真实导出（PowerPoint COM，`SaveAs(path, 32)` = ppSaveAsPDF）。

R3d 起 COM 生命周期复用 `renderer` 的**持久 PowerPoint 会话**（专用 STA 线程 + 单实例）：
不再每次 DispatchEx/Quit/taskkill（旧实现每请求 ~20s 花在收尾）。跨进程互斥仍由
会话按操作持 `t24-powerpoint-com.lock`（与 8100 QA 渲染共享，两进程同时 Open 会失败）。

失败一律如实抛 `PdfExportUnavailable`（未安装/自动化被拒/SaveAs 失败/非 PDF 产物）——
**绝不**产出假 PDF，**绝不**用改后缀冒充。
"""
from __future__ import annotations

import asyncio
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from presenton_adapter.engine.renderer import RenderUnavailable, _session

PP_SAVE_AS_PDF = 32  # PowerPoint PpSaveAsFileType.ppSaveAsPDF（renderer._save_pdf_with_app 同口径）

# 跨进程锁文件口径与 renderer._COM_LOCK_PATH / teacher-platform qa/render.py 一致
_COM_LOCK_PATH = Path(tempfile.gettempdir()) / "t24-powerpoint-com.lock"

_SAFE_STEM_RE = re.compile(r"[^0-9A-Za-z._\u4e00-\u9fff-]")


class PdfExportUnavailable(RuntimeError):
    """PowerPoint COM 导出不可用/失败——如实报错，不伪造 PDF。"""


@dataclass(frozen=True)
class PdfExportResult:
    pdf_bytes: bytes
    page_count: int
    engine_name: str = "powerpoint-com"
    engine_version: str = "unknown"
    elapsed_ms: int = 0


def _safe_stem(file_stem: str) -> str:
    """文件名词干白名单化（禁路径分隔符/穿越，长度受控）。"""
    cleaned = _SAFE_STEM_RE.sub("_", file_stem or "").strip("._-")
    return (cleaned or "deck")[:80]


def _export_pdf_sync(pptx_path: Path, pdf_path: Path) -> tuple[int, str]:
    """同步 COM 导出（持久 PowerPoint 会话内执行）。返回 (真实页数, PowerPoint 版本)。"""
    from presenton_adapter.engine.renderer import _save_pdf_with_app

    try:
        n, version = _session().call(
            lambda app: _save_pdf_with_app(app, pptx_path, pdf_path), timeout_s=300.0
        )
    except RenderUnavailable as exc:
        raise PdfExportUnavailable(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001  COM 异常（被拒/SaveAs 失败）如实上报
        raise PdfExportUnavailable(f"{type(exc).__name__}: {exc}") from exc
    # 闸门（一）：SaveAs 之后立刻验产物，不给下游任何“看似成功的空/假文件”
    if not pdf_path.exists():
        raise PdfExportUnavailable(f"SaveAs(32) 返回但 PDF 未落盘: {pdf_path.name}")
    if pdf_path.read_bytes()[:5] != b"%PDF-":
        raise PdfExportUnavailable("SaveAs 产物缺 %PDF- 魔数（拒绝当 PDF 返回）")
    return n, version


async def export_pdf_sync(
    pptx_bytes: bytes,
    *,
    file_stem: str = "deck",
    timeout_s: float = 180.0,
) -> PdfExportResult:
    """异步入口（同步主体 `_export_pdf_sync` 经 `asyncio.to_thread` 执行，与 renderer 一致）。

    失败（COM 不可用 / 超时 / 产物非法）一律抛 `PdfExportUnavailable`，不返回任何字节。
    """
    if not pptx_bytes:
        raise PdfExportUnavailable("pptx 字节为空")
    t0 = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="ps8201-pdf-") as td:
        tmp = Path(td)
        stem = _safe_stem(file_stem)
        pptx_path = tmp / f"{stem}.pptx"
        pdf_path = tmp / f"{stem}.pdf"
        pptx_path.write_bytes(pptx_bytes)
        try:
            n, version = await asyncio.wait_for(
                asyncio.to_thread(_export_pdf_sync, pptx_path, pdf_path), timeout=timeout_s
            )
        except TimeoutError as exc:  # asyncio.wait_for 超时（COM 挂起）
            raise PdfExportUnavailable(f"PowerPoint COM 导出超时（{timeout_s}s）") from exc
        data = pdf_path.read_bytes() if pdf_path.exists() else b""
    # 闸门（二）：返回前再验一次真实返回的字节
    if not data:
        raise PdfExportUnavailable("PDF 字节为 0")
    if data[:5] != b"%PDF-":
        raise PdfExportUnavailable("返回前校验失败：产物缺 %PDF- 魔数")
    return PdfExportResult(
        pdf_bytes=data,
        page_count=n,
        engine_version=version,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )
