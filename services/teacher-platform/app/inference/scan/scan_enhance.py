# coding: utf-8
"""扫描增强（OpenCV）—— 参数语义与前端 mathx/scanEnhance.ts 逐字镜像。

前端事实源：D:\\frontend\\src\\components\\mathx\\scanEnhance.ts:23-31
  顺序固定：对比（f=1+contrast/100，绕 128 拉伸）→ 亮度平移 → 提白
  （paper=148，c≥paper 时 c = paper + (c-paper)*(1+strength)），clamp 0-255。
  输出灰阶。默认 {brightness:6, contrast:30, whiten:true, strength:0.55}。

服务端另有三档独立增强（前端无对应，OCR 前置用；增强图只作识别输入与预览对照，
原图永久保留——R8）：morphological close 估背景去阴影 / 自适应二值化 / 透视纠正。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PAPER_LUMA = 148  # 前端 scanEnhance.ts:28 同值


@dataclass(frozen=True)
class ScanParams:
    """与前端 ScanParams 同语义（scanEnhance.ts:7-16）。"""

    brightness: float = 6    # -40..40
    contrast: float = 30     # -40..100
    whiten: bool = True
    strength: float = 0.55   # 0..1


SCAN_DEFAULTS = ScanParams()


def enhance_luma(v: float, p: ScanParams) -> float:
    """单点亮度增强：与前端 enhanceLuma 逐字等价（可单测）。"""
    f = 1 + p.contrast / 100
    c = (v - 128) * f + 128 + p.brightness
    if p.whiten and p.strength > 0:
        if c >= PAPER_LUMA:
            c = PAPER_LUMA + (c - PAPER_LUMA) * (1 + p.strength)
    return float(np.clip(c, 0, 255))


def bgr_to_luma(img_bgr: np.ndarray) -> np.ndarray:
    """ITU-R BT.601 亮度（前端 0.299/0.587/0.114，scanEnhance.ts:34）。"""
    return 0.299 * img_bgr[..., 2] + 0.587 * img_bgr[..., 1] + 0.114 * img_bgr[..., 0]


def enhance_pixels(img_bgr: np.ndarray, p: ScanParams = SCAN_DEFAULTS) -> np.ndarray:
    """增强 RGBA/BGR 像素 → 灰阶 uint8（r=g=b=增强后亮度，同前端 enhancePixels）。"""
    luma = bgr_to_luma(img_bgr.astype(np.float64))
    f = 1 + p.contrast / 100
    out = (luma - 128) * f + 128 + p.brightness
    if p.whiten and p.strength > 0:
        out = np.where(out >= PAPER_LUMA, PAPER_LUMA + (out - PAPER_LUMA) * (1 + p.strength), out)
    return np.clip(out, 0, 255).astype(np.uint8)


def remove_shadow(img_gray: np.ndarray, kernel_size: int = 51) -> np.ndarray:
    """形态学闭运算估背景光照场 → 差分归一提白（对标扫描王去阴影；服务端独立档）。"""
    import cv2

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    background = cv2.morphologyEx(img_gray, cv2.MORPH_CLOSE, kernel)
    norm = img_gray.astype(np.float64) * 255.0 / np.clip(background.astype(np.float64), 1, None)
    return np.clip(norm, 0, 255).astype(np.uint8)


def adaptive_binarize(img_gray: np.ndarray, block_size: int = 31, c: int = 15) -> np.ndarray:
    """自适应二值化：仅作 OCR 前置与预览对照，不替换原图/增强图存储。"""
    import cv2

    return cv2.adaptiveThreshold(
        img_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block_size, c,
    )
