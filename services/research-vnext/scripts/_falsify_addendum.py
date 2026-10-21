"""证伪脚本（ADDENDUM 缺陷 G/H）—— 逐字参数与容量闸。

用法：
    python -B scripts/_falsify_addendum.py apply-url | revert-url
    python -B scripts/_falsify_addendum.py apply-leak | revert-leak
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "research_vnext" / "core" / "llm" / "gateway.py"
MESSAGE = ROOT / "research_vnext" / "core" / "application" / "message_service.py"

# --- 缺陷 G：默认端点不再包含 `/chat/completions`（即"少写了路径"这一侧的错误） ---
GOOD_URL = 'DEFAULT_BASE_URL = "https://maas-api.cn-huabei-1.xf-yun.com/v2/chat/completions"'
BAD_URL = 'DEFAULT_BASE_URL = "https://maas-api.cn-huabei-1.xf-yun.com/v2"'

# --- 缺陷 H：容量槽位在 LLM 调用之后**不归还**（一次失败即永久毒化容量） ---
GOOD_LEAK = """    finally:
        capacity.release(lease)"""
BAD_LEAK = """    finally:
        pass  # FALSIFY: 槽位泄漏，不再归还"""


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _swap(target: pathlib.Path, good: str, bad: str, apply: bool) -> str:
    src = target.read_text(encoding="utf-8")
    a, b = (good, bad) if apply else (bad, good)
    if a not in src:
        print(f"FATAL: 在 {target.name} 找不到待替换片段")
        return ""
    out = src.replace(a, b, 1)
    target.write_text(out, encoding="utf-8")
    return sha(target)


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else ""
    if action == "apply-url":
        h = _swap(GATEWAY, GOOD_URL, BAD_URL, True)
    elif action == "revert-url":
        h = _swap(GATEWAY, GOOD_URL, BAD_URL, False)
    elif action == "apply-leak":
        h = _swap(MESSAGE, GOOD_LEAK, BAD_LEAK, True)
    elif action == "revert-leak":
        h = _swap(MESSAGE, GOOD_LEAK, BAD_LEAK, False)
    else:
        print(f"未知动作: {action}")
        return 2
    if not h:
        return 2
    print(f"{action}: sha256={h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
