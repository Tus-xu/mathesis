"""预下载 Docling 标准布局权重到 artifacts_path（Docker 离线包 / 本机缓存）。

用法：
  python download_models.py --artifacts .parser-cache/artifacts
  python download_models.py --layout-only --artifacts /opt/docling-artifacts

产出布局（Docling artifacts_path 约定）：
  <artifacts>/docling-project--docling-layout-heron/
  <artifacts>/docling-project--TableFormerV2/          # 新 TF
  <artifacts>/accurate/ + fast/                        # 旧 TableFormer（docling-models v2.3.0）
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def _snapshot_to_artifacts(cache_dir: Path, artifacts: Path, repo_id: str, revision: str | None = None) -> Path | None:
    from huggingface_hub import snapshot_download

    snap = snapshot_download(repo_id=repo_id, revision=revision, cache_dir=str(cache_dir))
    name = repo_id.replace("/", "--")
    dst = artifacts / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(snap, dst)
    print(f"OK {repo_id} -> {dst}", flush=True)
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    # 默认相对本 parser 包目录：services/research-vnext/research_vnext/core/parser/.parser-cache/artifacts
    ap.add_argument(
        "--artifacts",
        default=str(Path(__file__).resolve().parent / ".parser-cache" / "artifacts"),
    )
    ap.add_argument("--cache", default="")
    ap.add_argument("--layout-only", action="store_true")
    ap.add_argument("--endpoint", default=os.getenv("HF_ENDPOINT", "https://hf-mirror.com"))
    args = ap.parse_args()

    artifacts = Path(args.artifacts)
    artifacts.mkdir(parents=True, exist_ok=True)
    cache = Path(args.cache or (artifacts / ".hf-cache"))
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_ENDPOINT", args.endpoint)
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    # 1) layout heron（Standard 布局必需）
    _snapshot_to_artifacts(cache, artifacts, "docling-project/docling-layout-heron")

    if args.layout_only:
        return 0

    # 2) 新 TableFormer（transformers 版；当前 table_structure_model 仍读旧 accurate/）
    try:
        _snapshot_to_artifacts(cache, artifacts, "docling-project/TableFormerV2")
    except Exception as e:  # noqa: BLE001
        print(f"WARN TableFormerV2: {e}", flush=True)

    # 3) 旧 TableFormer 权重（accurate/tm_config.json）—— do_table_structure=True 需要
    try:
        _snapshot_to_artifacts(cache, artifacts, "docling-project/docling-models", revision="v2.3.0")
        src = artifacts / "docling-project--docling-models"
        if src.exists():
            # 旧包根下即 accurate/ fast/
            for sub in ("accurate", "fast"):
                if (src / sub).exists() and not (artifacts / sub).exists():
                    shutil.copytree(src / sub, artifacts / sub)
                    print(f"OK linked {sub}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"WARN docling-models v2.3.0 (tableformer): {e}", flush=True)

    print("done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
