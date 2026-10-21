"""把旧目录的真实验收截图复制到 docs/readme/ 并压缩（README 专用，控制仓库体积）。

只读取 D:\\math-arena\\deliverables 与新仓库 golden，输出到 docs/readme/。
用法：python tools/prepare_readme_assets.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "readme"
OLD = Path(r"D:\math-arena\deliverables\demo-seed-verify-141")
LINK = Path(r"D:\math-arena\deliverables\two-end-link\evidence")
GOLDEN = REPO / "docs"

# (目标文件名, 源路径, 目标最大宽度)
JOBS: list[tuple[str, Path, int]] = [
    ("student-home.png", OLD / "01-student-home.png", 1440),
    ("student-socratic.png", OLD / "02-socratic-guide.png", 1440),
    ("student-variant-chain.png", OLD / "03-variant-chain.png", 1440),
    ("student-figure.png", OLD / "04b-figure-annotated.png", 1440),
    ("student-errorbook.png", OLD / "05-errors.png", 1440),
    ("student-library.png", OLD / "06-library.png", 1440),
    ("student-report.png", OLD / "08-report.png", 1440),
    ("teacher-classes.png", OLD / "09-teacher-classes.png", 1440),
    ("teacher-grading.png", OLD / "10-teacher-grading.png", 1440),
    ("teacher-courseware.png", OLD / "11-teacher-courseware.png", 1440),
    ("research-landing.png", OLD / "12-research-landing.png", 1440),
    ("research-workspace.png", OLD / "13-research-workspace.png", 1440),
    ("golden-read.png", GOLDEN / "research-workspace/golden/READ-GOLDEN.png", 1120),
    ("golden-write.png", GOLDEN / "research-workspace/golden/WRITE-GOLDEN.png", 1120),
    (
        "golden-teacher-classroom.png",
        GOLDEN / "product/teacher-prep-center/golden/CLASSROOM-GOLDEN.png",
        1120,
    ),
    (
        "golden-teacher-ppt.png",
        GOLDEN / "product/teacher-prep-center/golden/PPT-GOLDEN.png",
        1120,
    ),
    (
        "golden-teacher-lessonplan.png",
        GOLDEN / "product/teacher-prep-center/golden/LESSONPLAN-GOLDEN.png",
        1120,
    ),
    # 双师课堂跨端联动证据链（教师建班 → 学生加入 → 发布作业 → AI 批改 → 学情回流）
    ("link-02-class-invite.png", LINK / "02-class-created-invite-code.png", 1440),
    ("link-04c-publish-success.png", LINK / "04c-publish-success.png", 1440),
    ("link-08b-grading-detail.png", LINK / "08b-grading-detail-photo.png", 1440),
    ("link-11c-prep-final.png", LINK / "11c-prep-final.png", 1440),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    total_before = 0
    total_after = 0
    for name, src, max_w in JOBS:
        if not src.exists():
            print(f"[SKIP] 源文件不存在: {src}")
            continue
        before = src.stat().st_size
        total_before += before
        dst = OUT / name
        with Image.open(src) as im:
            im = im.convert("RGB")
            if im.width > max_w:
                h = round(im.height * max_w / im.width)
                im = im.resize((max_w, h), Image.LANCZOS)
            # 截图以文字/色块为主，量化到 256 色调色板可大幅减小体积且肉眼无损
            im = im.quantize(colors=256, method=Image.MEDIANCUT, dither=Image.FLOYDSTEINBERG)
            im.save(dst, "PNG", optimize=True)
        after = dst.stat().st_size
        total_after += after
        print(f"[OK] {name}: {before // 1024}KB -> {after // 1024}KB  ({src.name})")
    print(f"\n合计 {total_before // 1024}KB -> {total_after // 1024}KB")


if __name__ == "__main__":
    main()
