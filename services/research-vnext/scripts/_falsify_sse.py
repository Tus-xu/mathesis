"""临时证伪脚本（缺陷 C）：把 SSR 事件名改回 `snapshot_required` / 无名域事件帧。

用法：python -B scripts/_falsify_sse.py apply|revert

同时记录目标文件的 SHA256，便于证明"改回后字节一致"。
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

TARGET = pathlib.Path(__file__).resolve().parents[1] / "research_vnext" / "core" / "api" / "routes.py"

# 缺陷 A：游标过期改回旧事件名 `snapshot_required`（前端会落 onGap → 静默无操作）
GOOD_A = '    return _sse(None, payload, event="snapshot")'
BAD_A = '    return _sse(None, payload, event="snapshot_required")'

# 缺陷 B：域事件改回**无名帧**（前端 f.event 为空 → onGap）
GOOD_B = '            yield _sse(ev["id"], redact_mapping(ev), event="event")'
BAD_B = '            yield _sse(ev["id"], redact_mapping(ev))'


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "apply"
    src = TARGET.read_text(encoding="utf-8")
    if action == "apply":
        missing = [n for n, g in (("A", GOOD_A), ("B", GOOD_B)) if g not in src]
        if missing:
            print(f"FATAL: 找不到待替换的修复行: {missing}")
            return 2
        out = src.replace(GOOD_A, BAD_A, 1).replace(GOOD_B, BAD_B, 1)
    else:
        missing = [n for n, b in (("A", BAD_A), ("B", BAD_B)) if b not in src]
        if missing:
            print(f"FATAL: 找不到缺陷行: {missing}")
            return 2
        out = src.replace(BAD_A, GOOD_A, 1).replace(BAD_B, GOOD_B, 1)
    TARGET.write_text(out, encoding="utf-8")
    print(f"{action}: sha256={sha(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
