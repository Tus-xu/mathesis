"""GAOKAO-Bench 数学卷 → quiz_questions 暂存管线（M1，B2 §5.1）。

数据源（manifest: corpus.sources.gaokao_bench，Apache-2.0）：
- repos/GAOKAO-Bench/Data/Objective_Questions/2010-2022_Math_{I,II}_MCQs.json
- repos/GAOKAO-Bench/Data/Subjective_Questions/2010-2022_Math_{I,II}_{Fill-in-the-Blank,Open-ended_Questions}.json

流程：parse → split（题干/选项）→ clean（LaTeX 规范化：定界符统一、中文标点
数学区替换）→ dedup（题干指纹）→ staging JSONL + 质检报告。
DB 入库模式（--db）等 B3 bootstrap 后启用（见 docs/handoff/DEP-B2-*.md）。

诚实性标注：
- GAOKAO-Bench 无难度字段 → difficulty 置空不编造（正确率口径待真实使用后回填）；
- kp_code/kp_path 由 kp-suggest 阶段补全，本管线不猜。

幂等：staging 全量重写；日志追加 corpus/logs/gaokao_pipeline.log。
运行：python -m corpus.pipelines.gaokao_to_quiz [--bench-dir <path>]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from pathlib import Path

DEFAULT_BENCH_DIR = Path("D:/teacher-research/repos/GAOKAO-Bench/Data")
STAGING_DIR = Path(__file__).resolve().parents[1] / "staging"
LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "gaokao_pipeline.log"

MATH_FILES: list[tuple[str, str]] = [
    # (文件名, q_type)
    ("Objective_Questions/2010-2022_Math_I_MCQs.json", "choice"),
    ("Objective_Questions/2010-2022_Math_II_MCQs.json", "choice"),
    ("Subjective_Questions/2010-2022_Math_I_Fill-in-the-Blank.json", "fill"),
    ("Subjective_Questions/2010-2022_Math_II_Fill-in-the-Blank.json", "fill"),
    ("Subjective_Questions/2010-2022_Math_I_Open-ended_Questions.json", "solve"),
    ("Subjective_Questions/2010-2022_Math_II_Open-ended_Questions.json", "solve"),
]

SOURCE_LABEL = "区库"  # V3QuizQuestion.source 枚举（校本/区库/拍照入库）

_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.S)
_INLINE_MATH = re.compile(r"(?<![\\$])\$([^$\n]+?)\$(?!\$)")
_ZH_PUNCT_IN_MATH = str.maketrans({
    "，": ", ", "。": ". ", "：": ": ", "；": "; ", "？": "? ",
    "（": "(", "）": ")", "　": " ",
})
_LEADING_INDEX = re.compile(r"^\s*\d{1,3}\s*[.、．]\s*")
_OPTION_LINE = re.compile(r"^([A-D])\s*[.、．]\s*(.+)$")


@dataclass
class QuizRecord:
    source: str
    source_detail: str
    year: str
    category: str
    index: int
    score: int | None
    q_type: str
    difficulty: str | None  # GAOKAO-Bench 无难度字段 → None（不编造）
    stem_latex: str
    options: dict[str, str] | None
    answer: str | None
    analysis: str | None
    kp_code: str | None = None
    kp_path: list[str] = field(default_factory=list)
    stem_hash: str = ""

    def finalize(self) -> "QuizRecord":
        self.stem_hash = hashlib.sha256(self.stem_latex.encode("utf-8")).hexdigest()[:16]
        return self


def _clean_math_segment(seg: str) -> str:
    """数式区清洗：中文标点 → ASCII、多余空白折叠（保留 \\text{} 内中文由 KaTeX 处理）。"""
    seg = seg.translate(_ZH_PUNCT_IN_MATH)
    seg = re.sub(r"[ \t]+", " ", seg)
    return seg.strip()


def _clean_latex(text: str) -> str:
    """定界符统一为 $..$；$$..$$（display）并入 $..$；数式区内做标点替换。"""
    # 源 JSON 单反斜杠误转义修复：\bar/\frac/\varepsilon 被 JSON 解码成控制字符
    text = (text.replace("\x08", "\\b")     # backspace  ← \bar \beta \binom
                .replace("\x0c", "\\f")     # formfeed   ← \frac \forall
                .replace("\x0b", "\\v"))    # vtab       ← \varepsilon \vec

    def _disp(m: re.Match) -> str:
        return f"${_clean_math_segment(m.group(1))}$"

    def _inl(m: re.Match) -> str:
        return f"${_clean_math_segment(m.group(1))}$"

    text = _DISPLAY_MATH.sub(_disp, text)
    text = _INLINE_MATH.sub(_inl, text)
    text = text.replace("\\leqslant", "\\leq").replace("\\geqslant", "\\geq")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _split_mcq(question: str) -> tuple[str, dict[str, str] | None]:
    """从题干中拆出 A-D 选项；行式与行内式都支持。返回 (stem, options)。"""
    lines = question.split("\n")
    opt_start = None
    options: dict[str, str] = {}
    for i, ln in enumerate(lines):
        m = _OPTION_LINE.match(ln.strip())
        if m and m.group(1) == "A":
            opt_start = i
            break
    if opt_start is not None:
        current = None
        for ln in lines[opt_start:]:
            m = _OPTION_LINE.match(ln.strip())
            if m:
                current = m.group(1)
                options[current] = m.group(2).strip()
            elif current and ln.strip():
                options[current] += ln.strip()  # 选项折行
        stem = "\n".join(lines[:opt_start]).strip()
        if len(options) >= 2:
            return stem, options
    # 行内式：A. xx B. xx …
    inline = re.split(r"(?=\b[BD]\s*[.、．])", question)
    if len(inline) >= 2:
        pairs = re.findall(r"([A-D])\s*[.、．]\s*(.*?)(?=(?:\s+[B-D]\s*[.、．])|$)", question)
        if len(pairs) >= 2:
            options = {k: v.strip() for k, v in pairs}
            stem = question[: question.find(f"{pairs[0][0]}")] .strip()
            return stem, options
    return question, None


def _norm_answer(ans) -> str | None:
    if ans is None:
        return None
    if isinstance(ans, list):
        return "".join(str(a).strip() for a in ans) or None
    return str(ans).strip() or None


def _norm_text(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def parse_file(path: Path, q_type: str) -> list[QuizRecord]:
    data = json.loads(path.read_text(encoding="utf-8"))
    records: list[QuizRecord] = []
    for ex in data.get("example", []):
        question = _clean_latex(str(ex.get("question") or ""))
        question = _LEADING_INDEX.sub("", question, count=1)
        options = None
        stem = question
        if q_type == "choice":
            stem, options = _split_mcq(question)
        rec = QuizRecord(
            source=SOURCE_LABEL,
            source_detail=path.name,
            year=str(ex.get("year") or "").strip(),
            category=str(ex.get("category") or "").strip("（）()"),
            index=int(ex.get("index") or 0),
            score=int(ex["score"]) if ex.get("score") is not None else None,
            q_type=q_type,
            difficulty=None,
            stem_latex=stem,
            options=options or None,
            answer=_norm_answer(ex.get("answer")),
            analysis=_norm_text(ex.get("analysis")),
        )
        records.append(rec.finalize())
    return records


def dedupe(records: list[QuizRecord]) -> tuple[list[QuizRecord], int]:
    seen: set[str] = set()
    out: list[QuizRecord] = []
    dupes = 0
    for r in records:
        key = f"{r.stem_hash}|{r.q_type}"
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        out.append(r)
    return out, dupes


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench-dir", default=str(DEFAULT_BENCH_DIR))
    args = ap.parse_args(argv)
    bench = Path(args.bench_dir)

    all_records: list[QuizRecord] = []
    per_file: dict[str, int] = {}
    missing: list[str] = []
    for rel, q_type in MATH_FILES:
        path = bench / rel
        if not path.exists():
            missing.append(rel)
            continue
        recs = parse_file(path, q_type)
        per_file[rel] = len(recs)
        all_records.extend(recs)

    records, dupes = dedupe(all_records)

    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    out_path = STAGING_DIR / "quiz_gaokao.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "bench_dir": str(bench),
        "missing_files": missing,
        "per_file_counts": per_file,
        "parsed_total": len(all_records),
        "dedup_removed": dupes,
        "staged_total": len(records),
        "by_q_type": {
            t: sum(1 for r in records if r.q_type == t)
            for t in ("choice", "fill", "solve")
        },
        "with_analysis": sum(1 for r in records if r.analysis),
        "years": sorted({r.year for r in records if r.year}),
        "difficulty_note": "GAOKAO-Bench 无难度字段，difficulty 置空不编造；正确率口径待真实使用后回填",
        "next_step": "node corpus/tools/katex_check.mjs corpus/staging/quiz_gaokao.jsonl",
    }
    report_path = STAGING_DIR / "quiz_gaokao_report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as lf:
        lf.write(f"{datetime.now(timezone.utc).isoformat()} stage={len(records)} "
                 f"parsed={len(all_records)} dupes={dupes} missing={missing}\n")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not missing else 1


if __name__ == "__main__":
    sys.exit(main())
