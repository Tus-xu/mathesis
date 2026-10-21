"""错题本资源快照的规范化与图形需求判断。"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlparse

_FIGURE_LANGUAGE = re.compile(r"如图|图中|几何|平面|棱|垂直|平行")


def _usable_image_src(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    src = value.strip()
    if src.startswith("data:image/"):
        return src
    parsed = urlparse(src)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return src
    return None


def _image_asset(value: object, alt: str = "题目配图") -> dict | None:
    src = _usable_image_src(value)
    return {"type": "image", "src": src, "alt": alt} if src else None


def normalize_error_assets(items: list | None, *, alt: str = "题目配图") -> list[dict]:
    """将错题历史图片、帧载荷和 GeoGebra 构造收敛为前端可渲染资源。"""
    normalized: list[dict] = []
    if not isinstance(items, list):
        return normalized

    for item in items:
        if isinstance(item, str):
            asset = _image_asset(item, alt)
            if asset:
                normalized.append(asset)
            continue
        if not isinstance(item, dict):
            continue
        if item.get("type") == "ggb":
            commands = item.get("commands")
            if isinstance(commands, list) and commands and all(isinstance(command, str) for command in commands):
                normalized.append(
                    {
                        "type": "ggb",
                        "view": "3d" if item.get("view") == "3d" else "2d",
                        "commands": commands,
                        "caption": str(item.get("caption") or ""),
                    }
                )
            continue

        asset = _image_asset(item.get("src") or item.get("url") or item.get("data_uri"), alt)
        if asset:
            normalized.append(asset)
            continue

        frames = item.get("frames")
        if isinstance(frames, Iterable) and not isinstance(frames, (str, bytes, dict)):
            for frame in frames:
                if not isinstance(frame, dict):
                    continue
                asset = _image_asset(frame.get("src") or frame.get("url") or frame.get("data_uri"), alt)
                if asset:
                    normalized.append(asset)
    return normalized


def has_usable_figure(question_text: str, assets: list[dict] | None) -> bool:
    """判断正解是否值得生成示意图，避免为非几何题添装饰图。"""
    return bool(assets) or bool(_FIGURE_LANGUAGE.search(question_text or ""))
