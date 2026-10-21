"""字体发现 + 覆盖检查 + fallback 链（fontTools cmap，真实安装字体）。

fallback 链语义：主字体（Microsoft YaHei）优先；某字符主字体缺字形时，
按链顺序找第一个覆盖该字符的已安装字体 → 记录 fallback 条目。
任何字符在整条链上都无覆盖 → 缺字形（QA 失败项）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTCollection, TTFont

WINDOWS_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

# fallback 链（主 → 备）：数学符号依赖 Segoe UI Symbol / Cambria Math
FONT_CHAIN: tuple[tuple[str, str], ...] = (
    ("Microsoft YaHei", "msyh.ttc"),
    ("Microsoft YaHei UI", "msyh.ttc"),
    ("SimSun", "simsun.ttc"),
    ("Segoe UI Symbol", "seguisym.ttf"),
    ("Cambria Math", "cambria.ttc"),
    ("Arial", "arial.ttf"),
    ("Calibri", "calibri.ttf"),
)


@dataclass
class FontResolution:
    primary: str
    fallbacks: dict[str, str] = field(default_factory=dict)  # char -> font name
    missing: list[str] = field(default_factory=list)  # 无任何字体覆盖的字符


def _load_cmap(path: Path) -> set[int] | None:
    try:
        if path.suffix.lower() == ".ttc":
            coll = TTCollection(str(path), lazy=True)
            cps: set[int] = set()
            for f in coll.fonts:
                try:
                    cmap = f.getBestCmap()
                    if cmap:
                        cps.update(cmap.keys())
                except Exception:  # noqa: BLE001
                    continue
            return cps
        f = TTFont(str(path), lazy=True)
        cmap = f.getBestCmap()
        return set(cmap.keys()) if cmap else None
    except Exception:  # noqa: BLE001
        return None


@lru_cache(maxsize=32)
def available_fonts() -> dict[str, Path]:
    """返回链上真实存在的字体 {family: path}（忽略缺失项并如实记录）。"""
    found: dict[str, Path] = {}
    for family, filename in FONT_CHAIN:
        p = WINDOWS_FONTS / filename
        if p.exists():
            found[family] = p
    return found


@lru_cache(maxsize=8)
def _cmap_cache(path_str: str) -> set[int] | None:
    return _load_cmap(Path(path_str))


def resolve_fonts(text: str) -> FontResolution:
    """对给定文本解析字体覆盖情况：主字体 + per-char fallback + 缺字形清单。"""
    fonts = available_fonts()
    primary = next((f for f, _ in FONT_CHAIN if f in fonts), None)
    res = FontResolution(primary=primary or "none")
    if primary is None:
        res.missing = sorted({ch for ch in text if not ch.isspace()})
        return res
    primary_cps = _cmap_cache(str(fonts[primary])) or set()
    others: list[tuple[str, set[int]]] = []
    for family, _ in FONT_CHAIN:
        if family != primary and family in fonts:
            cps = _cmap_cache(str(fonts[family]))
            if cps:
                others.append((family, cps))
    seen: set[str] = set()
    for ch in text:
        if ch.isspace() or ch in seen:
            continue
        seen.add(ch)
        cp = ord(ch)
        if cp in primary_cps:
            continue
        hit = next((name for name, cps in others if cp in cps), None)
        if hit is None:
            res.missing.append(ch)
        else:
            res.fallbacks[ch] = hit
    res.missing = sorted(set(res.missing))
    return res


def font_path_for(family: str) -> Path | None:
    return available_fonts().get(family)
