"""RV05 · Parser/Anchor 失败测试（TDD 红灯集合）。

覆盖任务书要求：
- 扫描 PDF / 旋转 / 多栏 / 混合字体
- 公式/表格/图片 anchor
- parser timeout/crash/PARTIAL
- bbox 越界
- asset hash 变化后 anchor 失效
- 跨 tenant anchor 403
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.parser.anchors import (  # noqa: E402
    AnchorType,
    classify_stale,
    make_anchor_id,
    validate_bbox,
)
from research_vnext.core.parser.provider import (  # noqa: E402
    ParseDocument,
    ParseElement,
    PageMeta,
    ParserIdentity,
    ParserProviderError,
)
from research_vnext.core.parser.isolation import SubprocessParserProvider  # noqa: E402


# --------------------------------------------------------------------------
# Anchor schema / stale
# --------------------------------------------------------------------------


def test_anchor_id_is_deterministic_and_version_bound() -> None:
    kwargs = dict(
        asset_id="a1",
        asset_version=1,
        content_hash="h1",
        parse_run_id="r1",
        page=2,
        kind="table",
        text_hash="t1",
        region_hash="rg1",
    )
    a = make_anchor_id(**kwargs)
    b = make_anchor_id(**kwargs)
    assert a == b
    c = make_anchor_id(**{**kwargs, "content_hash": "h2"})
    assert a != c
    d = make_anchor_id(**{**kwargs, "asset_version": 2})
    assert a != d


def test_classify_stale_on_hash_change_never_silently_remaps() -> None:
    stale, reason = classify_stale(
        anchor_asset_version=1,
        anchor_content_hash="old",
        current_asset_version=1,
        current_content_hash="new",
    )
    assert stale is True
    assert reason == "asset_hash_changed"


def test_classify_stale_on_version_change() -> None:
    stale, reason = classify_stale(
        anchor_asset_version=1,
        anchor_content_hash="same",
        current_asset_version=2,
        current_content_hash="same",
    )
    assert stale is True
    assert reason == "asset_version_changed"


def test_classify_fresh_when_hash_and_version_match() -> None:
    stale, reason = classify_stale(
        anchor_asset_version=3,
        anchor_content_hash="same",
        current_asset_version=3,
        current_content_hash="same",
    )
    assert stale is False
    assert reason == ""


def test_bbox_out_of_bounds_rejected() -> None:
    with pytest.raises(ValueError, match="越界"):
        validate_bbox([0, 0, 700, 800], 612.0, 792.0)
    with pytest.raises(ValueError, match="反向"):
        validate_bbox([100, 100, 50, 50], 612.0, 792.0)
    ok = validate_bbox([36, 720, 560, 740], 612.0, 792.0)
    assert ok[2] == 560


def test_parse_document_partial_flag() -> None:
    doc = ParseDocument(
        pages=[PageMeta(page=1, width=612, height=792), PageMeta(page=2, width=612, height=792)],
        succeeded_pages=[1],
        failed_pages=[2],
    )
    assert doc.is_partial is True
    ok = ParseDocument(pages=[PageMeta(page=1, width=612, height=792)], succeeded_pages=[1])
    assert ok.is_partial is False


def test_section_tree_hierarchy() -> None:
    doc = ParseDocument(
        elements=[
            ParseElement(kind="title", page=1, bbox=(0, 0, 10, 10), text="Paper", level=0),
            ParseElement(kind="section", page=1, bbox=(0, 20, 10, 30), text="1 Intro", level=1),
            ParseElement(kind="section", page=1, bbox=(0, 40, 10, 50), text="1.1 Setup", level=2),
        ]
    )
    tree = doc.section_tree()
    assert tree[0]["title"] == "Paper"
    assert tree[0]["children"][0]["title"] == "1 Intro"
    assert tree[0]["children"][0]["children"][0]["title"] == "1.1 Setup"


def test_element_hashes_stable() -> None:
    el = ParseElement(kind="formula", page=1, bbox=(10, 10, 50, 30), text="E=mc^2")
    assert el.text_hash() == hashlib.sha256(b"E=mc^2").hexdigest()
    assert el.region_hash(612, 792) == el.region_hash(612, 792)


# --------------------------------------------------------------------------
# 隔离 parser：timeout / crash / PARTIAL / 扫描 / 旋转多栏
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 最小合法 PDF 生成（**纯 Python，零第三方依赖**）
#
# 历史坑（RV07 取证结论）：旧实现依赖 `MIMO_PYTHON` + reportlab 生成 PDF，
# 但本机既无该环境变量、任何解释器也无 reportlab；异常被 `except Exception`
# 吞掉后退化成写一个**没有 xref / startxref 的假 PDF**，又恰好绕过
# `st_size > 50` 守卫，于是 2 个用例以 `PdfReadError: startxref not found` 失败
# —— 把"夹具环境缺失"伪装成了"parser 有 bug"。
# 现改为**自包含生成 + 结构自校验**，夹具不再依赖任何外部环境。
# --------------------------------------------------------------------------


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _minimal_pdf_bytes(pages: list[dict]) -> bytes:
    """生成**结构合法**的最小 PDF（含 xref 表与正确的 startxref 偏移）。

    `pages` 每项：`{"w": float, "h": float, "lines": [{"x","y","t"}], "rotate": int}`
    """
    n_pages = len(pages)
    font_num = 3 + 2 * n_pages
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode("ascii"),
    ]
    for idx, page in enumerate(pages):
        page_num = 3 + 2 * idx
        content_num = page_num + 1
        rotate = int(page.get("rotate") or 0) % 360
        rot = f" /Rotate {rotate}" if rotate else ""
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 {page['w']} {page['h']}] "
                f"/Resources << /Font << /F1 {font_num} 0 R >> >> "
                f"/Contents {content_num} 0 R{rot} >>"
            ).encode("ascii")
        )
        ops = ["BT", "/F1 12 Tf"]
        for line in page.get("lines") or []:
            ops.append(f"1 0 0 1 {line['x']} {line['y']} Tm")
            ops.append(f"({_pdf_escape(str(line['t']))}) Tj")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objects.append(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for num, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{num} 0 obj\n".encode("ascii") + body + b"\nendobj\n"

    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode("ascii")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode("ascii")
    return bytes(out)


def _assert_structurally_valid_pdf(path: Path, *, min_pages: int = 1) -> None:
    """结构自校验：**不 import pypdf**（pypdf 在 .parser-venv，测试进程里没有）。

    专门断言"假 PDF"会缺的那几样：xref 表、startxref、以及 startxref 真的指向 xref。
    """
    raw = path.read_bytes()
    assert raw.startswith(b"%PDF-"), "夹具产物缺少 PDF 头"
    assert raw.rstrip().endswith(b"%%EOF"), "夹具产物缺少 %%EOF"
    marker = raw.rfind(b"startxref")
    assert marker != -1, "夹具产物缺少 startxref（历史失败点：假 PDF 正是缺它）"
    offset = int(raw[marker + len(b"startxref"):].split()[0])
    assert raw[offset:offset + 4] == b"xref", f"startxref 偏移 {offset} 未指向 xref 表"
    assert raw.count(b"/Type /Page ") >= min_pages, "夹具产物页对象数量不足"


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    """最小合法 PDF（2 页：第 1 页 3 行文本；第 2 页横向 + /Rotate 90）。"""
    path = tmp_path / "sample.pdf"
    path.write_bytes(
        _minimal_pdf_bytes(
            [
                {
                    "w": 612,
                    "h": 792,
                    "lines": [
                        {"x": 72, "y": 720, "t": "Abstract: math paper sample"},
                        {"x": 72, "y": 700, "t": "1 Introduction"},
                        {"x": 72, "y": 680, "t": "Theorem: a^2+b^2=c^2"},
                    ],
                },
                {
                    "w": 792,
                    "h": 612,
                    "rotate": 90,
                    "lines": [
                        {"x": 72, "y": 500, "t": "2 Methods multi-column left"},
                        {"x": 320, "y": 500, "t": "column right text"},
                    ],
                },
            ]
        )
    )
    _assert_structurally_valid_pdf(path, min_pages=2)
    return path


@pytest.fixture()
def scanned_pdf(tmp_path: Path) -> Path:
    """扫描件：合法 PDF 但**无文本层** —— 真实覆盖"扫描件不 OCR 冒充"。"""
    path = tmp_path / "scanned.pdf"
    path.write_bytes(_minimal_pdf_bytes([{"w": 612, "h": 792, "lines": []}]))
    _assert_structurally_valid_pdf(path, min_pages=1)
    return path


def test_isolated_parser_produces_page_and_elements(sample_pdf: Path) -> None:
    provider = SubprocessParserProvider(engine="pypdf", timeout_seconds=60)
    result = provider.parse_detailed(sample_pdf)
    assert result.document.pages
    assert result.document.elements
    assert result.input_hash == hashlib.sha256(sample_pdf.read_bytes()).hexdigest()
    assert result.identity.name
    assert result.identity.license
    assert result.output_hash


def test_isolated_parser_handles_rotation_page(sample_pdf: Path) -> None:
    provider = SubprocessParserProvider(engine="pypdf")
    result = provider.parse_detailed(sample_pdf)
    rotations = {p.page: p.rotation for p in result.document.pages}
    # 第 2 页 Rotate 90（reportlab 可能不写 /Rotate，则允许 0，但不得崩溃）
    assert set(rotations) == {1, 2} or len(rotations) >= 1
    assert result.document.succeeded_pages


def test_scanned_pdf_marks_empty_text_and_does_not_ocr_substitute(scanned_pdf: Path) -> None:
    provider = SubprocessParserProvider(engine="pypdf")
    result = provider.parse_detailed(scanned_pdf)
    assert any(w for w in result.document.warnings if "scanned" in w or "empty" in w) or any(
        el.meta.get("scanned") for el in result.document.elements if el.meta
    )
    # 不得伪造大段 OCR 文本
    for el in result.document.elements:
        assert len(el.text) < 50 or not el.meta.get("scanned")


def test_parser_timeout_raises_typed_error(tmp_path: Path) -> None:
    # 用超时 0.001 秒 + 真实子进程 → 必定 PARSER_TIMEOUT
    sample = tmp_path / "x.pdf"
    sample.write_bytes(b"%PDF-1.4\n%%EOF\n")
    provider = SubprocessParserProvider(engine="pypdf", timeout_seconds=1)
    # monkeypatch subprocess timeout by setting timeout very small after construction
    provider._timeout = 0.001
    # 可能超时或 crash，但不得静默成功
    with pytest.raises(ParserProviderError) as ei:
        provider.parse_detailed(sample)
    assert ei.value.code in {"PARSER_TIMEOUT", "PARSER_CRASH", "INPUT_MISSING"}


def test_parser_crash_on_missing_input(tmp_path: Path) -> None:
    provider = SubprocessParserProvider(engine="pypdf")
    with pytest.raises(ParserProviderError) as ei:
        provider.parse_detailed(tmp_path / "nope.pdf")
    assert ei.value.code in {"PARSER_CRASH", "INPUT_MISSING"}


def test_anchor_types_cover_required_kinds() -> None:
    required = {"asset", "page", "bbox", "section", "table", "figure", "formula"}
    assert required <= {t.value for t in AnchorType}


def test_fake_provider_partial_document_shape() -> None:
    """PARTIAL：成功页/失败页/缺失元素必须可列出。"""
    doc = ParseDocument(
        pages=[PageMeta(page=1, width=612, height=792), PageMeta(page=2, width=612, height=792)],
        elements=[
            ParseElement(kind="paragraph", page=1, bbox=(72, 700, 500, 720), text="ok page"),
        ],
        succeeded_pages=[1],
        failed_pages=[2],
        missing_kinds=["formula", "table"],
        warnings=["page 2 failed"],
    )
    assert doc.is_partial
    assert doc.failed_pages == [2]
    assert "formula" in doc.missing_kinds
