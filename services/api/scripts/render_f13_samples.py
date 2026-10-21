"""F13 验证样例渲染：3 道典型题（函数/立体几何/解析几何）→ 渐进帧 SVG + demo 数据。

产出：
- deliverables/f13-visual-guidance/samples/*.svg          每帧 SVG（人工核对/效果图源）
- deliverables/f13-visual-guidance/samples/samples.json   载荷清单（含不变量检查结果）
- frontend-f13/demo/src/figureData.js                     demo 组件数据（真实渲染 data_uri）

frontend-f13 旧前端目录已归档到 D:\math-arena-legacy-20260914\，需要重跑本脚本时用
环境变量 F13_DEMO_DATA 指到归档里的 figureData.js 路径。

用法：cd services/api && .venv\\Scripts\\python.exe -m scripts.render_f13_samples
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from app.services.figure_renderer import (
    check_svg_invariants,
    derive_figure_frames,
    render_figure,
    render_figure_frames,
)

# Windows 控制台 GBK 下打印数学符号（²/√）会崩，统一 UTF-8 输出
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[3]  # scripts -> api -> services -> 仓库根
SAMPLES = REPO / "deliverables" / "f13-visual-guidance" / "samples"
# frontend-f13 已归档（见模块 docstring），默认路径不存在时用 F13_DEMO_DATA 覆盖
DEMO_DATA = Path(
    os.environ.get("F13_DEMO_DATA") or (REPO / "frontend-f13" / "demo" / "src" / "figureData.js")
)

# ---- P1 函数题：y = x² - 2x - 3 的图像与 x 轴交点 ----
P1_FIG = {
    "type": "function",
    "params": {
        "curves": [{"expr": "x**2-2*x-3", "label": "y=x²-2x-3"}],
        "x_range": [-3, 5],
        "y_range": [-5, 6],
        "points": [
            {"x": -1, "y": 0, "label": "(-1,0)"},
            {"x": 3, "y": 0, "label": "(3,0)"},
            {"x": 1, "y": -4, "label": "顶点(1,-4)"},
        ],
        "ticks": {"x": 1, "y": 1},
    },
}

# ---- P2 立体几何题：棱长 2 的正方体外接球 ----
# 正方体中心 (0,0,1)，外接球半径 = a·√3/2 = √3
P2_FIG = {
    "type": "sphere",
    "params": {
        "r": round(3**0.5, 4),
        "center": [0, 0, 1],
        "center_label": "O",
        "equator": True,
        "solid": {
            "type": "cube",
            "params": {"a": 2},
            "labels": {"A": "A", "B": "B", "C": "C", "D": "D"},
        },
    },
}

# ---- P3 解析几何题：直线 y=x-1 与圆 x²+y²=4 的交点 ----
# 交点：(1±√7)/2, (1±√7)/2 - 1
_x1 = round((1 + 7**0.5) / 2, 3)   # ≈ 1.823
_y1 = round(_x1 - 1, 3)            # ≈ 0.823
_x2 = round((1 - 7**0.5) / 2, 3)   # ≈ -0.823
_y2 = round(_x2 - 1, 3)            # ≈ -1.823
P3_FIG = {
    "type": "function",
    "params": {
        "curves": [
            {"expr": "sqrt(4-x**2)", "label": "x²+y²=4"},
            {"expr": "-sqrt(4-x**2)", "label": ""},
            {"expr": "x-1", "label": "y=x-1"},
        ],
        "x_range": [-2.6, 2.6],
        "y_range": [-2.6, 2.6],
        "points": [
            {"x": _x1, "y": _y1, "label": "A"},
            {"x": _x2, "y": _y2, "label": "B"},
        ],
        "ticks": {"x": 1, "y": 1},
    },
}

PROBLEMS = [
    {
        "key": "p1_function",
        "title": "函数题：作出 y=x²-2x-3 的图像，求与 x 轴交点",
        "fig": P1_FIG,
        "step_no": 1,
        "caption": "先观察抛物线的开口方向与对称轴位置",
    },
    {
        "key": "p2_solid",
        "title": "立体几何题：棱长 2 的正方体外接球",
        "fig": P2_FIG,
        "step_no": 1,
        "caption": "正方体 8 个顶点都在球面上，球心在体对角线中点",
    },
    {
        "key": "p3_analytic",
        "title": "解析几何题：直线 y=x-1 与圆 x²+y²=4 的交点",
        "fig": P3_FIG,
        "step_no": 2,
        "caption": "联立方程求交点，先画出圆与直线的位置关系",
    },
]


def main() -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"problems": []}

    for prob in PROBLEMS:
        key = prob["key"]
        frames = derive_figure_frames(prob["fig"])
        print(f"\n== {prob['title']} ==")
        entry: dict = {"key": key, "title": prob["title"], "frames": []}
        for i, fr in enumerate(frames, start=1):
            svg = render_figure(fr["figure"])
            problems = check_svg_invariants(fr["figure"], svg)
            fatal = [p for p in problems if p["severity"] == "fatal"]
            path = SAMPLES / f"{key}_f{i}_{fr['label']}.svg"
            path.write_text(svg, encoding="utf-8")
            print(
                f"  帧{i}「{fr['label']}」-> {path.name} "
                f"({len(svg)}B, 不变量: {'PASS' if not fatal else 'FATAL ' + fatal[0]['msg']})"
            )
            entry["frames"].append(
                {"label": fr["label"], "file": path.name, "svg_bytes": len(svg),
                 "invariants": problems}
            )
        payload = render_figure_frames(
            prob["fig"], step_no=prob["step_no"], caption=prob["caption"]
        )
        entry["figure_event"] = payload
        manifest["problems"].append(entry)

    # 清单
    (SAMPLES / "samples.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # demo 数据（真实渲染 data_uri，供 MathFigure 组件演示）
    demo_items = [
        {
            "step_no": p["step_no"],
            "caption": p["caption"],
            "frames": entry["figure_event"]["frames"],
            "figure_params": p["fig"],
        }
        for p, entry in zip(PROBLEMS, manifest["problems"], strict=True)
    ]
    js = (
        "// 由 scripts/render_f13_samples.py 生成（figure_renderer 真实渲染，勿手改）\n"
        f"export const FIGURES = {json.dumps(demo_items, ensure_ascii=False, indent=2)}\n"
    )
    DEMO_DATA.write_text(js, encoding="utf-8")
    print(f"\n已写出 {len(manifest['problems'])} 题的帧 SVG -> {SAMPLES}")
    print(f"demo 数据 -> {DEMO_DATA}")


if __name__ == "__main__":
    main()
