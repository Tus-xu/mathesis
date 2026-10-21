# coding: utf-8
"""Pix2Text HTTP 客户端（B4 识别主选 sidecar，teacher-pix2text:8103）。

协议调研结论（notes/B4-notes.md 主题一，源码 repos/Pix2Text/pix2text/serve.py:34-52）：
  POST /pix2text  multipart{image, file_type ∈ text/formula/text_formula/page,
  resized_shape=768, embed_sep, isolated_sep} → {status_code, results, output_dir}
  results 逐块 dict：position(Box)/type/text/score（docs/examples.md:80）。

OcrBlock 映射：type→kind（embed/isolated 公式归并 formula）、position→image_region
（归一化 0-1）、score→confidence；块合并（题号切分）由 recognition 域管线负责（M3）。
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import httpx
import numpy as np

DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class OcrBlock:
    """识别块（recognition 管线的统一中间结构）。"""

    kind: str                      # formula / text
    text: str = ''
    latex: str = ''
    confidence: float = 0.0
    image_region: tuple[float, float, float, float] | None = None  # 归一化 (x,y,w,h)


class Pix2TextClient:
    """sidecar 客户端；OCR 失败向上抛 Pix2TextError（管线降级 PaddleOCR 兜底，M3）。"""

    def __init__(self, base_url: str, *, timeout: float = DEFAULT_TIMEOUT):
        self._base_url = base_url.rstrip('/')
        self._timeout = timeout

    async def recognize(
        self,
        image_bytes: bytes,
        *,
        file_type: str = 'text_formula',
        resized_shape: int = 768,
    ) -> list[OcrBlock]:
        if file_type not in ('text', 'formula', 'text_formula', 'page'):
            raise ValueError(f'file_type 非法：{file_type!r}')
        size, _channels = _decode_size(image_bytes)
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f'{self._base_url}/pix2text',
                files={'image': ('photo.jpg', image_bytes, 'image/jpeg')},
                data={'file_type': file_type, 'resized_shape': str(resized_shape)},
            )
        if resp.status_code != 200:
            raise Pix2TextError(f'pix2text sidecar HTTP {resp.status_code}: {resp.text[:200]}')
        payload = resp.json()
        return _map_results(payload.get('results'), size)


class Pix2TextError(RuntimeError):
    pass


def _decode_size(image_bytes: bytes) -> tuple[tuple[int, int], int]:
    import cv2

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise Pix2TextError('图片解码失败（非图片内容？）')
    h, w = img.shape[:2]
    return (w, h), img.ndim


def _map_results(results: object, size: tuple[int, int]) -> list[OcrBlock]:
    """p2t results → OcrBlock（notes/B4-notes.md 主题一映射表）。"""
    width, height = size
    blocks: list[OcrBlock] = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get('text', '') or '').strip()
        if not text:
            continue
        kind = 'formula' if item.get('type') in ('isolated', 'embed', 'formula') else 'text'
        latex = ''
        if kind == 'formula':
            latex = text.strip('`$ \n')
            latex = latex.replace('$$\n', '').replace('\n$$', '').strip()
        region = None
        pos = item.get('position')
        if isinstance(pos, (list, tuple)) and len(pos) == 4 and width and height:
            x0, y0, x1, y1 = (float(v) for v in pos)
            region = (x0 / width, y0 / height, (x1 - x0) / width, (y1 - y0) / height)
        blocks.append(OcrBlock(
            kind=kind, text='' if kind == 'formula' else text, latex=latex,
            confidence=float(item.get('score', 0.0) or 0.0), image_region=region,
        ))
    return blocks
