"""Deck 发布前确定性整形（Phase E / Golden B Gate §6）——只整形，不改教学事实。

背景（设计 §2.2 / §6）：模板已是 Golden B 体系，缺的是**发布前的确定性内容整形**：
封面残留出版社/册次/课时前缀；页内与跨页重复（`y=kx+b` 写两遍、「同题两问」）；
教师口吻与工程黑话上屏；目标页只有 1~2 条；公式源码裸奔；课题本该有图却 0 图。

本模块只做六件事（全部确定性、无 LLM、无随机、幂等）：
1. 封面/页脚标题规范化：去出版社、册次、年份与「第X课时」前缀，**保留章节号**；
   封面副标题（教材行）只出现一次（spec 无 footer 字段：页脚文本来自 metadata.title，
   清理 metadata.title 即同时清掉页脚重复的完整书名）。
2. 去重：页内 elements/items/文本归一化去重；跨页逐字重复删除；同题回声删除
   （归一化后公共子串 ≥6 且含数字/拉丁）；删除教师口吻/占位符/工程黑话。
3. 薄页：非封面页真实内容单元 <2 → 并入相邻语义页（优先下一页；目标/引入优先
   并入概念/推导页），无合适目标则整页删除并重排 outline；不允许空页/近空页。
4. 公式：可见文本里的 `\\boxed{}` / `$...$` / `\\frac` 等转为干净 Unicode，或拆成
   `formula` 元素（formula 必须有 latex）；只删重复式，不删唯一数学事实。
5. 目标页：用 spec.teaching_strategy 的真实概念补「学习重点」，使 ≥3 条；
   没有真实来源时**不编造**，只记 warning。
6. 图形：点斜式/直线/斜率/一次函数/二次函数/幂函数课题且 deck 无任何 figure/asset 时，
   复用 `math_figures.asset_for` + `ensure_asset_stored` 挂 1 张真实 MATH_RENDER 资产；
   存储不可用则不挂（诚实降级）并记 warning，不阻断投影。

返回报告：`{removed_duplicates, merged_pages, removed_pages, figures_added, warnings}`
（`removed_duplicates` = 被删除/去重的可见行总数，含教师口吻/占位/黑话等非教学行）。
不改 `TeachingDeckSpec` schema、不改 TS 类型、不改 fixture 哈希；只改既有字段值。
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from app.ppt_v2.domain.spec import OutlineEntry, SlideElement, SlideSpec, TeachingDeckSpec
from app.teacher_prep.specs import math_figures
from app.teacher_prep.specs.math_text import (
    canonical_latex,
    latex_to_unicode,
    normalize_ascii_math_text,
    split_label_math,
)
from app.teacher_prep.specs.projector import (
    _FIGURE_PAGE_RE,
    _line_math_for_blueprint,
    _power_math_for_blueprint,
    _quadratic_math_for_blueprint,
    body_hash_of,
    is_placeholder_text,
)
from app.teacher_prep.text_safety import (
    is_teacher_annotation,
    restore_bare_mathbb,
    strip_edit_markers,
    strip_ghost_figure_refs,
    strip_internal_terms,
    strip_media_syntax,
    strip_orphan_punctuation_lines,
    strip_template_prefix,
)

logger = logging.getLogger(__name__)

POLISH_VERSION = "deck-polish-1.0.0"

# 跨页重复判定最小长度：与 `app.ppt_v2.qa.content.DUPLICATE_MIN_CHARS` 同口径
# （QA 只对 ≥6 字的内容行判跨页重复；此处对齐，避免「polish 删了 QA 不判」的错位）。
_DUP_MIN_CHARS = 6

# 元素正文展开顺序（与 export `_ELEMENT_BODY_ORDER`、enrich `_ROLE_ORDER` 同族）
_ROLE_BODY_ORDER = (
    "definition",
    "figure",
    "knowledge_card",
    "formula",
    "bullets",
    "steps",
    "table",
    "note",
)
_ROLE_BODY_RANK = {role: i for i, role in enumerate(_ROLE_BODY_ORDER)}
_STEPS_TYPES = ("derivation", "worked-example", "practice")
# 与页标题/教学环节重复的知识卡标题：不上屏为卡片标题，items 直接降为要点。
_GENERIC_CARD_TITLES = frozenset(
    {"学习目标", "学习重点", "学习难点", "本课要点", "本课要点梳理", "要点梳理", "课堂小结"}
)

# 单页元素上限（classroom 发布契约「单页 elements ≤12」，超出会被 8000 裁剪）
_MAX_ELEMENTS_PER_SLIDE = 12
# 单个列表元素条目上限（SlideElement.items max_length=10）
_MAX_ITEMS_PER_ELEMENT = 10

_TRAILING_PUNCT = "。？！?!；;，,：:"

# ── 标题规范化（封面 / metadata.title / 页脚）────────────────────────────────
# 册次：选择性必修第一册 / 必修一 / 选修2-1 等（保留后续章节号 1.2.1）
_VOLUME_RE = re.compile(
    r"(?:选择性必修|必修|选修)\s*第?\s*[一二三四五六七八九十百零\d]+\s*册?"
)
# 出版社：人教A版 / 苏教版 / 北师大版(2019) …
_PUBLISHER_RE = re.compile(
    r"(?:人教|苏教|北师大|华师大|湘教|沪教|鲁教|浙教|粤教|冀教|鄂教|川教|外研|岳麓|"
    r"沪科|沪粤|浙科|苏科|京教|津教|渝教|晋教|青岛|大象|教科)(?:\s*[A-Za-z]\s*版?|版)?"
)
# 「第X课时」/「共X课时」
_PERIOD_RE = re.compile(r"(?:共\s*)?第\s*[一二三四五六七八九十百\d]+\s*课时")
# 版本年份（人教A版2019）
_YEAR_RE = re.compile(r"(?:19|20)\d{2}\s*年?版?")

# ── 教师口吻 / 教研批注（deck 可见正文不得出现）─────────────────────────────
# 说明：`is_teacher_annotation` 已覆盖「引导学生…/组织学生…/布置作业…」等强指纹；
# 这里补投影实测漏网的三类：结合已有知识、结合教材开篇/情境、借助多媒体。
_TEACHER_VOICE_RE = re.compile(
    r"^\s*(?:"
    r"结合(?:已有|学生已有|教材开篇|教材引入|教材情境|教材内容|生活实际|实际情境)"
    r"|借助(?:多媒体|课件|几何画板|投影|动画)"
    r"|利用(?:多媒体|课件|几何画板|投影)"
    r"|通过(?:多媒体|课件|投影)"
    r"|(?:教师)?(?:引导|启发|组织|巡视|追问|讲授|讲解|板书|布置)"
    r")"
)
_TEACHER_VOICE_PHRASES = (
    "引导学生",
    "让学生",
    "请学生",
    "请同学",
    "要求学生",
    "启发学生",
    "组织学生",
    "借助多媒体",
    "利用多媒体",
    "通过多媒体",
    "结合已有知识",
    "结合学生已有",
    "教师引导",
    "教师提问",
    "教师巡视",
    "师生共同",
    "师生一起",
)
# 环节时间标注（画布/导出都不展示，但保留在 content 里作为教学节奏元数据）
_TIME_LINE_RE = re.compile(r"^\s*本环节用时\s*\d+\s*分钟\s*$")

# 同题回声阈值（与 projector `_QUESTION_ECHO_MIN_LCS` 同口径）
_ECHO_MIN_LCS = 6
# 同题回声只对「问题句」生效：例题解答（「解：…」「整理得…」）与公式步骤即使与题面
# 共享长片段也是**唯一解题事实**，绝不能被当成回声删掉（G-08「例题带解答」红线）。
_QUESTION_RE = re.compile(
    r"[？?]\s*$|^\s*(?:探究|思考|讨论|如何|怎样|为什么|什么|判断|观察|尝试|例\s*\d+\s*[:：]?\s*.*[？?])"
)

_OBJECTIVES_TITLE_RE = re.compile(r"学习目标|教学目标|^目标$|课堂目标")
_FIGURE_TOPIC_FALLBACK_RE = re.compile(r"直线|斜率|一次函数")
# R22：从“代数十图形”扩到真实课题（数列 / 集合 / 指数对数 / 三角）。
# 只在课题头（标题/前几页标题/教学引入）或全文强命中时触发，避免一道题里的
# 偶然词把整份课件带偏。
_SEQUENCE_TOPIC_RE = re.compile(r"等差|等比|数列|通项|前\s*n?\s*项和|递推公式|高斯求和|首末配对|倒序相加")
_SET_TOPIC_RE = re.compile(r"集合|交集|并集|补集|子集|韦恩图|\bVenn\b", re.IGNORECASE)
_EXPONENTIAL_TOPIC_RE = re.compile(r"指数函数|指数爆炸|指数增长")
_LOGARITHM_TOPIC_RE = re.compile(r"对数函数|对数运算|常用对数|自然对数")
_TRIG_TOPIC_RE = re.compile(r"三角函数|正弦函数|余弦函数|正弦曲线|余弦曲线")
_EXTRA_FIGURE_PAGE_RE: dict[str, re.Pattern[str]] = {
    "sequence": re.compile(r"数列|通项|前\s*n?\s*项和|等差|等比|求和|推导"),
    "sequence_geo": re.compile(r"数列|通项|前\s*n?\s*项和|等比|求和|推导"),
    "venn": re.compile(r"集合|交集|并集|补集|子集|韦恩"),
    "exponential": re.compile(r"指数|函数|图象|性质"),
    "logarithm": re.compile(r"对数|函数|图象|性质"),
    "trig": re.compile(r"三角|正弦|余弦|图象|性质"),
}
_NUM_RE = r"(-?\d+(?:\.\d+)?)"


def _first_number(patterns: tuple[str, ...], text: str) -> float | None:
    for pattern in patterns:
        matched = re.search(pattern, text)
        if not matched:
            continue
        try:
            value = float(matched.group(1))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return None


def _sequence_candidate(lines: list[str]) -> tuple[str, dict[str, float]] | None:
    blob = "\n".join(lines)
    if not _SEQUENCE_TOPIC_RE.search(blob):
        return None
    geometric = bool(re.search(r"等比|公比", blob))
    # 高斯求和/连续整数：用 d=1 的连续点列，而不是从别页抓到的公差
    is_gauss = bool(re.search(r"高斯|1\s*\+\s*2\s*\+|首末配对", blob))
    a1 = _first_number(
        (
            rf"(?:首项|第一项)\s*(?:为|=|：|:)?\s*{_NUM_RE}",
            rf"a[_₁1]\s*=\s*{_NUM_RE}",
        ),
        blob,
    )
    if a1 is None:
        a1 = 1.0
    n_value = _first_number(
        (
            rf"前\s*{_NUM_RE}\s*项",
            rf"n\s*=\s*{_NUM_RE}",
            rf"第\s*{_NUM_RE}\s*项",
        ),
        blob,
    )
    n = int(round(n_value)) if n_value is not None else (6 if (geometric or is_gauss) else 8)
    n = max(3, min(n, 10))
    if geometric:
        q = _first_number(
            (
                rf"(?:公比|q)\s*(?:为|=|：|:)?\s*{_NUM_RE}",
                rf"q\s*=\s*{_NUM_RE}",
            ),
            blob,
        )
        if q is None or q <= 0 or abs(q - 1.0) < 1e-9:
            q = 2.0
        return "sequence_geo", {"a1": a1, "q": q, "n": float(n)}
    d = _first_number(
        (
            rf"(?:公差|d)\s*(?:为|=|：|:)?\s*{_NUM_RE}",
            rf"\bd\s*=\s*{_NUM_RE}",
        ),
        blob,
    )
    if d is None:
        d = 1.0 if is_gauss else 2.0
    return "sequence", {"a1": a1, "d": d, "n": float(n)}


def _function_base(lines: list[str], *, log: bool) -> float:
    blob = "\n".join(lines)
    patterns = (
        (rf"(?:底数|a)\s*(?:为|=|：|:)?\s*{_NUM_RE}",)
        if not log
        else (rf"log_?\{{?{_NUM_RE}\}}?", rf"(?:底数|a)\s*(?:为|=|：|:)?\s*{_NUM_RE}")
    )
    value = _first_number(patterns, blob)
    if value is None or value <= 0 or abs(value - 1.0) < 1e-9:
        return 2.0
    return value
# 工程黑话（只在真命中时净化；不要无条件跑 strip_internal_terms——会吃集合描述法竖线）
_JARGON_RE = re.compile(r"SourceLocator|教材直连|溯源见备注|来源见备注")
# 数学源码证据：行内/显示式 `$…$`、`\cmd`、`^{`/`_{`（普通散文零改动，保护数学事实）
_MATH_SOURCE_RE = re.compile(r"\$|\\[a-zA-Z]{2,}|[\^_]\{")
# 公式胶囊标签：净化后只剩这些泛词（幽灵图引用被剥后的残片）→ 丢弃标签保留公式
_GENERIC_FORMULA_LABELS = frozenset({"直线", "图形", "图象", "图像", "函数", "如图", "结论", "方程"})


# ─────────────────────────────────────────────────────────────────────────────
# 归一化 / 清理原语
# ─────────────────────────────────────────────────────────────────────────────


def _norm(text: Any) -> str:
    """归一化键：去全部空白 + 去尾部句读（保留数学符号，防公式误判相等）。"""
    s = re.sub(r"\s+", "", str(text or ""))
    return s.rstrip(_TRAILING_PUNCT)


def _lcs_text(a: str, b: str) -> str:
    """a、b 的最长公共子串（短串 DP；deck 行长度量级，确定性）。"""
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


def _question_echo(candidate: str, existing: list[str]) -> bool:
    """candidate 是否为 existing 中某句的「同题回声」（公共子串 ≥6 且含数字/拉丁）。

    只对**问题句**成立（candidate 与回声源都必须是问题句）——例题解答/推导步骤不是
    回声，删掉即丢唯一教学事实。
    """
    if not _is_question_like(candidate):
        return False
    n_can = _norm(candidate)
    if len(n_can) < _ECHO_MIN_LCS:
        return False
    for raw in existing:
        if not _is_question_like(raw):
            continue
        shared = _lcs_text(n_can, _norm(raw))
        if len(shared) >= _ECHO_MIN_LCS and any(c.isascii() and c.isalnum() for c in shared):
            return True
    return False


def _is_question_like(text: str) -> bool:
    """问题句判定（问号结尾或以探究/思考/如何…开头）。"""
    return _QUESTION_RE.search(str(text or "").strip()) is not None


def _is_teacher_voice(text: str) -> bool:
    """整句是否教师口吻/教研批注（deck 可见正文不得出现）。"""
    raw = str(text or "")
    if not raw:
        return False
    if is_teacher_annotation(raw):
        return True
    if _TEACHER_VOICE_RE.match(raw):
        return True
    return any(phrase in raw for phrase in _TEACHER_VOICE_PHRASES)


_PHRASE_FIXES = (
    # 长括注在窄槽位会折成孤立右括号；改成无括号短句（数学含义不变）。
    (re.compile(r"直线斜率\s*k\s*必须存在（直线不与\s*x\s*轴垂直）"),
     "斜率 k 必须存在，直线不垂直于 x 轴"),
    (re.compile(r"点\s*P0\s*本身"), "点 P₁ 本身"),
)


def _clean_line(text: Any) -> str:
    """可见行净化：占位符/教师口吻/工程黑话/裸 LaTeX → 干净文本（空串=整行丢弃）。

    与 `sanitize_deck_text` 的关键差别：**不无条件**跑 `strip_internal_terms`
    （它的分隔符清理会把集合描述法的 `{x | x<4}` 竖线吃掉——数学事实红线），
    只在真的命中工程黑话时才净化；`latex_to_unicode` 也只在真出现数学源码证据时
    才跑（普通散文/中文标点原样保留）。
    """
    raw = str(text or "").strip()
    if not raw or is_placeholder_text(raw) or _is_teacher_voice(raw):
        return ""
    out = strip_media_syntax(raw)  # markdown 图/链接语法
    out = strip_edit_markers(out)  # （教师修订）/（待确认）/（AI 生成）…
    if _JARGON_RE.search(out):
        out = strip_internal_terms(out)  # SourceLocator/教材直连/溯源见备注
    out = strip_template_prefix(out)  # 「本节结论：」
    out = strip_ghost_figure_refs(out)  # 幽灵图引用（「如图」「对应图形」…）
    if _MATH_SOURCE_RE.search(out):
        out = restore_bare_mathbb(latex_to_unicode(out))  # $…$/\boxed/\frac → Unicode
    out = normalize_ascii_math_text(out)  # S_n / a_1 / …)/(2) → Sₙ / a₁ / …)/2
    out = strip_orphan_punctuation_lines(out)
    for pattern, replacement in _PHRASE_FIXES:
        out = pattern.sub(replacement, out)
    out = out.strip()
    if not out or is_placeholder_text(out) or _is_teacher_voice(out):
        return ""
    return out


def _clean_title(text: Any, fallback: str = "") -> str:
    """标题级净化：不因清洗为空而丢标题（fallback 保底）。"""
    cleaned = _clean_line(text)
    if cleaned:
        return cleaned
    return str(fallback or text or "").strip()


def _clean_lesson_title(title: Any) -> str:
    """课题/页脚标题规范化：去出版社、册次、年份、「第X课时」前缀；**保留章节号**。

    例：`苏教版选择性必修第一册1.2.1 直线的点斜式方程 第一课时`
      → `1.2.1 直线的点斜式方程`（页脚不再复读完整书名）。
    """
    raw = str(title or "").strip()
    if not raw:
        return raw
    out = _PERIOD_RE.sub(" ", raw)
    out = _VOLUME_RE.sub(" ", out)
    out = _PUBLISHER_RE.sub(" ", out)
    out = _YEAR_RE.sub(" ", out)
    out = re.sub(r"[ \t]{2,}", " ", out).strip(" -·、（）()：:，,")
    return out or raw


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _is_cover(slide: SlideSpec) -> bool:
    return slide.type == "title" or slide.layout_hint == "title"


def _is_objectives(slide: SlideSpec) -> bool:
    return _OBJECTIVES_TITLE_RE.search(slide.title or "") is not None


def _formula_key(latex: str | None) -> str:
    """公式归一化键（latex → Unicode → 去空白），空式返回空串。"""
    if not latex:
        return ""
    return _norm(latex_to_unicode(str(latex)))


def _clean_formula_label(label: Any) -> str:
    """公式胶囊标签净化：幽灵图残片（「直线」「如图」）不单独做标签。"""
    clean = _clean_line(label)
    return "" if _norm(clean) in _GENERIC_FORMULA_LABELS else clean


def _line_formula_keys(item: str) -> set[str]:
    """一行可见文本的归一化候选键：整行 + 「标签：」后的尾段（与公式元素比对用）。"""
    keys = {_norm(item)}
    raw = str(item or "")
    for sep in ("：", ":"):
        if sep in raw:
            tail = _norm(raw.rsplit(sep, 1)[-1])
            if tail:
                keys.add(tail)
    return {k for k in keys if k}


# ─────────────────────────────────────────────────────────────────────────────
# 页面内容单元（薄页判定）
# ─────────────────────────────────────────────────────────────────────────────


# legacy 散文页的单元数占位（>2：人工成品页不参与薄页合并）
_LEGACY_UNITS = 99


def _slide_units(slide: SlideSpec) -> int:
    """真实内容单元数（公式/条目/定义/知识卡/图形；note 页脚不算内容）。

    legacy 页（无 elements、只有 content 散文）返回 `_LEGACY_UNITS`——散文页是
    人工/旧链路的成品，不能按「行数」判薄（否则一段完整讲解会被误并）。
    """
    if _is_cover(slide):
        return 99
    if not slide.elements:
        return _LEGACY_UNITS if any(ln.strip() for ln in (slide.content or "").split("\n")) else 0
    units = 0
    for el in slide.elements:
        if el.role == "formula" and el.latex:
            units += 1
        elif el.role in ("bullets", "steps", "table"):
            units += len([it for it in (el.items or []) if str(it).strip()])
        elif el.role == "definition" and el.text:
            units += 1
        elif el.role == "knowledge_card":
            units += len([it for it in (el.items or []) if str(it).strip()])
            units += 1 if el.latex else 0
        elif el.role == "figure":
            units += 1 if (slide.assets or slide.math_object) else 0
        # note = 页脚小贴士，不计内容单元
    return units


def _slide_keys(slide: SlideSpec) -> set[str]:
    """本页全部可见键（正文/条目/公式 Unicode/卡片标题），跨元素去重用。"""
    keys: set[str] = set()
    for el in slide.elements:
        if el.role == "formula":
            key = _formula_key(el.latex)
            if key:
                keys.add(key)
            if el.text:
                keys.add(_norm(el.text))
            continue
        if el.text:
            keys.add(_norm(el.text))
        if el.title:
            keys.add(_norm(el.title))
        for it in el.items or []:
            keys |= _line_formula_keys(it)
    return {k for k in keys if k}


# ─────────────────────────────────────────────────────────────────────────────
# ① 封面 / metadata（页脚）标题
# ─────────────────────────────────────────────────────────────────────────────


def _polish_titles(spec: TeachingDeckSpec, report: dict[str, Any]) -> None:
    original_meta = spec.metadata.title
    cleaned_meta = _clean_lesson_title(original_meta)
    if cleaned_meta and cleaned_meta != original_meta:
        spec.metadata.title = cleaned_meta
        report["warnings"].append(f"页脚/课件名去除教材前缀：{cleaned_meta}")

    cover = next((s for s in spec.slides if _is_cover(s)), None)
    if cover is None:
        return
    cleaned_title = _clean_lesson_title(cover.title)
    if cleaned_title:
        cover.title = cleaned_title
    # 封面教学目标是同一句完整书名（PropertiesPanel 可见）→ 同步规范化
    if cover.teaching_goal:
        cover.teaching_goal = _clean_lesson_title(cover.teaching_goal) or cover.teaching_goal
    # 副标题（教材行）只出现一次：逐行净化 + 归一化去重 + 与标题重复行删除
    lines: list[str] = []
    seen: set[str] = set()
    title_keys = {_norm(cover.title), _norm(cleaned_title)}
    for raw in (cover.content or "").split("\n"):
        clean = _clean_line(raw)
        key = _norm(clean)
        if not key or key in seen or key in title_keys:
            continue
        seen.add(key)
        lines.append(clean)
    cover.content = "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# ② 页内清理 + 去重（教师口吻 / 占位 / 黑话 / 重复 / 同题回声 / 公式）
# ─────────────────────────────────────────────────────────────────────────────


def _polish_slide(slide: SlideSpec, report: dict[str, Any]) -> None:
    removed = 0
    slide.title = _clean_title(slide.title, fallback=slide.title)
    if not slide.elements:
        # legacy 页：content 是可见正文 → 逐行净化（LaTeX→Unicode、去口吻/占位/黑话）
        lines: list[str] = []
        seen: set[str] = set()
        for raw in (slide.content or "").split("\n"):
            clean = _clean_line(raw)
            key = _norm(clean)
            if not clean or key in seen:
                removed += 1
                continue
            seen.add(key)
            lines.append(clean)
        slide.content = "\n".join(lines)
        if removed:
            report["removed_duplicates"] += removed
        return

    # 先收集本页公式键（元素顺序不保证公式在前，故两遍扫描）
    formula_keys: set[str] = set()
    for el in slide.elements:
        if el.role == "formula" and el.latex:
            key = _formula_key(el.latex)
            if key:
                formula_keys.add(key)

    kept_units: list[str] = []  # 本页已保留的可比文本（同题回声判定）
    elements: list[SlideElement] = []
    for el in slide.elements:
        role = el.role
        if role == "figure":
            if slide.assets or slide.math_object:
                elements.append(el)
            else:
                removed += 1  # 幽灵图元素（无资产/无 math_object）
            continue

        if role == "formula":
            latex = canonical_latex(el.latex or "")
            label = _clean_formula_label(el.text) if el.text else ""
            if not latex:
                # 公式元素必须有 latex：先尝试从 text 里抽出显示式，再降级为文本要点
                extracted_label, extracted = split_label_math(el.text or "")
                latex = extracted or ""
                label = _clean_formula_label(extracted_label) or label
            if not latex:
                if label:
                    elements.append(SlideElement(role="bullets", items=[_clip(label, 200)]))
                    kept_units.append(label)
                removed += 1
                continue
            key = _formula_key(latex)
            if key and key in formula_keys and any(
                e.role == "formula" and _formula_key(e.latex) == key for e in elements
            ):
                removed += 1  # 同页重复公式
                continue
            formula_keys.add(key)
            elements.append(
                SlideElement(role="formula", latex=_clip(latex, 2000), text=_clip(label, 200) or None)
            )
            kept_units.append(label or latex_to_unicode(latex))
            continue

        # 非公式元素：逐条净化 + 显示式拆 formula + 页内去重
        items: list[str] = []
        for raw in el.items or []:
            label, latex = split_label_math(raw)
            if latex:
                fkey = _formula_key(latex)
                if fkey and not any(
                    e.role == "formula" and _formula_key(e.latex) == fkey for e in elements
                ):
                    formula_keys.add(fkey)
                    elements.append(
                        SlideElement(
                            role="formula",
                            latex=_clip(canonical_latex(latex), 2000),
                            text=_clip(_clean_formula_label(label), 200) or None,
                        )
                    )
                    kept_units.append(label or latex_to_unicode(latex))
                else:
                    removed += 1
                continue  # 显示式已由 formula 元素承载，行不再重复
            clean = _clean_line(raw)
            if not clean:
                removed += 1
                continue
            keys = _line_formula_keys(clean)
            if keys & formula_keys:
                removed += 1  # 行与同页公式同形 → 删行留公式
                continue
            key = _norm(clean)
            if key in {_norm(x) for x in items}:
                removed += 1
                continue
            if _question_echo(clean, kept_units):
                removed += 1  # 同题两写（同一数学核 + 同一问题核）
                continue
            items.append(_clip(clean, 200))
            kept_units.append(clean)

        text = _clean_line(el.text) if el.text else ""
        title = _clean_line(el.title) if el.title else ""
        if text and (_norm(text) in {_norm(x) for x in kept_units}):
            text = ""
        if role == "knowledge_card":
            latex = canonical_latex(el.latex or "") if el.latex else ""
            card_items = [it for it in items if _norm(it) not in formula_keys]
            if not (title or text or card_items or latex):
                removed += 1
                continue
            # 卡片标题与页标题/环节同文（如「学习目标」卡片 + 「学习目标」页）→
            # 标题不再上屏，items 降为要点，避免同页复读大标题。
            if title and (title == slide.title or title in _GENERIC_CARD_TITLES):
                if card_items:
                    elements.append(SlideElement(role="bullets", items=card_items[:_MAX_ITEMS_PER_ELEMENT]))
                    kept_units.extend(card_items)
                if text:
                    elements.append(SlideElement(role="bullets", items=[_clip(text, 200)]))
                    kept_units.append(text)
                continue
            elements.append(
                SlideElement(
                    role="knowledge_card",
                    title=_clip(title, 60) or None,
                    text=_clip(text, 400) or None,
                    latex=_clip(latex, 2000) or None,
                    items=card_items[:_MAX_ITEMS_PER_ELEMENT],
                )
            )
            if text:
                kept_units.append(text)
            kept_units.extend(card_items)
            continue
        if role in ("definition", "note"):
            if not text:
                removed += 1
                continue
            if _question_echo(text, kept_units) and role == "note":
                removed += 1  # 页脚贴士复读正文
                continue
            elements.append(SlideElement(role=role, text=_clip(text, 400)))
            kept_units.append(text)
            continue
        # bullets / steps / table
        if not items:
            removed += 1
            continue
        if role == "steps":
            # 版式会画步骤序号；剥掉 enrich 自带「1. / 2.」避免双编号
            items = [re.sub(r"^\s*\d+\s*[.、]\s*", "", str(it)) for it in items]
        elements.append(SlideElement(role=role, items=items[:_MAX_ITEMS_PER_ELEMENT]))  # type: ignore[arg-type]

    slide.elements = elements[:_MAX_ELEMENTS_PER_SLIDE]
    # 内容与元素同源（导出按 elements 展开；这里先粗同步，最终 _sync_content 统一重排）。
    # 注意：元素被清空时 content 也清空——content 是画布 fallback，留着不可见残留会
    # 让「空页」被当成 legacy 散文页逃过薄页判定。环节时间标注是教学节奏元数据，保留。
    time_lines = [
        ln.strip()
        for ln in (slide.content or "").split("\n")
        if _TIME_LINE_RE.match(ln.strip())
    ]
    slide.content = "\n".join(
        [
            *(line for el in slide.elements for line in _element_visible_lines(el)),
            *time_lines,
        ]
    )
    if removed:
        report["removed_duplicates"] += removed


def _element_visible_lines(el: SlideElement) -> list[str]:
    """一个元素在正文里的可见行（与 export `_element_body_lines` 同口径，公式转 Unicode）。"""
    if el.role == "figure":
        return []
    if el.role == "formula":
        latex = str(el.latex or "").strip()
        if not latex:
            return []
        rendered = latex_to_unicode(latex)
        label = str(el.text or "").strip()
        return [f"{label}：{rendered}" if label else rendered]
    lines: list[str] = []
    if el.role == "knowledge_card":
        if el.title:
            lines.append(str(el.title))
        lines.extend(str(it) for it in (el.items or []) if str(it).strip())
        if el.latex:
            lines.append(latex_to_unicode(str(el.latex)))
        if el.text:
            lines.append(str(el.text))
        return lines
    if el.text:
        lines.append(str(el.text))
    items = [str(it) for it in (el.items or []) if str(it).strip()]
    if el.role == "steps":
        # 版式自己画步骤序号；正文里剥掉「1. / 2.」避免双编号（含续页/拆分页）
        items = [re.sub(r"^\s*\d+\s*[.、]\s*", "", it) for it in items]
    lines.extend(items)
    return lines


# ─────────────────────────────────────────────────────────────────────────────
# ③ 跨页逐字重复（与 QA 同口径：归一化键 ≥6 字）
# ─────────────────────────────────────────────────────────────────────────────


def _dedupe_across_pages(spec: TeachingDeckSpec, report: dict[str, Any]) -> None:
    seen_global: set[str] = set()
    for slide in spec.slides:
        if _is_cover(slide) or not slide.elements:
            continue
        before_units = _slide_units(slide)
        backup = slide.model_copy(deep=True)
        removed = 0
        kept_elements: list[SlideElement] = []
        for el in slide.elements:
            if el.role == "formula":
                key = _formula_key(el.latex)
                if key and len(key) >= _DUP_MIN_CHARS and key in seen_global:
                    removed += 1
                    continue
                if key:
                    seen_global.add(key)
                kept_elements.append(el)
                continue
            if el.role in ("bullets", "steps", "table"):
                items = [
                    it
                    for it in (el.items or [])
                    if not (
                        len(_norm(it)) >= _DUP_MIN_CHARS and _norm(it) in seen_global
                    )
                ]
                removed += len(el.items or []) - len(items)
                if not items:
                    removed += 1
                    continue
                if items != list(el.items or []):
                    el.items = items  # type: ignore[assignment]
                for it in items:
                    key = _norm(it)
                    if len(key) >= _DUP_MIN_CHARS:
                        seen_global.add(key)
                kept_elements.append(el)
                continue
            if el.role == "knowledge_card":
                title = el.title or ""
                items = [
                    it
                    for it in (el.items or [])
                    if not (
                        len(_norm(it)) >= _DUP_MIN_CHARS and _norm(it) in seen_global
                    )
                ]
                removed += len(el.items or []) - len(items)
                latex = el.latex or ""
                key = _formula_key(latex) if latex else ""
                if key and len(key) >= _DUP_MIN_CHARS and key in seen_global:
                    latex = ""
                    removed += 1
                if key:
                    seen_global.add(key)
                text = el.text or ""
                if text and len(_norm(text)) >= _DUP_MIN_CHARS and _norm(text) in seen_global:
                    text = ""
                    removed += 1
                if not (title or items or latex or text):
                    removed += 1
                    continue
                kept_elements.append(
                    SlideElement(
                        role="knowledge_card",
                        title=title or None,
                        text=text or None,
                        latex=latex or None,
                        items=items,
                    )
                )
                if text:
                    seen_global.add(_norm(text))
                for it in items:
                    seen_global.add(_norm(it))
                continue
            if el.role in ("definition", "note"):
                text = str(el.text or "")
                key = _norm(text)
                if key and len(key) >= _DUP_MIN_CHARS and key in seen_global:
                    removed += 1
                    continue
                if key:
                    seen_global.add(key)
                kept_elements.append(el)
                continue
            kept_elements.append(el)

        slide.elements = kept_elements
        after_units = _slide_units(slide)
        if after_units == 0 and before_units > 0:
            # 保护性回滚：跨页去重不得把一页清成空页（空页只能由薄页合并处理）
            slide.elements = backup.elements
            report["warnings"].append(f"跨页去重跳过（会清空整页）：{slide.title}")
            continue
        if removed:
            report["removed_duplicates"] += removed


# ─────────────────────────────────────────────────────────────────────────────
# ④ 目标页补「学习重点」（只用真实概念，不编造）
# ─────────────────────────────────────────────────────────────────────────────


def _strategy_texts(spec: TeachingDeckSpec) -> list[str]:
    strategy = spec.teaching_strategy
    return [
        *strategy.concept_progression,
        *strategy.derivation_chain,
        *strategy.checks_for_understanding,
        str(strategy.intro or ""),
    ]


def _objective_supplements(spec: TeachingDeckSpec) -> list[str]:
    """目标页可补的真实条目：显式「重点/难点」行 + 本课概念（concept_progression）。"""
    out: list[str] = []
    seen: set[str] = set()
    for raw in _strategy_texts(spec):
        clean = _clean_line(raw)
        if clean.startswith(("重点", "难点")) and _norm(clean) not in seen:
            seen.add(_norm(clean))
            out.append(_clip(clean, 200))
    concepts: list[str] = []
    for raw in spec.teaching_strategy.concept_progression:
        clean = _clean_line(raw)
        key = _norm(clean)
        if clean and key not in seen and clean not in concepts:
            concepts.append(clean)
    if concepts:
        line = f"学习重点：{'、'.join(concepts[:3])}"
        if _norm(line) not in seen:
            out.append(_clip(line, 200))
    return out


def _enrich_objectives(spec: TeachingDeckSpec, report: dict[str, Any]) -> None:
    for slide in spec.slides:
        if _is_cover(slide) or not _is_objectives(slide) or not slide.elements:
            continue
        if _slide_units(slide) >= 3:
            continue
        for line in _objective_supplements(spec):
            if _slide_units(slide) >= 3:
                break
            if _norm(line) in _slide_keys(slide):
                continue
            _append_items(slide, [line], role="bullets")
        if _slide_units(slide) < 3:
            report["warnings"].append(
                f"目标页真实内容不足 3 条（{slide.title}），不编造补齐"
            )


# ─────────────────────────────────────────────────────────────────────────────
# ⑤ 薄页合并 / 删除 + outline 重排
# ─────────────────────────────────────────────────────────────────────────────


def _append_items(slide: SlideSpec, items: list[str], *, role: str = "bullets") -> int:
    """把条目并入本页（优先同 role；满 10 条另起一个列表元素；≤12 元素）。"""
    keys = _slide_keys(slide)
    fresh = [it for it in items if _norm(it) and _norm(it) not in keys]
    if not fresh:
        return 0
    slot = next((e for e in slide.elements if e.role == role and len(e.items or []) < _MAX_ITEMS_PER_ELEMENT), None)
    if slot is None:
        slot = next(
            (e for e in slide.elements if e.role in ("bullets", "steps") and len(e.items or []) < _MAX_ITEMS_PER_ELEMENT),
            None,
        )
    added = 0
    while fresh:
        if slot is None:
            if len(slide.elements) >= _MAX_ELEMENTS_PER_SLIDE:
                break
            slot = SlideElement(role=role, items=[])  # type: ignore[arg-type]
            slide.elements = [*slide.elements, slot]
        room = _MAX_ITEMS_PER_ELEMENT - len(slot.items or [])
        take, fresh = fresh[:room], fresh[room:]
        slot.items = [*(slot.items or []), *take]  # type: ignore[assignment]
        for it in take:
            keys.add(_norm(it))
        added += len(take)
        slot = None
    return added


def _append_formula(slide: SlideSpec, latex: str, label: str = "") -> bool:
    key = _formula_key(latex)
    if not key:
        return False
    if any(e.role == "formula" and _formula_key(e.latex) == key for e in slide.elements):
        return False
    if len(slide.elements) >= _MAX_ELEMENTS_PER_SLIDE:
        return False
    slide.elements = [
        *slide.elements,
        SlideElement(role="formula", latex=_clip(latex, 2000), text=_clip(label, 200) or None),
    ]
    # 新公式上版后，删掉与它同形的条目（行/公式只留一份）
    formula_norms = {_formula_key(e.latex) for e in slide.elements if e.role == "formula"}
    for el in slide.elements:
        if el.role in ("bullets", "steps") and el.items:
            el.items = [it for it in el.items if not (_line_formula_keys(it) & formula_norms)]  # type: ignore[assignment]
    slide.elements = [e for e in slide.elements if e.role not in ("bullets", "steps") or e.items]
    return True


def _merge_payload(target: SlideSpec, source: SlideSpec) -> None:
    """把源页真实内容并入目标页（公式 → formula；文本/条目 → 列表）。"""
    items: list[str] = []
    for el in source.elements:
        if el.role == "formula" and el.latex:
            _append_formula(target, str(el.latex), str(el.text or ""))
            continue
        if el.role == "knowledge_card":
            if el.latex:
                _append_formula(target, str(el.latex), str(el.title or ""))
            items.extend(str(it) for it in (el.items or []) if str(it).strip())
            if el.text:
                items.append(str(el.text))
            continue
        items.extend(_element_visible_lines(el))
    _append_items(target, [_clip(it, 200) for it in items if it.strip()], role="bullets")


def _merge_target(slides: list[SlideSpec], index: int) -> int | None:
    """薄页的并入目标：优先下一页；目标/引入优先概念/推导页；末页向前找语义页。"""
    src = slides[index]
    prefer_semantic = _is_objectives(src) or src.type == "introduction"
    forward = [j for j in range(index + 1, len(slides)) if not _is_cover(slides[j])]
    if prefer_semantic:
        for j in forward:
            if slides[j].type in ("concept", "definition", "derivation"):
                return j
    if forward:
        return forward[0]
    for j in range(index - 1, -1, -1):
        if _is_cover(slides[j]):
            continue
        if slides[j].type in ("concept", "definition", "derivation", "comparison", "inquiry", "introduction", "summary"):
            return j
    return None


def _fold_outline(work: TeachingDeckSpec, source_id: str, target_id: str) -> None:
    """把源页 outline 目标并入目标页条目（保留教学意图），源条目随后被过滤掉。"""
    src_entries = [e for e in work.outline if source_id in e.slide_ids]
    tgt_entries = [e for e in work.outline if target_id in e.slide_ids]
    if not src_entries or not tgt_entries:
        return
    tgt = tgt_entries[0]
    for entry in src_entries:
        goal = str(entry.goal or "").strip()
        if goal and goal not in tgt.goal:
            tgt.goal = f"{tgt.goal} · {goal}"
        entry.slide_ids = []


def _merge_thin_pages(work: TeachingDeckSpec, report: dict[str, Any]) -> None:
    guard = 0
    # 质量底线：整形不得把一份可用的短 deck 合并到 <3 页（课堂至少需要封面+内容+小结）。
    # 小 deck 宁可保留薄页，也不产出 1 页“空壳”。
    min_pages = 3
    while guard < 64:
        guard += 1
        slides = work.slides
        if len(slides) <= min_pages:
            return
        thin = next(
            (i for i, s in enumerate(slides) if not _is_cover(s) and _slide_units(s) < 2),
            None,
        )
        if thin is None:
            return
        source = slides[thin]
        if _slide_units(source) == 0:
            # 空页（清理后没有任何真实内容）：不并入他页（并入 0 条 = 无损合并），直接删除
            work.slides = [s for j, s in enumerate(slides) if j != thin]
            work.speaker_notes.pop(source.id, None)
            work.outline = [e for e in work.outline if source.id not in e.slide_ids]
            report["removed_pages"] += 1
            report["warnings"].append(f"删除清理后无真实内容的空页：{source.title}")
            continue
        target_idx = _merge_target(slides, thin)
        if target_idx is None:
            work.slides = [s for j, s in enumerate(slides) if j != thin]
            work.speaker_notes.pop(source.id, None)
            work.outline = [e for e in work.outline if source.id not in e.slide_ids]
            report["removed_pages"] += 1
            report["warnings"].append(f"删除无法并入的空页/薄页：{source.title}")
            continue
        target = slides[target_idx]
        _merge_payload(target, source)
        _fold_outline(work, source.id, target.id)
        note = work.speaker_notes.pop(source.id, None)
        if note and not work.speaker_notes.get(target.id):
            work.speaker_notes[target.id] = note
        if source.assets and not target.assets:
            target.assets = list(source.assets)
        work.slides = [s for j, s in enumerate(slides) if j != thin]
        report["merged_pages"] += 1
        report["warnings"].append(f"薄页并入相邻页：{source.title} → {target.title}")


# ─────────────────────────────────────────────────────────────────────────────
# ⑥ 可选图形（MATH_RENDER 资产；失败诚实降级，不阻断）
# ─────────────────────────────────────────────────────────────────────────────


def _dedupe_visual_pages(spec: TeachingDeckSpec, report: dict[str, Any]) -> bool:
    """版式可行性修复：同一资产只留一页，相邻页不重复 image-focus。

    8200 的序列硬约束要求「教学阶段切换处必须换族」，而 VISUAL_EVIDENCE 只有
    observe-and-infer 一个候选；两页相邻 image-focus 会令序列无解。这里只调整版式
    语义与重复图，不删除任何文本/公式/教学事实。

    规则（确定性）：
    - 一个 asset id 只允许被一页引用；后来的重复引用删除并记 warning；
    - 相邻两个 visual 页：后一页降级为 inquiry（标题含探究/思考/问题）或 concept；
    - 降级后若已无 asset/math_object，同时移除空的 figure 元素（不留空图位）。
    """
    used_assets: set[str] = set()
    prev_visual = False
    changed = False
    for slide in spec.slides:
        if _is_cover(slide):
            prev_visual = False
            continue
        visual = slide.type == "image-focus" or any(
            el.role == "figure" for el in slide.elements
        )
        if not visual:
            prev_visual = False
            continue

        kept: list[str] = []
        for asset_id in slide.assets:
            if asset_id in used_assets:
                report["removed_duplicates"] += 1
                report["warnings"].append(
                    f"重复图形资产 {asset_id} 从「{slide.title}」移除（同一资产只保留一页）"
                )
                changed = True
                continue
            used_assets.add(asset_id)
            kept.append(asset_id)
        slide.assets = kept

        has_visual_source = bool(slide.assets) or slide.math_object is not None
        if prev_visual or not has_visual_source:
            old_type = slide.type
            new_type = (
                "inquiry"
                if any(key in slide.title for key in ("探究", "思考", "问题"))
                else "concept"
            )
            slide.type = new_type  # type: ignore[assignment]
            slide.layout_hint = new_type  # type: ignore[assignment]
            if not has_visual_source:
                slide.elements = [el for el in slide.elements if el.role != "figure"]
            report["visual_pages_downgraded"] += 1
            report["warnings"].append(
                f"相邻/无图视觉页「{slide.title}」版式 {old_type} → {new_type}（不删内容）"
            )
            changed = True
            prev_visual = False
        else:
            prev_visual = True
    return changed


def _visual_body_units(slide: SlideSpec) -> int:
    """视觉页正文条目数（observe 版式右栏能承载的行数口径）。"""
    n = 0
    for el in slide.elements:
        if el.role in ("bullets", "steps", "table", "knowledge_card"):
            n += len(el.items or [])
        elif el.role in ("definition", "formula", "note"):
            n += 1
    return n


def _align_point_slope_notation(spec: TeachingDeckSpec, report: dict[str, Any]) -> None:
    """点斜式/斜截式课统一 P₁ 记号：enrich 偶发把 y₁ 写成 y₀，造成数学事实错误。

    只在同一页同时出现「点斜式/斜截式」与 y₁、y₀ 时替换；不碰其他课型的 y₀。
    """
    for slide in spec.slides:
        blob = "\n".join(_slide_text_for_check(slide))
        has_old = "y₀" in blob or "P₀" in blob
        has_new = "y₁" in blob or "P₁" in blob
        if not (has_old and has_new):
            continue
        # P₀→P₁ 可在任意使用 P₁ 记号的页修复；y₀→y₁ 只在点斜式/斜截式语境替换。
        if "点斜式" not in blob and "斜截式" not in blob and "P₀" not in blob:
            continue
        changed = False
        for el in slide.elements:
            for attr in ("text", "title"):
                value = getattr(el, attr)
                if isinstance(value, str) and ("y₀" in value or "P₀" in value):
                    setattr(el, attr, value.replace("y₀", "y₁").replace("P₀", "P₁"))
                    changed = True
            if el.items:
                new_items = [
                    str(it).replace("y₀", "y₁").replace("P₀", "P₁") for it in el.items
                ]
                if new_items != el.items:
                    el.items = new_items
                    changed = True
            if isinstance(el.latex, str) and "y_0" in el.latex:
                el.latex = el.latex.replace("y_0", "y_1")
                changed = True
        if changed:
            report["notation_fixes"] += 1
            report["warnings"].append(f"统一点斜式记号 P₁/y₁：「{slide.title}」")


def _slide_text_for_check(slide: SlideSpec) -> list[str]:
    out: list[str] = [slide.title]
    for el in slide.elements:
        out.extend(_element_visible_lines(el))
    return out


def _strip_practice_answers(spec: TeachingDeckSpec, report: dict[str, Any]) -> None:
    """练习页只留题面/条件，不把答案与解答步骤直接上屏（复审阻断）。

    只对 `practice` 页移除 formula/steps；若移除后不足 2 个真实内容单元则保留原样，
    避免把整页清空。答案仍可在教师教案/课堂练习面板中出现，不在学生可见的练习页泄题。
    """
    for slide in spec.slides:
        if slide.type != "practice":
            continue
        remaining = [
            el for el in slide.elements if el.role not in ("formula", "steps")
        ]
        if _slide_units_from_elements(remaining) < 2:
            continue
        removed = len(slide.elements) - len(remaining)
        if removed:
            slide.elements = remaining
            report["practice_answers_removed"] += removed
            report["warnings"].append(
                f"练习页「{slide.title}」移除 {removed} 个答案/解答元素（只保留题面与条件）"
            )


def _slide_units_from_elements(elements: list[SlideElement]) -> int:
    n = 0
    for el in elements:
        if el.role in ("bullets", "steps", "table", "knowledge_card"):
            n += len([it for it in (el.items or []) if str(it).strip()])
        elif el.role in ("definition", "formula", "note"):
            n += 1
    return n


def _retype_dense_intro(spec: TeachingDeckSpec, report: dict[str, Any]) -> None:
    """过密「课程引入」换概念双栏版式：7 条编号卡在 question-prompt 里会 4/3 留空。"""
    for slide in spec.slides:
        if slide.type != "introduction":
            continue
        if _slide_units_from_elements(slide.elements) < 6:
            continue
        slide.type = "concept"  # type: ignore[assignment]
        slide.layout_hint = "concept"  # type: ignore[assignment]
        report["intro_pages_retyped"] += 1
        report["warnings"].append(
            f"引入页「{slide.title}」内容 >6 条，版式 introduction → concept（避免 4/3 空格）"
        )


def _split_dense_inquiry(spec: TeachingDeckSpec, report: dict[str, Any]) -> None:
    """过密探究页拆出推导续页：题干/观察留在原页，步骤与注脚进「（续）」。"""
    i = 0
    while i < len(spec.slides):
        slide = spec.slides[i]
        if slide.type != "inquiry" or _slide_units_from_elements(slide.elements) <= 9:
            i += 1
            continue
        moved = [el for el in slide.elements if el.role in ("steps", "note")]
        kept = [el for el in slide.elements if el.role not in ("steps", "note")]
        if not moved or _slide_units_from_elements(kept) < 2:
            i += 1
            continue
        slide.elements = kept
        new_id = f"{slide.id}-cont"
        suffix = 2
        while any(s.id == new_id for s in spec.slides):
            new_id = f"{slide.id}-cont{suffix}"
            suffix += 1
        cont = SlideSpec(
            id=new_id,
            type="derivation",  # type: ignore[arg-type]
            teaching_goal=slide.teaching_goal,
            title=_clip(f"{slide.title}（续）", 120),
            content="",
            elements=moved,
            source_refs=list(slide.source_refs),
            layout_hint="derivation",  # type: ignore[arg-type]
            status=slide.status,
            qa_status="pending",
        )
        spec.slides.insert(i + 1, cont)
        for entry in spec.outline:
            if slide.id in entry.slide_ids:
                entry.slide_ids = [*entry.slide_ids, new_id]
                break
        else:
            spec.outline.append(
                OutlineEntry(id=f"stage-{new_id}", goal=slide.teaching_goal or slide.title, slide_ids=[new_id])
            )
        report["inquiry_pages_split"] += 1
        report["warnings"].append(f"探究页「{slide.title}」拆分续页（题干/步骤分页）")
        i += 2


def _split_dense_visual_pages(spec: TeachingDeckSpec, report: dict[str, Any]) -> bool:
    """视觉页过密 → 拆出「（续）」页，保住全部事实且让 observe 版式不溢出。

    8200 observe 版式右栏只适合 3 条短观察（Golden B 同口径）。enrich 后一页可能
    同时有定义、知识卡、推导、公式与注脚（实测 overflow/truncated）。

    修复：视觉页只保留图形 + 最多 3 条短观察（总长 ≤90 字）；定义/公式/步骤/
    知识卡剩余项/注脚全部移到紧随其后的「（续）」页（非视觉、按内容选 derivation
    或 concept）。不删除任何文本，只改变承载页。
    """
    changed = False
    i = 0
    while i < len(spec.slides):
        slide = spec.slides[i]
        visual = slide.type == "image-focus" or bool(slide.assets) or slide.math_object is not None
        if not visual or _is_cover(slide) or _visual_body_units(slide) <= 6:
            i += 1
            continue

        observations: list[str] = []
        obs_len = 0
        moved: list[SlideElement] = []
        for el in slide.elements:
            if el.role == "knowledge_card":
                rest: list[str] = []
                for item in el.items or []:
                    text = str(item)
                    if len(observations) < 3 and obs_len + len(text) <= 90:
                        observations.append(text)
                        obs_len += len(text)
                    else:
                        rest.append(text)
                if rest or el.title or el.latex:
                    moved.append(
                        SlideElement(
                            role="knowledge_card",
                            title=el.title,
                            items=rest,
                            text=el.text,
                            latex=el.latex,
                        )
                    )
                continue
            if el.role == "bullets":
                rest = []
                for item in el.items or []:
                    text = str(item)
                    if len(observations) < 3 and obs_len + len(text) <= 90:
                        observations.append(text)
                        obs_len += len(text)
                    else:
                        rest.append(text)
                if rest:
                    moved.append(SlideElement(role="bullets", items=rest))
                continue
            if el.role in ("definition", "formula", "steps", "table", "note"):
                moved.append(el)
                continue
            # figure / 其它：留在视觉页
        if not moved:
            i += 1
            continue

        slide.elements = [
            el for el in slide.elements if el.role == "figure"
        ] + ([SlideElement(role="bullets", items=observations)] if observations else [])
        cont_type = "derivation" if any(el.role == "steps" for el in moved) else "concept"
        new_id = f"{slide.id}-cont"
        suffix = 2
        while any(s.id == new_id for s in spec.slides):
            new_id = f"{slide.id}-cont{suffix}"
            suffix += 1
        cont = SlideSpec(
            id=new_id,
            type=cont_type,  # type: ignore[arg-type]
            teaching_goal=slide.teaching_goal,
            title=_clip(f"{slide.title}（续）", 120),
            content="",
            elements=moved,
            source_refs=list(slide.source_refs),
            layout_hint=cont_type,  # type: ignore[arg-type]
            status=slide.status,
            qa_status="pending",
        )
        spec.slides.insert(i + 1, cont)
        for entry in spec.outline:
            if slide.id in entry.slide_ids:
                entry.slide_ids = [*entry.slide_ids, new_id]
                break
        else:
            spec.outline.append(
                OutlineEntry(
                    id=f"stage-{new_id}",
                    goal=slide.teaching_goal or slide.title,
                    slide_ids=[new_id],
                )
            )
        report["visual_pages_split"] += 1
        report["warnings"].append(
            f"视觉页「{slide.title}」内容过密，拆分续页（保留全部事实，不删内容）"
        )
        changed = True
        i += 2
    return changed


# 一份 deck 的目标配图上限：旧实现最多 1 张，整份课件仍然“纯文字”。
# 3 张足以覆盖「引入 / 推导 / 练习」三类观察页，又不会让课件变成图片堆砌。
MAX_DECK_FIGURES = 3


def _figure_count(spec: TeachingDeckSpec) -> int:
    valid_assets = {asset.id for asset in spec.assets}
    count = 0
    for slide in spec.slides:
        if slide.math_object is not None:
            count += 1
        elif any(asset_id in valid_assets for asset_id in slide.assets):
            count += 1
    return count


def _deck_has_figure(spec: TeachingDeckSpec) -> bool:
    """是否已有**真实**图形：必须有可解析的资产或参数化 math_object。

    旧实现把“存在 role=figure 的元素”也算有图——空 figure 元素会让补图逻辑跳过，
    整份课件最终零图。现在只认真实资产/math_object（空引用不算）。
    """
    return _figure_count(spec) > 0


def _deck_lines(spec: TeachingDeckSpec) -> list[str]:
    lines: list[str] = [spec.metadata.title, str(spec.teaching_strategy.intro or "")]
    for slide in spec.slides:
        lines.append(slide.title)
        for el in slide.elements:
            lines.extend(_element_visible_lines(el))
        lines.extend(ln for ln in (slide.content or "").split("\n"))
    return [ln.strip() for ln in lines if str(ln).strip()]


def _figure_candidate(spec: TeachingDeckSpec) -> tuple[str, dict[str, float]] | None:
    """由 deck 自身文本确定性推导 (kind, params)；非图形课题返回 None。

    先看课题头（标题/前几页标题/教学引入）的强主题，避免“数列应用题里出现直线”
    被误配成直线图；再复用 projector 的现有参数解析（同一内核，禁止第二套图形语义）。
    """
    lines = _deck_lines(spec)
    head = "\n".join(
        [
            spec.metadata.title,
            str(spec.teaching_strategy.intro or ""),
            *[slide.title for slide in spec.slides[:3]],
        ]
    )
    for text in (head, "\n".join(lines)):
        if _SEQUENCE_TOPIC_RE.search(text):
            sequence = _sequence_candidate(lines)
            if sequence is not None:
                return sequence
        if _SET_TOPIC_RE.search(text):
            return "venn", {"n": 2.0}
        if _EXPONENTIAL_TOPIC_RE.search(text):
            return "exponential", {"a": _function_base(lines, log=False)}
        if _LOGARITHM_TOPIC_RE.search(text):
            return "logarithm", {"a": _function_base(lines, log=True)}
        if _TRIG_TOPIC_RE.search(text):
            return "trig", {"amp": 1.0}

    bp_view: dict[str, Any] = {
        "title": spec.metadata.title,
        "examples": [{"text": ln} for ln in lines],
        "concepts": [],
        "objectives": [],
        "key_points": [],
        "inquiries": [],
        "lesson_flow": [],
    }
    for resolver in (
        _power_math_for_blueprint,
        _quadratic_math_for_blueprint,
        _line_math_for_blueprint,
    ):
        found = resolver(bp_view)
        if found is not None:
            return found
    if any(_FIGURE_TOPIC_FALLBACK_RE.search(ln) for ln in lines):
        # 一次函数 / 只提「直线/斜率」的课：课堂基线直线（不猜测具体数值）
        return "line", {"k": -2.0, "x0": -1.0, "y0": 3.0}
    return None


def _figure_target(spec: TeachingDeckSpec, kind: str) -> SlideSpec | None:
    family_re = _FIGURE_PAGE_RE.get(kind) or _EXTRA_FIGURE_PAGE_RE.get(kind)
    candidates: list[tuple[int, SlideSpec]] = []
    # 只有 image-focus 与 introduction 的版式族能稳定承载 figure 槽
    # （VISUAL_EVIDENCE / CONTEXT_HOOK → observe-and-infer）；富文本推导页
    # enrich 后普遍 >6 单元，强行改 image-focus 会丢内容，因此不作为首选。
    priority = {"image-focus": 0, "introduction": 1, "derivation": 2, "concept": 3, "inquiry": 4}
    valid_assets = {asset.id for asset in spec.assets}
    for slide in spec.slides:
        if _is_cover(slide) or _is_objectives(slide):
            continue
        # 已挂真实图形/math_object 的页不重复补图（多图时按页序向后找下一个观察页）
        if slide.math_object is not None:
            continue
        if any(asset_id in valid_assets for asset_id in slide.assets):
            continue
        if slide.type not in priority:
            continue
        units = _slide_units(slide)
        if not (1 <= units <= 8):
            continue
        candidates.append((priority[slide.type], slide))
    if not candidates:
        return None
    if family_re is not None:
        matched = [
            (prio, slide)
            for prio, slide in candidates
            if family_re.search("\n".join([slide.title, *_slide_text(slide)]))
        ]
        if matched:
            candidates = matched
    candidates.sort(key=lambda p: p[0])
    return candidates[0][1]


def _slide_text(slide: SlideSpec) -> list[str]:
    out: list[str] = []
    for el in slide.elements:
        out.extend(_element_visible_lines(el))
    out.extend(ln for ln in (slide.content or "").split("\n") if ln.strip())
    return out


def _tune_figure_for_target(
    kind: str, target: SlideSpec
) -> tuple[str, dict[str, float]] | None:
    """按目标页（而不是整份课件）重算图形参数，避免把别页条件画到本页。

    真实事故：数列课「高斯求和故事引入」页被画成 d=-3.5 的递减点列，
    因为参数从后面的最值探究页抓取；图形必须服务当前页的观察任务。
    """
    target_lines = [target.title, *_slide_text(target)]
    if kind in ("sequence", "sequence_geo"):
        return _sequence_candidate(target_lines)
    if kind == "venn":
        return ("venn", {"n": 2.0}) if _SET_TOPIC_RE.search("\n".join(target_lines)) else None
    if kind == "exponential":
        return (
            ("exponential", {"a": _function_base(target_lines, log=False)})
            if _EXPONENTIAL_TOPIC_RE.search("\n".join(target_lines))
            else None
        )
    if kind == "logarithm":
        return (
            ("logarithm", {"a": _function_base(target_lines, log=True)})
            if _LOGARITHM_TOPIC_RE.search("\n".join(target_lines))
            else None
        )
    if kind == "trig":
        return ("trig", {"amp": 1.0}) if _TRIG_TOPIC_RE.search("\n".join(target_lines)) else None
    return None


def plan_math_figure(
    spec: TeachingDeckSpec, *, max_figures: int = 1
) -> dict[str, Any] | None:
    """规划下一张真实数学配图（不改 spec）：候选 kind/params + 目标页 + 已落存储资产。

    返回 None 表示：已达到配图上限 / 课题无图形族 / 无合适目标页 / 资产生成或存储失败。
    失败原因放在 ``warnings`` 中，由调用方决定是写报告还是返回给教师。
    """
    if _figure_count(spec) >= max(1, max_figures):
        return None
    warnings: list[str] = []
    found = _figure_candidate(spec)
    if found is None:
        return None
    kind, params = found
    target = _figure_target(spec, kind)
    if target is None:
        warnings.append(f"课题命中图形族（{kind}）但无合适的推导/概念页，未附图")
        return {"warnings": warnings, "kind": kind, "params": params, "asset": None, "target": None}
    tuned = _tune_figure_for_target(kind, target)
    if tuned is not None:
        kind, params = tuned
    try:
        asset, _data = math_figures.asset_for(kind, params)
        stored = math_figures.ensure_asset_stored(asset)
    except Exception as exc:  # noqa: BLE001 - 附图失败不阻断投影（诚实降级 + warning）
        warnings.append(f"图形资产生成失败（{kind}）：{type(exc).__name__}: {exc}")
        return {"warnings": warnings, "kind": kind, "params": params, "asset": None, "target": None}
    if not stored:
        warnings.append(f"图形资产落存储失败（{kind}），按缺图诚实降级（不挂无字节资产）")
        return {"warnings": warnings, "kind": kind, "params": params, "asset": None, "target": None}
    return {
        "warnings": warnings,
        "kind": kind,
        "params": params,
        "asset": asset,
        "target": target,
        # 内容寻址：不同页可能规划出同一张图（同 kind+params）。资产已存在时
        # 只把 id 挂到新页，禁止重复 add_asset（否则 spec 校验 asset id 重复）。
        "asset_exists": any(a.id == asset.id for a in spec.assets),
    }


def figure_patch_operations(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """把 plan_math_figure 的结果转成 deck patch ops（供 AI 充实后追加配图）。"""
    asset = plan.get("asset")
    target = plan.get("target")
    if asset is None or target is None:
        return []
    asset_payload = asset.model_dump(mode="json", exclude={"id"})
    slide_assets = [*target.assets]
    if asset.id not in slide_assets:
        slide_assets.append(asset.id)
    ops: list[dict[str, Any]] = []
    if not plan.get("asset_exists"):
        ops.append({"op": "add_asset", "target": asset.id, "value": asset_payload})
    ops.extend(
        [
            {"op": "set_slide", "target": target.id, "field": "assets", "value": slide_assets},
            {"op": "set_slide", "target": target.id, "field": "type", "value": "image-focus"},
            {"op": "set_slide", "target": target.id, "field": "layout_hint", "value": "image-focus"},
        ]
    )
    return ops


def _attach_math_figure(
    spec: TeachingDeckSpec, report: dict[str, Any], *, max_figures: int = 1
) -> None:
    # 默认 1 张（保持投影契约）；enrich/回填端点显式传 3，逐步补齐「引入/推导/练习」。
    for _ in range(max(1, max_figures)):
        plan = plan_math_figure(spec, max_figures=max_figures)
        if plan is None:
            return
        report["warnings"].extend(plan.get("warnings") or [])
        asset = plan.get("asset")
        target = plan.get("target")
        if asset is None or target is None:
            return
        if not any(a.id == asset.id for a in spec.assets):
            spec.assets = [*spec.assets, asset]
        if asset.id not in target.assets:
            target.assets = [*target.assets, asset.id]
        # 图形页走「图象观察」族（与 projector 附图同口径：observe family 才有 figure 槽位）
        target.type = "image-focus"
        target.layout_hint = "image-focus"
        report["figures_added"] += 1
        report["warnings"].append(f"补充真实数学图形（{plan['kind']}）到「{target.title}」")


# ─────────────────────────────────────────────────────────────────────────────
# 收尾：outline / content / 引用完整性
# ─────────────────────────────────────────────────────────────────────────────


def _sync_content(slide: SlideSpec) -> None:
    """content = 元素可见行的确定性展开（画布=导出=落库同源），保留环节时间标注。

    例题/练习页额外保留原 content 的「例/问题」题干：enrich 的 elements 可能只把
    题干拆进 knowledge_card，若这里用元素展开覆盖 content，worked/practice 版式
    会拿不到完整题面（实测渲染成空题框）。
    """
    if _is_cover(slide):
        return
    if not slide.elements:
        return
    original_problem = ""
    if slide.type in ("worked-example", "practice"):
        for raw_line in str(slide.content or "").splitlines():
            probe = raw_line.strip()
            if probe.startswith(("例", "问题")):
                original_problem = probe
                break
    ordered = sorted(
        enumerate(slide.elements),
        key=lambda pair: (_ROLE_BODY_RANK.get(pair[1].role, 99), pair[0]),
    )
    lines: list[str] = []
    for _, el in ordered:
        lines.extend(_element_visible_lines(el))
    extras = [
        ln.strip()
        for ln in (slide.content or "").split("\n")
        if _TIME_LINE_RE.match(ln.strip())
    ]
    out: list[str] = []
    seen: set[str] = set()
    if original_problem:
        lines.insert(0, original_problem)
    for ln in [*lines, *extras]:
        key = _norm(ln)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(ln)
    slide.content = "\n".join(out)


def _rebuild_outline(work: TeachingDeckSpec) -> None:
    valid = {s.id for s in work.slides}
    rebuilt = []
    for entry in work.outline:
        ids = [sid for sid in entry.slide_ids if sid in valid]
        if not ids:
            continue
        entry.slide_ids = ids
        rebuilt.append(entry)
    work.outline = rebuilt


def _prune_assets(work: TeachingDeckSpec) -> list[str]:
    used = {aid for slide in work.slides for aid in slide.assets}
    dropped = [a.id for a in work.assets if a.id not in used]
    if dropped:
        work.assets = [a for a in work.assets if a.id in used]
    return dropped


# ─────────────────────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────────────────────


def polish_deck_spec(
    spec: TeachingDeckSpec, *, classroom_compat: bool = False, max_figures: int = 1
) -> tuple[TeachingDeckSpec, dict[str, Any]]:
    """发布前确定性整形：返回 (polished_spec, report)。纯函数、幂等、不生成新事实。"""
    report: dict[str, Any] = {
        "removed_duplicates": 0,
        "merged_pages": 0,
        "removed_pages": 0,
        "figures_added": 0,
        "visual_pages_downgraded": 0,
        "visual_pages_split": 0,
        "practice_answers_removed": 0,
        "intro_pages_retyped": 0,
        "inquiry_pages_split": 0,
        "notation_fixes": 0,
        "warnings": [],
    }
    before_pages = len(spec.slides)
    work = spec.model_copy(deep=True)
    # 例题/练习题干必须在任何元素展开前捕获：后续 _polish_slide/_sync_content
    # 会把 content 重写为元素行，原始「例1 已知…」会丢失（worked/practice 版式
    # 因此渲染空题框）。这里先留原文，最终再插回。
    problem_lines: dict[str, str] = {}
    for slide in work.slides:
        if slide.type in ("worked-example", "practice"):
            for raw_line in str(slide.content or "").splitlines():
                probe = raw_line.strip()
                if probe.startswith(("例", "问题")):
                    problem_lines[slide.id] = probe
                    break

    _polish_titles(work, report)
    for slide in work.slides:
        _polish_slide(slide, report)
    _dedupe_across_pages(work, report)
    _strip_practice_answers(work, report)
    _align_point_slope_notation(work, report)
    _enrich_objectives(work, report)
    pages_before_merge = len(work.slides)
    _merge_thin_pages(work, report)
    pages_changed = len(work.slides) != pages_before_merge
    # 版式可行性：同一资产只留一页；相邻页不重复 image-focus（8200 序列约束要求
    # 教学阶段切换处换族，而 VISUAL_EVIDENCE 只有 observe-and-infer 一个候选）。
    # 只降级版式/去重图，不删内容。
    visual_changed = _dedupe_visual_pages(work, report)
    # 配图必须在 retype/split 之前：先让目标页拥有真实资产与 image-focus 语义，
    # 后续视觉页拆分才能看到它并按 observe 版式容量拆出续页（不再零图）。
    _attach_math_figure(work, report, max_figures=max_figures)
    if not classroom_compat:
        # 导出向分页：11 页 Golden B 结构；课堂快照不做 inquiry 分页。
        _retype_dense_intro(work, report)
    # 视觉页（有真实图）过密时两种模式都要拆分：课堂若直接塞 8 条观察会溢出。
    # 拆分只移动真实事实，不删内容、不产生空续页。
    split_changed = _split_dense_visual_pages(work, report)
    inquiry_changed = False
    if not classroom_compat:
        before_inquiry = len(work.slides)
        _split_dense_inquiry(work, report)
        inquiry_changed = len(work.slides) != before_inquiry
    for slide in work.slides:
        _sync_content(slide)
    for slide in work.slides:
        problem = problem_lines.get(slide.id)
        if not problem:
            continue
        lines = [
            ln for ln in str(slide.content or "").splitlines()
            if not re.fullmatch(r"例\s*\d*\s*(?:题目|已知条件)", ln.strip())
        ]
        if not any(_norm(ln) == _norm(problem) for ln in lines):
            lines.insert(0, problem)
        slide.content = "\n".join(lines)
    _rebuild_outline(work)
    if pages_changed or visual_changed or split_changed or inquiry_changed:
        dropped = _prune_assets(work)
        if dropped:
            report["warnings"].append(f"删除已无页引用的资产：{dropped}")

    try:
        polished = TeachingDeckSpec.model_validate(work.model_dump(mode="python"))
    except Exception as exc:  # noqa: BLE001 - 整形破坏引用完整性时回滚（不阻断投影）
        logger.error("deck_polish 校验失败，保留未整形 spec：%s", exc)
        rollback = dict(report)
        rollback["warnings"] = [*report["warnings"], f"整形结果校验失败，已回滚：{exc}"]
        return spec, rollback

    # 投影血缘 body_hash 反映真实落库正文（polish 后重算；projection 自身仍被排除）
    if polished.projection is not None:
        polished.projection.body_hash = body_hash_of(polished, exclude=("projection",))

    report["warnings"].append(
        f"deck_polish {POLISH_VERSION}：页数 {before_pages} → {len(polished.slides)}"
    )
    logger.info(
        "deck_polish done deck=%s pages=%s->%s removed_duplicates=%s merged=%s removed=%s figures=%s",
        polished.id,
        before_pages,
        len(polished.slides),
        report["removed_duplicates"],
        report["merged_pages"],
        report["removed_pages"],
        report["figures_added"],
    )
    return polished, report


__all__ = ["POLISH_VERSION", "polish_deck_spec"]
