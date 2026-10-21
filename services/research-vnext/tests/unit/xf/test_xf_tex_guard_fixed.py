"""RV-XF · LaTeX 前置校验（`_validate_tex_source`）回归单测。

覆盖 QA 黑盒证伪的漏过族在本层被**拒**，且合法文档**不被误伤**。
纯函数，不触网 / 不编译。
"""

from __future__ import annotations

import pytest

from research_vnext.core.xf.latex import _validate_tex_source
from research_vnext.core.xf.models import XF_TEX_SOURCE_INVALID, XfError


def _rejected(src: str) -> bool:
    try:
        _validate_tex_source(src)
        return False
    except XfError as e:
        assert e.code == XF_TEX_SOURCE_INVALID
        assert e.http_status == 400
        return True


# --------------------------------------------------------------------------
# 必须拒绝（QA 漏过族）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        # ^^ 十六进制转义家族（catcode-0）
        "^^5cinput{/etc/passwd}",
        "A^^5czznotacmd B",
        r"^^5cwrite18{touch /work/PWNED}",
        r"^^5cimmediate^^5copenout1=/work/x.tex",
        "^^5ccsname write^^5cendcsname",
        # 文件读取宏包 / 内部命令
        r"\lstinputlisting{/etc/passwd}",
        r"\VerbatimInput{/etc/passwd}",
        r"\verbatiminput{/etc/passwd}",
        r"\@@input{/etc/passwd}",
        r"\includegraphics{/etc/passwd}",
        r"\input{/etc/passwd}",
        r"\input{../../etc/passwd}",
        r"\include{secret}",
        r"\openout1=evil.tex",
        r"\openin0=/etc/passwd",
        r"\catcode`\@=11",
        r"\directlua{os.execute('id')}",
        r"\luaexec{os.execute('id')}",
        r"\special{ps: system('id')}",
        r"\read0 to \x",
        # 用户点名的既有用例必须仍被拒（不回归）
        r"\write18{rm -rf /}",
        # 危险宏包
        r"\usepackage{shellesc}",
        r"\usepackage{minted}",
        r"\usepackage{listings}",
        r"\usepackage{fancyvrb}",
        r"\usepackage[utf8]{inputenc}\usepackage{listings}",
        # 绝对路径 / 上跳（宽口径）
        r"\includegraphics{/root/.ssh/id_rsa}",
        r"\includegraphics{../../secret}",
        r"\includegraphics{C:\Windows\win.ini}",
    ],
)
def test_validator_rejects_dangerous(src: str) -> None:
    assert _rejected(src), f"前置校验竟漏过：{src!r}"


# --------------------------------------------------------------------------
# 不得误伤（合法文档 / 合法数学 / 相对路径 includegraphics）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "\\documentclass{article}\n\\begin{document}\nHello world.\n\\end{document}",
        "\\documentclass{article}\\begin{document}$\\int_0^1 x^2\\,dx=\\frac{1}{3}$\\end{document}",
        "\\documentclass{article}\\begin{document}$a<b$\\end{document}",
        "\\documentclass{article}\\begin{document}$x^{2}+y^{3}$\\end{document}",
        "\\documentclass{article}\\usepackage{amsmath}\\begin{document}\\begin{equation}E=mc^2\\end{equation}\\end{document}",
        "\\documentclass{article}\\begin{document}\\includegraphics{figures/plot.pdf}\\end{document}",
        "\\documentclass{article}\\begin{document}\\url{https://example.com/a/b}\\end{document}",
        "\\documentclass{article}\\begin{document}100\\% done\\end{document}",
    ],
)
def test_validator_allows_legitimate(src: str) -> None:
    _validate_tex_source(src)  # 不抛即通过
