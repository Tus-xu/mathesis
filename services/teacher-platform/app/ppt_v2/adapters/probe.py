"""R11 capability probe —— 真实供应商 API 最小探测（integration / 手工 opt-in）。

运行方式：
  1) R11_SMOKE=1 uv run pytest tests/integration/ppt_v2/test_capability_smoke.py -q
  2) 或直接 R11_SMOKE=1 uv run python -m app.ppt_v2.adapters.probe

输出只含 status / latency / model / dimension / error code；不含任何 key、原文、
教材全文、老师上传原图或签名 URL。embedding 实测维度 != 768 → ok=False + dim_mismatch
（fail closed，不写索引；报告按 NO-GO 处理）。
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import time

from pydantic import BaseModel, Field

from app.ppt_v2.adapters.dto import CapabilityProbe


def _err(name: str, exc: Exception, *, started: float) -> CapabilityProbe:
    return CapabilityProbe(
        name=name,
        ok=False,
        latency_ms=int((time.monotonic() - started) * 1000),
        detail=f"{type(exc).__name__}: {exc}",
        error_code="upstream",
    )


# ── embedding：单条中文 + batch，断言真实维度 ───────────────────────────────────


async def probe_embedding() -> CapabilityProbe:
    from app.ppt_v2.adapters.embedding import EmbeddingAdapter

    started = time.monotonic()
    try:
        adapter = EmbeddingAdapter()
        vecs = await adapter.embed(["椭圆的标准方程", "函数的单调性"])
        dim = len(vecs[0].vector)
        ok = dim == adapter.expected_dims
        return CapabilityProbe(
            name="embedding",
            ok=ok,
            latency_ms=int((time.monotonic() - started) * 1000),
            detail=f"batch=2 dim={dim} normalized={vecs[0].normalized}",
            model=adapter.model,
            dimension=dim,
            error_code=None if ok else "dim_mismatch",
        )
    except Exception as exc:
        return _err("embedding", exc, started=started)


# ── text：短回答 / structured JSON / stream / thinking 参数 ────────────────────

_PROBE_QUESTION = "用一句话回答：高中数学中椭圆的第一定义是什么？"


async def probe_text_short() -> CapabilityProbe:
    from app.ppt_v2.adapters.ai import AIPlatformAdapter

    ai = AIPlatformAdapter()
    started = time.monotonic()
    try:
        reply = await ai.chat(
            [{"role": "user", "content": _PROBE_QUESTION}],
            use_case="chat",
            temperature=0.3,
        )
        ok = bool(reply.text.strip())
        return CapabilityProbe(
            name="text_short",
            ok=ok,
            latency_ms=int((time.monotonic() - started) * 1000),
            detail=f"reply_len={len(reply.text)} finish={reply.finish_reason}",
            model=reply.model,
            error_code=None if ok else "empty",
        )
    except Exception as exc:
        return _err("text_short", exc, started=started)


class _OutlineProbe(BaseModel):
    title: str = Field(description="课题标题")
    goals: list[str] = Field(description="教学目标列表")


async def probe_text_structured() -> CapabilityProbe:
    from app.ppt_v2.adapters.ai import AIPlatformAdapter

    ai = AIPlatformAdapter()
    started = time.monotonic()
    try:
        obj = await ai.structured(
            _OutlineProbe,
            system="你是高中数学教研助手。",
            user="为课题《椭圆的定义》输出一个标题与两个教学目标。",
        )
        return CapabilityProbe(
            name="text_structured_json",
            ok=True,
            latency_ms=int((time.monotonic() - started) * 1000),
            detail=f"parsed={obj.model_dump_json(ensure_ascii=False)[:80]}",
        )
    except Exception as exc:
        return _err("text_structured_json", exc, started=started)


async def probe_text_stream() -> CapabilityProbe:
    from app.ppt_v2.adapters.ai import AIPlatformAdapter

    ai = AIPlatformAdapter()
    started = time.monotonic()
    try:
        tokens = []
        stream = ai.stream([{"role": "user", "content": _PROBE_QUESTION}], use_case="chat")
        async for chunk in stream:
            tokens.append(chunk.text)
        ok = bool(tokens)
        return CapabilityProbe(
            name="text_stream",
            ok=ok,
            latency_ms=int((time.monotonic() - started) * 1000),
            detail=f"tokens={len(tokens)} chars={sum(len(t) for t in tokens)}",
            error_code=None if ok else "empty",
        )
    except Exception as exc:
        return _err("text_stream", exc, started=started)


async def probe_thinking_param() -> CapabilityProbe:
    """验证供应商是否接受 thinking enabled（不魔改全局网关，单用例探测）。"""
    from app.ppt_v2.adapters.ai import AIPlatformAdapter

    ai = AIPlatformAdapter(spark_thinking=True)
    started = time.monotonic()
    try:
        await ai.chat([{"role": "user", "content": "1+1=?"}], use_case="chat", temperature=0.2)
        return CapabilityProbe(
            name="thinking_enabled",
            ok=True,
            latency_ms=int((time.monotonic() - started) * 1000),
            detail="spark thinking enabled 被接受",
            error_code=None,
        )
    except Exception as exc:
        status = getattr(exc, "status_code", None)
        return CapabilityProbe(
            name="thinking_enabled",
            ok=False,
            latency_ms=int((time.monotonic() - started) * 1000),
            detail=f"{type(exc).__name__} status={status}",
            error_code="param_rejected" if status and 400 <= status < 500 else "upstream",
        )


# ── vision：合成几何图 → 结构化；OCR opt-in ─────────────────────────────────────


def _synthetic_triangle_b64() -> str:
    """本地生成合成直角三角形 PNG（无敏感内容；Vision 探测输入）。"""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(3, 3))
    ax.plot([0, 3, 0, 0], [0, 0, 4, 0], "k-", linewidth=2)
    ax.text(0.1, -0.6, "A", fontsize=14)
    ax.text(3.0, -0.6, "B", fontsize=14)
    ax.text(-0.5, 4.0, "C", fontsize=14)
    ax.set_xlim(-1, 4)
    ax.set_ylim(-1, 5)
    ax.set_aspect("equal")
    ax.axis("off")
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


async def probe_vision() -> CapabilityProbe:
    from app.ppt_v2.adapters.vision import VisionAdapter

    va = VisionAdapter()
    image = _synthetic_triangle_b64()
    started = time.monotonic()
    try:
        res = await va.understand(image)
        ok = bool(res.purpose.strip())
        return CapabilityProbe(
            name="vision_structured",
            ok=ok,
            latency_ms=int((time.monotonic() - started) * 1000),
            detail=f"purpose={res.purpose[:60]} objects={len(res.objects)} relations={len(res.relations)}",
            model=res.model,
            error_code=None if ok else "empty",
        )
    except Exception as exc:
        return _err("vision_structured", exc, started=started)


async def probe_ocr() -> CapabilityProbe:
    from app.ppt_v2.adapters.vision import VisionAdapter

    va = VisionAdapter()
    image = _synthetic_triangle_b64()
    started = time.monotonic()
    try:
        draft = await va.ocr_opt_in(image)
        return CapabilityProbe(
            name="vision_ocr_optin",
            ok=True,
            latency_ms=int((time.monotonic() - started) * 1000),
            detail=f"is_draft={draft.is_draft} text_len={len(draft.text)}",
            model=draft.model,
        )
    except Exception as exc:
        return _err("vision_ocr_optin", exc, started=started)


# ── search：无敏感查询，验证 provenance / 幂等缓存 ──────────────────────────────


async def probe_search() -> CapabilityProbe:
    from app.ppt_v2.adapters.search import SearchAdapter

    sa = SearchAdapter()
    started = time.monotonic()
    try:
        first = await sa.search("高中数学 椭圆 定义", freshness_days=365, count=5)
        second = await sa.search("高中数学 椭圆 定义", freshness_days=365, count=5)
        sample = first.hits[0] if first.hits else None
        detail = (
            f"hits={len(first.hits)} cached_second={second.cached} "
            f"first_url={sample.url if sample else None} "
            f"first_time={sample.published_time if sample else None}"
        )
        return CapabilityProbe(
            name="search",
            ok=bool(first.hits),
            latency_ms=int((time.monotonic() - started) * 1000),
            detail=detail,
            error_code=None if first.hits else "empty",
        )
    except Exception as exc:
        return _err("search", exc, started=started)


# ── run_all ────────────────────────────────────────────────────────────────────


async def run_all() -> list[CapabilityProbe]:
    """顺序执行全部探测（对上游限流温和）。"""
    return [
        await probe_embedding(),
        await probe_text_short(),
        await probe_text_structured(),
        await probe_text_stream(),
        await probe_thinking_param(),
        await probe_vision(),
        await probe_ocr(),
        await probe_search(),
    ]


if __name__ == "__main__":
    if not os.getenv("R11_SMOKE"):
        print("设置 R11_SMOKE=1 才允许真实 API 探测（手工 opt-in，密钥轮换后）")
        raise SystemExit(0)
    matrix = asyncio.run(run_all())
    for p in matrix:
        print(f"{p.name}: ok={p.ok} latency={p.latency_ms}ms model={p.model} dim={p.dimension} :: {p.detail}")
