"""临时证伪脚本（缺陷 D/E）：消息写入的幂等与 revision 保护。

用法：python -B scripts/_falsify_message.py apply|revert|apply-rev|revert-rev

D：`expected_revision` 校验被移除（前端会静默覆盖别人的修改）
E：`client_message_id` 的**服务端摘要**判据改回"只信调用方"的空判据
   （同一 id + 不同内容会被当作重放吞掉）
"""

from __future__ import annotations

import hashlib
import pathlib
import sys

TARGET = pathlib.Path(__file__).resolve().parents[1] / "research_vnext" / "core" / "application" / "message_service.py"

GOOD_D = """        if expected_revision is not None and expected_revision != row.revision:
            raise RevisionConflict(expected_revision, row.revision, f"snapshot:{row.id}")"""
BAD_D = """        if False:  # FALSIFY: revision 保护被移除
            raise RevisionConflict(0, row.revision, f"snapshot:{row.id}")"""

GOOD_E = """            import hmac as _hmac

            if not _hmac.compare_digest(existing.canonical_digest, server_digest):"""
BAD_E = """            if False:  # FALSIFY: 服务端摘要校验被移除（只看 id 存在即重放）"""


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "apply"
    src = TARGET.read_text(encoding="utf-8")

    if action == "apply":
        if GOOD_D not in src:
            print("FATAL: 找不到 revision 校验行")
            return 2
        out = src.replace(GOOD_D, BAD_D, 1)
    elif action == "revert":
        if BAD_D not in src:
            print("FATAL: 找不到缺陷 D 行")
            return 2
        out = src.replace(BAD_D, GOOD_D, 1)
    elif action == "apply-idem":
        if GOOD_E not in src:
            print("FATAL: 找不到幂等摘要校验行")
            return 2
        out = src.replace(GOOD_E, BAD_E, 1)
    elif action == "revert-idem":
        if BAD_E not in src:
            print("FATAL: 找不到缺陷 E 行")
            return 2
        out = src.replace(BAD_E, GOOD_E, 1)
    else:
        print(f"未知动作: {action}")
        return 2

    TARGET.write_text(out, encoding="utf-8")
    print(f"{action}: sha256={sha(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
