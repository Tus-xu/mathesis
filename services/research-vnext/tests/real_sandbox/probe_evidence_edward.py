"""QA(Edward) 取证脚本：把 guard 放行的 payload 真编译，打印诊断/产物，坐实是否被利用。

运行：PYTHONPATH=. .venv/Scripts/python.exe tests/real_sandbox/probe_evidence_edward.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from research_vnext.core.xf.latex import compile_latex_sync
from research_vnext.core.export.compiler import tex_workdir_root_path
from research_vnext.core.xf.models import XfError

DOC = "\\documentclass{{article}}\n\\begin{{document}}\n{body}\n\\end{{document}}\n"
OUT = Path(tempfile.gettempdir()) / "qa-xf-evidence"


def wrap(body: str) -> str:
    return DOC.format(body=body)


def pdf_text(pdf_bytes: bytes) -> str:
    """尽力抽取 PDF 文本（pypdfium2 已在依赖里）。"""
    try:
        import pypdfium2 as pdfium

        p = OUT / "_tmp.pdf"
        p.write_bytes(pdf_bytes)
        with pdfium.PdfDocument(str(p)) as doc:
            return "\n".join(page.get_textpage().get_text_range() for page in doc)
    except Exception as e:  # noqa: BLE001
        return f"<text-extract-failed {type(e).__name__}: {e}>"


CASES = [
    ("CANARY_^^5czznotacmd", wrap("A^^5czznotacmd B")),
    ("INPUT_^^5cinput_nonexistent", wrap("X^^5cinput{/nonexistent_zzz_qa}Y")),
    ("INPUT_^^5cinput_etc_passwd", wrap("X^^5cinput{/etc/passwd}Y")),
    ("LST_lstinputlisting_etc_passwd",
     "\\documentclass{article}\n\\usepackage{listings}\n\\begin{document}\n"
     "\\lstinputlisting{/etc/passwd}\n\\end{document}\n"),
    ("VERB_VerbatimInput_etc_passwd",
     "\\documentclass{article}\n\\usepackage{fancyvrb}\n\\begin{document}\n"
     "\\VerbatimInput{/etc/passwd}\n\\end{document}\n"),
    ("WRITE18_^^5cwrite18", wrap(r"^^5cwrite18{echo PWNED > /work/PWNED_SHELL.txt}")),
    ("OPENOUT_^^5copenout",
     wrap(r"^^5cimmediate^^5copenout1=/work/PWNED_OPENOUT.txt "
          r"^^5cimmediate^^5cwrite1{PWNED} ^^5ccloseout1")),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    root = tex_workdir_root_path()
    before = {p.name for p in root.iterdir()} if root.exists() else set()

    for label, source in CASES:
        print(f"\n===== {label} =====")
        try:
            pdf, meta = compile_latex_sync(source, "main.tex")
            txt = pdf_text(pdf)
            print(f"OK bytes={len(pdf)} pages={meta.get('pages')}")
            print(f"PDF text[:400]={txt[:400]!r}")
            (OUT / f"{label}.pdf").write_bytes(pdf)
        except XfError as e:
            print(f"XfError code={e.code} http={e.http_status} msg={e.message!r}")
            diag = (getattr(e, "detail", {}) or {}).get("diagnostics") or {}
            errs = diag.get("errors")
            if errs:
                print(f"diagnostics.errors[:5]={errs[:5]!r}")

    if root.exists():
        after = {p.name for p in root.iterdir()}
        leaked = after - before
        print(f"\n===== 新建/残留工作目录 =====\n{leaked or '(无)'}")
        for name in leaked:
            d = root / name
            print(f"  {name}: {[f.name for f in d.iterdir()]}")


if __name__ == "__main__":
    main()
