"""QA(Edward) 探针：把 guard_latex 放行的危险 payload 真送进 docker xelatex 内核，
观察是否真的被执行/读文件。

运行：
    PYTHONPATH=. .venv/Scripts/python.exe tests/real_sandbox/probe_latex_edward.py
"""
from __future__ import annotations

import time

from research_vnext.core.xf.latex import compile_latex_sync
from research_vnext.core.xf.models import XfError

DOC = "\\documentclass{{article}}\n\\begin{{document}}\n{body}\n\\end{{document}}\n"


def wrap(body: str) -> str:
    return DOC.format(body=body)


# (标签, 整份源码, 期望观察点)
CASES: list[tuple[str, str, str]] = [
    # --- 对照：正常文档 ---
    ("BASELINE ok-doc", wrap("Hello world."), "应成功产出 PDF"),
    # --- 对照：guard 拦截（不应进容器，应 400） ---
    ("GUARD-BLOCKED write18", wrap(r"\write18{id}"), "应 400 XF_TEX_SOURCE_INVALID"),
    # --- ^^5c 十六进制转义：把 '\' 藏起来绕过 guard ---
    ("CANARY ^^5czznotacmd", wrap("A^^5czznotacmd B"),
     "若 ^^5c 生效=>Undefined control sequence 编译失败；否则成功(字面)"),
    ("EXPLOIT ^^5cinput /etc/passwd", wrap("X^^5cinput{/etc/passwd}Y"),
     "若 \input 生效=>把 /etc/passwd 读入文档"),
    ("EXPLOIT ^^5cwrite18 touch file", wrap(r"^^5cwrite18{touch /work/PWNED_VIA_WRITE18}"),
     "若 shell-escape 未关=>在 /work 生成 PWNED_VIA_WRITE18"),
    ("EXPLOIT ^^5copenout write file",
     wrap(r"^^5cimmediate^^5copenout1=/work/PWNED_VIA_OPENOUT.tex "
          r"^^5cimmediate^^5cwrite1{owned} ^^5ccloseout1"),
     "若生效=>在 /work 生成 PWNED_VIA_OPENOUT.tex"),
    ("EXPLOIT ^^5cinput nonexistent", wrap("X^^5cinput{/nonexistent_zzz_qa}Y"),
     "若 \input 生效=>'I can't find file' 失败"),
    # --- 文件读取宏包（guard 未拦） ---
    ("EXPLOIT lstinputlisting", "\\documentclass{article}\n\\usepackage{listings}\n"
     "\\begin{document}\n\\lstinputlisting{/etc/passwd}\n\\end{document}\n",
     "若生效=>typeset /etc/passwd"),
    ("EXPLOIT VerbatimInput", "\\documentclass{article}\n\\usepackage{fancyvrb}\n"
     "\\begin{document}\n\\VerbatimInput{/etc/passwd}\n\\end{document}\n",
     "若生效=>typeset /etc/passwd"),
    # --- 资源耗尽（小规模，观察机制而非极限压测） ---
    ("RESOURCE infinite loop", wrap(r"\loop\iftrue\repeat"),
     "应被超时/容量上限终止，服务不倒"),
]


def main() -> None:
    import sys

    only = sys.argv[1] if len(sys.argv) > 1 else ""
    for label, source, note in CASES:
        if only and only.lower() not in label.lower():
            continue
        t0 = time.perf_counter()
        try:
            pdf, meta = compile_latex_sync(source, "main.tex")
            dt = time.perf_counter() - t0
            print(f"[OK   ] {label:34s} {dt:6.1f}s bytes={len(pdf)} "
                  f"pages={meta.get('pages')}  ({note})")
        except XfError as e:
            dt = time.perf_counter() - t0
            print(f"[XfErr] {label:34s} {dt:6.1f}s code={e.code} http={e.http_status} "
                  f"msg={e.message[:120]!r}")
        except Exception as e:  # noqa: BLE001
            dt = time.perf_counter() - t0
            print(f"[RAISE] {label:34s} {dt:6.1f}s {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
