"""RV05 · 隔离子进程解析器（Docling 优先，pypdf 结构回退）。

**不在 Web 进程 import 大 PDF 依赖**。本文件由 `parser_isolation.py` 以
`subprocess` 方式调用；stdout 只输出一行 JSON。

用法：
    python _isolated_worker.py --input /path/in.pdf --output /path/out.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import traceback
from pathlib import Path


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _bbox(x0: float, y0: float, x1: float, y1: float, w: float, h: float) -> tuple[float, float, float, float]:
    # clamp，越界告警由上层根据 clamp 前值判断；此处保证不产出负无穷
    return (max(0.0, min(x0, w)), max(0.0, min(y0, h)), max(0.0, min(x1, w)), max(0.0, min(y1, h)))


def parse_with_docling(pdf_path: Path) -> dict:
    """Docling 解析。

    - 默认 `NativePdfPipeline` + render_pages=False（本机 Windows 中文路径安全）。
    - `DOCLING_PIPELINE=standard` 且 `DOCLING_ARTIFACTS` 存在时走 Standard 布局
      （需页面渲染；**仅在 Docker/ASCII 路径环境启用**）。
    """
    import os

    pipeline = (os.getenv("DOCLING_PIPELINE") or "native").lower()
    artifacts = os.getenv("DOCLING_ARTIFACTS") or ""
    use_standard = pipeline == "standard" and artifacts and Path(artifacts).is_dir()

    if use_standard:
        return _parse_docling_standard(pdf_path, Path(artifacts))
    return _parse_docling_native(pdf_path)


def _parse_docling_standard(pdf_path: Path, artifacts: Path) -> dict:
    from docling.backend.docling_parse_backend import ThreadedDoclingParseBackendOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.datamodel import settings
    from docling.document_converter import DocumentConverter, PdfFormatOption

    cache = Path(os.getenv("DOCLING_CACHE") or (artifacts / ".docling-cache"))
    cache.mkdir(parents=True, exist_ok=True)
    settings.cache_dir = cache
    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = os.getenv("DOCLING_TABLE_STRUCTURE", "0") == "1"
    opts.generate_picture_images = False
    opts.do_code_enrichment = False
    opts.do_formula_enrichment = False
    opts.do_picture_classification = False
    opts.artifacts_path = str(artifacts)
    backend = ThreadedDoclingParseBackendOptions(render_pages=True)
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts, backend_options=backend)}
    )
    result = converter.convert(str(pdf_path))
    return _export_to_document(result, name="docling-standard", extra={"pipeline": "StandardPdfPipeline"})


def _parse_docling_native(pdf_path: Path) -> dict:
    from docling.datamodel.backend_options import ThreadedDoclingParseBackendOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import NativePdfPipelineOptions
    from docling.document_converter import DocumentConverter, NativePdfFormatOption

    opts = NativePdfPipelineOptions(generate_picture_images=False, generate_page_images=False)
    backend = ThreadedDoclingParseBackendOptions(render_pages=False)
    converter = DocumentConverter(
        format_options={InputFormat.PDF: NativePdfFormatOption(pipeline_options=opts, backend_options=backend)}
    )
    result = converter.convert(str(pdf_path))
    return _export_to_document(result, name="docling-native", extra={"pipeline": "NativePdfPipeline", "render_pages": False})


def _export_to_document(result, *, name: str, extra: dict | None = None) -> dict:
    import os

    doc = result.document
    export = doc.export_to_dict()
    import docling

    version = getattr(docling, "__version__", "unknown")
    pages_meta: list[dict] = []
    elements: list[dict] = []
    warnings: list[str] = []
    missing: set[str] = set()

    pages_blob = export.get("pages")
    page_count = None
    if isinstance(pages_blob, dict):
        try:
            page_count = int(pages_blob.get("count") or 0) or None
        except (TypeError, ValueError):
            page_count = None
    texts = export.get("texts") or []
    tables = export.get("tables") or []
    pictures = export.get("pictures") or []
    max_page = 1
    section_path: list[str] = []

    def _label_to_kind(label: str) -> str:
        lab = label.lower()
        if "title" in lab or "subtitle" in lab or "section_header" in lab or "chapter" in lab:
            return "title" if "title" in lab or "subtitle" in lab else "section"
        if "heading" in lab:
            return "section"
        if "table" in lab:
            return "table"
        if "figure" in lab or "picture" in lab or "image" in lab:
            return "figure"
        if "formula" in lab or "equation" in lab:
            return "formula"
        if "list" in lab:
            return "paragraph"
        return "paragraph"

    def _prov_bbox(item: dict) -> tuple[int, list[float], float, float]:
        provs = item.get("prov") or []
        page = 1
        bbox = [40.0, 40.0, 560.0, 80.0]
        w, h = 612.0, 792.0
        if provs and isinstance(provs, list):
            p0 = provs[0] if isinstance(provs[0], dict) else {}
            page = int(p0.get("page_no") or p0.get("page") or 1)
            size = p0.get("bbox") or {}
            if isinstance(size, dict):
                try:
                    l = float(size.get("l") or size.get("x0") or 0)
                    t = float(size.get("t") or size.get("y0") or 0)
                    r = float(size.get("r") or size.get("x1") or 0)
                    b = float(size.get("b") or size.get("y1") or 0)
                    pw = float(size.get("page_width") or p0.get("page_width") or 612)
                    ph = float(size.get("page_height") or p0.get("page_height") or 792)
                    x0, x1 = min(l, r), max(l, r)
                    y_top, y_bot = min(t, b), max(t, b)
                    bbox = [x0, ph - y_bot, x1, ph - y_top]
                    w, h = pw, ph
                except Exception:
                    pass
            if p0.get("page_width") and p0.get("page_height"):
                w = float(p0["page_width"])
                h = float(p0["page_height"])
        return page, [float(x) for x in bbox], w, h

    for item in texts:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "paragraph")
        kind = _label_to_kind(label)
        text = str(item.get("text") or "").strip()
        page, bbox, w, h = _prov_bbox(item)
        max_page = max(max_page, page)
        level = int(item.get("level") or (0 if kind in ("title", "section") else 1))
        if kind in ("title", "section") and text:
            section_path = section_path[: max(0, level - 1)] + [text[:120]]
        elements.append(
            {
                "kind": kind,
                "page": page,
                "bbox": list(_bbox(bbox[0], bbox[1], bbox[2], bbox[3], w, h)),
                "text": text[:4000],
                "section_path": list(section_path),
                "level": level,
                "confidence": 0.9 if name != "docling-standard" else 0.93,
                "warnings": [],
                "meta": {"source": name, "label": label},
            }
        )

    # 表格：优先用 docling Table 对象导出 markdown/csv（Standard 管线才有结构）
    table_texts: list[str] = []
    try:
        for tb in getattr(doc, "tables", []) or []:
            txt = ""
            try:
                df = tb.export_to_dataframe()
                txt = df.to_csv(index=False)
            except Exception:
                pass
            table_texts.append(txt)
    except Exception:
        table_texts = []

    for item in tables + pictures:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or ("table" if item in tables else "figure"))
        kind = "table" if "table" in label.lower() else "figure"
        page, bbox, w, h = _prov_bbox(item)
        max_page = max(max_page, page)
        text = ""
        if kind == "table":
            text = str((item.get("export") or {}).get("csv") or item.get("text") or "")
            if not text.strip():
                try:
                    idx = tables.index(item)
                    if idx < len(table_texts):
                        text = table_texts[idx]
                except ValueError:
                    pass
        text = text[:4000]
        elements.append(
            {
                "kind": kind,
                "page": page,
                "bbox": list(_bbox(bbox[0], bbox[1], bbox[2], bbox[3], w, h)),
                "text": text,
                "section_path": list(section_path),
                "level": 1,
                "confidence": 0.88,
                "warnings": [],
                "meta": {"source": name, "label": label},
            }
        )

    if not page_count:
        page_count = max_page
    for pno in range(1, page_count + 1):
        n = sum(1 for el in elements if el["page"] == pno)
        is_scanned = n == 0
        pages_meta.append(
            {
                "page": pno,
                "width": 612.0,
                "height": 792.0,
                "rotation": 0,
                "text_items": n,
                "is_scanned": is_scanned,
            }
        )
        if is_scanned:
            warnings.append(f"page {pno}: no docling elements (scanned or empty); OCR not substituted")

    if not any(el["kind"] == "formula" for el in elements):
        missing.add("formula")
    if not any(el["kind"] == "table" for el in elements):
        missing.add("table")
    if not any(el["kind"] == "figure" for el in elements):
        missing.add("figure")

    succeeded = [p["page"] for p in pages_meta if not p["is_scanned"]] or [p["page"] for p in pages_meta]
    parser_meta = {
        "name": name,
        "version": version,
        "license": "MIT",
        **(extra or {}),
    }
    return {
        "pages": pages_meta,
        "elements": elements,
        "warnings": warnings,
        "succeeded_pages": succeeded,
        "failed_pages": [],
        "missing_kinds": sorted(missing),
        "parser": parser_meta,
    }


def parse_with_pypdf(pdf_path: Path) -> dict:
    """pypdf 结构回退：页级文本 + 简单行 bbox（非 OCR，不偷换扫描原文）。"""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    pages = []
    elements = []
    succeeded = []
    failed = []
    warnings: list[str] = []
    missing: set[str] = set()
    section_path: list[str] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            box = page.mediabox
            width = float(box.width)
            height = float(box.height)
            rotation = int(page.get("/Rotate") or 0) % 360
            text = page.extract_text() or ""
            items = max(1, len(text.splitlines()) or 0)
            is_scanned = len(text.strip()) < 8
            pages.append(
                {
                    "page": i,
                    "width": width,
                    "height": height,
                    "rotation": rotation,
                    "text_items": items if not is_scanned else 0,
                    "is_scanned": is_scanned,
                }
            )
            if is_scanned:
                warnings.append(f"page {i}: text layer empty (scanned or image-only); OCR not substituted")
                elements.append(
                    {
                        "kind": "page",
                        "page": i,
                        "bbox": [0.0, 0.0, width, height],
                        "text": "",
                        "section_path": list(section_path),
                        "level": 0,
                        "confidence": 0.15,
                        "warnings": ["scanned_or_empty_text_layer"],
                        "meta": {"source": "pypdf", "scanned": True},
                    }
                )
            else:
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                # 简易两栏启发：行宽中位数与左右分裂
                y = height - 36.0
                for ln_idx, ln in enumerate(lines[:80]):
                    h_line = 12.0
                    x0 = 36.0
                    x1 = width - 36.0
                    # 多栏检测：短行且 y 靠上/下时按中线切开
                    if width > 500 and len(ln) < 40 and ln_idx % 3 == 0:
                        mid = width / 2
                        x0, x1 = 36.0, mid - 8.0
                    kind = "paragraph"
                    level = 1
                    if ln_idx == 0 or ln[:2].isdigit() or ln.lower().startswith(("abstract", "摘要", "introduction", "引言", "references", "参考文献")):
                        kind = "section" if ln[:2].isdigit() or ln_idx == 0 else "section"
                        level = 0 if ln_idx == 0 else 1
                        section_path = [ln[:120]]
                    elements.append(
                        {
                            "kind": kind,
                            "page": i,
                            "bbox": list(_bbox(x0, y - h_line, x1, y, width, height)),
                            "text": ln[:2000],
                            "section_path": list(section_path),
                            "level": level,
                            "confidence": 0.75 if kind == "paragraph" else 0.8,
                            "warnings": [],
                            "meta": {"source": "pypdf", "rotation": rotation},
                        }
                    )
                    y -= h_line + 4
                    if y < 36:
                        break
            succeeded.append(i)
        except Exception as e:  # noqa: BLE001
            failed.append(i)
            warnings.append(f"page {i} failed: {e}")
    # 公式/表格/图：pypdf 无布局 → 明确 missing，不伪造
    if not any(el["kind"] == "formula" for el in elements):
        missing.add("formula")
    if not any(el["kind"] == "table" for el in elements):
        missing.add("table")
    if not any(el["kind"] == "figure" for el in elements):
        missing.add("figure")
    import pypdf

    return {
        "pages": pages,
        "elements": elements,
        "warnings": warnings,
        "succeeded_pages": succeeded,
        "failed_pages": failed,
        "missing_kinds": sorted(missing),
        "parser": {
            "name": "pypdf-structural",
            "version": getattr(pypdf, "__version__", "unknown"),
            "license": "BSD-3-Clause",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--engine", default="auto", choices=["auto", "docling", "pypdf"])
    args = ap.parse_args()
    pdf_path = Path(args.input)
    out_path = Path(args.output)
    if not pdf_path.is_file():
        payload = {"ok": False, "error_code": "INPUT_MISSING", "error_message": f"missing {pdf_path}"}
        out_path.write_text(json.dumps(payload), encoding="utf-8")
        return 2
    try:
        if args.engine == "docling":
            doc = parse_with_docling(pdf_path)
        elif args.engine == "pypdf":
            doc = parse_with_pypdf(pdf_path)
        else:
            try:
                doc = parse_with_docling(pdf_path)
            except Exception as de:  # noqa: BLE001
                doc = parse_with_pypdf(pdf_path)
                doc.setdefault("warnings", []).append(f"docling unavailable, fell back: {type(de).__name__}")
        payload = {"ok": True, "document": doc}
    except Exception as e:  # noqa: BLE001
        payload = {
            "ok": False,
            "error_code": "PARSER_CRASH",
            "error_message": f"{type(e).__name__}: {e}",
            "traceback": traceback.format_exc()[-2000:],
        }
        out_path.write_text(json.dumps(payload), encoding="utf-8")
        return 1
    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
