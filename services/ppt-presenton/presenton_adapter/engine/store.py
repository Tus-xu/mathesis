"""内容寻址 artifact 存储（8201 引擎侧）。

ref = sha256(bytes)；路径 <engine_data_dir>/artifacts/<ref[:2]>/<ref>.<ext>。
相同内容天然单副本 → 装配/页面均按 ref 幂等（同请求无重复 artifact 的引擎侧基础）。
deck_key：装配级确定性键 = sha256(builder_version + deck_spec_sha256 + 各页 page_ref 串)。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "artifacts").mkdir(parents=True, exist_ok=True)
        (self.root / "decks").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def put(self, data: bytes, ext: str) -> str:
        ref = self.sha256(data)
        d = self.root / "artifacts" / ref[:2]
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{ref}.{ext}"
        if not p.exists():
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
        return ref

    def get_path(self, data: bytes, ext: str) -> Path:
        """落盘并返回本地路径（渲染器需要真实文件路径）。"""
        ref = self.sha256(data)
        d = self.root / "artifacts" / ref[:2]
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{ref}.{ext}"
        if not p.exists():
            tmp = p.with_suffix(p.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(p)
        return p

    def get(self, ref: str) -> bytes | None:
        d = self.root / "artifacts" / ref[:2]
        if not d.exists():
            return None
        for p in d.glob(f"{ref}.*"):
            return p.read_bytes()
        return None

    def exists(self, ref: str) -> bool:
        d = self.root / "artifacts" / ref[:2]
        return d.exists() and any(d.glob(f"{ref}.*"))

    def put_json(self, payload: dict, name: str) -> str:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return self.put(data, "json")

    @staticmethod
    def deck_key(builder_version: str, deck_spec_sha256: str, page_refs: list[str]) -> str:
        canon = json.dumps(
            {"v": builder_version, "spec": deck_spec_sha256, "pages": page_refs},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    def deck_path(self, deck_key: str) -> Path:
        return self.root / "decks" / f"{deck_key}.pptx"
