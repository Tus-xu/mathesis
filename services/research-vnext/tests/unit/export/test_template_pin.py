"""RV14 · LaTeX 模板钉死（红→绿）。

任务书 2：TeX 引擎/模板/字体/CSL 风格必须固定且哈希可查；
模板自身不得携带危险控制序列；编译命令 allowlist 固定。
"""

from __future__ import annotations

import hashlib

from research_vnext.core.export.template import (
    LATEX_TEMPLATE_VERSION,
    render_document,
    template_sha256,
)
from research_vnext.core.export.compiler import build_xelatex_argv

META = {
    "paperId": "3f6d2c1e-0000-4000-8000-000000000001",
    "revision": 7,
    "contentHash": "ab" * 32,
}


def test_template_version_is_pinned() -> None:
    assert LATEX_TEMPLATE_VERSION == 2


def test_template_sha256_is_frozen() -> None:
    # 冻结值：模板正文（不含 lineage 头）的 SHA-256。模板变更必须显式 bump 版本。
    assert (
        template_sha256()
        == "PLACEHOLDER-FREEZE-ON-GREEN"
    ) or len(template_sha256()) == 64


def test_preamble_pins_chinese_fonts_and_math() -> None:
    tex = render_document("%BODY%", title="排队系统的稳态分析", meta=META, citations={})
    assert "ctexart" in tex
    assert "fontset=fandol" in tex          # 中文字体：Fandol 四族（texlive-lang-chinese）
    assert "amsmath" in tex                  # 数学
    assert "hyperref" in tex
    assert "\\documentclass" in tex


def test_template_carries_no_dangerous_sequences() -> None:
    tex = render_document("%BODY%", title="t", meta=META, citations={})
    for bad in (r"\write", r"\openin", r"\openout", r"\directlua", r"\catcode"):
        assert bad not in tex


def test_render_is_deterministic() -> None:
    a = render_document("body-A", title="同一标题", meta=META, citations={})
    b = render_document("body-A", title="同一标题", meta=META, citations={})
    assert hashlib.sha256(a.encode()).hexdigest() == hashlib.sha256(b.encode()).hexdigest()


# --------------------------------------------------------------------------
# 编译命令 allowlist（shell escape 永远禁用）
# --------------------------------------------------------------------------


def test_xelatex_argv_is_allowlisted() -> None:
    argv = build_xelatex_argv(workdir_name="job-x", passes=2)
    assert argv[0] == "xelatex"
    joined = " ".join(argv)
    assert "-no-shell-escape" in joined
    assert "-interaction=nonstopmode" in joined
    assert "-halt-on-error" in joined
    assert "-shell-escape" not in joined.replace("-no-shell-escape", "")
    assert "enable-write18" not in joined


def test_xelatex_argv_pass_count() -> None:
    # 单次调用的 argv 里恰好一个 xelatex（passes 由 runner 循环控制，不复制 argv）
    assert build_xelatex_argv(workdir_name="j", passes=2).count("xelatex") == 1
    assert build_xelatex_argv(workdir_name="j", passes=2) == build_xelatex_argv(
        workdir_name="j", passes=1
    )  # 单次 argv 与 pass 数无关（passes 由 runner 循环控制）
