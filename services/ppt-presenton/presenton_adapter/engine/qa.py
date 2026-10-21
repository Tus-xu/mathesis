"""页级 QA 编排：构建 → 渲染 PNG → 指标复核。

QA 判定（全部真实证据，无占位）：
- overflow/overlap/截断：metrics 对构建期 shape 盒做独立复核（构造期 clamp 不掩盖 QA）；
- 公式：页内数学字符必须全部被 fallback 链覆盖（missing 列表为空）；
- 图形：嵌入图形字节可被 PIL 解码、非空白、且尺寸有效；
- 渲染：PNG 存在、非零、可解码、非空白（像素方差 > 阈值）。
"""
from __future__ import annotations

import io
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image, ImageStat

from presenton_adapter.engine import renderer
from presenton_adapter.engine.builder import BuiltPage, PageRequest, build_page
from presenton_adapter.engine.fonts import resolve_fonts
from presenton_adapter.engine.metrics import measure_page


@dataclass
class PageQAResult:
    ok: bool
    content_sha256: str
    fragment_ref: str
    png_ref: str | None
    fragment_size: int = 0
    png_size: int = 0
    qa: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


def _png_is_contentful(png_bytes: bytes, *, std_threshold: float = 6.0) -> tuple[bool, float]:
    im = Image.open(io.BytesIO(png_bytes)).convert("L")
    stat = ImageStat.Stat(im)
    return float(stat.stddev[0]) > std_threshold, float(stat.stddev[0])


def _figure_ok(blob: bytes | None) -> tuple[bool, str]:
    if not blob:
        return False, "figure-missing"
    if len(blob) < 200:
        return False, "figure-too-small"
    try:
        im = Image.open(io.BytesIO(blob))
        im.verify()
        im2 = Image.open(io.BytesIO(blob))
        if im2.width < 80 or im2.height < 80:
            return False, f"figure-dims-{im2.width}x{im2.height}"
        contentful, _ = _png_is_contentful(blob, std_threshold=4.0)
        if not contentful:
            return False, "figure-blank"
    except Exception as exc:  # noqa: BLE001
        return False, f"figure-undecodable:{type(exc).__name__}"
    return True, "ok"


async def run_page_qa(req: PageRequest, store, *, render: bool = True) -> PageQAResult:
    import asyncio
    import time

    t0 = time.monotonic()
    errors: list[str] = []
    built: BuiltPage = await asyncio.to_thread(build_page, req)

    # 文本度量复核
    pm = measure_page(built.boxes)
    overflow = pm.has_overflow
    overlap = pm.has_overlap
    truncated = pm.truncated_chars
    if overflow:
        bad = [s.shape_id for s in pm.shapes if s.overflow_w or s.overflow_h]
        errors.append(f"overflow:{','.join(bad)}")
    if overlap:
        errors.append(f"overlap:{pm.overlaps}")
    if truncated:
        errors.append(f"truncated:{truncated}")

    # 公式字体覆盖
    full_text = (req.slide.get("title", "") + "\n" + req.slide.get("content", "") + "\n" + built.notes_text)
    fr = resolve_fonts(full_text)
    formula_ok = not fr.missing
    if not formula_ok:
        errors.append(f"missing-glyphs:{fr.missing}")

    # 图形检查
    has_figure = built.has_figure
    figure_state = "absent"
    if has_figure:
        # 图形资产来源：slide.assets（教材/教师上传）+ 参数化 math_object 占位 id。
        # 注意：math_object 页的图形不在 slide.assets 里 → 旧实现 next(...) 会因空生成器
        # 抛 StopIteration（async 上下文中升级为 RuntimeError 500）。
        from presenton_adapter.engine.layout_resolver import MATH_OBJECT_ASSET_ID

        candidates = [*(req.slide.get("assets") or []), MATH_OBJECT_ASSET_ID]
        aid = next((a for a in candidates if a in req.assets_bytes and req.assets_bytes[a]), None)
        if aid is None:
            errors.append("figure:figure-missing")
            figure_state = "figure-missing"
        else:
            ok, state = _figure_ok(req.assets_bytes[aid])
            figure_state = state
            if not ok:
                errors.append(f"figure:{state}")

    # 渲染检查
    png_ref = None
    render_state = "skipped"
    if render:
        import tempfile

        frag_path = Path(store.get_path(built.fragment_bytes, "pptx"))
        try:
            with tempfile.TemporaryDirectory(prefix="ps8201-qa-") as td:
                n, pngs = await renderer.png_export(frag_path, Path(td), timeout_s=90)
                if n != 1:
                    errors.append(f"render-page-count:{n}")
                png_bytes = Path(pngs[0]).read_bytes()
                contentful, std = _png_is_contentful(png_bytes)
                render_state = f"ok-std{std:.1f}"
                if not contentful:
                    errors.append("render-blank")
                png_ref = store.put(png_bytes, "png")
        except Exception as exc:  # noqa: BLE001
            render_state = f"failed:{type(exc).__name__}: {exc}"
            errors.append(f"render:{render_state}")

    ok = not errors
    frag_ref = store.put(built.fragment_bytes, "pptx")
    png_size = len(store.get(png_ref) or b"") if png_ref else 0
    return PageQAResult(
        ok=ok,
        content_sha256=built.content_sha256,
        fragment_ref=frag_ref,
        png_ref=png_ref,
        fragment_size=len(built.fragment_bytes),
        png_size=png_size,
        qa={
            "overflow": overflow,
            "overlap": overlap,
            "truncated_chars": truncated,
            "formula_ok": formula_ok,
            "font_fallbacks": fr.fallbacks,
            "font_missing": fr.missing,
            "math_chars": built.math_chars,
            "figure": figure_state,
            "render": render_state,
            "shape_metrics": [asdict(s) for s in pm.shapes],
            "overlaps": pm.overlaps,
        },
        errors=errors,
        elapsed_ms=int((time.monotonic() - t0) * 1000),
    )
