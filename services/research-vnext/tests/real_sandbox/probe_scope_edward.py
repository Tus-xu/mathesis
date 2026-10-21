"""QA(Edward) 探针2：界定 (a) 可读文件范围 (b) shell-escape 是否真的关 (c) 相对路径 openout。

运行：PYTHONPATH=. .venv/Scripts/python.exe tests/real_sandbox/probe_scope_edward.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from research_vnext.core.xf.latex import compile_latex_sync
from research_vnext.core.xf.models import XfError

OUT = Path(tempfile.gettempdir()) / "qa-xf-evidence"


def pdf_text(pdf_bytes: bytes) -> str:
    try:
        import pypdfium2 as pdfium

        p = OUT / "_t.pdf"
        p.write_bytes(pdf_bytes)
        with pdfium.PdfDocument(str(p)) as doc:
            return "\n".join(pg.get_textpage().get_text_range() for pg in doc)
    except Exception as e:  # noqa: BLE001
        return f"<extract-failed {type(e).__name__}>"


READ_DOC = ("\\documentclass{{article}}\n\\usepackage{{fancyvrb}}\n\\begin{{document}}\n"
            "\\VerbatimInput{{{path}}}\n\\end{{document}}\n")


def read_probe(path: str) -> str:
    src = READ_DOC.format(path=path)
    try:
        pdf, _ = compile_latex_sync(src, "main.tex")
        return "OK: " + pdf_text(pdf)[:160].replace("\r", " ").replace("\n", " ")
    except XfError as e:
        return f"XfError {e.code} http={e.http_status} {e.message[:100]}"
    except Exception as e:  # noqa: BLE001
        return f"RAISE {type(e).__name__}: {e}"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print("===== (a) 可读文件范围（VerbatimInput，guard 未拦）=====")
    for p in ("/etc/passwd", "/etc/hostname", "/etc/hosts", "/proc/self/environ",
              "/proc/1/cmdline", "/etc/os-release"):
        print(f"  {p:24s} -> {read_probe(p)}")

    print("\n===== (b) shell-escape 是否关闭：write18 落地后回读 =====")
    # 若 shell-escape 打开：cat /etc/passwd > /work/x.txt 会成功，随后 VerbatimInput 回读成功
    chain = (
        "\\documentclass{article}\n\\usepackage{fancyvrb}\n\\begin{document}\n"
        "^^5cimmediate^^5cwrite18{cat /etc/passwd > /work/x.txt}\n"
        "\\VerbatimInput{/work/x.txt}\n\\end{document}\n"
    )
    try:
        pdf, _ = compile_latex_sync(chain, "main.tex")
        print("  OK(!) shell 落地回读:", pdf_text(pdf)[:120].replace("\n", " "))
    except XfError as e:
        print(f"  XfError {e.code} http={e.http_status} msg={e.message[:120]!r}  -> shell 未执行(预期)")

    print("\n===== (c) openout 相对路径 vs 绝对路径 =====")
    for target in ("PWNED_REL.txt", "/work/PWNED_ABS.txt"):
        src = ("\\documentclass{article}\n\\begin{document}\n"
               f"^^5cimmediate^^5copenout1={target} "
               "^^5cimmediate^^5cwrite1{{PWNED}} ^^5ccloseout1\nX\n\\end{document}\n")
        try:
            pdf, _ = compile_latex_sync(src, "main.tex")
            print(f"  openout {target:20s} -> OK bytes={len(pdf)}")
        except XfError as e:
            d = (getattr(e, "detail", {}) or {}).get("diagnostics", {}).get("errors") or []
            print(f"  openout {target:20s} -> {e.code} {e.message[:70]!r} errs={[x.get('message','')[:50] for x in d][:2]}")


if __name__ == "__main__":
    main()
