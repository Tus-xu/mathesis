"""教材切片插图只读服务（R4a「资料页真实化」）。

背景：授权切片 markdown 中的 `![](images/<sha>.jpg)` 相对路径指向切片批次目录里的图片
文件（`importers/slice_importer` 只统计不搬运；R13 资产化前无 MinIO key）。资料页/教材
抽屉要「图真实渲染」（G21），就需要一个**只读**的按文件名取图出口：

- 路径不可控：文件名严格正则 + 只在受控切片目录内查找（不接受任何路径分隔符）；
- 只读：不写库、不写存储、不动 rights 状态机；
- 鉴权：与其它 teacher-v3 端点一致（登录教师）；查不到一律 404，不泄漏目录结构。

切片根目录经环境变量 `TEXTBOOK_SLICE_ROOTS`（os.pathsep 分隔）配置；缺省为本机授权
切片镜像目录（见 `_DEFAULT_ROOTS`）。文件索引懒加载 + TTL 缓存（导入批次为不可变产物）。
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from ..core.deps import get_current_user
from ..core.security import UserToken

router = APIRouter()

# 只接受切片导出的 hash 文件名（无扩展名之外的点、无路径分隔符）
_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{4,128}\.(?:jpg|jpeg|png|webp)$", re.IGNORECASE)
_IMAGE_DIR_NAMES = ("images", "image")
_SUFFIX_MEDIA = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# 切片批次目录（授权教材切片；D-002：无原页 → 图片只在批次 images/ 子目录内）
_DEFAULT_ROOTS = (
    r"D:\ppt-rescue-control\20260909-R05\教材切片\AC切片镜像\processed",
    r"D:\知识库\AC切片",
)

_CACHE_TTL_SECONDS = 300.0

_index: dict[str, Path] = {}
_indexed_at: float = 0.0


def _roots() -> list[Path]:
    raw = os.environ.get("TEXTBOOK_SLICE_ROOTS", "").strip()
    if raw:
        return [Path(p.strip()) for p in raw.split(os.pathsep) if p.strip()]
    return [Path(p) for p in _DEFAULT_ROOTS]


def _is_image_dir(name: str) -> bool:
    return name in _IMAGE_DIR_NAMES or name.startswith("images")


def _scan_image_dir(sub: Path, index: dict[str, Path]) -> None:
    for f in sub.iterdir():
        if f.is_file() and _NAME_RE.match(f.name):
            index.setdefault(f.name, f)


def _rebuild_index() -> None:
    global _indexed_at
    index: dict[str, Path] = {}
    for root in _roots():
        if not root.is_dir():
            continue
        if _is_image_dir(root.name):
            _scan_image_dir(root, index)
            continue
        # 根自身可能是批次目录（含 images/）或批次父目录（processed/<batch>/images*/）；
        # 有界递归（≤3 层）覆盖两种排布，导入批次为不可变产物、TTL 内不重扫。
        for base in [root, *[d for d in root.iterdir() if d.is_dir()]]:
            for sub in [base, *[d for d in base.iterdir() if d.is_dir()]]:
                if _is_image_dir(sub.name):
                    _scan_image_dir(sub, index)
                else:
                    for leaf in (d for d in sub.iterdir() if d.is_dir()):
                        if _is_image_dir(leaf.name):
                            _scan_image_dir(leaf, index)
    _index.clear()
    _index.update(index)
    _indexed_at = time.monotonic()


def _lookup(name: str) -> Path | None:
    if not _NAME_RE.match(name):
        return None
    if not _index or (time.monotonic() - _indexed_at) > _CACHE_TTL_SECONDS:
        _rebuild_index()
    return _index.get(name)


@router.get("/textbook-v2/slice-images/{name}")
async def slice_image(name: str, user: UserToken = Depends(get_current_user)) -> FileResponse:
    """按切片图片文件名取图（只读）。未命中/非法名 → 404（前端降级占位，不泄漏源码）。"""
    path = _lookup(name)
    if path is None or not path.is_file():
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="slice image not found")
    return FileResponse(
        path,
        media_type=_SUFFIX_MEDIA.get(path.suffix.lower(), "image/jpeg"),
        headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


__all__ = ["router"]
