"""按 KaTeX 质检报告过滤暂存题库（修复或剔除并记录——B2 §5.1 纪律）。

输入：corpus/staging/quiz_gaokao.jsonl + corpus/staging/quiz_gaokao_katex_report.json
输出：覆写 quiz_gaokao.jsonl（剔除失败题）+ quiz_gaokao_excluded.json（留痕）
幂等：无失败段时为 no-op。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

STAGING = Path(__file__).resolve().parents[1] / "staging"


def main() -> int:
    src = STAGING / "quiz_gaokao.jsonl"
    katex_report_path = STAGING / "quiz_gaokao_katex_report.json"
    excluded_path = STAGING / "quiz_gaokao_excluded.json"
    if not katex_report_path.exists():
        print("katex report 缺失，先跑 katex_check.mjs", file=sys.stderr)
        return 2
    report = json.loads(katex_report_path.read_text(encoding="utf-8"))
    failed_hashes = {f["where"].split(":")[0] for f in report.get("failures", []) if ":" in f["where"]}
    if not failed_hashes:
        print("无失败段，no-op")
        return 0

    kept: list[dict] = []
    excluded: list[dict] = []
    for line in src.read_text(encoding="utf-8").split("\n"):
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("stem_hash") in failed_hashes:
            excluded.append(rec)
        else:
            kept.append(rec)

    excluded_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reason": "KaTeX 编译失败（修复后仍无法通过），剔除并留痕",
        "count": len(excluded),
        "records": excluded,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    with src.open("w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"excluded={len(excluded)} kept={len(kept)} -> {src.name} 已覆写")
    return 0


if __name__ == "__main__":
    sys.exit(main())
