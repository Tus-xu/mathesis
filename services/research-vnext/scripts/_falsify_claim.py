"""临时证伪脚本（缺陷 F）：`claim_holds` 被 exit_code 直接决定。

把 `evaluate_run` 改成"退出码为 0 就算结论成立" —— 这正是任务书禁止的推理。
用法：python -B scripts/_falsify_claim.py apply|revert
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

TARGET = pathlib.Path(__file__).resolve().parents[1] / "research_vnext" / "core" / "domain" / "rv_domain_rules.py"

GOOD = '        "claim_holds": None,'
BAD = '        "claim_holds": (exit_code == 0),  # FALSIFY: 用退出码冒充结论成立'


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "apply"
    src = TARGET.read_text(encoding="utf-8")
    if action == "apply":
        if GOOD not in src:
            print("FATAL: 找不到 claim_holds 行")
            return 2
        out = src.replace(GOOD, BAD, 1)
    else:
        if BAD not in src:
            print("FATAL: 找不到缺陷行")
            return 2
        out = src.replace(BAD, GOOD, 1)
    TARGET.write_text(out, encoding="utf-8")
    print(f"{action}: sha256={sha(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
