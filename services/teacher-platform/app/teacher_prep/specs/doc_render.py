"""LessonPlanSpec 真实导出：统一文档模型 → markdown / PDF / DOCX（R3e 教案投影保真）。

产物契约（产物审查 H 回炉要求 + Golden「教案.png」MUST，节序 Golden 序）：
- meta pill 行：学科 / 教材 / 班级 / 课时 四枚真实 pill（无班级 → 真实年级；绝不占位班级）；
- 蓝色编号节 1–6：教学目标 → 教学重难点 → 教学过程 → 板书设计 → 作业布置 → 教学反思；
- 教学过程 = **5 列真实表格**（环节 / 时间 / 教师活动 / 学生活动 / 设计意图）；
- 板书设计 = 三栏（文字要点 | 数学图形 | 公式区），中列嵌 math_figures 参数化图（与 PPT 同源）；
- 公式数学式排版（禁裸源码）：PDF 叠式分式（math_text.draw_math）、DOCX OMML（失败降级
  Unicode）、markdown Unicode——`$…$`/`\\boxed` 零进可见产物；
- 内部 UUID / hash 零进教师可见产物：meta/正文/表格/板书全部干净；R6-planfix 起 markdown
  追溯附录改为**人类可读溯源**「依据（教材溯源）」（教材 · 章节 · chunk 主题，由导出任务
  解析 locator 注入标签），机器 locator 串零进教师产物（T24 可追溯性契约以可读形态保留；
  PDF/DOCX 是教师打印/编辑产物，不带内部标识）；
- 无废页：空的可选节（分层支持/诊断响应）**不渲染空壳**；「课堂活动/例题/评价」尾三节
  不再渲染（H2：超出 Golden 六章结构），内容按 `_merged_flow_stages` 归位「教学环节」表
  （零丢失）；「（无）」诚实空态只出现在六章正文；条目文字不再悬挂内部依据行。

三格式共用 `_document_lines`（同一份文档模型），markdown/PDF/DOCX 只决定各自排版，
教学内容零漂移；渲染器差异仅限：追溯附录（md-only）、章节规范名（PDF/DOCX 按 A8 映射）。
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Literal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Flowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from reportlab.platypus import Image as RLImage

from . import math_figures
from .lesson_plan_schema import LessonPlanSpec, PlanItem
from .math_text import (
    draw_math,
    latex_to_omml,
    latex_to_unicode,
    measure_math,
    parse_math,
    split_label_math,
)

CJK_FONT = "STSong-Light"  # reportlab 内置 CID 字体（无外部字体文件依赖）

_PAGE_SIZE = A4
_MARGIN = 18 * mm

# Golden 视觉常量（教案.png）：蓝色编号方块 / pill 底色 / 墨色
_BRAND = colors.HexColor("#2563EB")
_PILL_BG = colors.HexColor("#EFF4FF")
_PILL_EDGE = colors.HexColor("#BFD3F2")
_INK = colors.HexColor("#1f2d3d")

# A8 规范章名（PDF/DOCX 标题映射；markdown 保留 T24 契约名）
_CHAPTER_ALIAS = {"重点与难点": "教学重难点", "教学环节": "教学过程"}
# Golden 六章（编号 1–6，节序即 Golden 序）
_CORE_SECTIONS = ("教学目标", "重点与难点", "教学环节", "板书设计", "作业布置", "教学反思")
# 六章之后的可选节（T24 契约）：无真实内容整节不渲染（杜绝「分层支持（无）」空壳）。
# R6-planfix（H2 遗留·中）：「课堂活动 / 例题 / 评价」尾三节超出 Golden 六章结构，
# 不再作为章节渲染——其真实内容按 `_merged_flow_stages` 的归位依据并入「教学环节」过程表。
_OPTIONAL_SECTIONS = ("分层支持", "诊断响应")


@dataclass(frozen=True)
class _Line:
    """共享文档行：三种渲染器只决定各自的排版，不各自决定内容。

    payload：pills → [(label, value)]；table → (headers, rows)；board → (texts, png|None, formulas)
    （texts/formulas 为 PlanItem 列表；math → (label, latex)）。
    """

    kind: Literal["title", "h1", "h2", "meta", "body", "item", "numbered", "note",
                  "pills", "table", "board", "math"]
    text: str = ""
    payload: Any = None
    num: int | None = None
    source_ref: str | None = None
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)


def _filename(spec: LessonPlanSpec, ext: str) -> str:
    # 教师可读下载名 = 真实课题名 + 类型 + revision（禁止只给 UUID 而无法区分）。
    # 中文安全：下载响应头走 content_disposition 的 RFC 5987 编码。
    from app.storage.artifacts import safe_download_name

    stem = safe_download_name(spec.metadata.title, fallback="教案")
    return f"{stem}-教案-r{spec.revision}.{ext}"


def _clean(text: str) -> str:
    """可见文本净化：编辑标记剥除 + `$…$`/`\\boxed` 源码 → 干净 Unicode 数学文本。"""
    from app.teacher_prep.text_safety import strip_edit_markers

    return latex_to_unicode(strip_edit_markers(str(text or ""))).strip()


def _inline(text: str) -> str:
    """行内文本（表格/条目）：净化 + 数学 Unicode（零裸源码）。"""
    return _clean(text)


def _pills_of(spec: LessonPlanSpec) -> list[tuple[str, str]]:
    """meta pill 行（Golden 四 pill：学科 / 教材 / 班级 / 课时）。R14 裁定 3.1–3.3：
    学科=产品常量；教材=去重展示名；班级=真实班级名 > 真实年级；课时=教学范围+时长。
    缺失值诚实显示「—」（绝不「未指定班级」这类占位话术）。"""
    m = spec.metadata
    duration = f"{m.duration_minutes} 分钟" if m.duration_minutes else ""
    scope = (m.lesson_scope or "").strip()
    lesson = f"{scope}（{duration}）" if scope and duration else (scope or duration or "—")
    return [
        ("学科", (m.subject or "").strip() or "高中数学"),
        ("教材", (m.textbook or "").strip() or "—"),
        ("班级", (m.class_name or "").strip() or (m.grade or "").strip() or "—"),
        ("课时", lesson),
    ]


def _item_lines(
    items: list[PlanItem], *, notes: bool, numbered: bool = False,
    labels: dict[str, str] | None = None,
) -> list[_Line]:
    if not items:
        return [_Line("body", "（无）")]
    lines: list[_Line] = []
    for i, item in enumerate(items, start=1):
        if numbered:
            lines.append(_Line("numbered", _inline(item.text), num=i))
        else:
            lines.append(_Line("item", _inline(item.text)))
        # R6-planfix：教师可见产物零机器 locator——有可读标签挂标签，无标签不挂
        # （原始 SourceLocator 串不再出现在 md 正文/附录；T24 溯源改由「依据（教材溯源）」附录承载）。
        if notes and item.source_ref and (labels or {}).get(item.source_ref):
            lines.append(_Line("note", f"依据：{labels[item.source_ref]}"))
        if notes and item.evidence_refs:
            lines.append(_Line("note", f"证据：{'、'.join(item.evidence_refs)}"))
    return lines


def _key_difficult_lines(items: list[PlanItem]) -> list[_Line]:
    """重难点小胶囊（§F.1）：kind=key → 「重点：」/ difficult → 「难点：」。"""
    if not items:
        return [_Line("body", "（无）")]
    out: list[_Line] = []
    for it in items:
        label = {"key": "重点：", "difficult": "难点："}.get(it.kind or "", "")  # type: ignore[arg-type]
        text = _inline(it.text)
        if label and not text.startswith(label.rstrip("：")):
            text = f"{label}{text}"
        out.append(_Line("item", text))
    return out


# ── 尾三节语义归位（R6-planfix / H2 遗留·中）─────────────────────────────────
# Golden / LEAD-BAR 4.3 = 六章结构；「课堂活动 / 例题 / 评价」不再作为章节渲染。
# 其蓝图桶来源与归位依据（内容零丢失，只挪位置）：
# - spec.activities ← 蓝图 inquiries 桶：探究问题本来就是「探究/思考」环节的教学内容
#   （本课 inquiry-1 与该环节教师提问同核），并入该环节教师活动列；无探究环节时追加
#   「问题探究」行（时长在蓝图无据 → 时间列「—」诚实）；
# - spec.examples ← 蓝图 examples 桶（题干+解答）：课堂例题属教学过程（deck 同内容
#   即「例题精讲/练一练」页），追加「例题精讲」行；时长无据 → 「—」；不挪作业布置
#   （课后属性蓝图无据，不脑补）；
# - spec.assessment：逐句比对过程表教师活动——已在过程的句子（本课「本节课我们学习了…」
#   在课堂小结行、「都清楚作业要求了吗？」在布置作业行）不再重复呈现（H2：评价=表格
#   流程句重复）；未出现的检测问题并入「小结/总结」环节，无则追加「课堂检测」行。
# 同核判定：归一化包含 或 公共子串 ≥6 且含数字/拉丁（同 deck `_question_echo` 口径）。

_ECHO_MIN_LCS = 6
_SUMMARY_STAGE_RE = re.compile(r"小结|总结|归纳|作业|检测")
_INQUIRY_STAGE_RE = re.compile(r"探究|思考|讨论")


def _norm_cn(text: str) -> str:
    return re.sub(r"\s+", "", _clean(text))


def _lcs_cn(a: str, b: str) -> str:
    """最长公共子串（短串 DP；过程表行文本量级，确定性）。"""
    if not a or not b:
        return ""
    prev = [0] * (len(b) + 1)
    best_len, best_end = 0, 0
    for i in range(1, len(a) + 1):
        cur = [0] * (len(b) + 1)
        ai = a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best_len:
                    best_len, best_end = cur[j], i
        prev = cur
    return a[best_end - best_len : best_end] if best_len else ""


def _echo_of(candidate: str, existing: list[str]) -> bool:
    """candidate 与既有过程表内容是否同句/同题两写（不重复呈现；同核内容零丢失）。"""
    n = _norm_cn(candidate)
    if not n:
        return True
    for raw in existing:
        e = _norm_cn(raw)
        if not e:
            continue
        if n == e or n in e or e in n:
            return True
        shared = _lcs_cn(n, e)
        if len(shared) >= _ECHO_MIN_LCS and any(ch.isascii() and ch.isalnum() for ch in shared):
            return True
    return False


class _StageView:
    """过程表行视图（与 LessonPlanStage 同名属性面；合并结果不回写 spec）。"""

    def __init__(
        self,
        title: str,
        duration_minutes: int | None,
        teacher: list[PlanItem],
        student: list[PlanItem],
        intent: list[PlanItem],
        *,
        items: list[PlanItem] | None = None,
        summary: str | None = None,
    ):
        self.title = title
        self.duration_minutes = duration_minutes
        self.items = items if items is not None else []
        self.summary = summary
        self.columns = SimpleNamespace(
            teacher_activity=teacher, student_activity=student, design_intent=intent
        )


def _insert_pos(stages: list[_StageView]) -> int:
    """新环节行插入点：小结/作业类环节之前（例题/探究属新授内容，居过程表前段）。"""
    for i, v in enumerate(stages):
        if _SUMMARY_STAGE_RE.search(v.title or ""):
            return i
    return len(stages)


def _merged_flow_stages(spec: LessonPlanSpec) -> list[_StageView]:
    stages: list[_StageView] = []
    for st in spec.lesson_flow:
        cols = st.columns
        teacher = list(cols.teacher_activity) if cols.teacher_activity else list(st.items)
        stages.append(
            _StageView(
                st.title, st.duration_minutes, teacher,
                list(cols.student_activity), list(cols.design_intent),
                items=list(st.items), summary=st.summary,
            )
        )
    flow_texts = [t for v in stages for t in [*(it.text for it in v.columns.teacher_activity),
                                              *(it.text for it in v.items)]]

    # ① inquiry → 探究/思考环节教师活动列
    inq_stage = next((v for v in stages if _INQUIRY_STAGE_RE.search(v.title or "")), None)
    unmatched_inq = [it for it in spec.activities if not _echo_of(it.text, flow_texts)]
    if inq_stage is not None:
        inq_stage.columns.teacher_activity.extend(unmatched_inq)
        unmatched_inq = []
    # ② examples → 「例题精讲」行（课堂例题属教学过程；时长无据诚实「—」）
    ex_items = [it for it in spec.examples if not _echo_of(it.text, flow_texts)]
    if ex_items:
        stages.insert(_insert_pos(stages), _StageView("例题精讲", None, ex_items, [], []))
    if unmatched_inq:
        stages.insert(_insert_pos(stages), _StageView("问题探究", None, unmatched_inq, [], []))
    # ③ assessment → 已在过程表的句子不重复；未出现的并入小结环节 / 追加「课堂检测」行
    unmatched_as = [it for it in spec.assessment if not _echo_of(it.text, flow_texts)]
    if unmatched_as:
        sum_stage = next((v for v in stages if re.search(r"小结|总结|归纳", v.title or "")), None)
        if sum_stage is not None:
            sum_stage.columns.teacher_activity.extend(unmatched_as)
        else:
            stages.append(_StageView("课堂检测", None, unmatched_as, [], []))
    return stages


def _flow_time(minutes: int | None) -> str:
    """时间列短格式（Golden「4min」口径）：ASCII 紧凑，窄列不折行。"""
    return f"{minutes}min" if minutes else "—"


def _flow_table_payload(spec: LessonPlanSpec) -> tuple[list[str], list[list[str]]]:
    """教学过程 5 列表格数据（环节/时间/教师活动/学生活动/设计意图）。

    列内容与蓝图同源（G20 ②）：教师活动 = 环节 items + 教师提问（`StageColumns` 为空时
    退化 stage.items）+ 尾三节归位内容（`_merged_flow_stages`），学生活动/设计意图取
    五列真实列；缺失诚实显示「—」。
    """
    headers = ["环节", "时间", "教师活动", "学生活动", "设计意图"]
    rows: list[list[str]] = []
    for stage in _merged_flow_stages(spec):
        teacher = stage.columns.teacher_activity or stage.items
        student = stage.columns.student_activity
        intent = stage.columns.design_intent or ([PlanItem(text=stage.summary)] if stage.summary else [])
        rows.append([
            _inline(stage.title),
            _flow_time(stage.duration_minutes),
            "<br>".join(_inline(it.text) for it in teacher) or "—",
            "<br>".join(_inline(it.text) for it in student) or "—",
            "<br>".join(_inline(it.text) for it in intent) or "—",
        ])
    return headers, rows


def _board_png(spec: LessonPlanSpec) -> bytes | None:
    """板书中列参数化图（math_figures；与 PPT math_object 同源参数）。渲染失败诚实 None。"""
    mo = spec.board_design.math_object
    if mo is None:
        return None
    try:
        return math_figures.math_object_png(mo.kind, dict(mo.params or {}))
    except Exception:  # noqa: BLE001 - 图形渲染失败时板书中列诚实空态，不假图占位
        return None


def _all_source_refs(spec: LessonPlanSpec) -> list[str]:
    """教案全量 SourceLocator 聚合（md 追溯附录的解析输入；保序去重、零 page_refs）。"""
    refs: list[str] = []
    seen: set[str] = set()

    def _add(ref: str | None) -> None:
        if ref and ref not in seen:
            seen.add(ref)
            refs.append(ref)

    buckets: list[list[PlanItem]] = [
        spec.objectives, spec.key_difficult_points, spec.activities, spec.examples,
        spec.homework, spec.reflection, spec.assessment, spec.evidence,
        spec.board_design.text_points, spec.board_design.formulas,
    ]
    for stage in spec.lesson_flow:
        buckets.append(stage.items)
        buckets.append(stage.columns.teacher_activity)
        buckets.append(stage.columns.student_activity)
        buckets.append(stage.columns.design_intent)
    for support in spec.stratified_support:
        buckets.append(support.items)
    for items in buckets:
        for it in items:
            _add(it.source_ref)
    for diag in spec.diagnostic_response:
        _add(diag.source_ref)
    for ref in spec.source_refs:
        _add(ref)
    _add(spec.context_ref.locator)
    return refs


def _document_lines(
    spec: LessonPlanSpec, *, meta: dict[str, Any], traceability: bool = False
) -> list[_Line]:
    m = spec.metadata
    # R6-planfix：locator → 教师可读溯源标签（教材 · 章节 · chunk 主题）；由导出任务解析注入。
    labels: dict[str, str] = dict(meta.get("locator_labels") or {})
    lines: list[_Line] = [
        _Line("title", m.title),
        _Line("pills", payload=_pills_of(spec)),
    ]
    if meta.get("generated_at"):
        lines.append(_Line("meta", f"生成时间：{meta['generated_at']}"))

    def _section(num: int, name: str, body: list[_Line]) -> list[_Line]:
        return [_Line("h1", name, num=num), *body]

    # ① 教学目标
    lines += _section(1, "教学目标", _item_lines(spec.objectives, notes=traceability, labels=labels))
    # ② 教学重难点
    lines += _section(2, "重点与难点", _key_difficult_points_lines(spec, traceability, labels))
    # ③ 教学过程（5 列真实表格；含尾三节归位内容，见 `_merged_flow_stages`）
    headers, rows = _flow_table_payload(spec)
    body: list[_Line] = [_Line("table", payload=(headers, rows))] if rows else [_Line("body", "（无）")]
    lines += _section(3, "教学环节", body)
    # ④ 板书设计（三栏含图）
    lines += _section(4, "板书设计", [_Line("board", payload=(spec.board_design, _board_png(spec)))])
    # ⑤ 作业布置（编号条目；Golden 编号 3 条口径）
    lines += _section(5, "作业布置", _item_lines(spec.homework, notes=traceability, numbered=True, labels=labels))
    # ⑥ 教学反思
    lines += _section(6, "教学反思", _item_lines(spec.reflection, notes=traceability, labels=labels))

    # 可选节（T24 契约）：非空才渲染（空壳节 = 结构污染，产物审查 H）。
    # R6-planfix：「课堂活动/例题/评价」尾三节不再渲染——内容已归位教学环节表（零丢失）。
    optional: list[tuple[str, list[_Line]]] = [
        ("分层支持", _stratified_lines(spec, traceability, labels)),
        ("诊断响应", _diagnostic_lines(spec, traceability, labels)),
    ]
    for name, body_lines in optional:
        if not body_lines or (len(body_lines) == 1 and body_lines[0].kind == "body"):
            continue  # 「（无）」空壳不进产物
        lines += [_Line("h1", name), *body_lines]

    # 追溯附录（仅 markdown）：T24 可追溯性契约，但教师可见形态改为**人类可读溯源**
    # （教材 · 章节 · chunk 主题）——机器 locator 串零进教师产物（与 PDF/DOCX 同口径 0 UUID）。
    if traceability:
        refs = _all_source_refs(spec)
        resolved = [r for r in refs if labels.get(r)]
        annex: list[_Line] = [_Line("h1", "依据（教材溯源）")]
        if resolved:
            annex += [_Line("item", labels[r]) for r in resolved]
        else:
            annex.append(_Line("body", "（无）"))
        unresolved = len(refs) - len(resolved)
        if unresolved:
            annex.append(_Line("body", f"另有 {unresolved} 条内部教材定位未在教材库解析（不影响教学使用）"))
        if spec.evidence:
            annex.append(_Line("h2", "证据明细"))
            annex += _item_lines(spec.evidence, notes=True, labels=labels)
        lines += annex

    return lines


def _key_difficult_points_lines(
    spec: LessonPlanSpec, traceability: bool, labels: dict[str, str] | None = None
) -> list[_Line]:
    lines = _key_difficult_lines(spec.key_difficult_points)
    if traceability:
        for it in spec.key_difficult_points:
            label = (labels or {}).get(it.source_ref or "") if it.source_ref else None
            if it.source_ref and label:
                lines.append(_Line("note", f"依据：{label}"))
    return lines


def _stratified_lines(
    spec: LessonPlanSpec, traceability: bool, labels: dict[str, str] | None = None
) -> list[_Line]:
    if not spec.stratified_support:
        return []
    lines: list[_Line] = []
    for support in spec.stratified_support:
        lines.append(_Line("h2", f"{support.label}（{support.group}）"))
        lines.extend(_item_lines(support.items, notes=traceability, labels=labels))
    return lines


def _diagnostic_lines(
    spec: LessonPlanSpec, traceability: bool, labels: dict[str, str] | None = None
) -> list[_Line]:
    if not spec.diagnostic_response:
        return []
    lines: list[_Line] = []
    for diag in spec.diagnostic_response:
        label = (labels or {}).get(diag.source_ref or "") if diag.source_ref else None
        suffix = f"（依据：{label}）" if traceability and label else ""
        lines.append(_Line("item", f"{diag.finding_ref}：{_inline(diag.response)}{suffix}"))
    return lines


# ── markdown 渲染 ─────────────────────────────────────────────────────────────


def render_lesson_plan_markdown(
    spec: LessonPlanSpec, *, meta: dict[str, Any] | None = None
) -> tuple[str, str]:
    """渲染教案 markdown（带追溯附录），返回 (markdown 文本, 文件名)。"""
    out: list[str] = []
    for line in _document_lines(spec, meta=meta or {}, traceability=True):
        if line.kind == "title":
            out += [f"# {line.text}", ""]
        elif line.kind == "h1":
            out += [f"## {line.text}", ""]
        elif line.kind == "h2":
            out += [f"### {line.text}", ""]
        elif line.kind == "item":
            out.append(f"- {line.text}")
        elif line.kind == "numbered":
            out.append(f"{line.num}. {line.text}")
        elif line.kind == "note":
            # 可追溯信息（source_ref/evidence_refs）缩进挂在所属条目下
            out.append(f"  - {line.text}")
        elif line.kind == "meta":
            out.append(f"> {line.text}")
        elif line.kind == "pills":
            out.append(" ｜ ".join(f"**{k}**：{v}" for k, v in line.payload))
            out.append("")
        elif line.kind == "table":
            headers, rows = line.payload
            out.append("| " + " | ".join(headers) + " |")
            out.append("|" + "---|" * len(headers))
            for row in rows:
                out.append("| " + " | ".join(row) + " |")
            out.append("")
        elif line.kind == "board":
            design, png = line.payload
            out.append("| 文字要点 | 数学图形 | 公式区 |")
            out.append("|---|---|---|")
            texts = "<br>".join(_inline(it.text) for it in design.text_points) or "—"
            figure = (
                f"![数学图形](data:image/png;base64,{__import__('base64').b64encode(png).decode('ascii')})"
                if png
                else "（本课无参数化数学图形）"
            )
            formula_parts: list[str] = []
            for it in design.formulas:
                label, latex = split_label_math(it.text)
                math_text = latex_to_unicode(latex or it.text)
                formula_parts.append(f"{label}：{math_text}" if label else math_text)
            out.append(f"| {texts} | {figure} | {'<br>'.join(formula_parts) or '—'} |")
            out.append("")
        else:
            out.append(line.text)
            out.append("")
    text = "\n".join(_normalize_blank_lines(out)).rstrip() + "\n"
    return text, _filename(spec, "md")


def _normalize_blank_lines(lines: list[str]) -> list[str]:
    """章节标题前后补空行（markdown 渲染稳定性）；条目行不插入空行。"""
    out: list[str] = []
    for line in lines:
        if (line.startswith("## ") or line.startswith("### ")) and out and out[-1] != "":
            out.append("")
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    return out


# ── PDF 渲染（reportlab：pill 行 / 蓝色编号节 / 5 列表 / 板书三栏含图）─────────


def _ensure_cjk_font() -> None:
    """注册 CID 字体（幂等）：STSong-Light 由 reportlab 自带 CID 表提供，无需字体文件。"""
    if CJK_FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(CJK_FONT))


def _sw(text: str, font: str, size: float) -> float:
    return pdfmetrics.stringWidth(text, font, size)


class _PillRow(Flowable):
    """meta pill 行（Golden：四枚圆角 pill，浅蓝底 + 品牌蓝字）。"""

    def __init__(self, pills: list[tuple[str, str]], *, size: float = 9.5):
        super().__init__()
        self._pills = pills
        self._size = size
        self._widths = [_sw(f"{k}：{v}", CJK_FONT, size) + 16 for k, v in pills]
        self.width = sum(self._widths) + 8 * (len(pills) - 1)
        self.height = size + 11

    def draw(self) -> None:
        c = self.canv
        x = 0.0
        y0 = 1.5
        for (label, value), w in zip(self._pills, self._widths, strict=True):
            c.setFillColor(_PILL_BG)
            c.setStrokeColor(_PILL_EDGE)
            c.setLineWidth(0.8)
            c.roundRect(x, y0, w, self.height - 3, (self.height - 3) / 2, stroke=1, fill=1)
            c.setFillColor(_BRAND)
            c.setFont(CJK_FONT, self._size)
            c.drawString(x + 8, y0 + (self.height - 3) / 2 - self._size * 0.36, f"{label}：{value}")
            x += w + 8


class _SectionHead(Flowable):
    """蓝色编号方块 + 章标题（Golden 编号节 1–6）。"""

    def __init__(self, number: int, title: str, *, size: float = 13.0):
        super().__init__()
        self._num = number
        self._title = title
        self._size = size
        self._box = size + 7
        self.width = self._box + 8 + _sw(title, CJK_FONT, size)
        self.height = self._box + 10

    def draw(self) -> None:
        c = self.canv
        y0 = 4.0
        c.setFillColor(_BRAND)
        c.roundRect(0, y0, self._box, self._box, 3, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont(CJK_FONT, self._size * 0.8)
        c.drawCentredString(self._box / 2, y0 + self._box / 2 - self._size * 0.3, str(self._num))
        c.setFillColor(_INK)
        c.setFont(CJK_FONT, self._size)
        c.drawString(self._box + 8, y0 + self._box / 2 - self._size * 0.36, self._title)


class _MathFlow(Flowable):
    """显示式排版（叠式分式/上下标，math_text.draw_math）：标签 + 真数学式，零源码。

    超宽公式按列宽等比缩字号（最低 7pt），绝不越栏溢出。
    """

    def __init__(self, label: str | None, latex: str, *, size: float = 10.5, max_width: float | None = None):
        super().__init__()
        self._label = (label or "").strip()
        self._size = size
        self._node = parse_math(latex)
        self._label_w = _sw(self._label, CJK_FONT, size) + (5 if self._label else 0)
        self._mw, self._asc, self._desc = measure_math(
            self._node, font=CJK_FONT, size=size, string_width=_sw
        )
        self.width = self._label_w + self._mw
        self.height = self._asc + self._desc + 3
        if max_width and self.width > max_width:
            scale = max(0.62, max_width / self.width)
            self._size = size * scale
            self._label_w = _sw(self._label, CJK_FONT, self._size) + (5 if self._label else 0)
            self._mw, self._asc, self._desc = measure_math(
                self._node, font=CJK_FONT, size=self._size, string_width=_sw
            )
            self.width = self._label_w + self._mw
            self.height = self._asc + self._desc + 3

    def draw(self) -> None:
        c = self.canv
        y = self._desc + 1.5
        if self._label:
            c.setFont(CJK_FONT, self._size)
            c.setFillColor(colors.HexColor("#475569"))
            c.drawString(0, y, self._label)
        draw_math(
            self._node, c, self._label_w, y, font=CJK_FONT, size=self._size,
            string_width=_sw, color=_INK,
        )


def _para(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(text).replace("\n", "<br/>"), style)


def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle(
            "lp-title", fontName=CJK_FONT, fontSize=17, leading=26, spaceAfter=8, wordWrap="CJK"
        ),
        "body": ParagraphStyle("lp-body", fontName=CJK_FONT, fontSize=10.5, leading=17, wordWrap="CJK"),
        "h2plain": ParagraphStyle(
            "lp-h2plain", fontName=CJK_FONT, fontSize=13, leading=20, spaceAfter=2,
            textColor=_INK, wordWrap="CJK",
        ),
        "item": ParagraphStyle(
            "lp-item", fontName=CJK_FONT, fontSize=10.5, leading=17, leftIndent=10, wordWrap="CJK"
        ),
        "note": ParagraphStyle(
            "lp-note",
            fontName=CJK_FONT,
            fontSize=9,
            leading=14,
            leftIndent=20,
            textColor=colors.HexColor("#5a6672"),
            wordWrap="CJK",
        ),
        "cell": ParagraphStyle("lp-cell", fontName=CJK_FONT, fontSize=9, leading=13, wordWrap="CJK"),
        "cell_head": ParagraphStyle(
            "lp-cell-head", fontName=CJK_FONT, fontSize=9.5, leading=13, wordWrap="CJK",
            textColor=colors.HexColor("#1e3a8a"),
        ),
    }


_TABLE_WIDTH = _PAGE_SIZE[0] - 2 * _MARGIN
# 时间列 40pt：「45min」短格式整体一行不折行（H2 时间列「5分/钟」折行遗留）；
# 教师活动列相应让 4pt，总宽不变。
_FLOW_COL_WIDTHS = [52, 40, 132, 118, 138]
_BOARD_COL_WIDTHS = [158, 122, 200]
_MAX_CELL_ITEMS = 6  # PDF 行内条目上限（超长环节拆「（续）」行，防单行超高页）


def _flow_pdf_rows(spec: LessonPlanSpec) -> list[list[str]]:
    """教学过程表行（PDF 分块版）：条目过多的环节拆「（续）」行，保证行高可控。"""
    rows: list[list[str]] = []
    for stage in _merged_flow_stages(spec):
        teacher = stage.columns.teacher_activity or stage.items
        student = stage.columns.student_activity
        intent = stage.columns.design_intent or ([PlanItem(text=stage.summary)] if stage.summary else [])
        n = max(len(teacher), 1)
        chunks = max(1, (n + _MAX_CELL_ITEMS - 1) // _MAX_CELL_ITEMS)
        for ci in range(chunks):
            part = teacher[ci * _MAX_CELL_ITEMS : (ci + 1) * _MAX_CELL_ITEMS]
            title = stage.title if ci == 0 else f"{stage.title}（续）"
            time = _flow_time(stage.duration_minutes) if (stage.duration_minutes and ci == 0) else "—"
            rows.append([
                _inline(title),
                time,
                "<br/>".join(escape(_inline(it.text)) for it in part) or "—",
                "<br/>".join(escape(_inline(it.text)) for it in student) if ci == 0 else "—",
                "<br/>".join(escape(_inline(it.text)) for it in intent) if ci == 0 else "—",
            ])
    return rows


def _table_style(header: bool = True) -> TableStyle:
    cmds: list[tuple] = [
        ("GRID", (0, 0), (-1, -1), 0.6, _PILL_EDGE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        cmds += [
            ("BACKGROUND", (0, 0), (-1, 0), _PILL_BG),
            ("LINEBELOW", (0, 0), (-1, 0), 1.0, _BRAND),
        ]
    return TableStyle(cmds)


def render_lesson_plan_pdf(
    spec: LessonPlanSpec, *, meta: dict[str, Any] | None = None
) -> tuple[bytes, str]:
    """渲染教案 PDF（Golden 保真：pill 行 / 蓝色编号节 / 5 列表 / 板书三栏含图 / 真数学式）。"""
    _ensure_cjk_font()
    styles = _styles()
    story: list[Any] = []
    pending_head: list[Any] = []  # 章标题暂存：与首个内容块同流（防标题孤行留页底）

    def _flush_head() -> None:
        nonlocal pending_head
        if pending_head:
            story.extend(pending_head)
            pending_head = []

    for line in _document_lines(spec, meta=meta or {}, traceability=False):
        if line.kind == "title":
            story.append(_para(line.text, styles["title"]))
        elif line.kind == "h1":
            _flush_head()
            if line.num:
                pending_head = [_SectionHead(line.num, _CHAPTER_ALIAS.get(line.text, line.text)), Spacer(1, 2)]
            else:
                pending_head = [_para(_CHAPTER_ALIAS.get(line.text, line.text), styles["h2plain"]), Spacer(1, 2)]
        elif line.kind == "h2":
            _flush_head()
            story.append(_para(line.text, styles["item"]))
        elif line.kind == "pills":
            _flush_head()
            story.append(_PillRow(line.payload))
            story.append(Spacer(1, 4))
        elif line.kind == "table":
            _flush_head()
            headers, _ = line.payload
            rows = _flow_pdf_rows(spec)
            data = [[Paragraph(escape(h), styles["cell_head"]) for h in headers]]
            for row in rows:
                data.append([
                    Paragraph(escape(row[0]), styles["cell"]),
                    Paragraph(escape(row[1]), styles["cell"]),
                    Paragraph(row[2], styles["cell"]),
                    Paragraph(row[3], styles["cell"]),
                    Paragraph(row[4], styles["cell"]),
                ])
            table = Table(data, colWidths=_FLOW_COL_WIDTHS, repeatRows=1)
            table.setStyle(_table_style())
            story.append(table)
            story.append(Spacer(1, 4))
        elif line.kind == "board":
            design, png = line.payload
            text_cell: list[Any] = []
            if design.text_points:
                for it in design.text_points:
                    text_cell.append(_para("· " + _inline(it.text), styles["cell"]))
            else:
                text_cell.append(_para("—", styles["cell"]))
            fig_cell: list[Any] = []
            if png:
                from PIL import Image as PILImage

                im = PILImage.open(io.BytesIO(png))
                img_w = 42 * mm
                img = RLImage(io.BytesIO(png), width=img_w, height=img_w * im.height / im.width)
                img.hAlign = "CENTER"
                fig_cell.append(img)
            else:
                fig_cell.append(_para("（本课无参数化数学图形）", styles["cell"]))
            formula_cell: list[Any] = []
            if design.formulas:
                for it in design.formulas:
                    label, latex = split_label_math(it.text)
                    try:
                        if latex:
                            formula_cell.append(_MathFlow(f"{label}：" if label else None, latex,
                                                          max_width=_BOARD_COL_WIDTHS[2] - 12))
                        else:
                            formula_cell.append(_para(_inline(it.text), styles["cell"]))
                    except Exception:  # noqa: BLE001 - 公式解析失败降级 Unicode（绝不裸源码）
                        formula_cell.append(_para(_inline(it.text), styles["cell"]))
                    formula_cell.append(Spacer(1, 3))
            else:
                formula_cell.append(_para("—", styles["cell"]))
            head_row = [[Paragraph(escape(h), styles["cell_head"]) for h in ("文字要点", "数学图形", "公式区")]]
            table = Table([*head_row, [text_cell, fig_cell, formula_cell]], colWidths=_BOARD_COL_WIDTHS)
            table.setStyle(_table_style())
            if pending_head:
                story.append(KeepTogether([*pending_head, table]))  # 标题与板书表同页
                pending_head = []
            else:
                story.append(table)
            story.append(Spacer(1, 4))
        elif line.kind == "numbered":
            _flush_head()
            story.append(_para(f"{line.num}. {line.text}", styles["item"]))
        elif line.kind == "item":
            _flush_head()
            story.append(_para("· " + line.text, styles["item"]))
        elif line.kind == "note":
            _flush_head()
            story.append(_para(line.text, styles["note"]))
        else:
            _flush_head()
            story.append(_para(line.text, styles["body"]))
    _flush_head()
    story.append(Spacer(1, 6))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=_PAGE_SIZE,
        leftMargin=_MARGIN,
        rightMargin=_MARGIN,
        topMargin=_MARGIN,
        bottomMargin=_MARGIN,
        title=spec.metadata.title,
        subject=f"LessonPlanSpec revision {spec.revision}",
    )
    doc.build(story, onFirstPage=_page_footer, onLaterPages=_page_footer)
    return buf.getvalue(), _filename(spec, "pdf")


def _page_footer(canvas: Any, doc: Any) -> None:
    """页脚页码：分页是真实排版结果，页码即页数证据。"""
    canvas.saveState()
    canvas.setFont(CJK_FONT, 9)
    canvas.setFillColor(colors.HexColor("#8a939c"))
    canvas.drawCentredString(_PAGE_SIZE[0] / 2, _MARGIN / 2, f"第 {doc.page} 页")
    canvas.restoreState()


# ── DOCX 渲染（T24 导出终审 A3/A4/A8）───────────────────────────────────────
# 与 markdown/PDF 共用 `_document_lines`：教学内容只有一份来源，三种格式不漂移。
DOCX_CN_FONT = "微软雅黑"  # 中西文都钉中文字体，避免导出后中文变豆腐块


def render_lesson_plan_docx(
    spec: LessonPlanSpec, *, meta: dict[str, Any] | None = None
) -> tuple[bytes, str]:
    """渲染教案 DOCX（真实 WordprocessingML），返回 (docx bytes, 文件名)。

    - 章节按 A8 规范名映射（重点与难点→教学重难点、教学环节→教学过程）+ 蓝色编号方块；
    - 教学过程 5 列真实 Word 表格；板书三栏表格（中列嵌参数化图 PNG）；
    - 公式走 OMML（原生 Word 公式；转换失败降级 Unicode），零裸源码；
    - 运行级钉扎中文字体（w:rFonts ascii/hAnsi/eastAsia），Word 打开即正常。
    """
    from docx import Document
    from docx.oxml import OxmlElement, parse_xml
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    def _apply(
        run: Any, *, bold: bool = False, size: float = 10.5, color: str | None = None
    ) -> None:
        run.font.name = DOCX_CN_FONT
        run.font.size = Pt(size)
        run.font.bold = bold
        if color:
            run.font.color.rgb = RGBColor.from_string(color)
        rpr = run._element.get_or_add_rPr()  # noqa: SLF001 - 需要写 eastAsia 字体族
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = rpr.makeelement(qn("w:rFonts"), {})
            rpr.insert(0, rfonts)
        for attr in ("w:ascii", "w:hAnsi", "w:eastAsia"):
            rfonts.set(qn(attr), DOCX_CN_FONT)

    def _shade(element: Any, fill: str) -> None:
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:fill"), fill)
        element.append(shd)

    def _cell_shade(cell: Any, fill: str) -> None:
        _shade(cell._tc.get_or_add_tcPr(), fill)  # noqa: SLF001

    def _math_para(parent: Any, label: str | None, latex: str) -> None:
        p = parent.add_paragraph() if hasattr(parent, "add_paragraph") else parent
        if label:
            _apply(p.add_run(label), size=10, color="475569")
        omml = latex_to_omml(latex)
        if omml:
            try:
                p._p.append(parse_xml(omml))  # noqa: SLF001 - OMML 原生公式
                return
            except Exception:  # noqa: BLE001 - OMML 注入失败降级 Unicode
                pass
        _apply(p.add_run(latex_to_unicode(latex)), size=10.5)

    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = DOCX_CN_FONT
    normal.font.size = Pt(10.5)

    for line in _document_lines(spec, meta=meta or {}, traceability=False):
        kind = line.kind
        text = _CHAPTER_ALIAS.get(line.text, line.text) if kind == "h1" else line.text
        if kind == "h1":
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(12)
            if line.num:  # Golden 六章带蓝色编号方块；可选节（num=None）纯标题
                num_run = p.add_run(f" {line.num} ")
                _apply(num_run, bold=True, size=11, color="FFFFFF")
                _shade(num_run._element.get_or_add_rPr(), "2563EB")  # noqa: SLF001
                _apply(p.add_run(f" {text}"), bold=True, size=14, color="1F2D3D")
            else:
                _apply(p.add_run(text), bold=True, size=13, color="1F2D3D")
        elif kind == "h2":
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(6)
            _apply(p.add_run(text), bold=True, size=12)
        elif kind == "title":
            p = doc.add_paragraph()
            _apply(p.add_run(text), bold=True, size=17, color="111827")
        elif kind == "pills":
            t = doc.add_table(rows=1, cols=len(line.payload))
            t.style = "Table Grid"
            for cell, (label, value) in zip(t.rows[0].cells, line.payload, strict=True):
                _cell_shade(cell, "EFF4FF")
                cp = cell.paragraphs[0]
                _apply(cp.add_run(f"{label}：{value}"), bold=True, size=10, color="2563EB")
        elif kind == "table":
            headers, rows = line.payload
            t = doc.add_table(rows=1 + len(rows), cols=len(headers))
            t.style = "Table Grid"
            for cell, h in zip(t.rows[0].cells, headers, strict=True):
                _cell_shade(cell, "EFF4FF")
                _apply(cell.paragraphs[0].add_run(h), bold=True, size=10, color="1E3A8A")
            for r_i, row in enumerate(rows, start=1):
                for cell, value in zip(t.rows[r_i].cells, row, strict=True):
                    first = True
                    for seg in value.split("<br>"):
                        p = cell.paragraphs[0] if first else cell.add_paragraph()
                        first = False
                        _apply(p.add_run(seg), size=9.5)
        elif kind == "board":
            design, png = line.payload
            t = doc.add_table(rows=2, cols=3)
            t.style = "Table Grid"
            heads = ["文字要点", "数学图形", "公式区"]
            for cell, h in zip(t.rows[0].cells, heads, strict=True):
                _cell_shade(cell, "EFF4FF")
                _apply(cell.paragraphs[0].add_run(h), bold=True, size=10, color="1E3A8A")
            left, mid, right = t.rows[1].cells
            for it in design.text_points or []:
                p = left.paragraphs[0] if not left.paragraphs[0].text and not left.paragraphs[0].runs else left.add_paragraph()
                _apply(p.add_run("· " + _inline(it.text)), size=9.5)
            if png:
                mid.paragraphs[0].add_run().add_picture(io.BytesIO(png), width=Cm(4.6))
            else:
                _apply(mid.paragraphs[0].add_run("（本课无参数化数学图形）"), size=9.5)
            for it in design.formulas or []:
                label, latex = split_label_math(it.text)
                _math_para(right, (label + "：") if label else None, latex or it.text)
        elif kind == "numbered":
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Pt(10)
            _apply(p.add_run(f"{line.num}. {line.text}"), size=10.5)
        elif kind == "item":
            p = doc.add_paragraph()
            _apply(p.add_run(f"· {line.text}"), size=10.5)
            p.paragraph_format.left_indent = Pt(10)
        elif kind == "note":
            p = doc.add_paragraph()
            _apply(p.add_run(line.text), size=9, color="5A6672")
            p.paragraph_format.left_indent = Pt(20)
        elif kind == "meta":
            p = doc.add_paragraph()
            _apply(p.add_run(line.text), size=9.5, color="6B7280")
        else:  # body
            p = doc.add_paragraph()
            _apply(p.add_run(line.text), size=10.5)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), _filename(spec, "docx")


# R6-planfix：导出任务用——把 spec 全部 SourceLocator 串收集出来解析成可读标签。
collect_source_refs = _all_source_refs

__all__ = [
    "CJK_FONT",
    "DOCX_CN_FONT",
    "collect_source_refs",
    "render_lesson_plan_docx",
    "render_lesson_plan_markdown",
    "render_lesson_plan_pdf",
]
