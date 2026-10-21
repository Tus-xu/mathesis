"""RV14 · 编译产物金丝雀扫描（detect 最后一层）。

覆盖 `core/export/compiler.py` 的 `_scan_pdf_for_leak` /
`_assert_no_output_leak` 与 `DockerTexCompiler.compile` 的产物闸：
- 明文 / Flate 压缩流内的金丝雀都能命中；
- 正常内容（含真实 fixture PDF）**不误伤**；
- 泄漏产物 → `EXPORT_OUTPUT_LEAK_SUSPECT` 且**不返回** PDF。
"""

from __future__ import annotations

import zlib
from pathlib import Path

import pytest

from research_vnext.core.export.compiler import (
    CompileError,
    CompileResult,
    DockerTexCompiler,
    _scan_pdf_for_leak,
)

# 仓库内真实 PDF（用于证明正常产物无假阳性）
_REAL_PDF = Path(__file__).resolve().parents[2] / "fixtures" / "rv05-sample.pdf"


def _fake_pdf(content: bytes, *, compress: bool = False) -> bytes:
    """构造“PDF 形状”的字节串，把 `content` 放进一个内容流（可 Flate 压缩）。"""
    stream = zlib.compress(content) if compress else content
    return (
        b"%PDF-1.5\n1 0 obj<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
        + stream + b"\nendstream\nendobj\ntrailer<<>>\n%%EOF\n"
    )


# --------------------------------------------------------------------------
# 扫描内核：命中 / 不误伤
# --------------------------------------------------------------------------


def test_plaintext_canary_detected(tmp_path: Path) -> None:
    p = tmp_path / "main.pdf"
    p.write_bytes(_fake_pdf(b"root:x:0:0:root:/root:/bin/bash"))
    assert "root:x:0:0" in _scan_pdf_for_leak(p)


def test_flate_compressed_stream_canary_detected(tmp_path: Path) -> None:
    # 内容流被 Flate 压缩：正则/原始字节直接搜不到，必须解压后才命中。
    p = tmp_path / "main.pdf"
    p.write_bytes(_fake_pdf(b"daemon:x:1:1:daemon:/usr/sbin:/usr/sbin/nologin", compress=True))
    assert "daemon:x:1:1" in _scan_pdf_for_leak(p)


def test_bin_sh_and_etc_path_detected(tmp_path: Path) -> None:
    p = tmp_path / "main.pdf"
    p.write_bytes(_fake_pdf(b"look at /etc/hosts and run /bin/sh"))
    hits = _scan_pdf_for_leak(p)
    assert "/bin/sh" in hits
    assert "/etc/hosts" in hits


def test_os_release_markers_detected(tmp_path: Path) -> None:
    p = tmp_path / "main.pdf"
    p.write_bytes(_fake_pdf(b"DISTRIB_ID=Ubuntu\nHOSTNAME=box\n/etc/os-release\n"))
    hits = _scan_pdf_for_leak(p)
    assert {"DISTRIB_ID=", "HOSTNAME=", "/etc/os-release"} <= set(hits)


def test_clean_content_has_no_false_positive(tmp_path: Path) -> None:
    p = tmp_path / "main.pdf"
    p.write_bytes(_fake_pdf(b"Normal paper. Inline $x^{2}-4x+4$ and $a^2+b^2=c^2$."))
    assert _scan_pdf_for_leak(p) == []


def test_real_fixture_pdf_has_no_false_positive() -> None:
    """对仓库内真实 PDF 扫描必须为空 —— 证明不误伤正常产物。"""
    assert _REAL_PDF.exists(), f"missing fixture: {_REAL_PDF}"
    assert _scan_pdf_for_leak(_REAL_PDF) == []


# --------------------------------------------------------------------------
# DockerTexCompiler.compile 产物闸（注入 stub runner，确定性，不赌 docker）
# --------------------------------------------------------------------------


class _FakeRunner:
    """写出指定 PDF 字节的 stub runner（绕过真实 docker）。"""

    def __init__(self, pdf_bytes: bytes) -> None:
        self._pdf = pdf_bytes

    def __call__(self, argv, *, workdir, timeout_seconds, env=None):  # noqa: ANN001
        (Path(workdir) / "main.pdf").write_bytes(self._pdf)
        return CompileResult(returncode=0, log="ok", pages=1)


def test_docker_compiler_rejects_leaking_output(tmp_path: Path) -> None:
    c = DockerTexCompiler(timeout_seconds=5, max_output_bytes=10**6)
    c._runner = _FakeRunner(_fake_pdf(b"root:x:0:0:root:/root:/bin/bash"))  # noqa: SLF001
    with pytest.raises(CompileError) as ei:
        c.compile(workdir=tmp_path, passes=1)
    assert ei.value.code == "EXPORT_OUTPUT_LEAK_SUSPECT"


def test_docker_compiler_rejects_compressed_leak(tmp_path: Path) -> None:
    c = DockerTexCompiler(timeout_seconds=5, max_output_bytes=10**6)
    c._runner = _FakeRunner(_fake_pdf(b"root:x:0:0", compress=True))  # noqa: SLF001
    with pytest.raises(CompileError) as ei:
        c.compile(workdir=tmp_path, passes=1)
    assert ei.value.code == "EXPORT_OUTPUT_LEAK_SUSPECT"


def test_docker_compiler_deletes_leaking_pdf(tmp_path: Path) -> None:
    """命中即删（第二重确保）：泄漏产物不得留在磁盘上，不依赖调用方 finally。"""
    c = DockerTexCompiler(timeout_seconds=5, max_output_bytes=10**6)
    c._runner = _FakeRunner(_fake_pdf(b"root:x:0:0:root:/root:/bin/bash"))  # noqa: SLF001
    with pytest.raises(CompileError) as ei:
        c.compile(workdir=tmp_path, passes=1)
    assert ei.value.code == "EXPORT_OUTPUT_LEAK_SUSPECT"
    assert not (tmp_path / "main.pdf").exists(), "泄漏产物必须被显式删除"


def test_docker_compiler_accepts_clean_output(tmp_path: Path) -> None:
    c = DockerTexCompiler(timeout_seconds=5, max_output_bytes=10**6)
    c._runner = _FakeRunner(_fake_pdf(b"Normal paper, no canaries."))  # noqa: SLF001
    res = c.compile(workdir=tmp_path, passes=1)
    assert res.returncode == 0
    assert res.pages == 1
