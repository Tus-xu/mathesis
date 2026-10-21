"""临时证伪脚本：#4 try_accept 改回用 rowcount 判断（即被修复的缺陷）。

用法：python -B scripts/_falsify_apply.py apply|revert
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

TARGET = pathlib.Path(__file__).resolve().parents[1] / "research_vnext" / "core" / "application" / "job_service.py"

GOOD = "    return db.execute(stmt).first() is not None"
BAD = "    return bool(db.execute(stmt).rowcount)"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "apply"
    src = TARGET.read_text(encoding="utf-8")
    if action == "apply":
        if GOOD not in src:
            print("FATAL: 找不到待替换的修复行（文件可能已被改动）")
            return 2
        out = src.replace(GOOD, BAD, 1)
    else:
        if BAD not in src:
            print("FATAL: 找不到缺陷行，无需回退")
            return 2
        out = src.replace(BAD, GOOD, 1)
    TARGET.write_text(out, encoding="utf-8")
    print(f"{action}: sha256={sha(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
