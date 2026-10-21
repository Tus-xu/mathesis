"""Classroom Typography Policy + Style Tokens（引擎中立，值 = 语义档位而非坐标）。

Policy（质量基线，非硬编码进 domain schema）：
- 普通页主标题 ≥ 32pt；
- 正文 ≥ 17pt（本套取 18pt）；
- 关键定义/结论/公式明显高于正文（22–26pt）；
- 辅助说明可小（15/12pt）但不得承载主要教学信息；
- 行距 body ≥ 1.35；禁止靠缩小字号/裁内容解决 overflow（由 planner density 分流）。

颜色角色对齐 DECISIONS §9 教师端语义色；页面不得自造颜色/圆角/阴影。
"""
from __future__ import annotations

from typing import Any

POLICY_VERSION = "classroom-typo-policy-1.0.0"

# 字号档位（pt）
TYPE_SCALE_PT: dict[str, float] = {
    "display": 44.0,    # hero 封面主标题
    "title": 32.0,      # 普通页主标题（policy 下限 32）
    "emphasis": 22.0,   # 定义横幅/关键结论
    "equation": 26.0,   # 公式焦点行
    "eq-inline": 20.0,  # 正文块内独立数学行
    "body": 18.0,       # 正文（policy 下限 17）
    "support": 15.0,    # 辅助说明（不得承载主要教学信息）
    "caption": 12.0,    # 页脚/元信息
}

LINE_SPACING: dict[str, float] = {"display": 1.12, "title": 1.15, "body": 1.4, "support": 1.35}

SPACING_SCALE_IN: dict[str, float] = {
    "page-margin": 0.90, "header-gap": 0.42, "block-gap": 0.34, "gutter": 0.52,
}

COLOR_ROLES: dict[str, str] = {
    "ink": "#1E293B", "muted": "#64748B", "subtle": "#94A3B8",
    "brand": "#2563EB", "brand-bg": "#EFF6FF", "cyan": "#06B6D4",
    "surface": "#FFFFFF", "soft-bg": "#F1F5F9", "line": "#E2E8F0",
    "ok": "#22C55E", "warn": "#F59E0B", "danger": "#EF4444",
}

FONT_STACK = {"primary": "Microsoft YaHei", "fallback": "SimSun", "latin-digits": "Inter"}

# 语义强调样式（slot → 档位引用；引擎据此映射本地格式）
EMPHASIS_STYLES: dict[str, dict[str, Any]] = {
    "math-line": {"size": "eq-inline", "bold": True, "color": "brand"},
    "equation-focus-line": {"size": "equation", "bold": True, "color": "brand"},
    "definition-banner": {"size": "emphasis", "bold": True, "color": "ink", "bg": "brand-bg"},
    "kicker": {"size": "support", "bold": True, "color": "brand"},
    "numbered-dot": {"size": "body", "bold": True, "color": "brand"},
}

BORDER_POLICY = {"radius": {"card": 0.10, "banner": 0.08, "chip": 0.5},
                 "stroke-pt": 1.0, "shadow": "none-flat"}

IMAGE_TREATMENT = {"fit": "contain", "border": "line", "caption": "support"}

ACCENT_MOTIFS = ["accent-bar", "numbered-dot", "definition-banner", "kicker-phase-tag"]


def tokens_for_engine() -> dict[str, Any]:
    """ResolvedVisualPlan 携带的 tokens 摘要（引擎 adapter 据此映射本地格式）。"""
    return {
        "policy_version": POLICY_VERSION,
        "font": FONT_STACK,
        "type_scale_pt": TYPE_SCALE_PT,
        "line_spacing": LINE_SPACING,
        "spacing_in": SPACING_SCALE_IN,
        "color_roles": COLOR_ROLES,
        "emphasis_styles": EMPHASIS_STYLES,
        "border_policy": BORDER_POLICY,
        "image_treatment": IMAGE_TREATMENT,
        "accent_motifs": ACCENT_MOTIFS,
    }
