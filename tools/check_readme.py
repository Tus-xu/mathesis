"""README 完整性校验：图片路径 / 锚点死链 / Markdown 结构 / 仓库路径真实性。

CI 与本地均可复跑：python tools/check_readme.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"

# README 中提到的仓库内路径/脚本（需真实存在）
EXPECTED_PATHS = [
    ".env.example",
    "deploy/docker-compose.yml",
    "services/api/run_server.py",
    "services/api/pyproject.toml",
    "frontend/package.json",
    "frontend/e2e/two-end-link-acceptance.mjs",
    "tools/seed_demo_accounts.py",
    "tools/prepare_readme_assets.py",
    "AGENTS.md",
    "docs/后端现状审计报告.md",
    "docs/audits/2026-10-21-migration-completeness-audit.md",
    "docs/research-workspace/",
    "docs/product/teacher-prep-center/",
    "docs/design/",
    "docs/audits/",
]


def slugify(title: str) -> str:
    """GitHub 风格标题锚点：小写、去标点、空格转 -、保留中文。"""
    s = title.strip().lower()
    s = re.sub(r"[^\w\u4e00-\u9fff\- ]", "", s)
    return s.replace(" ", "-")


def strip_code_fences(text: str) -> str:
    """去掉 ``` 代码块内容，避免把 bash 注释误判为 Markdown 标题。"""
    out = []
    in_fence = False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            out.append(ln)
    return "\n".join(out)


def main() -> int:
    if not README.exists():
        print("FATAL: README.md 不存在")
        return 1

    text = README.read_text(encoding="utf-8")
    prose = strip_code_fences(text)
    lines = prose.splitlines()
    errors: list[str] = []

    # ---------- 1. 本地图片路径真实存在 ----------
    img_refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text) + re.findall(
        r'<img[^>]+src="([^"]+)"', text
    )
    local_imgs = [r for r in img_refs if not r.startswith(("http://", "https://"))]
    for rel in local_imgs:
        if not (REPO / rel).exists():
            errors.append(f"[IMG] 图片不存在: {rel}")
    print(f"[IMG] 本地图片 {len(local_imgs)} 个 → 存在 {len(local_imgs) - len([e for e in errors if e.startswith('[IMG]')])} 个")

    # 每张图必须有 alt
    for m in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", text):
        if not m.group(1).strip():
            errors.append(f"[ALT] 缺 alt 文本: {m.group(2)}")
    for m in re.finditer(r"<img[^>]+>", text):
        if "alt=" not in m.group(0):
            errors.append(f"[ALT] <img> 缺 alt: {m.group(0)[:80]}")

    # ---------- 2. 目录锚点与实际标题一致 ----------
    headings = [ln.lstrip("#").strip() for ln in lines if ln.startswith("#")]
    valid_anchors = {slugify(h) for h in headings}
    toc_links = [
        m.group(1)
        for m in re.finditer(r"\]\(#([^)]+)\)", text)
    ] + [
        m.group(1)
        for m in re.finditer(r'<a href="#([^"]+)"', text)
    ]
    for anchor in toc_links:
        if anchor not in valid_anchors:
            errors.append(f"[ANCHOR] 目录死链: #{anchor}  （可用锚点: {sorted(valid_anchors)}）")

    # 页内跳转链接（非锚点）指向仓库内文件
    for m in re.finditer(r"\]\((?!https?://|#)([^)]+)\)", text):
        target = m.group(1).split("#")[0]
        if target and not (REPO / target).exists():
            errors.append(f"[LINK] 链接目标不存在: {target}")

    # ---------- 3. Markdown 结构 ----------
    fences = text.count("```")
    if fences % 2 != 0:
        errors.append(f"[MD] 代码块围栏数量为奇数（{fences}），有未闭合代码块")

    if fences < 2:
        errors.append("[MD] README 没有任何代码块")

    for tag in ("details", "summary", "p", "sub", "b", "mermaid"):
        pass  # 容器类标签不强制配对，避免误报

    # ---------- 4. README 提到的仓库路径真实存在 ----------
    for p in EXPECTED_PATHS:
        if not (REPO / p).exists():
            errors.append(f"[PATH] README 引用的仓库路径不存在: {p}")

    # ---------- 5. 首屏要素 ----------
    if not text.startswith("<p align=\"center\">"):
        errors.append("[HERO] README 未以居中 Hero 开头")
    if "License" not in text:
        errors.append("[LICENSE] 缺 License 章节")

    # ---------- 汇报 ----------
    print(f"[ANCHOR] 锚点链接 {len(toc_links)} 个，全部有效" if not any(
        e.startswith("[ANCHOR]") for e in errors) else "[ANCHOR] 存在死链")
    print(f"[MD] 代码块围栏 {fences} 个（{'配对' if fences % 2 == 0 else '未配对'}）")
    print(f"[PATH] 仓库路径校验 {len(EXPECTED_PATHS)} 项")

    if errors:
        print(f"\n❌ 发现 {len(errors)} 个问题：")
        for e in errors:
            print("  " + e)
        return 1
    print("\n✅ README 校验全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
