"""RV14 · 验收链专用测试（门控：仅当设置 RV14_ACCEPTANCE_DIR 时运行）。

产出（写到 $RV14_ACCEPTANCE_DIR）：
- paper.md / paper.tex / paper.pdf   —— 真实中文数学 PaperSpec 的三格式产物
- manifest.json                       —— rv14.manifest.v1 原文
- PDF-CONTACT-SHEET.png               —— PDF 全页面缩略拼图（视觉 QA）
- hashes.json                         —— ARTIFACT-HASHES.md 的机器数据源

常规回归（未设 RV14_ACCEPTANCE_DIR）自动跳过，无副作用。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest

OUT_DIR = os.environ.get("RV14_ACCEPTANCE_DIR", "")
pytestmark = pytest.mark.skipif(
    not OUT_DIR, reason="验收专用：需显式设置 RV14_ACCEPTANCE_DIR"
)

from tests.integration.conftest import auth_headers  # noqa: E402
from tests.integration.test_rv09_intake_to_maps import sess, stub_llm  # noqa: E402,F401,F811
from tests.integration.test_rv14_paper_export import (  # noqa: E402,F401
    _export,
    _job,
    _run_job,
    _setup_paper,
)

BASE = "/api/research-v1"


def test_acceptance_export_real_chinese_paper(client, sess, stub_llm) -> None:  # noqa: F811
    out = Path(OUT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    s = sess

    _setup_paper(client, s)
    r = _export(client, s, formats=["markdown", "latex", "pdf"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deduped"] is False
    job_id = body["jobId"]

    result = _run_job(job_id)
    assert result["status"] == "SUCCEEDED", result

    job = _job(job_id)
    artifact_id = job.result["artifactId"]
    manifest = job.result["manifest"]

    # ---- 三格式真实落盘（走完整下载授权链路：mint → token → download）----
    hashes: dict[str, dict] = {"artifactId": artifact_id, "jobId": job_id, "files": {}}
    for fmt, fname in (("markdown", "paper.md"), ("latex", "paper.tex"), ("pdf", "paper.pdf")):
        link = client.post(
            f"{BASE}/paper-exports/{artifact_id}/download-link",
            json={"format": fmt},
            headers=auth_headers(s["ctx"]),
        )
        assert link.status_code == 200, link.text
        token = link.json()["token"]
        got = client.get(
            f"{BASE}/paper-exports/{artifact_id}/download",
            params={"token": token, "format": fmt},
        )
        assert got.status_code == 200, got.text
        (out / fname).write_bytes(got.content)
        sha = hashlib.sha256(got.content).hexdigest()
        assert sha == manifest["outputs"][fname]["sha256"], f"{fname} 与 manifest 哈希不一致"
        hashes["files"][fname] = {
            "sha256": sha,
            "sizeBytes": len(got.content),
            **({"pages": manifest["outputs"][fname].get("pages")} if fmt == "pdf" else {}),
        }

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    hashes["manifest"] = {
        "manifestVersion": manifest["manifestVersion"],
        "serializer": manifest["serializer"],
        "template": manifest["template"],
        "compiler": manifest["compiler"],
        "cslStyle": manifest["cslStyle"],
    }

    # ---- PDF 真实性核验 + 页面渲染 contact sheet ----
    pdf_bytes = (out / "paper.pdf").read_bytes()
    assert pdf_bytes[:4] == b"%PDF" and pdf_bytes.rstrip().endswith(b"%%EOF")

    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(out / "paper.pdf"))
    n = len(doc)
    assert n >= 1
    text = "".join(doc[i].get_textpage().get_text_range() for i in range(n))
    for needle in ("稳态分析", "Little", "引论"):
        assert needle in text, f"PDF 文本缺少 {needle!r}"
    hashes["pdfTextChecks"] = ["稳态分析", "Little", "引论"]

    try:
        from PIL import Image, ImageDraw

        scale = 1.5
        thumbs = []
        for i in range(n):
            bmp = doc[i].render(scale=scale)
            thumbs.append(bmp.to_pil())
        cols = min(3, n)
        rows = (n + cols - 1) // cols
        tw = max(t.width for t in thumbs)
        th = max(t.height for t in thumbs)
        pad, label_h = 12, 26
        sheet = Image.new(
            "RGB",
            (cols * (tw + pad) + pad, rows * (th + label_h + pad) + pad),
            (240, 240, 244),
        )
        draw = ImageDraw.Draw(sheet)
        for i, t in enumerate(thumbs):
            x = pad + (i % cols) * (tw + pad)
            y = pad + (i // cols) * (th + label_h + pad)
            sheet.paste(t, (x, y))
            draw.rectangle([x, y, x + t.width - 1, y + t.height - 1], outline=(160, 160, 170))
            draw.text((x + 4, y + t.height + 5), f"page {i + 1}/{n} · rv14 paper.pdf",
                      fill=(60, 60, 70))
        sheet.save(out / "PDF-CONTACT-SHEET.png")
        hashes["contactSheet"] = f"PDF-CONTACT-SHEET.png ({n} page)"
    except ImportError:  # noqa: SIM105 - pillow 缺失时 contact sheet 跳过（哈希仍可验证）
        hashes["contactSheet"] = "SKIPPED (pillow unavailable)"

    (out / "hashes.json").write_text(
        json.dumps(hashes, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    print("RV14-ACCEPTANCE-OK", artifact_id, "pages=", n)
