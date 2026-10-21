# -*- coding: utf-8 -*-
"""题库导入共享工具：图片 → data URI（题库 image 列既有先例，见 backfill_figures.py）。"""
import base64
import io
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PIL import Image  # noqa: E402

MAX_BYTES = 380_000  # 单图 data URI 软上限，超过降质重压


def load_image_data_uri(path: Path, max_dim: int = 900) -> str | None:
    """读图片 → 等比缩到 max_dim → PNG 优先（文字清晰），过大转 JPEG q80。失败返回 None。"""
    try:
        img = Image.open(path)
        img.load()
    except Exception:
        return None
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_dim:
        scale = max_dim / float(max(w, h))
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    for fmt, kwargs, mime in (
        ("PNG", {"optimize": True}, "image/png"),
        ("JPEG", {"quality": 80}, "image/jpeg"),
    ):
        buf = io.BytesIO()
        try:
            img.save(buf, fmt, **kwargs)
        except Exception:
            continue
        data = buf.getvalue()
        if len(data) <= MAX_BYTES:
            return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")
    return None
