"""本地 artifact 存储（8200 侧）：object_key（pps2/ 前缀）→ 磁盘文件。

MinIO/对象存储接线留后续轮；本轮以本地盘承载 8200 的 artifact 事实
（DB 侧 ppt_v2_artifacts 登记哈希与大小，object_key 全局唯一）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.config import get_settings


class LocalArtifactStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or get_settings().artifact_root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def canonical_sha256(payload: dict) -> str:
        canon = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    def path_for(self, object_key: str) -> Path:
        if not object_key.startswith("pps2/") or ".." in object_key:
            raise ValueError(f"非法 object_key: {object_key!r}（必须 pps2/ 前缀且无路径穿越）")
        return self.root / object_key[len("pps2/"):]

    def put(self, object_key: str, data: bytes) -> tuple[str, int]:
        p = self.path_for(object_key)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
        return self.sha256_bytes(data), len(data)

    def get(self, object_key: str) -> bytes | None:
        p = self.path_for(object_key)
        return p.read_bytes() if p.exists() else None

    def exists(self, object_key: str) -> bool:
        return self.path_for(object_key).exists()
