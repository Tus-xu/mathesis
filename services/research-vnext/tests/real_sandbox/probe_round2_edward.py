"""QA(Edward) 第2轮：攻击工程师新加的 `_validate_tex_source`，并核验 G5 容器清扫。

运行：PYTHONPATH=. .venv/Scripts/python.exe tests/real_sandbox/probe_round2_edward.py
"""
from __future__ import annotations

import subprocess
import time

from research_vnext.core.xf.latex import compile_latex_sync
from research_vnext.core.xf.models import XfError

D = "\\documentclass{{article}}\n\\begin{{document}}\n{body}\n\\end{{document}}\n"


def doc(body: str) -> str:
    return D.format(body=body)


# (标签, 源码, 期望)
CASES: list[tuple[str, str, str]] = [
    ("^^5cinput{/etc/passwd}", doc("X^^5cinput{/etc/passwd}Y"), "应 400（^^ 被拒）"),
    ("^^5cwrite18{id}", doc("^^5cwrite18{id}"), "应 400"),
    ("\\VerbatimInput{/etc/passwd}", doc("\\VerbatimInput{/etc/passwd}"), "应 400"),
    ("\\lstinputlisting{/etc/passwd}", doc("\\lstinputlisting{/etc/passwd}"), "应 400"),
    ("\\input{/etc/passwd}", doc("\\input{/etc/passwd}"), "应 400"),
    ("\\include{/etc/passwd}", doc("\\include{/etc/passwd}"), "应 400"),
    ("\\openout1=/etc/x", doc("\\openout1=/etc/x"), "应 400"),
    ("\\write18{rm -rf /}", doc("\\write18{rm -rf /}"), "应 400"),
    ("\\includegraphics{/etc/passwd}", doc("\\includegraphics{/etc/passwd}"), "应 400（绝对路径）"),
    ("\\InputIfFileExists{/etc/passwd}{}{}", doc("\\InputIfFileExists{/etc/passwd}{}{}"), "应 400"),
    ("\\IfFileExists{/etc/passwd}{}{}", doc("\\IfFileExists{/etc/passwd}{}{}"), "应 400"),
    ("\\@input{/etc/passwd}", doc("\\@input{/etc/passwd}"), "应 400"),
    ("\\usepackage{fancyvrb}", "\\documentclass{article}\\usepackage{fancyvrb}\\begin{document}x\\end{document}", "应 400"),
    ("\\usepackage{minted}", "\\documentclass{article}\\usepackage{minted}\\begin{document}x\\end{document}", "应 400"),
    ("\\input{../../etc/passwd}", doc("\\input{../../etc/passwd}"), "应 400"),
    # 规避新「绝对路径正则」的候选（看是否能进容器）
    ("EVASION \\includegraphics{\\string/etc/passwd}", doc("\\includegraphics{\\string/etc/passwd}"), "若 400=挡住；否则进了容器"),
    ("EVASION \\includegraphics{\\char47etc/passwd}", doc("\\includegraphics{\\char47etc/passwd}"), "若 400=挡住；否则进了容器"),
    ("EVASION \\XeTeXpdffile\"/etc/passwd\"", doc("\\XeTeXpdffile\"/etc/passwd\""), "若 400=挡住；否则进了容器"),
    ("EVASION \\input via \\expandafter{/etc}", doc("\\expandafter\\input{/etc/passwd}"), "应 400"),
    # 合法文档（防过度拦截）
    ("LEGIT $x^2+1$", doc("公式 $x^2+1$ 正常。"), "应 200 + 真 PDF"),
    ("LEGIT hyperref https://", "\\documentclass{article}\\usepackage{hyperref}\\begin{document}\\href{https://example.com}{x}\\end{document}", "应 200"),
    ("LEGIT amsmath + 图片相对名", "\\documentclass{article}\\usepackage{amsmath}\\begin{document}a\\end{document}", "应 200"),
]


def containers() -> list[str]:
    p = subprocess.run(["docker", "ps", "-q", "--filter", "ancestor=math-arena-tex:0.1.0"],
                       capture_output=True, text=True, timeout=30, check=False)
    return [x for x in (p.stdout or "").splitlines() if x.strip()]


def main() -> None:
    print("######## 攻击新守卫 _validate_tex_source ########")
    for label, src, note in CASES:
        try:
            pdf, meta = compile_latex_sync(src, "main.tex")
            print(f"[OK   ] {label:46s} bytes={len(pdf):6d} pages={meta.get('pages')}  ({note})")
        except XfError as e:
            print(f"[{e.http_status:>3}] {label:46s} {e.code:24s} {e.message[:60]!r}  ({note})")

    print("\n######## G5 · 无限循环后容器残留（应 0）########")
    before = len(containers())
    t0 = time.perf_counter()
    try:
        compile_latex_sync(doc("\\loop\\iftrue\\repeat"), "main.tex")
    except XfError as e:
        print(f"  loop -> {e.code} http={e.http_status} {time.perf_counter()-t0:.1f}s")
    time.sleep(2)
    after = containers()
    print(f"  容器: before={before} after={len(after)} {after}")


if __name__ == "__main__":
    main()
