"""R07 rule 分类器：chunk markdown → 10 类 content unit + problem（题干/提示/解答/答案分离）。

纯函数、零 IO、零模型依赖；LLM 只能作为 llm 参数注入（输出仍只进 DTO），
`llm=None` 时 rule 路径独立成立（验收门：禁用 AI 后 rule 提取仍可运行）。

信号与可解释性：
- 每个 unit 记录 extraction_method = "rule:<命中信号>"（如 rule:heading:example）
- 无明确信号 → 不假装：并入前一块/前 unit 延续，或 fallback 低置信 + review
- 图片引用 → 只建 figure 占位 unit + review 条目（图题资产归 R13，零图片）
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .dto import ExtractionPlan, ProblemCandidate, ReviewEntry, UnitCandidate

# 提取器版本（幂等/报告用）
EXTRACTOR_VERSION = "r07-rule-v1"

# 10 类 content unit（与 models.CONTENT_UNIT_TYPES 一致）
UNIT_TYPES = (
    "definition",
    "theorem",
    "formula",
    "example",
    "exercise",
    "inquiry",
    "figure",
    "introduction",
    "derivation",
    "summary",
)

# ── 信号正则（命中即解释；全部可单测）────────────────────────────────────────
# 子标题：## 标题 / ### 标题 …（避免把正文行首的数字当标题）
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
# 公式：行内 $...$ 与块级 $$...$$
_INLINE_LATEX_RE = re.compile(r"\$[^$\n]+\$")
_BLOCK_LATEX_RE = re.compile(r"\$\$[^$\n]+\$\$")
# 图片引用：markdown ![..](..) / 相对路径 images/... / LaTeX includegraphics
_IMG_MD_RE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)\s*\)")
_IMG_PATH_RE = re.compile(r"(?:images|image)[/\\][A-Za-z0-9_./\\-]+")
_IMG_TEX_RE = re.compile(r"\\includegraphics")

# 解答/提示/答案分隔标记（行首冒号形式；"证明"仅当行首且带冒号，避免题干误判）
_SOLUTION_START_RE = re.compile(r"^\s*(?:解|解析|解答|证明|分析)\s*[:：]")
_HINT_START_RE = re.compile(r"^\s*(?:提示|点拨|思路|学法)\s*[:：]")
_ANSWER_START_RE = re.compile(r"^\s*答案\s*[:：]")

# 问题开始标记（标题或正文开头）
_EXAMPLE_HEAD_RE = re.compile(r"^例\s*\d*\s*[.:：、]?")
_EXERCISE_HEAD_RE = re.compile(r"^(练习|习题|复习(参考)?题|复习题|拓展)\s*[.:：、]?\s*\d*")
_INQUIRY_HEAD_RE = re.compile(r"^(思考|探究|观察|实验)\s*[.:：、]?")
_SUMMARY_HEAD_RE = re.compile(r"^(小结|本章小结|回顾与思考)")
_THEOREM_HEAD_RE = re.compile(r"^定理\s*\d*")
_FORMULA_HEAD_RE = re.compile(r"^(公式|法则|运算性质)\s*\d*")
_DERIVATION_HEAD_RE = re.compile(r"^(推导|证明|演绎)")
_DEFINITION_HEAD_RE = re.compile(r"^(定义|概念)\s*\d*")
_INTRODUCTION_HEAD_RE = re.compile(r"^(引入|引言|章引言|节引言)")

# 正文强模式
_DEFINITION_BODY_RE = re.compile(r"(叫做|叫作|称为|叫做作|称它为|我们称)")  # 定义信号
_INTRO_BODY_RE = re.compile(r"(本节我们|本章我们|在小学和初中|我们已经(学过|知道|接触)|下面我们|我们先来|显然|为了更好地)")  # 引入信号
_DERIVATION_BODY_RE = re.compile(r"(由此可得|从而有|于是|因为[^，。]*所以|由[^，。]*得|可推出|类比(于|出))")  # 推导信号
_SUMMARY_BODY_RE = re.compile(r"(本章我们学习了|本节我们学习了|小结|归纳得)")  # 总结信号
_EXAMPLE_BODY_RE = re.compile(r"(?:^|\s)(例|例题|例\s*\d)\s*[.:：、]?")  # 正文中例题
_EXERCISE_BODY_RE = re.compile(r"(?:^|\s)(练习|习题|做一做)\s*[.:：、]?\s*\d*")
_THEOREM_BODY_RE = re.compile(r"^如果[^。]{2,40}[，,](?:那么|则|就)|定理")
_INQUIRY_BODY_RE = re.compile(r"(思考|探究|观察)")


@dataclass
class MarkdownBlock:
    """chunk 内一个语义块：heading 可为 None（无子标题）。"""

    heading: str | None
    body: str

    def full_text(self) -> str:
        return f"## {self.heading}\n{self.body}" if self.heading else self.body


@dataclass
class Classification:
    """一块的分类结果（可解释）。"""

    type: str
    confidence: float
    hits: list[str]  # 命中的信号标签（extraction_method 素材）
    is_problem: bool = False
    problem_kind: str | None = None  # example/exercise/inquiry
    is_solution: bool = False  # 以解/解析/证明：开头（承接上一个未闭合 problem）
    review_reason: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# 块切分
# ─────────────────────────────────────────────────────────────────────────────


def split_markdown_blocks(text: str) -> list[MarkdownBlock]:
    """把连续 markdown 按 `#`~`######` 子标题切成有序块。

    - 无子标题 → 单块（heading=None）；
    - 保持原文顺序与内容（块头标题行保留在 full_text 中，body 不含标题行）。
    """
    text = text or ""
    lines = text.splitlines()
    blocks: list[MarkdownBlock] = []
    cur_heading: str | None = None
    buf: list[str] = []
    for line in lines:
        m = _HEADING_RE.match(line.strip())
        if m:
            if buf or cur_heading is not None:
                blocks.append(MarkdownBlock(heading=cur_heading, body="\n".join(buf)))
            cur_heading = m.group(2).strip()
            buf = []
        else:
            if cur_heading is None and not buf:
                # 前置自由文本（如题号行）并入首个块
                buf.append(line)
            else:
                buf.append(line)
    if buf or cur_heading is not None:
        blocks.append(MarkdownBlock(heading=cur_heading, body="\n".join(buf)))
    return blocks


# ─────────────────────────────────────────────────────────────────────────────
# 信号检测
# ─────────────────────────────────────────────────────────────────────────────


def detect_figure_refs(text: str) -> list[str]:
    """返回图片引用（相对路径/markdown 图），零图片：只记录不落资产。"""
    refs: list[str] = []
    for m in _IMG_MD_RE.finditer(text):
        refs.append(m.group(1))
    for m in _IMG_PATH_RE.finditer(text):
        refs.append(m.group(0))
    if _IMG_TEX_RE.search(text):
        refs.append("\\includegraphics")
    return refs


def extract_latex(text: str, *, limit: int = 4000) -> str | None:
    """提取全文公式（行内+块级）拼接；无公式返回 None。"""
    parts: list[str] = []
    seen: set[str] = set()
    for m in _BLOCK_LATEX_RE.finditer(text):
        seg = m.group(0).strip()
        if seg not in seen:
            seen.add(seg)
            parts.append(seg)
    for m in _INLINE_LATEX_RE.finditer(text):
        seg = m.group(0).strip()
        if seg not in seen:
            seen.add(seg)
            parts.append(seg)
    if not parts:
        return None
    joined = "\n".join(parts)
    return joined if len(joined) <= limit else joined[:limit]


def _match_head(text: str, patterns: tuple[re.Pattern[str], str] | None) -> tuple[str, str] | None:
    """匹配标题首段信号；返回 (pattern_key, matched)。"""
    head = (text or "").strip().splitlines()[0][:40]
    for reg, key in patterns:
        if reg.match(head):
            return key, reg.match(head).group(0)
    return None


_HEAD_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_EXAMPLE_HEAD_RE, "example"),
    (_EXERCISE_HEAD_RE, "exercise"),
    (_INQUIRY_HEAD_RE, "inquiry"),
    (_SUMMARY_HEAD_RE, "summary"),
    (_DERIVATION_HEAD_RE, "derivation"),
    (_DEFINITION_HEAD_RE, "definition"),
    (_THEOREM_HEAD_RE, "theorem"),
    (_FORMULA_HEAD_RE, "formula"),
    (_INTRODUCTION_HEAD_RE, "introduction"),
)


def _body_signal(body: str) -> tuple[str, float, str] | None:
    """正文强模式 → (type, confidence, key)。首个命中即可解释。"""
    if _SOLUTION_START_RE.match(body):
        return None  # 解答块由 problem 流程承接，不单独分类
    if _DEFINITION_BODY_RE.search(body):
        return "definition", 0.8, "body:definition"
    if _INTRO_BODY_RE.search(body):
        return "introduction", 0.7, "body:introduction"
    if _SUMMARY_BODY_RE.search(body):
        return "summary", 0.7, "body:summary"
    if _DERIVATION_BODY_RE.search(body):
        return "derivation", 0.7, "body:derivation"
    if _EXAMPLE_BODY_RE.search(body):
        return "example", 0.7, "body:example"
    if _EXERCISE_BODY_RE.search(body):
        return "exercise", 0.7, "body:exercise"
    if _THEOREM_BODY_RE.search(body):
        return "theorem", 0.6, "body:theorem"
    if _INQUIRY_BODY_RE.search(body):
        return "inquiry", 0.6, "body:inquiry"
    return None


def is_solution_start(body: str) -> bool:
    """以 解/解析/解答/证明/分析： 开头 → 承接上一 problem 的解答。"""
    return bool(_SOLUTION_START_RE.match(body)) or bool(_HINT_START_RE.match(body)) or bool(_ANSWER_START_RE.match(body))


def classify_block(
    block: MarkdownBlock,
    *,
    section_title: str,
    first_chunk_of_section: bool,
) -> Classification:
    """对一块做可解释分类。

    优先级：标题信号 > 正文强模式 > 图占位 > 结构兜底。
    无明确信号的块：confidence 0.35，标记 review（不假装）。
    """
    text = block.full_text()
    figs = detect_figure_refs(text)
    heading = (block.heading or "").strip()
    body = block.body.strip()

    # 1) 图占位优先：标题形如"图9-1"或块几乎只含图引用（零图片：只建占位 unit）
    if figs and (
        not body or len(body.strip()) < 40 or re.match(r"^图\s*\d*", heading) or _IMG_TEX_RE.search(text)
    ):
        return Classification(
            type="figure",
            confidence=0.7,
            hits=["figure_ref"],
            review_reason="R07 零图片：图题资产移交 R13（本块仅占位记录）",
        )

    # 2) 标题信号（最可靠）
    hm = _match_head(heading, _HEAD_PATTERNS) if heading else None
    if hm:
        key, _ = hm
        return Classification(
            type=key,
            confidence=0.9,
            hits=[f"heading:{key}", "figure_ref" if figs else ""],
            is_problem=key in ("example", "exercise", "inquiry"),
            problem_kind=key if key in ("example", "exercise", "inquiry") else None,
            review_reason=f"含图引用 {figs}" if figs else None,
        )

    # 3) 解答块：承接上题（由编排层处理；type 仅占位）
    if is_solution_start(body):
        return Classification(
            type="example",
            confidence=0.6,
            hits=["solution-marker"],
            is_solution=True,
        )

    # 4) 正文强模式
    sig = _body_signal(body)
    if sig:
        t, conf, key = sig
        return Classification(
            type=t,
            confidence=conf,
            hits=[key, "figure_ref" if figs else ""],
            is_problem=t in ("example", "exercise", "inquiry"),
            problem_kind=t if t in ("example", "exercise", "inquiry") else None,
            review_reason=f"含图引用 {figs}" if figs else None,
        )

    # 5) 结构兜底：section 标题块 / 首个 chunk → introduction；否则低置信延续
    if heading and heading == section_title:
        return Classification(
            type="introduction",
            confidence=0.85,
            hits=["heading:section-title"],
        )
    if first_chunk_of_section and not heading and not body:
        return Classification(type="introduction", confidence=0.5, hits=["first-chunk-empty"])

    # 无信号：不假装（review_reason 由编排层追加延续目标）
    return Classification(type="", confidence=0.35, hits=["no-signal"], review_reason="低置信：无明确语义信号")


def split_problem_text(body: str) -> tuple[str, str | None]:
    """把题目文本拆成 (题干, 提示+解答+答案合并文本)。

    - 分隔标记：提示/点拨/思路 → 解/解析/解答/证明/分析 → 答案；
    - 无分隔 → (body, None)；保留原顺序（提示在前，解答、答案依次在后）。
    """
    lines = body.splitlines()
    stem: list[str] = []
    solution: list[str] = []
    in_solution = False
    for line in lines:
        ls = line.strip()
        if not in_solution and (_SOLUTION_START_RE.match(ls) or _HINT_START_RE.match(ls) or _ANSWER_START_RE.match(ls)):
            in_solution = True
        if in_solution:
            solution.append(ls)
        else:
            stem.append(ls)
    if not in_solution:
        return body, None
    return "\n".join(stem).strip(), "\n".join(solution).strip() or None


def clean_exercise_per_problem(body: str) -> list[str]:
    """把同一练习块内多个小题拆成多个题干（形如 '1．'/'1.'/'（1）' 等前缀）。"""
    parts: list[str] = []
    cur: list[str] = []
    marker_re = re.compile(r"^\s*(\d+[.．、]|（\d+）|\(\d+\))\s*")
    for line in body.splitlines():
        ls = line.strip()
        if ls and marker_re.match(ls) and cur:
            parts.append("\n".join(cur))
            cur = [ls]
        else:
            cur.append(ls)
    if cur:
        parts.append("\n".join(cur))
    return parts


# ─────────────────────────────────────────────────────────────────────────────
# 主编排：chunks → ExtractionPlan
# ─────────────────────────────────────────────────────────────────────────────


def extract_plan(
    chunks: list[dict[str, Any]],
    *,
    section_id: Any,
    section_title: str = "",
    source_id: Any = None,
    llm: Callable[[str], dict[str, Any]] | None = None,
) -> ExtractionPlan:
    """rule 主编排（chunks 顺序 = DB 顺序；内容格式与 R06 load_chunks 一致）。

    状态机：
    - 问题块（例/练习/习题/思考/探究…）→ 生成 problem（题干/解答拆分）＋对应 unit；
    - 解答块（解：/解析：/证明：…）→ 承接最近一个未闭合 problem 的 solution；
    - 无信号块 → 并入上一 unit 延续（不假装新语义），review 记录；
    - llm 参数为可选增强（R11 接入 gateway）；None → 纯 rule，独立成立。
    """
    plan = ExtractionPlan(
        section_id=section_id,
        section_title=section_title,
        source_id=source_id,
        chunk_count=len(chunks),
    )
    open_problem: ProblemCandidate | None = None  # 最近一个未闭合 problem
    last_unit: UnitCandidate | None = None
    problem_order = 0

    for ci, chunk in enumerate(chunks, start=1):
        content = chunk.get("content", "")
        blocks = split_markdown_blocks(content)
        first_chunk = ci == 1

        for block in blocks:
            if not block.full_text().strip():
                continue
            cls = classify_block(block, section_title=section_title, first_chunk_of_section=first_chunk)

            # ── 解答块：承接最近未闭合 problem（跨 chunk 例题）─────────────
            if cls.is_solution:
                if open_problem is not None:
                    open_problem.solution_text = (open_problem.solution_text or "") + "\n" + block.body.strip()
                    open_problem.chunks_span.append(ci)
                else:
                    plan.reviews.append(
                        ReviewEntry(chunk_idx=ci, reason="孤立解答块（无对应题干）", detail=block.body.strip()[:60], kind="problem")
                    )
                if last_unit is not None:  # unit 原文延续，保持顺序
                    last_unit.text += "\n" + block.full_text().strip()
                    last_unit.chunk_idx_to = max(last_unit.chunk_idx_to, ci)
                continue

            # ── 问题块：题干/解答拆分 + 生成 problem candidate ────────────
            if cls.is_problem:
                kind = cls.problem_kind or cls.type
                stem, solution = split_problem_text(block.body)
                # 探究（思考/观察）整块为 1 个 problem（保留完整探究文本，不拆小题）；
                # 习题/练习才按 '1.'/'（1）' 前缀拆多个小题。
                subs = clean_exercise_per_problem(stem) if kind == "exercise" else [stem]
                block_problems: list[ProblemCandidate] = []
                for sub in subs:
                    problem_order += 1
                    p = ProblemCandidate(
                        section_id=section_id,
                        kind=kind,
                        order=problem_order,
                        chunk_idx=ci,
                        stem_text=sub.strip(),
                        solution_text=None,
                        chunks_span=[ci],
                        provenance=f"textbook_v2/extraction/{EXTRACTOR_VERSION}/chunk_{ci}",
                        review_reason=cls.review_reason,
                    )
                    block_problems.append(p)
                    plan.problems.append(p)
                if solution:
                    # 块级共享解答：挂到最后一个小题（多小题时其余标 review）
                    block_problems[-1].solution_text = solution
                    if len(block_problems) > 1:
                        for p in block_problems[:-1]:
                            plan.reviews.append(
                                ReviewEntry(chunk_idx=ci, reason="多小题共享解答", detail=p.stem_text[:40], kind="problem")
                            )
                    open_problem = None
                else:
                    # 未闭合：后续解答块承接（跨 chunk）
                    open_problem = block_problems[-1]
                unit = UnitCandidate(
                    section_id=section_id,
                    chunk_idx_from=ci,
                    chunk_idx_to=ci,
                    type=cls.type,
                    text=block.full_text().strip(),
                    latex=extract_latex(block.full_text()),
                    confidence=cls.confidence,
                    extraction_method="rule:" + "|".join(h for h in cls.hits if h),
                    review_status="pending",
                    review_reason=cls.review_reason,
                )
                plan.units.append(unit)
                last_unit = unit
                continue

            # ── 普通 unit 块 ───────────────────────────────────────────────
            if not cls.type:
                if last_unit is not None:
                    # 无信号：并入上一 unit 延续（不假装新语义）
                    last_unit.text += "\n" + block.full_text().strip()
                    last_unit.chunk_idx_to = max(last_unit.chunk_idx_to, ci)
                    last_unit.review_status = "pending"
                    plan.reviews.append(
                        ReviewEntry(chunk_idx=ci, reason="无信号并入上一单元", detail=cls.review_reason or "", kind="unit")
                    )
                    continue
                plan.issues.append(f"chunk {ci} 首块无信号（fallback introduction）")
                cls = Classification(type="introduction", confidence=0.35, hits=["no-signal-fallback"])

            unit = UnitCandidate(
                section_id=section_id,
                chunk_idx_from=ci,
                chunk_idx_to=ci,
                type=cls.type,
                text=block.full_text().strip(),
                latex=extract_latex(block.full_text()),
                confidence=cls.confidence,
                extraction_method="rule:" + "|".join(h for h in cls.hits if h),
                review_status="pending",
                review_reason=cls.review_reason,
            )
            plan.units.append(unit)
            last_unit = unit

    # 收尾：未闭合 problem → 仅 example/exercise 进 review（inquiry 无解答是自然形态，不构成噪声）
    for p in plan.problems:
        p.chunks_span = sorted(set(p.chunks_span))
        if p.solution_text is None and p.kind != "inquiry":
            plan.reviews.append(
                ReviewEntry(
                    chunk_idx=p.chunk_idx,
                    reason="跨chunk例题未检索到解答闭合",
                    detail="chunks=" + ",".join(str(c) for c in p.chunks_span),
                    kind="problem",
                )
            )

    # 图题资产占位 unit → 必进 review 队列（统一收尾，避免重复；R07 零图片，归 R13）
    figure_reviewed_chunks = {r.chunk_idx for r in plan.reviews if r.reason == "图题资产占位（R07 零图片，归 R13）"}
    for u in plan.units:
        if u.type == "figure" and u.chunk_idx_from not in figure_reviewed_chunks:
            plan.reviews.append(
                ReviewEntry(
                    chunk_idx=u.chunk_idx_from,
                    reason="图题资产占位（R07 零图片，归 R13）",
                    detail=u.review_reason or "",
                    kind="unit",
                )
            )

    # LLM 可选增强：输出只进 DTO，绝不改写原文/跳过 review。
    # review_status 一律保持 DB 默认 pending（人工校正语义），review 队列只记录需人工看的 case。
    for u in plan.units:
        if llm is not None:
            verdict = llm(u.text)
            if isinstance(verdict, dict) and verdict.get("type") in UNIT_TYPES:
                u.type = verdict["type"]
                u.confidence = float(verdict.get("confidence") or u.confidence or 0.0)
                u.extraction_method = "llm:" + str(verdict.get("model") or "unknown")
                if not verdict.get("confirmed"):
                    u.review_status = "pending"
                    plan.reviews.append(
                        ReviewEntry(chunk_idx=u.chunk_idx_from, reason="llm 未确认", detail=verdict.get("note") or "")
                    )
    return plan
