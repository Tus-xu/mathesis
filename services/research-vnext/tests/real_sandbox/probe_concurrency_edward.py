"""QA(Edward) 并发探针：
(1) 4 路并发「正常」编译 —— 验证工作目录隔离、无互相污染、无崩溃。
(2) N 路并发「无限循环」编译（短超时）—— 验证超时后容器是否遗留/累积。

运行：
  PYTHONPATH=. .venv/Scripts/python.exe tests/real_sandbox/probe_concurrency_edward.py normal
  PYTHONPATH=. .venv/Scripts/python.exe tests/real_sandbox/probe_concurrency_edward.py loop
"""
from __future__ import annotations

import os
import sys
import threading
import time

from research_vnext.core.xf.latex import compile_latex_sync
from research_vnext.core.xf.models import XfError


def _wrap(body: str) -> str:
    return f"\\documentclass{{article}}\n\\begin{{document}}\n{body}\n\\end{{document}}\n"


def _text(pdf: bytes) -> str:
    import tempfile
    from pathlib import Path

    import pypdfium2 as pdfium

    p = Path(tempfile.mkdtemp()) / "x.pdf"
    p.write_bytes(pdf)
    with pdfium.PdfDocument(str(p)) as doc:
        return "".join(pg.get_textpage().get_text_range() for pg in doc)


def run_normal(n: int = 4) -> None:
    results: dict[int, str] = {}

    def worker(i: int) -> None:
        marker = f"DOCMARKER{i:03d}UNIQUE"
        t0 = time.perf_counter()
        try:
            pdf, _ = compile_latex_sync(_wrap(marker), "main.tex")
            txt = _text(pdf)
            ok = marker in txt and all(
                f"DOCMARKER{j:03d}" not in txt for j in range(n) if j != i
            )
            results[i] = (
                f"OK iso={ok} bytes={len(pdf)} {time.perf_counter()-t0:.1f}s"
            )
        except XfError as e:
            results[i] = f"XfErr {e.code} {e.message[:60]}"
        except Exception as e:  # noqa: BLE001
            results[i] = f"RAISE {type(e).__name__}: {e}"

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    t0 = time.perf_counter()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    print(f"=== {n} 路并发正常编译，总耗时 {time.perf_counter()-t0:.1f}s ===")
    for i in sorted(results):
        print(f"  worker[{i}] {results[i]}")


def run_loop(n: int = 3) -> None:
    results: dict[int, str] = {}

    def worker(i: int) -> None:
        t0 = time.perf_counter()
        try:
            compile_latex_sync(_wrap(r"\loop\iftrue\repeat"), "main.tex")
            results[i] = "UNEXPECTED OK"
        except XfError as e:
            results[i] = f"{e.code} http={e.http_status} {e.message[:50]} {time.perf_counter()-t0:.1f}s"
        except Exception as e:  # noqa: BLE001
            results[i] = f"RAISE {type(e).__name__}"

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    t0 = time.perf_counter()
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    print(f"=== {n} 路并发无限循环编译（timeout={os.getenv('RESEARCH_TEX_TIMEOUT_SECONDS')}），"
          f"总耗时 {time.perf_counter()-t0:.1f}s ===")
    for i in sorted(results):
        print(f"  worker[{i}] {results[i]}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
    if mode == "loop":
        run_loop()
    else:
        run_normal()
