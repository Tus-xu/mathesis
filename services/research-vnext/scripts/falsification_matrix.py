"""证伪（falsification）实验驱动脚本。

对每个关键修复：**临时重新引入缺陷** → 运行对应测试证明它**变红** →
**从备份恢复**（字节级还原）→ 断言 SHA256 与原始值完全一致。

设计要点：恢复用**字节备份**而不是"反向字符串替换"。
反向替换会经过 Python 的文本层，在 Windows 上可能把 CRLF 规范化成 LF，
于是"看起来还原了"但文件已经变了 —— 那样就无法证明"文件字节一致"。
"""

from __future__ import annotations

import hashlib
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")

TARGETS = {
    "sse": ROOT / "research_vnext" / "core" / "api" / "routes.py",
    "message": ROOT / "research_vnext" / "core" / "application" / "message_service.py",
    "job": ROOT / "research_vnext" / "core" / "application" / "job_service.py",
    "rules": ROOT / "research_vnext" / "core" / "domain" / "rv_domain_rules.py",
    "gateway": ROOT / "research_vnext" / "core" / "llm" / "gateway.py",
}

# (脚本, apply 参数, revert 参数, 目标键, 测试目标, -k 过滤, 期望失败的最小个数)
EXPERIMENTS: list[tuple[str, str, str, str, list[str], str, int]] = [
    (
        "_falsify_sse.py", "apply", "revert", "sse",
        ["tests/integration/test_sse_wire_cr6b.py"], "", 3,
    ),
    (
        "_falsify_message.py", "apply-idem", "revert-idem", "message",
        ["tests/integration/test_messages_cr6.py"], "conflict or replay", 1,
    ),
    (
        "_falsify_message.py", "apply", "revert", "message",
        ["tests/integration/test_messages_cr6.py"], "revision", 1,
    ),
    (
        "_falsify_apply.py", "apply", "revert", "job",
        ["tests/integration/test_rv03_job_runtime.py"], "dedupe", 1,
    ),
    (
        "_falsify_claim.py", "apply", "revert", "rules",
        ["tests/unit/test_rv_domain_rules.py", "tests/integration/test_rv09_to_rv13_domain.py"],
        "claim_holds or exit_code", 1,
    ),
    # ---- LEAD ADDENDUM：逐字参数 + 容量闸 ----
    (
        "_falsify_addendum.py", "apply-url", "revert-url", "gateway",
        ["tests/unit/test_llm_gateway_params.py"],
        "base_url or endpoint or default", 2,
    ),
    (
        "_falsify_addendum.py", "apply-leak", "revert-leak", "message",
        ["tests/integration/test_single_provider_degradation.py"],
        "capacity_is_released", 1,
    ),
]


def sha_of(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_pytest(targets: list[str], kfilter: str) -> tuple[str, int]:
    cmd = [PY, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider", "--no-header"]
    if kfilter:
        cmd += ["-k", kfilter]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, errors="replace")
    tail = [ln for ln in (r.stdout or "").splitlines() if "passed" in ln or "failed" in ln]
    return (tail[-1] if tail else "(no summary)"), r.returncode


def main() -> int:
    if not pathlib.Path(PY).exists():
        print(f"FATAL: 找不到解释器 {PY}")
        return 2

    backup_dir = pathlib.Path(tempfile.mkdtemp(prefix="rv-falsify-"))
    originals = {key: sha_of(p) for key, p in TARGETS.items()}
    for key, p in TARGETS.items():
        shutil.copy2(p, backup_dir / f"{key}.bak")

    print("原始 SHA256（实验开始前）：")
    for key, h in originals.items():
        print(f"  {key:8s} {h}")
    print()

    failures: list[str] = []
    for idx, (script, apply_arg, revert_arg, key, tests, kfilter, min_fail) in enumerate(EXPERIMENTS, 1):
        path = TARGETS[key]
        print("=" * 78)
        print(f"[{idx}/{len(EXPERIMENTS)}] 证伪 {script} {apply_arg}  → 目标 {key}")
        a = subprocess.run(
            [PY, "-B", str(ROOT / "scripts" / script), apply_arg],
            cwd=str(ROOT), capture_output=True, text=True,
        )
        print("  apply:", (a.stdout or a.stderr).strip())
        mutated = sha_of(path)
        assert mutated != originals[key], "缺陷未真正引入（文件未变化）"

        summary, rc = run_pytest(tests, kfilter)
        print("  测试结果:", summary)
        print("  预期: 至少", min_fail, "个失败（红）")
        red = rc != 0
        print("  判定:", "RED ✓（测试确实抓住了这个缺陷）" if red else "GREEN ✗（测试无法发现该缺陷！）")
        if not red:
            failures.append(f"{script}:{apply_arg} 未能被测试发现")

        # 从**字节备份**恢复，并断言字节一致
        shutil.copy2(backup_dir / f"{key}.bak", path)
        restored = sha_of(path)
        same = restored == originals[key]
        print(f"  恢复后 sha256={restored}")
        print(f"  与原始字节一致: {same}")
        if not same:
            failures.append(f"{script}:{apply_arg} 恢复后字节不一致")
        print()

    print("=" * 78)
    if failures:
        print("证伪实验存在未达标项：")
        for f in failures:
            print("  -", f)
        return 1
    print(f"全部 {len(EXPERIMENTS)} 个证伪实验达标：缺陷 → 测试变红；恢复 → 字节一致。")
    shutil.rmtree(backup_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
