"""QA(Edward) 临时探针：枚举 guard_latex 对各类危险控制序列的实际拦截行为。

纯函数，不触网、不编译。运行：
    PYTHONPATH=. .venv/Scripts/python.exe tests/real_sandbox/probe_guard_edward.py
"""
from __future__ import annotations

from research_vnext.core.export.serializer import SerializerError, guard_latex

# 每条：(标签, payload)。payload 是「会被写进 main.tex 的整份源码里的危险片段」。
CASES: list[tuple[str, str]] = [
    # ---- baseline：应当被拦 ----
    ("write18 braces", r"\write18{rm -rf /}"),
    ("write18 space", r"\write 18 {x}"),
    ("write braces", r"\write{out}{x}"),
    ("immediate write18", r"\immediate\write18{id}"),
    ("input abs", r"\input{/etc/passwd}"),
    ("input rel", r"\input{../../etc/passwd}"),
    ("include", r"\include{secret}"),
    ("openout", r"\openout1=evil.tex"),
    ("openin", r"\openin0=/etc/passwd"),
    ("catcode", r"\catcode`\@=11"),
    ("csname", r"\csname write\endcsname"),
    ("directlua", r"\directlua{os.execute('id')}"),
    ("luaexec", r"\luaexec{os.execute('id')}"),
    ("special", r"\special{ps: system('id')}"),
    ("read", r"\read0 to \x"),
    # ---- 变体/绕过候选 ----
    ("uppercase WRITE18", r"\WRITE18{id}"),
    ("mixed Write18", r"\write18 vs \Write18"),
    ("percent-split wri%te", "\\wri%te18{id}"),
    ("newline-split wri\\nte", "\\wri\nte18{id}"),
    ("caret-hex ^^5cwrite18", "^^5cwrite18{id}"),
    ("caret-hex ^^5cinput", "^^5cinput{/etc/passwd}"),
    ("caret-hex ^^5copenout", "^^5copenout1=evil.tex"),
    ("caret-hex ^^5ccatcode", "^^5ccatcode`\\@=11"),
    ("caret-hex ^^5ccsname", "^^5ccsname write^^5cendcsname"),
    ("lstinputlisting", r"\lstinputlisting{/etc/passwd}"),
    ("VerbatimInput", r"\VerbatimInput{/etc/passwd}"),
    ("usepackage shellesc", r"\usepackage{shellesc}\ShellEscape{id}"),
    ("usepackage minted", r"\usepackage{minted}"),
    ("usepackage listings", r"\usepackage{listings}"),
    ("def redefine mywrite", r"\def\mywrite{hello}\mywrite"),
    ("newcommand", r"\newcommand{\foo}{bar}\foo"),
    ("@@input", r"\@@input{/etc/passwd}"),
    ("csname concat", r"\csname wr\endcsname ite18"),
    ("write with digit 0", r"\write0{x}"),
    ("write2 no brace", r"\immediate\write-1{x}"),
    ("Includegraphics abs", r"\includegraphics{/etc/passwd}"),
    ("char-hex build", r"\char92 write18"),
    ("input without space", r"\input/ etc/passwd"),
]


def main() -> None:
    passed, blocked = [], []
    for label, payload in CASES:
        try:
            guard_latex(payload)
            passed.append((label, payload))
        except SerializerError as e:
            blocked.append((label, payload, e.code))
    print(f"===== BLOCKED by guard_latex ({len(blocked)}) =====")
    for label, payload, code in blocked:
        print(f"  [BLOCK] {label:28s} code={code}")
    print(f"\n===== PASSED guard_latex ({len(passed)})  <-- 这些是候选漏过项 =====")
    for label, payload in passed:
        print(f"  [PASS ] {label:28s} repr={payload!r}")


if __name__ == "__main__":
    main()
