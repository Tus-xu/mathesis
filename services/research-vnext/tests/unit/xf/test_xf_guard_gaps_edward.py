"""RV-XF · `guard_latex` 漏过面（QA/Edward 对抗性验证新增）。

设计：
- `test_guard_blocks_*`：应当拦住的经典控制序列 —— 期望**绿**。
- `test_guard_gap_*`：经真机 xelatex 复现的**漏过** payload —— 期望 `guard_latex`
  拦截；当前实现漏过 → 用例 **XFAIL**（strict=False），作为「已知缺口」的可持续
  回归证据。缺口修复后这些会转为 XPASS，提示可收敛为强断言。

证据（真机 docker math-arena-tex:0.1.0 复现，见 tests/real_sandbox/）：
- `^^5c` 是 catcode-0 的 `\\`，`^^5cinput{/etc/passwd}` 真被执行；
- `\\lstinputlisting{/etc/passwd}` / `\\VerbatimInput{/etc/passwd}` 编译成功，
  **/etc/passwd 明文出现在返回的 PDF 文本层**。
"""

from __future__ import annotations

import pytest

from research_vnext.core.export.serializer import SerializerError, guard_latex


def _blocked(payload: str) -> bool:
    try:
        guard_latex(payload)
        return False
    except SerializerError:
        return True


# --------------------------------------------------------------------------
# 应当拦住（绿）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        r"\write18{rm -rf /}",
        r"\write 18 {x}",
        r"\write{out}{x}",
        r"\write0{x}",
        r"\immediate\write-1{x}",
        r"\immediate\write18{id}",
        r"\input{/etc/passwd}",
        r"\input{../../etc/passwd}",
        r"\include{secret}",
        r"\openout1=evil.tex",
        r"\openin0=/etc/passwd",
        r"\catcode`\@=11",
        r"\csname write\endcsname",
        r"\directlua{os.execute('id')}",
        r"\luaexec{os.execute('id')}",
        r"\special{ps: system('id')}",
        r"\read0 to \x",
    ],
)
def test_guard_blocks_documented_primitives(payload: str) -> None:
    assert _blocked(payload), f"guard_latex 竟漏过了 {payload!r}"


# --------------------------------------------------------------------------
# 已知漏过（XFAIL）——catcode `^^5c` 十六进制转义家族
# --------------------------------------------------------------------------


@pytest.mark.xfail(strict=False, reason="GAP: ^^5c 十六进制转义绕过逐字正则；真机执行 \\input")
@pytest.mark.parametrize(
    "payload",
    [
        "^^5cwrite18{id}",
        "^^5cinput{/etc/passwd}",
        "^^5copenout1=evil.tex",
        "^^5ccatcode`\\@=11",
        "^^5ccsname write^^5cendcsname",
        "^^5cimmediate^^5cwrite18{id}",
    ],
)
def test_guard_gap_hex_escape_family(payload: str) -> None:
    assert _blocked(payload), f"guard_latex 漏过了 ^^5c 转义：{payload!r}"


# --------------------------------------------------------------------------
# 已知漏过（XFAIL）——文件读取宏包 / 内部命令
# --------------------------------------------------------------------------


@pytest.mark.xfail(strict=False, reason="GAP: 未拦文件读取宏包；真机 /etc/passwd 明文进 PDF")
@pytest.mark.parametrize(
    "payload",
    [
        r"\lstinputlisting{/etc/passwd}",
        r"\VerbatimInput{/etc/passwd}",
        r"\@@input{/etc/passwd}",
        r"\includegraphics{/etc/passwd}",
    ],
)
def test_guard_gap_file_reading_macros(payload: str) -> None:
    assert _blocked(payload), f"guard_latex 漏过了文件读取宏：{payload!r}"


@pytest.mark.xfail(strict=False, reason="GAP: 未拦危险宏包加载（shellesc/minted 需 -shell-escape，纵深不足）")
@pytest.mark.parametrize(
    "payload",
    [
        r"\usepackage{shellesc}\ShellEscape{id}",
        r"\usepackage{minted}\inputminted{python}{/etc/passwd}",
        r"\usepackage{listings}",
        r"\usepackage{fancyvrb}",
    ],
)
def test_guard_gap_dangerous_packages(payload: str) -> None:
    assert _blocked(payload), f"guard_latex 漏过了危险宏包：{payload!r}"
