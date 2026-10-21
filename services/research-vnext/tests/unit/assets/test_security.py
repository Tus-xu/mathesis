"""RV04 unit tests · upload security (failing-first targets)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from research_vnext.core.assets.security import (
    AssetRejected,
    check_archive_bomb,
    scan_upload,
    sniff_media_type,
)


def test_sniff_pdf_ignores_claimed_type(tmp_path: Path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"%PDF-1.4\n%fake")
    r = scan_upload(p, claimed_media_type="image/png")
    assert r.media_type == "application/pdf"
    assert any(f["code"] == "MIME_MISMATCH" for f in r.findings)


def test_mime_forgery_pdf_claimed_but_exe(tmp_path: Path):
    p = tmp_path / "evil.pdf"
    p.write_bytes(b"MZ\x90\x00" + b"\x00" * 64)
    with pytest.raises(AssetRejected) as ei:
        scan_upload(p, claimed_media_type="application/pdf")
    assert ei.value.code in {"MALWARE_POLICY", "MIME_FORGERY"}


def test_oversized_upload_rejected(tmp_path: Path):
    p = tmp_path / "big.bin"
    p.write_bytes(b"A" * 1000)
    with pytest.raises(AssetRejected) as ei:
        scan_upload(p, max_bytes=100)
    assert ei.value.code == "TOO_LARGE"


def test_empty_upload_rejected(tmp_path: Path):
    p = tmp_path / "empty.pdf"
    p.write_bytes(b"")
    with pytest.raises(AssetRejected) as ei:
        scan_upload(p)
    assert ei.value.code == "EMPTY_UPLOAD"


def test_compression_bomb_ratio(tmp_path: Path):
    p = tmp_path / "bomb.zip"
    # 高压缩比 zip
    with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.txt", b"0" * (2 * 1024 * 1024))
    size = p.stat().st_size
    assert size < 50 * 1024
    with pytest.raises(AssetRejected) as ei:
        check_archive_bomb(p, compressed_size=size)
    assert ei.value.code == "ARCHIVE_BOMB"


def test_hostile_archive_entry_quarantined(tmp_path: Path):
    p = tmp_path / "hostile.zip"
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("payload.pdf.exe", b"MZ not really")
        zf.writestr("readme.txt", b"hello")
    r = scan_upload(p)
    assert r.scan_status == "QUARANTINED"
    assert any(f["code"] in {"ARCHIVE_HOSTILE_ENTRY", "ARCHIVE_DOUBLE_EXT"} for f in r.findings)


def test_script_magic_rejected(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_bytes(b"#!/bin/sh\nrm -rf /\n")
    with pytest.raises(AssetRejected) as ei:
        scan_upload(p)
    assert ei.value.code == "MALWARE_POLICY"
