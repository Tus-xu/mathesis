"""RV14 v2 · 行内富文本与列表导出（图片样式对齐）。

用户样例（8 Sensitivity Analysis 页）要求的排版能力：
1. `**粗体**` → \textbf{}（Key Insight 段）；
2. `$…$` 行内公式原样进入 LaTeX（R_ref = 0.300Ω 等）；
3. `- ` 圆点列表 → itemize（四参数列表）；
4. 文本其余部分照常转义（守卫不受影响）。
"""

from __future__ import annotations

import pytest

from research_vnext.core.export.serializer import (
    SERIALIZER_VERSION,
    SerializerError,
    serialize_latex,
    serialize_markdown,
)

META = {"paperId": "3f6d2c1e-0000-4000-8000-000000000001", "revision": 7, "contentHash": "ab" * 32}


def test_inline_bold_becomes_textbf_and_survives_escape() -> None:
    blocks = [{"id": "p1", "type": "paragraph", "nodeRev": 1,
               "text": "**Key Insight**: 热质量与扩散率主导敏感度，50% 扰动仍稳健。"}]
    tex = serialize_latex(blocks, meta=META, citations={})
    assert "\\textbf{Key Insight}" in tex
    assert "50\\%" in tex  # 粗体外文本照常转义
    md = serialize_markdown(blocks, meta=META)
    assert "**Key Insight**" in md


def test_inline_math_passes_through_and_is_guarded() -> None:
    blocks = [{"id": "p1", "type": "paragraph", "nodeRev": 1,
               "text": "参考内阻 $R_{ref} = 0.300\\Omega$，扩散率 $k = 6.67 \\times 10^{-4}s^{-1}$。"}]
    tex = serialize_latex(blocks, meta=META, citations={})
    assert "$R_{ref} = 0.300\\Omega$" in tex
    assert "$k = 6.67 \\times 10^{-4}s^{-1}$" in tex
    md = serialize_markdown(blocks, meta=META)
    assert "$R_{ref} = 0.300\\Omega$" in md


def test_inline_math_forbidden_sequence_rejected_with_locator() -> None:
    blocks = [{"id": "evil-p", "type": "paragraph", "nodeRev": 1,
               "text": "see $\\input{/etc/passwd}$ for details"}]
    with pytest.raises(SerializerError) as ei:
        serialize_latex(blocks, meta=META, citations={})
    assert ei.value.code == "EXPORT_FORBIDDEN_SEQUENCE"
    assert ei.value.node_id == "evil-p"


def test_bullet_list_renders_itemize() -> None:
    blocks = [{"id": "lst", "type": "paragraph", "nodeRev": 1,
               "text": "- 参考内阻 $R_{ref} = 0.300\\Omega$\n"
                       "- KiBaM 容量比 $c = 0.60$\n"
                       "- 热质量 $C_{th} = 40.0J/K$"}]
    tex = serialize_latex(blocks, meta=META, citations={})
    assert "\\begin{itemize}" in tex
    assert tex.count("\\item") == 3
    assert "$R_{ref} = 0.300\\Omega$" in tex
    assert "- " not in tex  # 原始 markdown 前缀不残留
    md = serialize_markdown(blocks, meta=META)
    md_list_rows = [ln for ln in md.splitlines() if ln.strip().startswith("- ")]
    assert len(md_list_rows) == 3  # md 侧保留列表原样（按行计数，不受其他文本干扰）


def test_ordered_list_renders_enumerate() -> None:
    blocks = [{"id": "lst", "type": "paragraph", "nodeRev": 1,
               "text": "1. 全局参数敏感度\n2. 环境温度影响\n3. 电池老化效应"}]
    tex = serialize_latex(blocks, meta=META, citations={})
    assert "\\begin{enumerate}" in tex and tex.count("\\item") == 3


def test_plain_paragraph_with_leading_dash_word_not_a_list() -> None:
    """普通段落恰好含 '- '（如负号语境）不误判——需整段都是列表项。"""
    blocks = [{"id": "p1", "type": "paragraph", "nodeRev": 1,
               "text": "温度 - 25 度下测量"}]  # ' - ' 前有空格非行首列表
    tex = serialize_latex(blocks, meta=META, citations={})
    assert "\\begin{itemize}" not in tex


def test_serializer_version_bumped_to_2() -> None:
    assert SERIALIZER_VERSION == "2"


def test_sample_page_style_end_to_end() -> None:
    """按用户样例页（8 Sensitivity Analysis）复刻的结构可导出、确定性。"""
    import hashlib

    blocks = [
        {"id": "sec8", "type": "section", "nodeRev": 1, "text": "Sensitivity Analysis", "number": "8"},
        {"id": "p1", "type": "paragraph", "nodeRev": 1,
         "text": "需求 3 要求量化 Time-to-Empty（TTE）预测如何随建模假设、"
                 "关键参数扰动与使用模式变化而波动。"},
        {"id": "h81", "type": "heading", "nodeRev": 1, "text": "Global Sensitivity Analysis", "level": 2},
        {"id": "p2", "type": "paragraph", "nodeRev": 1,
         "text": "Sobol 一阶指数度量单位参数变化引起的 TTE 相对变化："},
        {"id": "eq1", "type": "equation", "nodeRev": 1,
         "latex": r"S_i = \left| \frac{\Delta TTE_i / TTE_{base}}{\Delta\theta_i / \theta_i} \right|",
         "display": True},
        {"id": "lst", "type": "paragraph", "nodeRev": 1,
         "text": "- 参考内阻 $R_{ref} = 0.300\\Omega$\n"
                 "- KiBaM 容量比 $c = 0.60$\n"
                 "- KiBaM 扩散率 $k = 6.67 \\times 10^{-4}s^{-1}$\n"
                 "- 热质量 $C_{th} = 40.0J/K$"},
        {"id": "p3", "type": "paragraph", "nodeRev": 1,
         "text": "每个参数扰动 ±25%。敏感度排序为："},
        {"id": "eq2", "type": "equation", "nodeRev": 1,
         "latex": r"C_{th}(0.038) > k(0.033) > c(0.026) > R_{ref}(0.017)", "display": True},
        {"id": "p4", "type": "paragraph", "nodeRev": 1,
         "text": "**Key Insight**: 热质量与 KiBaM 扩散率主导敏感度，"
                 "证实 H-E-T 框架中热-电耦合与充电恢复机制的重要性。"},
    ]
    a = serialize_latex(blocks, meta=META, citations={})
    b = serialize_latex(blocks, meta=META, citations={})
    assert hashlib.sha256(a.encode()).hexdigest() == hashlib.sha256(b.encode()).hexdigest()
    assert "\\begin{itemize}" in a
    assert "\\textbf{Key Insight}" in a
    assert r"\left|" in a
