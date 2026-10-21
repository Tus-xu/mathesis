"""R20 双 Spec 确定性投影器（LessonPlanSpec + TeachingDeckSpec）。

铁律：
- 同一 approved blueprint revision + 不可变 context snapshot → 双 Spec；
- 纯函数投影（同输入 → 同 body hash）；LLM 不参与事实改写（不反推 Blueprint）；
- schema 校验即 Pydantic 构造校验（失败即异常，不落库）；
- 幂等锚点：`compute_projection_hash`（输入规范化 hash）由 tp_spec_projections
  UNIQUE 约束承接；同输入重试 → 重放；不同输入 → 新投影；
- lineage：prompt/model/run（prompt_release_id/ai_run_id/model/run_hash/body_hash）；
- 教材引用一律 SourceLocator：deck 的 textbook_sources 走
  TextbookSourceRef.from_locator（page_refs 恒空）；教案 source_refs 只收 locator 串。

同时兼容两种已确认蓝图 body：
- R16 直形（proposal body 直接为 TeachingBlueprintProposal）；
- R20 修订形（body 为 TeachingBlueprintRevisionProposal，内容在 body["blueprint"]）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from app.ppt_v2.domain.spec import (
    ChangeLogEntry,
    DeckMetadata,
    OutlineEntry,
    SelectedExample,
    SlideElement,
    SlideSpec,
    TeachingDeckSpec,
    TeachingStrategy,
    TextbookSourceRef,
)
from app.teacher_prep.blueprint.projector import sanitize_title
from app.teacher_prep.context.models import TextbookTeachingContext, canonical_json
from app.teacher_prep.text_safety import (
    FORM_TERMS,
    is_teacher_annotation,
    normalize_form_terms,
    sanitize_deck_text,
    sanitize_visible_text,
    strip_template_prefix,
)
from app.teacher_prep_v2.contracts.sourcelocator import _V1_RE as _LOCATOR_V1_RE

from . import math_figures
from .lesson_plan_schema import (
    BlueprintRef as LpBlueprintRef,
)
from .lesson_plan_schema import (
    BoardDesign as LpBoardDesign,
)
from .lesson_plan_schema import (
    ChangeLogEntry as LpChangeLogEntry,
)
from .lesson_plan_schema import (
    ContextRef as LpContextRef,
)
from .lesson_plan_schema import (
    DiagnosticResponse as LpDiagnosticResponse,
)
from .lesson_plan_schema import (
    LessonPlanMetadata,
    LessonPlanSpec,
    LessonPlanStage,
    PlanItem,
)
from .lesson_plan_schema import (
    SpecProjectionRef as LpSpecProjectionRef,
)
from .lesson_plan_schema import (
    StageColumns as LpStageColumns,
)
from .lesson_plan_schema import (
    StratifiedSupport as LpStratifiedSupport,
)
from .math_text import latex_to_unicode, split_label_math

# v13（R3e 产物投影层回炉）：内容质量与产物干净化——
#   ① deck 每页 ≥2 条真实内容项（内容不足宁可少页也不空页），例题页解答由增稠补全；
#   ② 数学源码零进产物文本（`$…$`/`\boxed`/`\frac` 在生成源头转干净 Unicode 数学式）；
#   ③ 公式 canonical（显示式抽 `latex` 字段 + 标签行；点斜式记法统一 x₁/y₁）；
#   ④ 幽灵图引用剥除 + 方程形式术语守卫（点斜式/斜截式/两点式不混）；
#   ⑤ 直线/直线系 math_object（只存独立参数，派生不落库）：deck 走 MATH_RENDER 资产
#      （8201 只渲染 ellipse），教案板书中列同源出图（板书三栏含图）；
#   ⑥ G20 同源：deck「学习目标」= blueprint objectives（与教案同源）；流程页挂时间标注。
# v14: G6 产物复审回炉（deck 专属；教案不受影响）——
#      ① 「推导与论证」页零内容根治：theorems 桶里「本节结论：X」型标题回声句（与
#         目标/概念/重点几乎同名）不再充当推导正文；点斜式课在回声清空后按教材标准
#         流程**确定性组装**真实推导步骤（设点 → 斜率公式 → 整理得点斜式 → 方程与
#         直线对应 → 适用条件与斜截式，全部使用本课 canonical 公式，零编造）；
#      ② deck 逐页确定性去重：可见行与同页公式元素归一化重复 → 删行留公式；
#         归一化完全重复的行删首条之外的全部；与页标题几乎同名的孤立短句删除；
#      ③ 探究环节吸收 inquiries 桶时跳过「同题两写」回声（同数学核 + 长公共子串）；
#      ④ 收尾页剔除「小结：」模板回声行（环节标签不是可教学正文）。
# v12: R8-T deck 内容净化（落库边界）：教研批注句（「引导学生…」）不进 deck 版面；
#      「本节结论：」模板前缀剥离；工程黑话（「（AI 生成变式）」/SourceLocator/教材直连）
#      零泄漏；跨页逐字重复去重（收尾页只用未用过的真实内容，事实耗尽时用环节摘要/标题
#      撑住 ≥3 项）；封面补教师可读副标题（教材标识）。
# v10: 教案重难点确定性归并（同类「重点/难点」至多一条，多条以「；」连接；修正
#      「重点 / 重点 / 难点」三条的语义错误）。
# v9: 板书三栏清洗（文字要点剥教材章节号；公式区只放公式；公式条目禁止回填文字栏）。
# v7: W2.3 design_intent 三级兜底补全（蓝图级 design_intent 桶作第三来源）。
# v6: W2.3 投影增厚（_slide_elements 每内容页 definition+bullets / 目标页
#     figure+knowledge_card+formula / ≥2 项 note 兜底；_stage_columns design_intent
#     hints→summary 兜底；_board_design 三栏来源补强；LessonPlanStage.summary 透传）。
#     bump 使旧输入重投影产生新 projection_hash（旧 spec 不回写，教师重新投影即得新版）。
# v5: 教案六章扩展（board_design/homework/reflection/过程表五列出列）；v4: 椭圆页生成参数化 math_object；v3: 占位符不入版面 + 收尾页真实要点
# v16（二次函数补图）：图形资产候选扩到 quadratic（抛物线/二次函数，纯 MATH_RENDER
#      资产通道、不挂 math_object——MathObjectSpec.kind 契约锁死）；附图去掉「整册一张」
#      限制 → 按**页内容相关性**附图（每页至多 1 张、相邻连续带图 ≤2 页防 8200 视觉族
#      「同族连续 ≤2」规划失败、deck 级按 id 去重）；二次函数系数由蓝图真实方程
#      y=ax²+bx+c 确定性解析（解析不出退 y=x² 课堂基线）。
# v17（幂函数补图）：图形资产候选再扩一族 power（教材「五个常见幂函数」图象族：
#      y=x / x² / x³ / x^(1/2) / x⁻¹，同走 MATH_RENDER 资产通道）。此前幂函数课题
#      **完全无图**（候选只有 line/quadratic + 椭圆 math_object），而「五个常见幂函数的
#      图象与性质」恰恰是幂函数课最需要配图的页。判定与 quadratic 互斥（文本显式出现
#      「幂函数」才认），升版使已确认蓝图重新投影时产生新 spec（旧 v16 spec 不回写）。
PROJECTOR_VERSION = "teacher_prep.specs.v17"

StageKind = Literal[
    "introduction", "new_content", "example", "exercise", "summary", "homework",
]

# lesson_flow stage → deck SlideType（确定性映射）
_STAGE_TO_SLIDE_TYPE: dict[str, str] = {
    "introduction": "introduction",
    "new_content": "concept",
    "example": "worked-example",
    "exercise": "practice",
    "summary": "summary",
    "homework": "practice",
}

# ── 椭圆页 → 参数化 math_object（R4；确定性：仅按页语义判定，不猜测数值）──────
# 页内容同时命中「椭圆」与（焦点/标准方程/对称/范围/特殊点）语义 → 该页承载参数化
# 椭圆图形（kind=ellipse）。
# R8-U：参数（a/b）优先从本课**真实方程**确定性解析（标准式 \frac{x^2}{A}+\frac{y^2}{B}=1
# 或一般式 A x^2 + B y^2 = C），保证「同页公式 ↔ 图形参数」数学一致；解析不到具体数值
# （教材 canonical 是符号式 a²/b²）时退回课堂基线 a=5, b=3。半焦距 c=√(a²−b²) 恒派生。
# deck 与教案板书（board_design.math_object）**同源**：共用同一参数解析函数。
_ELLIPSE_RE = re.compile(r"椭圆")
_ELLIPSE_TOPIC_RE = re.compile(r"焦点|F_?1|标准方程|对称|范围|特殊点|扁平|离心率|x\^2")
_MATH_OBJECT_TYPES = ("concept", "derivation", "inquiry")

# 标准式：\frac{x^2}{25} + \frac{y^2}{9}（分母即 a²、b²）
_ELLIPSE_STD_RE = re.compile(
    r"\\frac\s*\{\s*x\s*\^\s*2\s*\}\s*\{\s*(\d+(?:\.\d+)?)\s*\}\s*\+\s*"
    r"\\frac\s*\{\s*y\s*\^\s*2\s*\}\s*\{\s*(\d+(?:\.\d+)?)\s*\}"
)
# 斜杠式：x^2/25 + y^2/9
_ELLIPSE_SLASH_RE = re.compile(
    r"x\s*\^\s*2\s*/\s*(\d+(?:\.\d+)?)\s*\+\s*y\s*\^\s*2\s*/\s*(\d+(?:\.\d+)?)"
)
# 一般式：4x^2 + 9y^2 = 36（系数缺省为 1）
_ELLIPSE_GENERAL_RE = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)?\s*x\s*\^\s*2\s*\+\s*(\d+(?:\.\d+)?)?\s*y\s*\^\s*2\s*=\s*(\d+(?:\.\d+)?)"
)

# 课堂基线（人教 A 版标准示例；解析不到具体数值时使用）
_DEFAULT_ELLIPSE_PARAMS: dict[str, float] = {"a": 5.0, "b": 3.0}


def _ellipse_params_from_text(text: str) -> dict[str, float] | None:
    """从文本中的椭圆方程解析 (a, b)；无法得到「焦点在 x 轴」的合法椭圆时返回 None。

    只接受 a² ≥ b²（长轴在 x 轴，与图形渲染口径一致）；圆（a²=b²）不是椭圆 → 忽略。
    数值全部确定性解析，不做任何猜测。
    """
    if not text:
        return None
    candidates: list[tuple[float, float]] = []
    for m in _ELLIPSE_STD_RE.finditer(text):
        candidates.append((float(m.group(1)), float(m.group(2))))
    for m in _ELLIPSE_SLASH_RE.finditer(text):
        candidates.append((float(m.group(1)), float(m.group(2))))
    for m in _ELLIPSE_GENERAL_RE.finditer(text):
        ca = float(m.group(1) or 1.0)
        cb = float(m.group(2) or 1.0)
        rhs = float(m.group(3))
        if ca > 0 and cb > 0 and rhs > 0:
            candidates.append((rhs / ca, rhs / cb))
    for a2, b2 in candidates:
        if a2 > b2 > 0:  # 长轴在 x 轴（a² > b²），且非圆
            return {"a": round(a2**0.5, 6), "b": round(b2**0.5, 6)}
    return None


def _ellipse_params_for_blueprint(bp: dict[str, Any]) -> dict[str, float]:
    """本课椭圆参数（确定性）：优先解析蓝图真实方程，否则用课堂基线。

    deck 页与教案板书共用本函数 → 二者 math_object 参数恒同源（FROZEN-CONTRACT §F.4）。
    """
    parts: list[str] = [str(bp.get("title") or "")]
    for bucket in ("objectives", "concepts", "theorems", "examples", "inquiries",
                   "key_points", "difficult_points", "homework", "reflection"):
        parts.extend(_bucket_texts(bp.get(bucket)))
    for stage in bp.get("lesson_flow") or []:
        if isinstance(stage, dict):
            parts.append(str(stage.get("title") or ""))
            parts.extend(_bucket_texts(stage.get("items") or []))
            parts.extend(_bucket_texts(stage.get("board_notes") or []))
    return _ellipse_params_from_text("\n".join(parts)) or dict(_DEFAULT_ELLIPSE_PARAMS)


def _ellipse_math_object(params: dict[str, float] | None = None) -> dict[str, Any]:
    from app.ppt_v2.domain.spec import MathObjectSpec

    return MathObjectSpec(kind="ellipse", params=dict(params or _DEFAULT_ELLIPSE_PARAMS)).model_dump()


# ── R3e 术语守卫上下文（确定性）────────────────────────────────────────────────
# 「本课真实教授的方程形式名」= 形式名出现在蓝图的**定义性位置**（标题/概念/定理/
# 重点/板书）；据此把 LLM 教错的形式名（「两点式」混入点斜式课）在生成源头纠正。
_TAUGHT_FORMS: frozenset[str] = frozenset()


def _taught_forms_of(bp: dict[str, Any]) -> frozenset[str]:
    parts: list[str] = [str(bp.get("title") or "")]
    for bucket in ("concepts", "theorems", "key_points", "board_notes"):
        parts.extend(_bucket_texts(bp.get(bucket)))
    for stage in bp.get("lesson_flow") or []:
        if isinstance(stage, dict):
            parts.extend(_bucket_texts(stage.get("board_notes") or []))
    blob = "\n".join(parts)
    return frozenset(term for term in FORM_TERMS if term in blob)


def _fix_terms(text: str) -> str:
    """方程形式术语守卫（上下文 = 本课教授形式名；见 `_taught_forms_of`）。"""
    return normalize_form_terms(text, _TAUGHT_FORMS)


# ── R3e 直线 / 直线系数学对象（只存独立参数；截距/斜率三角派生）────────────────
# 课件图形：MATH_RENDER 资产（8201 引擎只渲染 ellipse，line 走资产通道、同源参数）；
# 教案板书中列：BoardDesign.math_object（kind=line/line_family）同源出图。
# 参数解析确定性：优先本课**真实例题**的点与斜率，其次 y=kx+b 直线系，最后课堂基线
# （苏教版本节开篇例：过 A(-1,3)、斜率 -2）。
_LINE_TOPIC_RE = re.compile(r"点斜式|斜截式|两点式|直线的方程|直线方程|y\s*=\s*kx")
_LINE_POINT_RE = re.compile(
    r"过(?:定点|点)\s*[（(]?\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*[）)]?"
)
_LINE_SLOPE_RE = re.compile(r"斜率\s*(?:k\s*=\s*)?(-?\d+(?:\.\d+)?)")
_LINE_FAMILY_RE = re.compile(r"y\s*=\s*kx\s*([+＋])\s*(\d+(?:\.\d+)?)")
_LINE_BASELINE: dict[str, float] = {"k": -2.0, "x0": -1.0, "y0": 3.0}


def _line_math_for_blueprint(bp: dict[str, Any]) -> tuple[str, dict[str, float]] | None:
    """本课直线数学对象 (kind, params)；非直线课题返回 None（不猜测数值）。"""
    from app.teacher_prep.specs.math_text import latex_to_unicode

    raws: list[str] = []
    for bucket in ("examples", "inquiries", "concepts", "objectives", "key_points"):
        raws.extend(_bucket_texts(bp.get(bucket)))
    for stage in bp.get("lesson_flow") or []:
        if isinstance(stage, dict):
            raws.extend(_bucket_texts(stage.get("items")))
            raws.extend(_bucket_texts(stage.get("teacher_questions")))
            raws.extend(_bucket_texts(stage.get("board_notes")))
    blob = "\n".join(raws)
    if not _LINE_TOPIC_RE.search(blob):
        return None
    clean = latex_to_unicode(blob)
    for text in (latex_to_unicode(r) for r in raws):
        m_pt = _LINE_POINT_RE.search(text)
        m_k = _LINE_SLOPE_RE.search(text)
        if m_pt and m_k:
            return "line", {
                "k": float(m_k.group(1)),
                "x0": float(m_pt.group(1)),
                "y0": float(m_pt.group(2)),
            }
    m_fam = _LINE_FAMILY_RE.search(clean.replace(" ", ""))
    if m_fam:
        b = float(m_fam.group(2)) * (1 if m_fam.group(1) in "+＋" else -1)
        return "line_family", {"b": b}
    return "line", dict(_LINE_BASELINE)


def _line_math_object(kind: str, params: dict[str, float]) -> dict[str, Any]:
    from app.ppt_v2.domain.spec import MathObjectSpec

    return MathObjectSpec(kind=kind, params=dict(params)).model_dump()


# ── 二次函数（抛物线）图形参数：纯 MATH_RENDER 资产通道（同 line/line_family）─────
# 契约约束（必须遵守）：MathObjectSpec.kind 锁死 ellipse/line/line_family（8200
# deck.py 更只收 ellipse）→ 二次函数**不挂 math_object**，只走图形资产通道：
# asset_for 派生 → service.ensure_asset_stored 落 ppt_v2_assets → resolve 注入
# figure 槽 → 8201 _figure_for_assets 取字节（渲染端零改动）。
# 系数解析确定性：正则抽本课真实方程 y=ax²+bx+c 的 a/b/c（缺项补 0、支持分式系数）；
# 解析不出 → 课堂基线 y = x²（a=1, b=0, c=0 基础抛物线，不猜测具体数值）。
_QUAD_TOPIC_RE = re.compile(r"二次函数|抛物线")


def _quadratic_math_for_blueprint(bp: dict[str, Any]) -> tuple[str, dict[str, float]] | None:
    """本课二次函数图形参数 (kind="quadratic", params)；非二次函数课题返回 None。"""
    raws: list[str] = []
    for bucket in ("examples", "inquiries", "concepts", "objectives", "key_points"):
        raws.extend(_bucket_texts(bp.get(bucket)))
    for stage in bp.get("lesson_flow") or []:
        if isinstance(stage, dict):
            raws.extend(_bucket_texts(stage.get("items")))
            raws.extend(_bucket_texts(stage.get("teacher_questions")))
            raws.extend(_bucket_texts(stage.get("board_notes")))
    blob = "\n".join(raws)
    if not _QUAD_TOPIC_RE.search(blob):
        return None
    for text in (latex_to_unicode(r) for r in raws):
        coeffs = math_figures.quadratic_coeffs_from_text(text)
        if coeffs is not None:
            return "quadratic", coeffs
    return "quadratic", dict(math_figures.QUADRATIC_BASELINE)


# 图形候选 → 页内容相关性（按页 blob 命中图形族语义才附图；确定性正则）。
# 二次函数页含 x²/x^2 上标（latex_to_unicode → ²）或「顶点/开口/对称轴」语义即相关。
_POWER_TOPIC_RE = re.compile(r"幂函数")


def _power_math_for_blueprint(bp: dict[str, Any]) -> tuple[str, dict[str, float]] | None:
    """本课幂函数图象族参数 (kind="power", params)；非幂函数课题返回 None（不猜测）。

    幂函数与二次函数/直线互斥：只有在文本**显式**出现「幂函数」时才认（避免所有含
    y=x² 的课都被误挂幂函数图象族）。图形为教材「五个常见幂函数」标准图象族，
    无自由数值参数 → 固定 n=5（内容寻址常量，同参数恒同资产）。
    """
    raws: list[str] = []
    for bucket in ("examples", "inquiries", "concepts", "objectives", "key_points"):
        raws.extend(_bucket_texts(bp.get(bucket)))
    for stage in bp.get("lesson_flow") or []:
        if isinstance(stage, dict):
            raws.extend(_bucket_texts(stage.get("items")))
            raws.extend(_bucket_texts(stage.get("teacher_questions")))
            raws.extend(_bucket_texts(stage.get("board_notes")))
    blob = "\n".join(raws)
    if not _POWER_TOPIC_RE.search(blob):
        return None
    return "power", {"n": math_figures.POWER_FAMILY_N}


# 图形候选 → 页内容相关性（按页 blob 命中图形族语义才附图；确定性正则）。
# 二次函数页含 x²/x^2 上标（latex_to_unicode → ²）或「顶点/开口/对称轴」语义即相关。
_FIGURE_PAGE_RE: dict[str, re.Pattern[str]] = {
    "line": re.compile(r"点斜式|斜截式|两点式|直线|斜率|y\s*=\s*kx"),
    "line_family": re.compile(r"点斜式|斜截式|两点式|直线|斜率|y\s*=\s*kx"),
    "quadratic": re.compile(r"二次函数|抛物线|开口|对称轴|顶点|x\s*²|\^\s*\{?2"),
    "power": re.compile(r"幂函数|图象|图像"),
}


# ── R3e 例题确定性解答（产物审查 G-08/G-09「例题带解答」；纯代数、零编造）──────
# 只在题面**显式给出**数值（点 + 斜率 / 截距 + 点）时生成解答步骤；数值全部来自
# 题面，k/b 由四则运算确定性导出（Fraction 保真）；解析不到 → 不生成（诚实）。
# deck 例题页与教案例题章共用本函数（G20 ③ 两产物解答同源）。
_EX_POINT_RE = re.compile(
    r"过(?:点\s*)?[（(]\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*[）)]"
)
_EX_INTERCEPT_RE = re.compile(r"截距\s*(?:是|为|=|：)?\s*(-?\d+(?:\.\d+)?)")


def _fmt_num(value: Any) -> str:
    from fractions import Fraction

    v = value if isinstance(value, Fraction) else Fraction(str(value))
    if v.denominator == 1:
        return str(v.numerator)
    return f"{v.numerator}/{v.denominator}"


def _fmt_signed(term: str, value: Any) -> str:
    """「y − 2」/「y + 2」/「y」形态（term=y 或 x）。"""
    from fractions import Fraction

    v = value if isinstance(value, Fraction) else Fraction(str(value))
    if v == 0:
        return term
    return f"{term} - {_fmt_num(v)}" if v > 0 else f"{term} + {_fmt_num(-v)}"


def _fmt_linear(k: Any, b: Any) -> str:
    """斜截式 y = kx + b 的规范字符串（k=±1 省略系数）。"""
    from fractions import Fraction

    kv = k if isinstance(k, Fraction) else Fraction(str(k))
    bv = b if isinstance(b, Fraction) else Fraction(str(b))
    left = "y = "
    if kv == 0:
        return left + _fmt_num(bv)
    coef = "" if kv == 1 else ("-" if kv == -1 else _fmt_num(kv))
    return f"{left}{coef}x" + (f" + {_fmt_num(bv)}" if bv > 0 else (f" - {_fmt_num(-bv)}" if bv < 0 else ""))


def worked_solution_steps(question: str) -> list[str]:
    """题面 → 确定性解答步骤（直线方程例题）；数值不足返回 []（绝不编造）。"""
    from fractions import Fraction

    text = latex_to_unicode(str(question or ""))
    m_pt = _EX_POINT_RE.search(text)
    m_k = _LINE_SLOPE_RE.search(text)
    m_b = _EX_INTERCEPT_RE.search(text)
    x0 = y0 = k = b = None
    if m_pt:
        x0 = Fraction(m_pt.group(1))
        y0 = Fraction(m_pt.group(2))
    if m_k:
        k = Fraction(m_k.group(1))
    if m_b:
        b = Fraction(m_b.group(1))

    if x0 is not None and y0 is not None and k is not None:
        # 过点 (x₀, y₀) + 斜率 k → 点斜式代入；b = y₀ − k·x₀ → 斜截式整理
        intercept = y0 - k * x0
        point_slope = f"{_fmt_signed('y', y0)} = {_fmt_num(k)}({_fmt_signed('x', x0)})"
        return [
            f"解：将点 ({_fmt_num(x0)}, {_fmt_num(y0)}) 与斜率 k = {_fmt_num(k)} 代入点斜式方程，得 {point_slope}",
            f"整理得斜截式方程 {_fmt_linear(k, intercept)}",
        ]
    if x0 is not None and y0 is not None and b is not None:
        # 截距 b + 过点 (x₀, y₀) → 设 y = kx + b，解出 k，再代回点斜式
        if x0 == 0:
            return []
        k2 = (y0 - b) / x0
        point_slope = f"{_fmt_signed('y', y0)} = {_fmt_num(k2)}({_fmt_signed('x', x0)})"
        return [
            f"解：由截距 b = {_fmt_num(b)} 设斜截式方程 y = kx"
            + (f" + {_fmt_num(b)}" if b > 0 else (f" - {_fmt_num(-b)}" if b < 0 else ""))
            + f"，代入点 ({_fmt_num(x0)}, {_fmt_num(y0)}) 解得 k = {_fmt_num(k2)}",
            f"斜截式方程为 {_fmt_linear(k2, b)}；代回点斜式方程得 {point_slope}",
        ]
    return []


# ── G6（v14）deck 确定性清理：标题回声判定 / 点斜式推导组装 / 归一化去重 ─────────
# 范围铁律：本节全部助手只服务 project_deck_spec（deck spec）；教案投影不经过它们。


def _norm_line(text: Any) -> str:
    """行归一化：去全部空白 + 去尾部句读（保留运算符号与括号，防数学式误判相等）。"""
    s = re.sub(r"\s+", "", str(text or ""))
    return s.rstrip("。？！?!；;，,：:")


def _has_math_char(text: str) -> bool:
    """是否含数学/拉丁/数字字符（标题回声判定用：回声行是纯中文短语）。"""
    return any(ch.isdigit() or (ch.isascii() and ch.isalpha()) for ch in text)


def _lcs_text(a: str, b: str) -> str:
    """a、b 的一个最长公共子串（短串 DP；deck 行长度量级，确定性）。"""
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


# 「同题两写」判定阈值：公共子串 ≥6 且含拉丁/数字（同一数学核 + 同一问题核）。
_QUESTION_ECHO_MIN_LCS = 6


def _question_echo(candidate: str, existing: list[str]) -> bool:
    """candidate 是否为 existing 中某句的同题回声（如「…y=kx+2有什么共同特点？」vs
    「探究直线y=kx+2的共同特点」——同一数学对象、同一问题写两遍）。"""
    n_can = _norm_line(candidate)
    if len(n_can) < _QUESTION_ECHO_MIN_LCS:
        return False
    for raw in existing:
        shared = _lcs_text(n_can, _norm_line(raw))
        if len(shared) >= _QUESTION_ECHO_MIN_LCS and any(
            c.isascii() and c.isalnum() for c in shared
        ):
            return True
    return False


# 点斜式课题的教材标准推导（确定性组装；只用本课 canonical 公式与教材通用事实，
# 不含任何具体数值编造）。任务裁决口径：蓝图推导环节无真实内容时按此组装。
# R6-planfix：第 2 步收尾不用「（x ≠ x₁）」尾括号（强调样式大字号下会把「）」孤到行首），
# 改写为句内条件「，其中 x ≠ x₁」——语义不丢失、无行尾孤字。
_LINE_DERIVATION_STEPS: tuple[str, ...] = (
    "设点 P(x, y) 是直线上不同于定点 P₁(x₁, y₁) 的任意一点",
    "由斜率公式，过 P₁(x₁, y₁) 与 P(x, y) 两点的斜率 k = (y - y₁)/(x - x₁)，其中 x ≠ x₁",
    "去分母并整理，得 y - y₁ = k(x - x₁)，这就是直线的点斜式方程",
    "定点 P₁(x₁, y₁) 的坐标也满足该方程：直线上每个点的坐标都满足方程，满足方程的点都在直线上",
    "点斜式方程的适用条件是斜率 k 存在；当定点取 (0, b) 时，方程化为 y = kx + b，即斜截式，b 是直线在 y 轴上的截距",
)

_POINT_SLOPE_RE = re.compile(r"点斜式")


def _is_point_slope_lesson(bp: dict[str, Any]) -> bool:
    """本课是否点斜式课（标题/概念/重点/定理明示「点斜式」）——推导组装只服务本课型。"""
    parts = [str(bp.get("title") or "")]
    for bucket in ("concepts", "key_points", "theorems"):
        parts.extend(_bucket_texts(bp.get(bucket)))
    return _POINT_SLOPE_RE.search("\n".join(parts)) is not None


def _echo_fact_keys(bp: dict[str, Any]) -> set[str]:
    """标题回声判定基准：目标/概念/重点条目的归一化键（这些桶已独立承载同内容）。"""
    keys: set[str] = set()
    for bucket in ("objectives", "concepts", "key_points"):
        for text in _bucket_texts(bp.get(bucket)):
            key = _norm_line(_fix_terms(sanitize_deck_text(text)))
            if key:
                keys.add(key)
    return keys


def _is_theorem_echo(text: str, echo_keys: set[str]) -> bool:
    """「本节结论：X」型标题回声句：剥模板前缀/净化后与目标/概念/重点几乎同名。

    仅做归一化后的**全等**判定（保守）：真实定理句（含公式或完整陈述）不会命中。
    """
    stripped = _norm_line(_fix_terms(sanitize_deck_text(text)))
    if not stripped:
        return True
    return stripped in echo_keys


# ── R3e 内容单元：原始条目 →（干净可见行，(标签, latex) 显示公式）──────────────
def _content_units(raw_texts: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """分条抽取：可见行（干净 Unicode 数学式、零源码）+ 显示式（标签 + canonical latex）。

    「标签：$latex$」形态 → 行 + 公式双投影（deck 公式胶囊 / 教案公式区同源）；
    整句文字（含 `$b$是…` 这类行内小数学）→ 只入可见行。
    """
    lines: list[str] = []
    formulas: list[tuple[str, str]] = []
    seen_latex: set[str] = set()
    for raw in raw_texts or []:
        label, latex = split_label_math(raw)
        if latex and latex not in seen_latex:
            seen_latex.add(latex)
            formulas.append((_fix_terms(label), latex))
        clean = _fix_terms(sanitize_deck_text(raw))
        if clean:
            lines.append(clean)
    return lines, formulas

# ── 「（待确认）」占位符处理（v3）────────────────────────────────────────────
# R16 诚实机制：教材覆盖不足时 LLM **必须**用「（待确认）」措辞占位而非编造
# （app/teacher_prep/blueprint/prompt.py:119；空桶占位 app/teacher_prep/blueprint/projector.py:170）。
# 但它不是可教学的面版内容：内核对 deck 做版面/叙事 QA 时，占位符会直接导致收尾页
# 要点不足（ppt-studio-v2 qa_deck.py:133-140 `summary-too-thin`，要求末页 map-2col/supporting
# items ≥3）或内部标签泄漏。因此投影侧统一剔除占位符、只用**真实有据**的事实组页，
# 并把未解决数量如实披露（不编造、不静默）。
_PLACEHOLDER_RE = re.compile(r"[（(]\s*待确认")
# 「PXX/P.XX」类假页码：教材铁律零 page_refs，LLM 编不出的页码必须占位而非编造，
# 而占位符不是可教学面版内容（同「（待确认）」处理）。
_FAKE_PAGE_RE = re.compile(r"\b[Pp]\s*\.?\s*X{1,3}\b|\b第\s*X\s*页\b")


def is_placeholder_text(text: Any) -> bool:
    """是否为占位文本（「（待确认…）」或假页码 PXX/P.X/第X页）。"""
    if not isinstance(text, str):
        return False
    return _PLACEHOLDER_RE.search(text) is not None or _FAKE_PAGE_RE.search(text) is not None


def _bucket_texts(bucket: Any) -> list[str]:
    """取一个桶里的文本（兼容 `{text: …}` 与裸字符串），保留原顺序与原文。"""
    out: list[str] = []
    for it in bucket or []:
        raw = it.get("text") if isinstance(it, dict) else it
        text = str(raw or "").strip()
        if text:
            out.append(text)
    return out


def _real_texts(*buckets: Any) -> list[str]:
    """跨多个桶取**真实可教学**的文本：丢占位符、丢教研批注、剥内部标记/黑话、去重、保序。

    R8-T：deck 正文不得出现「引导学生…」这类教师动作描述（只作用于 deck；教案教师活动列
    不走本函数，不受影响）。
    """
    out: list[str] = []
    seen: set[str] = set()
    for bucket in buckets:
        for text in _bucket_texts(bucket):
            if is_placeholder_text(text) or is_teacher_annotation(text):
                continue
            clean = _fix_terms(sanitize_deck_text(text))  # 编辑标记/工程黑话/模板前缀不得入版面
            if not clean:
                continue
            key = re.sub(r"\s+", "", clean)
            if key in seen:
                continue
            seen.add(key)
            out.append(clean)
    return out


def _real_pairs(*buckets: Any) -> list[tuple[str, str | None]]:
    """跨桶取真实文本 + 每条的 item 级 SourceLocator（对齐对；去重口径同 _real_texts）。"""
    out: list[tuple[str, str | None]] = []
    seen: set[str] = set()
    for bucket in buckets:
        for it in bucket or []:
            raw = it.get("text") if isinstance(it, dict) else it
            text = str(raw or "").strip()
            if not text or is_placeholder_text(text) or is_teacher_annotation(text):
                continue
            clean = sanitize_deck_text(text)
            if not clean:
                continue
            key = re.sub(r"\s+", "", clean)
            if key in seen:
                continue
            seen.add(key)
            ref = it.get("source_ref") if isinstance(it, dict) else None
            out.append((clean, ref if isinstance(ref, str) and ref else None))
    return out


def _texts_and_refs(*buckets: Any) -> tuple[list[str], list[str]]:
    """同 `_real_texts`，但同步返回每条文本的 item 级 SourceLocator（溯源进 deck 备注）。

    去重口径一致：同文本只保留首个 ref；无 ref 的文本不产生 ref。
    """
    pairs = _real_pairs(*buckets)
    texts = [t for t, _ in pairs]
    refs = [ref for _, ref in pairs if ref]
    return texts, refs


def count_placeholders(*buckets: Any) -> int:
    """统计占位符条数（用于如实披露「未解决项」，不用于编造内容）。"""
    return sum(
        1
        for bucket in buckets
        for text in _bucket_texts(bucket)
        if is_placeholder_text(text)
    )


def _flow_stage_items(stage: dict[str, Any]) -> list[str]:
    """单个 lesson_flow 环节的真实 items（丢占位符、去重）。"""
    return _real_texts(stage.get("items") or [])



@dataclass(frozen=True)
class ProjectionRunLineage:
    """prompt/model/run lineage（投影输入侧；LLM 未参与则全 None）。"""

    prompt_release_id: str | None = None
    ai_run_id: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class ProjectionInputs:
    """一次投影的全部确定性输入（projection_hash 的唯一真源）。"""

    blueprint_id: str
    blueprint_revision: int
    blueprint_body: dict[str, Any]
    context_snapshot_hash: str
    profile_ref: dict[str, Any]
    projector_version: str = PROJECTOR_VERSION


def compute_projection_hash(inputs: ProjectionInputs) -> str:
    """投影输入规范化 hash（确定性幂等锚点；同输入 → 同 hash）。"""
    payload = {
        "projector": inputs.projector_version,
        "blueprint_id": inputs.blueprint_id,
        "blueprint_revision": inputs.blueprint_revision,
        "blueprint_body": inputs.blueprint_body,
        "context_snapshot_hash": inputs.context_snapshot_hash,
        "profile_ref": inputs.profile_ref,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def body_hash_of(spec: Any, *, exclude: tuple[str, ...]) -> str:
    """Spec body 规范化 hash（排除自引用块，如 projection）。"""
    payload = spec.model_dump(mode="json", exclude=exclude)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def normalize_blueprint(body: dict[str, Any]) -> dict[str, Any]:
    """兼容 R16 直形 / R20 修订形已确认蓝图 body → 归一化为内容 dict。

    修订形：content 在 body["blueprint"]，新维度在 body[stratified_support/...]。
    """
    if isinstance(body.get("blueprint"), dict):
        return {
            "bp": body["blueprint"],
            "stratified_support": list(body.get("stratified_support") or []),
            "diagnostic_response": list(body.get("diagnostic_response") or []),
            "evidence": list(body.get("evidence") or []),
            "warnings": list(body.get("warnings") or []) + list(body.get("warnings_ack") or []),
        }
    return {
        "bp": body,
        "stratified_support": [],
        "diagnostic_response": [],
        "evidence": [],
        "warnings": list(body.get("warnings_ack") or []),
    }


def _item_list(items: list[Any], *, evidence_refs: list[str] | None = None) -> list[PlanItem]:
    refs = evidence_refs or []
    out: list[PlanItem] = []
    for it in items or []:
        raw = it.get("text") if isinstance(it, dict) else it
        raw_text = str(raw or "")
        # v3：占位符不是可交付内容（教案里出现「（待确认：…）」等于交半成品），一律剔除；
        # 未解决项由投影结果之外的上层闸门负责提示教师补齐（见 project_* 的 change_log / warnings）。
        if is_placeholder_text(raw_text):
            continue
        # W1-A：剥编辑内部标记（「（教师修订）」等）——只删标记，不动 source_ref。
        # R3e：`\\boxed` 包装剥除 + 方程形式术语守卫（`$…$` 保留给 doc_render 排版）。
        text = _fix_terms(sanitize_visible_text(raw_text))
        if not text:
            continue
        src_ref = it.get("source_ref") if isinstance(it, dict) else None
        out.append(PlanItem(text=text[:400], source_ref=src_ref, evidence_refs=list(refs)))
    return out


def _collect_locators(body: dict[str, Any]) -> list[str]:
    """聚合全部 SourceLocator（教案 source_refs 字段；零 page_refs）。"""
    refs: list[str] = []
    seen: set[str] = set()
    for bucket in (
        "objectives", "prior_knowledge", "concepts", "theorems", "examples",
        "inquiries", "design_intent", "key_points", "difficult_points",
    ):
        for item in body.get(bucket) or []:
            ref = item.get("source_ref") if isinstance(item, dict) else None
            if ref and ref not in seen:
                seen.add(ref)
                refs.append(ref)
    for stage in body.get("lesson_flow") or []:
        for item in stage.get("items") or []:
            ref = item.get("source_ref") if isinstance(item, dict) else None
            if ref and ref not in seen:
                seen.add(ref)
                refs.append(ref)
    return refs


# ── §F 六章扩展：板书 / 过程表列 / 作业 / 反思 的确定性 + 推导投影 ─────────────
# 反幻觉铁律（§F.4）：
# - board_design.text_points / formulas：**确定性投影**（board_notes / 教材定理公式），
#   绝不由 LLM 自由创作；无依据即空 + warning。
# - board_design.math_object：与 PPT 同源（复用 _ellipse_math_object，参数服务端派生）。
# - homework / reflection：LLM 推导桶（内容来自已确认蓝图 body）；无依据留空 + warning。


def _dedup_bucket_items(*buckets: Any) -> list[Any]:
    """把多个条目桶拼成去重列表（按归一化文本去重、保序、丢空/占位符/编辑标记）。"""
    out: list[Any] = []
    seen: set[str] = set()
    for bucket in buckets:
        for it in bucket or []:
            text = it.get("text") if isinstance(it, dict) else it
            text = str(text or "").strip()
            if not text or is_placeholder_text(text):
                continue
            clean = sanitize_visible_text(text)
            if not clean:
                continue
            key = re.sub(r"\s+", "", clean)
            if key in seen:
                continue
            seen.add(key)
            out.append({**it, "text": clean} if isinstance(it, dict) else clean)
    return out


def _kinded_items(items: list[Any], kind: str) -> list[PlanItem]:
    """重难点条目 + 小胶囊类型（§F.1）；占位符/空项由 _item_list 统一剔除。"""
    out = _item_list(items)
    for item in out:
        item.kind = kind  # type: ignore[assignment]
    return out


def _merge_kind_items(items: list[PlanItem], kind: str) -> list[PlanItem]:
    """重难点确定性归并（R8 语义修正）：同一类（重点/难点）**至多一条**。

    实测缺陷：`key_points` 有 2 条时教案渲染成「重点 / 重点 / 难点」三条（语义错误）。
    确定性归并口径：同类多条以「；」连接为一条（内容不丢、保序、按文本去重），
    source_ref 取首条有值的条目；仍为空则整类不出现（不编造）。
    """
    texts: list[str] = []
    first_ref: str | None = None
    for it in items:
        text = it.text.strip()
        if not text or text in texts:
            continue
        if first_ref is None and it.source_ref:
            first_ref = it.source_ref
        texts.append(text)
    if not texts:
        return []
    return [PlanItem(text=_clip("；".join(texts), 400), kind=kind, source_ref=first_ref)]  # type: ignore[arg-type]


def _stage_columns(
    stage: dict[str, Any], *, plan_intents: list[Any] | None = None
) -> LpStageColumns:
    """教学过程表三列内容（§F.2）：教师活动 / 学生活动 / 设计意图（行 = 一个 stage）。

    W2.3 来源补强（`design_intent` 三级确定性兜底，杜绝整列 `—`，L4）：
    ① 环节 hints（最优）→ ② 环节 summary（W1.3 陈述句摘要）→ ③ 蓝图级
    `design_intent` 桶（§B.1 教材事实桶，跨环节复用真实依据、零编造）。
    """
    design_intent = _item_list(stage.get("hints") or [])
    if not design_intent:
        summary = str(stage.get("summary") or "").strip()
        if summary and not is_placeholder_text(summary):
            design_intent = [PlanItem(text=summary)]
    if not design_intent and plan_intents:
        design_intent = _item_list(plan_intents)
    return LpStageColumns(
        teacher_activity=_item_list(
            _dedup_bucket_items(stage.get("items"), stage.get("teacher_questions"))
        ),
        student_activity=_item_list(stage.get("student_activities") or []),
        design_intent=design_intent,
    )


def _board_notes_of(bp: dict[str, Any]) -> list[Any]:
    """板书文字要点来源：lesson_flow 各 stage 的 board_notes（§F.4 确定性投影）。"""
    notes: list[Any] = []
    for stage in bp.get("lesson_flow") or []:
        if isinstance(stage, dict):
            notes.extend(stage.get("board_notes") or [])
    return notes


# 椭圆 canonical 公式（服务端派生，非 LLM 创作）：标准方程 + 半焦距关系。
_ELLIPSE_CANONICAL_FORMULAS = (
    r"\frac{x^2}{a^2} + \frac{y^2}{b^2} = 1 \quad (a > b > 0)",
    r"c = \sqrt{a^2 - b^2}",
)


def _lesson_math_object(bp: dict[str, Any]) -> dict[str, Any] | None:
    """教案板书中列 math_object：与 PPT **同源**（确定性，参数由服务端派生）。

    判定口径与 project_deck_spec 一致：内容同时命中「椭圆」与（焦点/标准方程/对称/
    范围/特殊点…）语义 → 椭圆图；直线课题（点斜式/斜截式/直线方程）→ line/line_family
    图（R3e；8201 只渲染 ellipse，deck 侧同源走 MATH_RENDER 资产）。仅扫描 deck 会承载
    图形的同族内容（flow items / concepts / inquiries / theorems），排除 overview。
    """
    parts: list[str] = []
    for stage in bp.get("lesson_flow") or []:
        if isinstance(stage, dict):
            parts.append(str(stage.get("title") or ""))
            parts.extend(_bucket_texts(stage.get("items") or []))
    parts.extend(_bucket_texts(bp.get("concepts")))
    parts.extend(_bucket_texts(bp.get("inquiries")))
    parts.extend(_bucket_texts(bp.get("theorems")))
    blob = "\n".join(parts)
    if _ELLIPSE_RE.search(blob) and _ELLIPSE_TOPIC_RE.search(blob):
        return _ellipse_math_object(_ellipse_params_for_blueprint(bp))
    line_math = _line_math_for_blueprint(bp)
    if line_math is not None:
        return _line_math_object(*line_math)
    return None


# 直线课题 canonical 公式（服务端派生，非 LLM 创作；与直线图形同源）。
_LINE_CANONICAL_FORMULAS = (
    r"y - y_1 = k(x - x_1)",
    r"y = kx + b",
    r"k = \frac{y_2 - y_1}{x_2 - x_1}",
)
# 板书里的作业行（「课后作业：…」）→ 归位作业布置章（产物审查 H：作业行错置进板书区）
_HOMEWORK_NOTE_RE = re.compile(r"^\s*(课后作业|作业|课后练习)\s*[：:]\s*")


def _homework_notes_of(bp: dict[str, Any]) -> list[str]:
    """board_notes 里的作业行 → 作业条目（剥「课后作业：」前缀）。确定性。"""
    out: list[str] = []
    for raw in _board_notes_of(bp):
        text = str(raw.get("text") if isinstance(raw, dict) else raw or "").strip()
        m = _HOMEWORK_NOTE_RE.match(text)
        if m:
            tail = text[m.end():].strip()
            if tail:
                out.append(tail)
    return out


def _board_design(bp: dict[str, Any], math_object: dict[str, Any] | None) -> LpBoardDesign:
    """板书设计（§F.3）：文字要点（左）/ 数学图形（中）/ 公式区（右）。

    - text_points：确定性投影自 lesson_flow[].board_notes（**非公式**行；作业行归位
      作业章、教材章节号剥除）；无 board_notes 时退化为教材事实 `concepts`（W2.3 补强：
      仍空再退化重点/目标真实事实——全部是蓝图/教材既有事实的确定性投影，非 LLM 创作）。
      仍空 → 空列 + warning。
    - formulas：board_notes 的「标签：$…$」显示式（R3e：不再留在文字栏）+ 教材事实
      `theorems`/`concepts` 中的公式型条目；数学对象课追加服务端 canonical 公式
      （椭圆/直线，与 PPT 图形同源）。
    - R3e 板书栏语义（产物审查 H 回炉）：**逐行**分流——含等式的显示式只进公式栏
      （label + canonical latex，按 latex 去重），条件式/整句只进文字栏（干净 Unicode
      零源码）；文字栏一律剥教材章节号 + 模板前缀（「本节结论：」），纯目标句不进板书。
    """
    notes_raw = [
        str(it.get("text") if isinstance(it, dict) else it or "").strip()
        for it in _board_notes_of(bp)
    ]
    text_points: list[PlanItem] = []
    note_formulas: list[PlanItem] = []
    note_latex_seen: set[str] = set()

    def _note_line_to_board(line: str) -> None:
        label, latex = split_label_math(line)
        if latex and "=" in latex:
            if latex in note_latex_seen:
                return
            note_latex_seen.add(latex)
            note_formulas.append(PlanItem(text=f"{label}：${latex}$" if label else f"${latex}$"))
            return
        clean = _fix_terms(sanitize_deck_text(line))
        if not clean:
            return
        clean = strip_template_prefix(clean).strip()
        if not clean or _OBJECTIVE_OPEN_RE.match(clean):
            return
        text_points.append(PlanItem(text=clean))

    for raw in notes_raw:
        for line in raw.splitlines():
            line = line.strip()
            if not line or _HOMEWORK_NOTE_RE.match(line):
                continue
            _note_line_to_board(line)
    if not text_points:
        text_points = [
            it
            for it in _item_list(bp.get("concepts") or [])
            if not _formula_latex(it.text)  # 整条公式型概念已在公式栏，不双栏重复
        ]
    if not text_points:
        text_points = _item_list(bp.get("key_points") or []) or _item_list(bp.get("objectives") or [])
    formula_items = [*note_formulas, *_item_list(bp.get("theorems") or [])]
    formula_items += [
        PlanItem(text=it.text)
        for it in _item_list(bp.get("concepts") or [])
        if _formula_latex(it.text)
    ]
    if math_object is not None:
        kind = math_object.get("kind")
        canonical = (
            _ELLIPSE_CANONICAL_FORMULAS
            if kind == "ellipse"
            else _LINE_CANONICAL_FORMULAS[:2] if kind == "line_family" else _LINE_CANONICAL_FORMULAS
        )
        formula_items += [PlanItem(text=f"${eq}$") for eq in canonical]
    # v8：公式区只放公式。蓝图 `theorems` 里混着「本节结论：掌握……的方法」这类整句
    # （实测出现在黄金图右侧公式栏，观感像教研批注）→ 归位到文字要点，内容不丢。
    # 注意：只对**非公式条目**做归位，公式条目必须留在公式栏（否则会同时在两栏出现）。
    prose_points = [
        p
        for p in (
            _rehome_board_prose(it) for it in formula_items if not _is_formula_text(it.text)
        )
        if p is not None
    ]
    formula_items = [it for it in formula_items if _is_formula_text(it.text)]
    if prose_points:
        known = {it.text for it in text_points}
        text_points = [*text_points, *[it for it in prose_points if it.text not in known]]
    # v8/R3e：教材章节号剥除放在归位**之后**（实测「3.1.2 …\n椭圆标准方程：$…$」归位句
    # 逃过剥离 → 产物审查 H 板书栏带目录噪音）；文字栏零 `$…$` 源码（干净 Unicode）。
    cleaned_points: list[PlanItem] = []
    for it in text_points:
        text = _strip_section_no(it).text
        text = _fix_terms(latex_to_unicode(text)).strip()
        if text:
            cleaned_points.append(it.model_copy(update={"text": text[:400]}))
    text_points = cleaned_points
    # 按文本/latex 去重（theorems 与 canonical/概念公式可能重复），保序
    seen: set[str] = set()
    seen_latex: set[str] = set()
    formulas: list[PlanItem] = []
    for item in formula_items:
        if item.text in seen:
            continue
        latex_part = _formula_latex(item.text) or item.text
        if latex_part in seen_latex:
            continue
        seen.add(item.text)
        seen_latex.add(latex_part)
        formulas.append(item)
    return LpBoardDesign(text_points=text_points, math_object=math_object, formulas=formulas)


# 收尾页「小结：」模板回声（G6）：summary 环节 board_note 以小结类标签开头 → 环节标签，
# 不是可教学正文（真实结论 = 公式/重点/难点，已由其它候选承载）。
_SUMMARY_ECHO_NOTE_RE = re.compile(r"^\s*(?:本节小结|本课小结|课时小结|课堂小结|小结)\s*[：:]\s*")

# ── LessonPlanSpec 投影 ──────────────────────────────────────────────────────


def project_lesson_plan_spec(
    *,
    spec_id: str,
    blueprint_id: str,
    blueprint_revision: int,
    blueprint_body: dict[str, Any],
    context: TextbookTeachingContext,
    profile_ref: dict[str, Any],
    lineage: ProjectionRunLineage,
    projected_at: datetime | None = None,
    run_hash: str,
    spec_revision: int = 1,
    class_name: str | None = None,
) -> LessonPlanSpec:
    """已确认蓝图 + context → LessonPlanSpec（确定性；Pydantic 构造即 schema 校验）。

    `spec_revision`：同一蓝图下的教案版本号（「活产物」语义，服务层取既有 max+1）。
    缺省 1 = 该蓝图的首版；改动课堂设计后再次投影必须是**新版本**（否则撞
    UNIQUE(blueprint_id, revision) → 500）。
    `class_name`：真实班级名（成绩册 classes.name；R14 裁定 3.2「按真实绑定」）；
    未绑定班级时缺省 → meta 班级 pill 退化为真实年级（绝不硬编码占位班级）。
    """
    global _TAUGHT_FORMS
    now = projected_at or datetime.now(UTC)
    content = normalize_blueprint(blueprint_body)
    bp = content["bp"]
    _TAUGHT_FORMS = _taught_forms_of(bp)

    # 标题清理贯通（§B.3）：即使旧/手工 body 的 title 带占位后缀，产物 metadata.title
    # 也零占位（确定性、幂等；清理后非空）。
    title = sanitize_title(bp.get("title") or "教案", fallback="教案")
    sel = context.selection
    src = context.source
    # 教材展示名（R14 裁定 3.3 去重）：出版社 + 册次（edition==volume 时不重复拼接）
    volume = str(src.volume or src.edition or "").strip()
    textbook = " ".join(p for p in (str(src.publisher or "").strip(), volume) if p)
    metadata = LessonPlanMetadata(
        title=title,
        grade=src.grade or None,
        duration_minutes=sum(s.get("duration_minutes") or 0 for s in bp.get("lesson_flow") or []) or None,
        language="zh-CN",
        lesson_scope=sel.lesson_scope or None,
        subject="高中数学",  # 产品范围常量（R14 裁定 3.1）：本模块只服务高中数学备课
        textbook=textbook or None,
        class_name=(class_name or None) or (src.grade or None),  # 真实班级 > 年级 > 省略（R14 3.2）
    )

    # 重难点确定性归并（v10）：同类至多一条（多条以「；」连接），杜绝「重点/重点/难点」。
    key_difficult = [
        *_merge_kind_items(_kinded_items(bp.get("key_points") or [], "key"), "key"),
        *_merge_kind_items(_kinded_items(bp.get("difficult_points") or [], "difficult"), "difficult"),
    ]

    lesson_flow = [
        LessonPlanStage(
            stage=s.get("stage", "new_content"),
            title=sanitize_title(s.get("title"), fallback="教学环节"),
            summary=s.get("summary"),  # W1.3/W2.3 additive 透传（过程表设计意图兜底源）
            duration_minutes=s.get("duration_minutes"),
            items=_item_list(s.get("items") or []),
            columns=_stage_columns(s, plan_intents=bp.get("design_intent") or []),
        )
        for s in bp.get("lesson_flow") or []
    ]

    # §F 六章扩展：④板书设计 / ⑤作业布置 / ⑥教学反思（additive；无依据留空 + warning）
    math_object = _lesson_math_object(bp)
    board_design = _board_design(bp, math_object)
    # 作业 = homework 桶 + board_notes 里的作业行（「课后作业：教材习题 1.2 第 1-5 题」
    # 此前错置进板书区——产物审查 H；归位后作业章拿到真实第 3 条）
    homework_texts: list[str] = []
    for text in (*_bucket_texts(bp.get("homework") or []), *_homework_notes_of(bp)):
        clean = _fix_terms(sanitize_visible_text(text))
        if clean and clean not in homework_texts:
            homework_texts.append(clean)
    homework = [PlanItem(text=t[:400]) for t in homework_texts]
    reflection = _item_list(bp.get("reflection") or [])

    stratified = [
        LpStratifiedSupport(
            group=group,
            label=f"{group} 支持",
            items=_item_list(content["stratified_support"], evidence_refs=["stratified_support"]),
        )
        for group in ("foundation", "standard", "advanced")
        if any(True for _ in content["stratified_support"])
    ]
    diagnostic = [
        LpDiagnosticResponse(
            finding_ref=it.get("finding_id") or "",
            response=it.get("detail") or "",
            source_ref=it.get("ref"),
        )
        for it in content["evidence"]
    ]
    evidence_items = [
        PlanItem(text=it.get("detail") or "", source_ref=it.get("ref"), evidence_refs=[it.get("finding_id")])
        for it in content["evidence"]
    ]

    locator = context.locator or ""
    source_refs = list(dict.fromkeys(_collect_locators(bp) + ([locator] if locator else [])))
    unresolved = count_placeholders(
        bp.get("objectives"), bp.get("prior_knowledge"), bp.get("concepts"), bp.get("theorems"),
        bp.get("examples"), bp.get("inquiries"), bp.get("key_points"), bp.get("difficult_points"),
    )
    warnings = list(dict.fromkeys(content["warnings"]))
    if unresolved:
        warnings.append(f"unresolved_blueprint_placeholders:{unresolved}")
    # §F 六章空态披露（诚实，不编造）：板书列 / 作业 / 反思无依据时只记 warning。
    if not board_design.text_points:
        warnings.append("board_design_text_points_empty")
    if not board_design.formulas:
        warnings.append("board_design_formulas_empty")
    if not homework:
        warnings.append("homework_no_basis")
    if not reflection:
        warnings.append("reflection_no_basis")
    total_minutes = sum(int(s.get("duration_minutes") or 0) for s in bp.get("lesson_flow") or [] if isinstance(s, dict))
    if total_minutes > 45:
        warnings.append(f"lesson_flow_over_time_budget:{total_minutes}>45")
    warnings = list(dict.fromkeys(warnings))
    lp_projection = LpSpecProjectionRef(
        projector=PROJECTOR_VERSION,
        approved_blueprint_id=blueprint_id,
        approved_blueprint_revision=blueprint_revision,
        context_snapshot_hash=context.snapshot_hash,
        prompt_release_id=lineage.prompt_release_id,
        ai_run_id=lineage.ai_run_id,
        model=lineage.model,
        run_hash=run_hash,
        body_hash="0" * 64,  # 构造占位（pattern 64hex）；构造后立即回填真实 body_hash
        projected_at=now,
    )
    spec = LessonPlanSpec(
        id=spec_id,
        revision=spec_revision,
        purpose="lesson_plan",
        metadata=metadata,
        blueprint_ref=LpBlueprintRef(blueprint_id=blueprint_id, revision=blueprint_revision),
        context_ref=LpContextRef(
            context_snapshot_hash=context.snapshot_hash,
            source_id=context.source.id,
            section_id=context.section.id,
            locator=locator,
        ),
        objectives=_item_list(bp.get("objectives") or []),
        key_difficult_points=[o for o in key_difficult if o.text.strip()],
        lesson_flow=lesson_flow,
        board_design=board_design,
        homework=homework,
        reflection=reflection,
        # 课堂活动 = inquiries 桶；与 deck 同口径净化（幽灵图引用剥除——G20 ② 描述
        # 两产物一致，「与对应图形」这类引用不存在图形的短语零进产物）。
        activities=[
            PlanItem(text=cleaned[:400], source_ref=it.source_ref, evidence_refs=it.evidence_refs)
            for it in _item_list(bp.get("inquiries") or [])
            if (cleaned := _fix_terms(sanitize_deck_text(it.text)))
        ],
        examples=_plan_examples(bp.get("examples") or []),
        stratified_support=stratified,
        diagnostic_response=diagnostic,
        # 评价 = 小结/练习环节的真实检测提问（v13：此前误取末环节 items——「说明作业要求，
        # 布置课后作业」错置进「评价」节，产物审查 H；无依据即空，不编造）。
        assessment=_item_list(_dedup_bucket_items(*[
            s.get("teacher_questions") or []
            for s in (bp.get("lesson_flow") or [])
            if isinstance(s, dict) and s.get("stage") in ("summary", "exercise", "example", "homework")
        ])),
        evidence=evidence_items,
        warnings=warnings,
        source_refs=source_refs,
        projection=lp_projection,
        change_log=[
            LpChangeLogEntry(
                revision=spec_revision,
                ts=now,
                kind="create" if spec_revision <= 1 else "regenerate",
                summary=f"从 approved blueprint revision {blueprint_revision} 投影（run_hash={run_hash[:12]}…）",
            )
        ],
    )
    spec.projection.body_hash = body_hash_of(spec, exclude=("projection",))
    return spec


# ── TeachingDeckSpec 投影 ────────────────────────────────────────────────────


def _textbook_sources(context: TextbookTeachingContext) -> list[TextbookSourceRef]:
    """教材溯源：direct-chunk 铁律——使用 context facts 的 canonical **chunk 级** locator 集合。

    section 级 locator 会被 8200 ingest 拒绝（其 LOCATOR_RE 要求 /chunk… 尾段）；
    新写入零 page_refs；无 chunk 级证据时显式为空（由 ingest 明确报 locator_missing，不编造）。
    """
    buckets = (
        context.objectives,
        context.prior_knowledge,
        context.concepts,
        context.theorems,
        context.examples,
        context.inquiries,
    )
    chunk_refs: list[str] = []
    seen: set[str] = set()
    for bucket in buckets:
        for fact in bucket or []:
            ref = getattr(fact, "chunk_ref", None) or ""
            if ref and "/chunk" in ref and ref not in seen:
                seen.add(ref)
                chunk_refs.append(ref)
    locator = context.locator if context.locator and "/chunk" in context.locator else None
    return [
        TextbookSourceRef(
            textbook_id=context.source.id,
            section_id=context.section.id,
            page_refs=[],  # 新写入零假页码
            chunk_refs=chunk_refs[:50],
            locator=locator,
        )
    ]


# ── SlideSpec 语义元素投影（FROZEN-CONTRACT §A）──────────────────────────────
# 确定性投影：把每页的**真实事实**映射为语义元素（定义带/公式/知识卡/图形/要点），
# 由渲染层从 role 派生版式。**不编造**：元素文本全部来自该页既有 items / 服务端
# canonical 公式（math_object 派生），长度按 §A.3 约束裁剪。
_FORMULA_ONLY_RE = re.compile(r"^\s*\$(.+?)\$\s*$", re.DOTALL)
_STEPS_TYPES = ("derivation", "worked-example")

# ── 板书三栏清洗（v8）：黑板上不放目录噪音，也不把整句结论当公式 ──────────────
# 教材章节号（3.1 / 3.1.2）后跟空白 → 是教材定位符，不是板书内容；单项条目号
# 「1. 范围：…」不匹配（要求 ≥1 个点号且有后续空白）。
_SECTION_NO_RE = re.compile(r"^\s*\d+(?:\.\d+){1,}\s+")
# 数学片段（行内/显示式 $...$）
_MATH_SPAN_RE = re.compile(r"\$[^$]*\$")
# 去掉数学片段后仍剩超过该长度的自然语言 → 该条是文字要点而非公式
_FORMULA_PROSE_MAX = 12
# 「本节结论：」是投影内部标签，不是黑板内容 → 落版前剥掉
_BOARD_PROSE_LABEL_RE = re.compile(r"^\s*本节结论[：:]\s*")
# 剥标签后仍是「掌握/理解/体会…」的目标句 → 属于教学目标章节，不进板书
_OBJECTIVE_OPEN_RE = re.compile(r"^(掌握|理解|体会|了解|学会|培养|提升|能|会)")


def _strip_section_no(item: PlanItem) -> PlanItem:
    """剥掉板书文字要点开头的教材章节号（无命中时原样返回）。"""
    return item.model_copy(update={"text": _SECTION_NO_RE.sub("", item.text)})


def _is_formula_text(text: str) -> bool:
    """板书公式区准入：必须含数学片段，且数学之外的文字极短（「标准方程：$…$」可入，

    「本节结论：掌握利用椭圆标准方程研究几何性质的方法」不可入）。"""
    if not _MATH_SPAN_RE.search(text):
        return False
    return len(_MATH_SPAN_RE.sub("", text).strip()) <= _FORMULA_PROSE_MAX


def _rehome_board_prose(item: PlanItem) -> PlanItem | None:
    """公式区里的整句 → 归位为文字要点；纯目标句返回 None（该条不进板书）。"""
    text = _BOARD_PROSE_LABEL_RE.sub("", item.text).strip()
    if not text or _OBJECTIVE_OPEN_RE.match(text):
        return None
    return item.model_copy(update={"text": text})


def _clip(text: str, limit: int) -> str:
    """按 §A.3 字段长度上限裁剪（超长截断加省略号，不改变语义来源）。"""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _label_example(text: str, origin: Any) -> str:
    """例题显示文本：**不再注入**「（AI 生成变式）」打标（R8-T P0 实测：该打标是工程黑话，
    不得进入教师可见正文）。AI 变式的反幻觉信息保留在数据层 `source_origin`（仍不冒充教材
    例题：`selected_examples` 只登记教材题）。统一走 deck 可见文本净化。
    """
    return _fix_terms(sanitize_deck_text(text))


def _plan_examples(bucket: list[Any]) -> list[PlanItem]:
    """教案例题项（六章之「例题」）：真实文本（剥内部标记/黑话）+ SourceLocator。

    R3e：每例题附**确定性解答步骤**（`worked_solution_steps`，与 deck 例题页同源；
    G-08/G-09 例题带解答、G20 ③ 两产物一致）。解析不到数值时不生成（诚实）。
    """
    out: list[PlanItem] = []
    for it in bucket or []:
        raw = it.get("text") if isinstance(it, dict) else it
        base = str(raw or "").strip()
        if not base or is_placeholder_text(base):
            continue
        clean = sanitize_visible_text(base)
        if not clean:
            continue
        origin = it.get("source_origin") if isinstance(it, dict) else None
        ref = it.get("source_ref") if isinstance(it, dict) else None
        out.append(PlanItem(text=_label_example(clean, origin)[:400], source_ref=ref))
        for step in worked_solution_steps(base):
            out.append(PlanItem(text=step[:400], source_ref=ref))
    return out


def _formula_latex(text: str) -> str | None:
    """整条文本即显示公式（`$...$`）时返回 latex 源码（不含 $ 定界符），否则 None。"""
    match = _FORMULA_ONLY_RE.match(text)
    return match.group(1).strip() if match else None


def _math_object_canonical_latex(math_object: dict[str, Any] | None) -> str | None:
    """与图形同源的 canonical 公式（服务端派生，非 LLM 创作）。"""
    if isinstance(math_object, dict) and math_object.get("kind") == "ellipse":
        return _ELLIPSE_CANONICAL_FORMULAS[0]
    return None


def _content_item_count(lines: list[str], formulas: list[tuple[str, str]]) -> int:
    """页内容项计数（可见行 + 显示式）——「每页 ≥2 条真实内容项」门（KERNEL §2.1）。

    R3e：按**页面内容**计数（不是元素数）——04/05 空白废页的根因就是元素多、
    真正落到版面上的内容只有 1 行。
    """
    return len([ln for ln in lines if ln.strip()]) + len(formulas)


def _slide_elements(
    *,
    slide_type: str,
    title: str,
    lines: list[str] | None = None,
    formulas: list[tuple[str, str]] | None = None,
    math_object: dict[str, Any] | None,
    note_text: str | None = None,
    items: list[str] | None = None,
) -> list[dict[str, Any]]:
    """页事实 → §A.2 语义元素（确定性；无坐标）。

    角色语义正确（KERNEL-RUBRIC §2.1 / LEAD-BAR §4.2）：
    - **`definition` 只用于真正的定义页**（`slide_type == "definition"`）；目标/要点用
      `bullets`（避免「学习目标」被渲染成「定义带」的语义错误，W1-A）；
    - 推导/例题页用 `steps`；参数化图形页用 `figure` + `knowledge_card` + `formula`；
    - **画布与导出同源**（R3e）：元素文本 = 页面可见行（`content`）的语义拆分，
      显示式进 formula 元素（标签 + canonical latex）——杜绝「画布≠导出」。
    元素文本全部来自该页可见行 / 服务端 canonical 公式 / 真实环节字段，**零编造**。

    入参兼容：`lines`/`formulas` 为已拆分内容单元；旧调用（R14 内核单测）直接传
    `items`（原始条目），此处经 `_content_units` 确定性拆分（同一行为口径）。
    """
    if lines is None and formulas is None:
        lines, formulas = _content_units(items or [])
    lines = list(lines or [])
    formulas = list(formulas or [])
    elements: list[dict[str, Any]] = []
    canonical = _math_object_canonical_latex(math_object)
    is_steps = slide_type in _STEPS_TYPES
    is_definition_page = slide_type == "definition"

    # ① definition：**仅定义页**承载定义带（首条正文）
    consumed = 0
    if is_definition_page and lines:
        elements.append({"role": "definition", "text": _clip(lines[0], 400)})
        consumed = 1
    # ② figure：页承载参数化图形（figure_ref="math_object"）
    if math_object is not None:
        elements.append({"role": "figure", "figure_ref": "math_object"})
    # ③ formula：显示式（标签 + canonical latex；画布 KaTeX，导出走可见行）
    seen_latex: set[str] = set()
    for label, latex in formulas:
        if latex in seen_latex:
            continue
        seen_latex.add(latex)
        el: dict[str, Any] = {"role": "formula", "latex": _clip(latex, 2000)}
        if label:
            el["text"] = _clip(label, 200)
        elements.append(el)
    if canonical is not None and canonical not in seen_latex:
        elements.append({"role": "formula", "latex": _clip(canonical, 2000)})
    # ④ knowledge_card：图形页必产（canonical 与图形同源）；非步骤页其余正文入卡
    if canonical is not None and not is_steps:
        elements.append(
            {
                "role": "knowledge_card",
                "title": _clip(title, 60),
                "latex": canonical,
                "items": [_clip(x, 200) for x in lines[consumed:][:10]],
            }
        )
        consumed = len(lines)
    # ⑤ steps / bullets：其余可见行
    rest = lines[consumed:]
    if rest:
        role = "steps" if is_steps else "bullets"
        elements.append({"role": role, "items": [_clip(x, 200) for x in rest[:10]]})  # type: ignore[arg-type]
    # ⑥ note：真实环节摘要/教学提示（画布页脚；不足 2 项时也兜底进元素密度）
    if _content_item_count(lines, formulas) < 2 and note_text:
        note = _fix_terms(sanitize_deck_text(note_text))
        note = _clip(note, 400)
        existing = {e.get("text") for e in elements if e.get("text")}
        if note and note not in existing and not is_placeholder_text(note):
            elements.append({"role": "note", "text": note})
    return elements


def _raw_texts(*buckets: Any) -> list[str]:
    """真实条目**原文**（丢占位/教研批注、按净化文本去重；公式源码保留待 `_content_units` 抽取）。"""
    out: list[str] = []
    seen: set[str] = set()
    for bucket in buckets:
        for text in _bucket_texts(bucket):
            if is_placeholder_text(text) or is_teacher_annotation(text):
                continue
            key = re.sub(r"\s+", "", sanitize_deck_text(text) or text or "")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(text)
    return out


def _refs_of(*buckets: Any) -> list[str]:
    """条目级 SourceLocator（deck 备注溯源；不进可见正文）。"""
    out: list[str] = []
    seen: set[str] = set()
    for bucket in buckets:
        for it in bucket or []:
            ref = it.get("source_ref") if isinstance(it, dict) else None
            if ref and ref not in seen:
                seen.add(ref)
                out.append(ref)
    return out


# 页型语义（标题 → SlideType；相邻版式多样性由 `_dedupe_layouts` 兜底）
_TITLE_TYPE_RE = (
    (re.compile(r"小结|总结|归纳"), "summary"),
    (re.compile(r"例题|例\s*\d|示范"), "worked-example"),
    (re.compile(r"练习|练一练|巩固|检测"), "practice"),
    (re.compile(r"推导|推演|论证|证明"), "derivation"),
    (re.compile(r"探究|思考|探索|观察|发现"), "inquiry"),
    (re.compile(r"引入|导入|情境|回顾"), "introduction"),
)


def _page_type(title: str, default: str) -> str:
    for pat, kind in _TITLE_TYPE_RE:
        if pat.search(title or ""):
            return kind
    return default


# 相邻页版式不重复（KERNEL §2.1）：冲突时按语义次优族轮换（确定性、无随机）
_LAYOUT_FALLBACKS: dict[str, tuple[str, ...]] = {
    "concept": ("derivation", "comparison", "inquiry"),
    "derivation": ("concept", "comparison", "inquiry"),
    "inquiry": ("concept", "comparison", "derivation"),
    "introduction": ("inquiry", "concept", "comparison"),
    "worked-example": ("practice", "derivation", "concept"),
    "practice": ("worked-example", "comparison", "concept"),
    "summary": ("comparison", "concept", "derivation"),
    "comparison": ("concept", "summary", "derivation"),
    "image-focus": ("concept", "derivation", "comparison"),
    "definition": ("concept", "derivation", "comparison"),
    "overview": ("concept", "definition", "derivation"),
    "title": ("concept", "introduction", "summary"),
}
_VALID_LAYOUTS = {
    "title", "introduction", "concept", "definition", "derivation", "worked-example",
    "comparison", "image-focus", "practice", "inquiry", "summary", "overview",
}


def _dedupe_layouts(slides: list[SlideSpec]) -> None:
    """相邻页 layout_hint 去重（冲突页换语义次优族；绝不留相邻重复版式）。"""
    for i in range(1, len(slides)):
        prev = slides[i - 1].layout_hint
        cur = slides[i].layout_hint
        if cur != prev:
            continue
        for alt in _LAYOUT_FALLBACKS.get(str(cur), ()):
            if alt in _VALID_LAYOUTS and alt != prev:
                slides[i].layout_hint = alt  # type: ignore[assignment]
                break


def _dedupe_titles(slides: list[SlideSpec]) -> None:
    """页标题零雷同（产物审查 G-07：「探究与思考/探索与思考」标题雷同）。"""
    seen: dict[str, int] = {}
    for slide in slides:
        title = slide.title
        n = seen.get(title, 0)
        seen[title] = n + 1
        if n:
            slide.title = f"{title}（{n + 1}）"


def _line_formula_keys(item: str) -> set[str]:
    """一行可见文本的归一化候选键：整行 + 「标签：」后的尾段（与公式元素比对用）。"""
    whole = _norm_line(item)
    keys = {whole}
    for sep in ("：", ":"):
        if sep in item:
            tail = _norm_line(item.rsplit(sep, 1)[-1])
            if tail:
                keys.add(tail)
    return keys


def _is_title_echo_item(item: str, title: str) -> bool:
    """「标题回声」孤立短句：纯中文短语（无数学字符）、≤14 字、与页标题几乎同名。"""
    n_item = _norm_line(item)
    n_title = _norm_line(title)
    if not n_item or len(n_item) > 14 or len(n_item) < 4 or _has_math_char(item):
        return False
    return n_item == n_title or n_item in n_title or n_title in n_item


def _dedupe_deck_slides(slides: list[SlideSpec]) -> None:
    """G6 deck 逐页确定性去重（只作用 deck spec；教案不受影响）：

    ① 可见行 vs 同页公式元素：行归一化后（整行或「标签：」尾段）与某公式元素的
       Unicode 渲染相同 → 删行留公式卡（同页公式只展示一次）；
    ② 归一化后完全重复的行 → 只保留首条；
    ③ 「标题回声」孤立短句（与页标题几乎同名）→ 删除。
    底线：页内容项（公式 + 正文行）不足 2 的页整页跳过去重（不制造薄页）。
    同步从 `content` 剔除被删的同名行（导出/画布/内容三处同源一致）。
    """
    for slide in slides:
        if slide.type == "title" or slide.layout_hint == "title":
            continue
        formula_norms = {
            _norm_line(latex_to_unicode(str(e.latex or "")))
            for e in slide.elements
            if e.role == "formula" and e.latex
        } - {""}
        body_items = [
            it
            for e in slide.elements
            if e.role in ("bullets", "steps") for it in (e.items or [])
        ]
        extra_texts = [
            str(e.text) for e in slide.elements if e.role in ("definition", "note") and e.text
        ]
        if len(formula_norms) + len(body_items) + len(extra_texts) < 2:
            continue
        dropped: set[str] = set()
        seen: set[str] = set()
        for e in slide.elements:
            if e.role not in ("bullets", "steps") or not e.items:
                continue
            kept: list[str] = []
            for item in e.items:
                keys = _line_formula_keys(item)
                norm = _norm_line(item)
                if keys & formula_norms:
                    dropped |= keys
                    continue
                if norm in seen:
                    dropped.add(norm)
                    continue
                if _is_title_echo_item(item, slide.title) and (len(seen) >= 1 or formula_norms):
                    dropped.add(norm)
                    continue
                seen.add(norm)
                kept.append(item)
            if kept != e.items:
                e.items = kept  # type: ignore[assignment]
        if not dropped:
            continue
        kept_lines = [
            ln for ln in slide.content.split("\n") if _norm_line(ln) not in dropped
        ]
        slide.content = "\n".join(kept_lines).strip()


def _lesson_display_name(full: str, context: TextbookTeachingContext) -> str:
    """封面短标题 = 真实课名（剥教材名/章节号/课时；产物审查 G-01「标题是章节号」）。"""
    name = str(full or "")
    src = context.source
    for token in (src.publisher, src.volume, src.edition, src.grade):
        if token:
            name = name.replace(str(token), "")
    name = re.sub(r"\d+(?:\.\d+)+", " ", name)
    name = re.sub(r"第[一二三四五六七八九十百\d]+课时", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -·、（）()")
    return (name or str(full or "课件"))[:24]


def project_deck_spec(
    *,
    spec_id: str,
    blueprint_id: str,
    blueprint_revision: int,
    blueprint_body: dict[str, Any],
    context: TextbookTeachingContext,
    profile_ref: dict[str, Any],
    lineage: ProjectionRunLineage,
    projected_at: datetime | None = None,
    run_hash: str,
) -> TeachingDeckSpec:
    """已确认蓝图 + context → TeachingDeckSpec（确定性；schema 校验 + 引用完整性内置）。

    v3/v13 组页原则（对齐全 8200 VPL/QA 门 + 产物审查 G 回炉判据）：
    - **只排真实有据的内容**：`（待确认…）` 占位符一律不进版面；教研批注/幽灵图引用/
      裸 LaTeX 源码零进可见文本（生成源头产出干净文本）；
    - **每页 ≥2 条真实内容项**：不足先从未用事实池补，仍不足 → **不产该页**（宁少页不空页）；
      例题页豁免（解答由增稠补全）；收尾页必须撑住（summary items ≥3）；
    - **公式 canonical**：「标签：$…$」→ 可见行（干净 Unicode）+ formula 元素（canonical latex）；
    - **不重复成页**：同一 stage 只产一页；页间逐字去重；相邻页版式/标题不重复；
    - **页数守内核预算 [8,12]**；真实内容不足 8 页时按桶补页，仍不足则以**少页 + 显式
      warning** 诚实交付（不注水）。
    """
    global _TAUGHT_FORMS
    now = projected_at or datetime.now(UTC)
    content = normalize_blueprint(blueprint_body)
    bp = content["bp"]
    _TAUGHT_FORMS = _taught_forms_of(bp)

    slides: list[SlideSpec] = []
    outline: list[OutlineEntry] = []
    unresolved = count_placeholders(
        bp.get("objectives"),
        bp.get("prior_knowledge"),
        bp.get("concepts"),
        bp.get("theorems"),
        bp.get("examples"),
        bp.get("inquiries"),
        bp.get("key_points"),
        bp.get("difficult_points"),
    )

    objectives_raw = _raw_texts(bp.get("objectives"))
    objectives_refs = _refs_of(bp.get("objectives"))
    deck_title = _fix_terms(sanitize_title(bp.get("title") or "课件", fallback="课件"))

    def _cover_subtitle() -> str:
        """封面副标题：教师可读教材标识 + 课时（与课名零重复；R8-T 零内部术语）。"""
        src = context.source
        parts = [str(src.publisher or "").strip(), str(src.volume or src.edition or "").strip()]
        base = " ".join(p for p in parts if p)
        scope = str(context.selection.lesson_scope or "").strip()
        return f"{base} · {scope}" if scope and scope not in base else base

    math_object_attached = False  # 只标注首个承载椭圆语义的页（确定性；避免同族页重复贴图）
    ellipse_params = _ellipse_params_for_blueprint(bp)
    # R3e：直线课题图形走 MATH_RENDER 资产（8201 只渲染 ellipse；同源参数服务端出图）；
    # 二次函数课题（抛物线）同走纯资产通道（MathObjectSpec.kind 契约不含 quadratic
    # → 二次函数**不挂 math_object**，只进 slide.assets）。
    line_math = _line_math_for_blueprint(bp)
    quad_math = _quadratic_math_for_blueprint(bp)
    power_math = _power_math_for_blueprint(bp)
    # v16：图形资产候选 → 按**内容相关页**附图（去掉「整册一张」单次限制；每页至多
    # 1 张、相邻连续带图 ≤2 页、deck 级按 id 去重）。
    figure_candidates: list[tuple[str, dict[str, float], Any]] = []
    for m in (line_math, quad_math, power_math):
        if m is not None:
            figure_candidates.append((m[0], m[1], math_figures.asset_for(m[0], m[1])[0]))
    figure_assets_used: set[str] = set()
    used_text_keys: set[str] = set()  # 已上版面的文本（归一化键）→ 页间去重（KERNEL §2.1）

    def _key(text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    def _note_of(stage: dict[str, Any]) -> str | None:
        """环节的真实页脚兜底文本：陈述句摘要 → 教学提示 → 教师提问（绝不用标题标签）。"""
        candidates: list[str] = []
        summary = sanitize_deck_text(str(stage.get("summary") or ""))
        if summary and not is_teacher_annotation(summary):
            candidates.append(summary)
        for group in ("hints", "teacher_questions"):
            for it in stage.get(group) or []:
                raw = it.get("text") if isinstance(it, dict) else it
                clean = sanitize_deck_text(str(raw or ""))
                if clean and not is_placeholder_text(str(raw or "")) and not is_teacher_annotation(clean):
                    candidates.append(clean)
        return candidates[0] if candidates else None

    # 未用事实池（补单薄页 / 收尾页；只用真实事实，不注水、不与其它页重复）。
    # examples 不入池：例题题面/解答归例题页（G-08「例题带解答」），禁止被补白偷走。
    pool_raws: list[str] = []
    _seen_pool: set[str] = set()
    for raw in (
        _raw_texts(bp.get("key_points"))
        + _raw_texts(bp.get("difficult_points"))
        + _raw_texts(bp.get("objectives"))
        + _raw_texts(bp.get("concepts"))
        + _raw_texts(bp.get("theorems"))
        + _raw_texts(bp.get("inquiries"))
    ):
        k = _key(sanitize_deck_text(raw) or raw)
        if k and k not in _seen_pool:
            _seen_pool.add(k)
            pool_raws.append(raw)

    def _unused_raw(limit: int) -> list[str]:
        out: list[str] = []
        for raw in pool_raws:
            if len(out) >= limit:
                break
            if _key(sanitize_deck_text(raw) or raw) in used_text_keys:
                continue
            out.append(raw)
        return out

    def _add(
        *,
        type_: str,
        goal: str,
        title: str,
        raw_items: list[str],
        source_refs: list[str] | None = None,
        note_text: str | None = None,
        duration_minutes: int | None = None,
        allow_thin: bool = False,
        attach_figure: bool = False,
    ) -> str | None:
        """组一页：内容不足先补事实池，仍不足 → 返回 None（不产空页）。"""
        nonlocal math_object_attached
        clean_title = _fix_terms(sanitize_deck_text(title)) or _fix_terms(sanitize_deck_text(goal)) or "教学环节"
        lines, formulas = _content_units(raw_items)
        if _content_item_count(lines, formulas) < 2 and not allow_thin:
            extra = _unused_raw(2)
            if extra:
                more_lines, more_formulas = _content_units([*raw_items, *extra])
                if _content_item_count(more_lines, more_formulas) > _content_item_count(lines, formulas):
                    lines, formulas = more_lines, more_formulas
        if _content_item_count(lines, formulas) < 2 and not allow_thin:
            return None

        blob = f"{clean_title}\n" + "\n".join(lines)
        attach_math = (
            not math_object_attached
            and type_ in _MATH_OBJECT_TYPES
            and type_ != "image-focus"
            and _ELLIPSE_RE.search(blob) is not None
            and _ELLIPSE_TOPIC_RE.search(blob) is not None
        )
        if attach_math:
            math_object_attached = True
        mo = _ellipse_math_object(ellipse_params) if attach_math else None

        asset_ids: list[str] = []
        effective_type = type_
        if attach_figure:
            # v16 按内容相关页附图：页 blob 命中该图形族语义才附（候选序直线 → 二次函数
            # 确定性择一）；相邻连续带图页 ≤2（8200 视觉族「同族连续 ≤2」硬门 → 绝不让
            # 规划无解）；每页至多 1 张、同一资产 id 幂等去重。
            for cand_kind, _cand_params, cand_asset in figure_candidates:
                if _FIGURE_PAGE_RE.get(cand_kind) is None:
                    continue
                if _FIGURE_PAGE_RE[cand_kind].search(blob) is None:
                    continue
                run = 0
                for prev in reversed(slides):
                    if not prev.assets:
                        break
                    run += 1
                if run >= 2:
                    break
                figure_assets_used.add(cand_asset.id)
                asset_ids = [cand_asset.id]
                # 图形页走「图象观察」族（observe-and-infer 唯一候选 → 图形必进版面）
                effective_type = "image-focus"
                break

        # 时间标注（G20：deck 流程页与教案过程表时间一致可见）
        content_lines = list(lines)
        if duration_minutes:
            content_lines.append(f"本环节用时 {duration_minutes} 分钟")

        elements = _slide_elements(
            slide_type=effective_type,
            title=clean_title,
            lines=lines,
            formulas=formulas,
            math_object=mo,
            note_text=note_text,  # W1-A：页脚兜底用**真实内容**（环节摘要/提示/提问），非标题标签
        )
        sid = f"slide-{len(slides):02d}"
        slides.append(
            SlideSpec(
                id=sid,
                type=effective_type,  # type: ignore[arg-type]
                teaching_goal=goal or clean_title or "教学环节",
                title=clean_title,
                content="\n".join(content_lines),
                elements=elements,
                source_refs=list(source_refs or []),
                assets=asset_ids,
                visual_intent="",
                layout_hint=effective_type if effective_type in _VALID_LAYOUTS else type_,  # type: ignore[arg-type]
                math_object=mo,
            )
        )
        for line in content_lines:
            used_text_keys.add(_key(line))
        outline.append(OutlineEntry(id=f"stage-{len(slides) - 1:02d}", goal=goal or clean_title, slide_ids=[sid]))
        return sid

    # ① 封面（HOOK；正文=教师可读教材标识 + 课时，hero 家族副题槽；课名 ≠ 章节号）
    slides.append(
        SlideSpec(
            id="slide-00",
            type="title",
            teaching_goal=deck_title,
            title=_lesson_display_name(deck_title, context),
            content=_cover_subtitle(),
            layout_hint="title",
        )
    )
    outline.append(OutlineEntry(id="stage-00", goal="封面", slide_ids=["slide-00"]))

    # ② 学习目标（G20 同源：与教案「教学目标」同一 blueprint objectives 投影）
    if objectives_raw:
        _add(
            type_="concept",
            goal="明确学习目标",
            title="学习目标",
            raw_items=objectives_raw,
            source_refs=objectives_refs,
            allow_thin=True,
        )

    # ③ lesson_flow 环节：只保留有真实内容的环节；summary/homework 环节并入收尾池
    closing_raw: list[str] = []
    formula_units: list[tuple[str, str]] = []  # 全课显示式（收尾页公式总结用）
    flow_semantic_added = False
    inquiry_merged = False
    _DEFERRED_TYPES = frozenset({"worked-example", "practice"})
    deferred_stages: list[dict[str, Any]] = []

    def _stage_raws(stage: dict[str, Any]) -> list[str]:
        return (
            _raw_texts(stage.get("items"))
            + _raw_texts(stage.get("board_notes"))
            + _raw_texts(stage.get("teacher_questions"))
        )

    for stage in bp.get("lesson_flow") or []:
        if not isinstance(stage, dict):
            continue
        stage_kind = stage.get("stage", "new_content")
        title = _fix_terms(sanitize_title(stage.get("title"), fallback="教学环节"))
        stage_note = _note_of(stage)
        if stage_kind in ("summary", "homework"):
            # 小结/作业环节不单独成页：真实结论进收尾页，作业进收尾页「作业：」行。
            # 环节 items 也是真实内容（W1-A：不得静默丢失——此前只收 board_notes，
            # 「归纳集合与元素的关系」这类小结 items 整体蒸发）；教研批注句由 _raw_texts 过滤。
            # G6：「小结：X」模板回声行（剥前缀后是环节标签式短语的 board_note）不进
            # 收尾页——其信息已由本课公式卡 + 重点/难点条承载，原样上版即「游离条目」。
            for raw in _raw_texts(stage.get("items")) + _raw_texts(stage.get("board_notes")):
                m = re.match(r"^\s*(课后)?作业\s*[：:]\s*", raw)
                if m:
                    tail = raw[m.end() :].strip()
                    if tail:
                        closing_raw.append(f"作业：{tail}")
                elif stage_kind == "summary" and _SUMMARY_ECHO_NOTE_RE.match(raw):
                    continue
                elif stage_kind == "homework":
                    closing_raw.append(f"作业：{raw}")
                else:
                    closing_raw.append(raw)
            continue
        raws = _stage_raws(stage)
        # 「探究/思考」环节吸收 inquiries 桶（教材探究问题就是该环节内容，杜绝近重复页）；
        # G6：与环节已有提问「同题两写」的 inquiry 条目跳过（同一数学核被问两遍）。
        if re.search(r"探究|思考", title):
            inq_all = _raw_texts(bp.get("inquiries"))
            if inq_all:
                inq = [q for q in inq_all if not _question_echo(q, raws)]
                raws = [*raws, *inq]
                inquiry_merged = True
        if not raws:
            if stage_note:
                raws = [stage_note]
            else:
                continue
        type_ = _page_type(title, _STAGE_TO_SLIDE_TYPE.get(stage_kind, "concept"))
        refs = _refs_of(stage.get("items"), stage.get("board_notes"), stage.get("teacher_questions"))
        attach_figure = type_ in _MATH_OBJECT_TYPES and bool(figure_candidates)
        entry = {"type_": type_, "title": title, "raws": raws, "stage": stage, "note": stage_note,
                 "refs": refs, "attach_figure": attach_figure}
        if type_ in _DEFERRED_TYPES:
            deferred_stages.append(entry)
            continue
        if type_ in ("concept", "definition", "derivation", "inquiry"):
            flow_semantic_added = True
        sid = _add(
            type_=type_, goal=title, title=title, raw_items=raws, source_refs=refs, note_text=stage_note,
            duration_minutes=stage.get("duration_minutes"), attach_figure=entry["attach_figure"],
        )
        if sid is None:
            closing_raw.extend(raws)  # 内容不足不产空页：内容不丢，进收尾页
        else:
            _, fmls = _content_units(raws)
            formula_units.extend(fmls)

    # ④ 概念锚定：flow 未产语义页时，用 concepts 桶补（真实、带 chunk 定位）
    concept_raws = _raw_texts(bp.get("concepts"))
    if concept_raws and not flow_semantic_added:
        sid = _add(
            type_="concept", goal="概念与定义", title="核心概念", raw_items=concept_raws,
            source_refs=_refs_of(bp.get("concepts")),
        )
        if sid:
            flow_semantic_added = True
            _, fmls = _content_units(concept_raws)
            formula_units.extend(fmls)

    # ④b 定理/推导：theorems 真实项（G6：「本节结论：X」型标题回声句不充当推导正文）。
    # 回声清空且本课是点斜式课 → 按教材标准流程确定性组装真实推导步骤
    # （canonical 公式 + 教材通用事实，零编造、零占位符）；source_refs 保留定理桶溯源。
    theorem_raws = _raw_texts(bp.get("theorems"))
    derivation_raws: list[str] = []
    if theorem_raws:
        echo_keys = _echo_fact_keys(bp)
        derivation_raws = [t for t in theorem_raws if not _is_theorem_echo(t, echo_keys)]
        if not derivation_raws and line_math is not None and _is_point_slope_lesson(bp):
            derivation_raws = list(_LINE_DERIVATION_STEPS)
    if derivation_raws:
        sid = _add(
            type_="derivation", goal="推理与推导", title="推导与论证", raw_items=derivation_raws,
            source_refs=_refs_of(bp.get("theorems")),
        )
        if sid:
            _, fmls = _content_units(derivation_raws)
            formula_units.extend(fmls)
        else:
            closing_raw.extend(derivation_raws)

    # ⑤ 问题探究：inquiries 未被探究环节吸收时单独成页（标题与探究环节零雷同）
    if not inquiry_merged:
        inquiry_raws = _raw_texts(bp.get("inquiries"))
        if inquiry_raws:
            sid = _add(
                type_="inquiry", goal="问题探究", title="问题探究", raw_items=inquiry_raws,
                source_refs=_refs_of(bp.get("inquiries")),
            )
            if sid:
                _, fmls = _content_units(inquiry_raws)
                formula_units.extend(fmls)
            else:
                closing_raw.extend(inquiry_raws)

    # ⑤b 延后的 example/exercise 环节：在探究页之后发（VPL 多样性门）
    for d in deferred_stages:
        sid = _add(
            type_=d["type_"], goal=d["title"], title=d["title"], raw_items=d["raws"],
            source_refs=d["refs"], note_text=d.get("note"), duration_minutes=d["stage"].get("duration_minutes"),
            allow_thin=True, attach_figure=d["attach_figure"],
        )
        if sid is None:
            closing_raw.extend(d["raws"])
        else:
            _, fmls = _content_units(d["raws"])
            formula_units.extend(fmls)

    # ⑥ 例题与练习：examples 真实项（题干逐字一致；解答有 solution 字段则直用，
    #    否则直线例题由 `worked_solution_steps` 确定性代数补全——G-08/G-09 例题带解答）
    example_entries: list[dict[str, Any]] = []
    for it in bp.get("examples") or []:
        raw = it.get("text") if isinstance(it, dict) else it
        base = str(raw or "").strip()
        if not base or is_placeholder_text(base) or is_teacher_annotation(base):
            continue
        solution_raw = str((it or {}).get("solution") or "").strip() if isinstance(it, dict) else ""
        if solution_raw:
            solution = [s.strip() for s in solution_raw.split("\n") if s.strip()]
        else:
            solution = worked_solution_steps(base)
        ref = it.get("source_ref") if isinstance(it, dict) else None
        example_entries.append({"text": base, "solution": solution, "ref": ref})
    for idx, entry in enumerate(example_entries):
        raws = [entry["text"], *entry["solution"]]
        label = re.match(r"(例\s*\d+)", entry["text"])
        title = "例题精讲" if idx == 0 else ("练一练" + (f"（{label.group(1).replace(' ', '')}）" if label else ""))
        sid = _add(
            type_="worked-example" if idx == 0 else "practice",
            goal="例题示范" if idx == 0 else "巩固应用",
            title=title, raw_items=raws,
            source_refs=[entry["ref"]] if entry["ref"] else [],
            allow_thin=True,
        )
        if sid is None:
            closing_raw.extend(raws)

    # ⑦ 页数下限：真实内容不足 8 页时，按**尚未上版面**的桶事实继续补页（去重、不注水）。
    if len(slides) < 8:
        for bucket_key, type_, goal, title in (
            ("key_points", "concept", "本节重点", "重点梳理"),
            ("difficult_points", "comparison", "难点辨析", "难点辨析"),
            ("prior_knowledge", "introduction", "先备知识", "先备知识"),
        ):
            if len(slides) >= 8:
                break
            fresh = [r for r in _raw_texts(bp.get(bucket_key)) if _key(sanitize_deck_text(r) or r) not in used_text_keys]
            if fresh:
                _add(type_=type_, goal=goal, title=title, raw_items=fresh, source_refs=_refs_of(bp.get(bucket_key)))

    # ⑦b 单薄页充实：任一内容页 <2 条真实内容项 → 从**尚未使用**的真实事实池补足（进 `_add`）
    for slide in slides:
        if slide.type == "title" or slide.layout_hint == "title":
            continue
        lines = [ln for ln in slide.content.split("\n") if ln.strip()]
        if len(lines) >= 2:
            continue
        extra = _unused_raw(2)
        if not extra:
            continue
        extra_lines, _ = _content_units(extra)
        if not extra_lines:
            continue
        bullet = slide.elements[-1] if slide.elements and slide.elements[-1].role in ("bullets", "steps") else None
        if bullet is not None:
            bullet.items = [*bullet.items, *[_clip(x, 200) for x in extra_lines]][:10]
        else:
            slide.elements = [*slide.elements, SlideElement(role="bullets", items=[_clip(x, 200) for x in extra_lines])]
        slide.content = "\n".join([*lines, *extra_lines])
        for t in extra_lines:
            used_text_keys.add(_key(t))

    # ⑧ 收尾页（必须最后，且 items ≥3）：真实数学小结 = 本课核心公式 + 重点/难点 + 作业。
    #    反「活动描述 + 游离条目名」（产物审查 G-10）：不复读流程标题，不放教师动作句。
    closing_candidates: list[str] = [
        *(f"${latex}$" for _, latex in dict.fromkeys(formula_units)),
        *_raw_texts(bp.get("key_points")),
        *_raw_texts(bp.get("difficult_points")),
        *(f"作业：{text}" for text in _raw_texts(bp.get("homework"))),
        *closing_raw,
    ]
    summary_raws: list[str] = []
    summary_seen: set[str] = set()
    for raw in closing_candidates:
        k = _key(sanitize_deck_text(raw) or raw)
        if not k or k in used_text_keys or k in summary_seen:
            continue
        summary_seen.add(k)
        summary_raws.append(raw)
    # 收尾页必须撑住（KERNEL §2.1：summary items ≥3）：仅当**真实内容确实不足 3 条**的
    # 退化蓝图才复用其它页事实，且复用受「与任一页共享 ≤1 条」约束（W1-A：收尾页不得
    # 复读其它页版面——实测「集合的三条性质/元素与集合的从属关系」双条复读引爆门）。
    def _slide_keys(s: SlideSpec) -> set[str]:
        keys = {_key(ln) for ln in s.content.split("\n") if ln.strip()}
        for e in s.elements:
            for t in [*(e.items or []), *([e.text] if e.text else [])]:
                if t:
                    keys.add(_key(t))
        return keys

    if len(summary_raws) < 3:
        page_keys = [_slide_keys(s) for s in slides]
        summary_keys = {_key(sanitize_deck_text(r) or r) for r in summary_raws}
        for raw in closing_candidates:
            k = _key(sanitize_deck_text(raw) or raw)
            if not k or k in summary_seen:
                continue
            if any(len(pk & (summary_keys | {k})) > 1 for pk in page_keys):
                continue  # 复用后会与某页共享 ≥2 条 → 不复用
            summary_seen.add(k)
            summary_keys.add(k)
            summary_raws.append(raw)
            if len(summary_raws) >= 3:
                break
    if summary_raws:
        sid = _add(type_="summary", goal="归纳本课核心", title="课堂小结", raw_items=summary_raws, allow_thin=True)
        if sid is not None:
            _, fmls = _content_units(summary_raws)
            formula_units.extend(fmls)

    # 版式/标题零重复（相邻 layout_hint 不重复、标题零雷同）——组页完成后统一修正
    # G6：先做逐页内容去重（行 vs 公式 / 完全重复 / 标题回声），再修版式与标题。
    _dedupe_deck_slides(slides)
    _dedupe_layouts(slides)
    _dedupe_titles(slides)

    # 选中例题只登记**教材例题**（problem_id 语义为真实题库/教材题；AI 变式不冒充）。
    selected_examples = [
        SelectedExample(problem_id=str(it.get("text", ""))[:32], usage="guided")
        for it in (bp.get("examples") or [])
        if isinstance(it, dict)
        and it.get("source_origin") != "ai_generated"
        and not is_placeholder_text(it.get("text"))
    ]

    # R20 projection 血缘（可选字段；旧 DeckSpec 读完全兼容）
    from app.ppt_v2.domain.spec import SpecProjectionRef as DeckProjectionRef

    deck = TeachingDeckSpec(
        id=spec_id,
        revision=1,
        metadata=DeckMetadata(
            title=deck_title,
            grade=context.source.grade or None,
            duration_minutes=sum(s.get("duration_minutes") or 0 for s in bp.get("lesson_flow") or []) or None,
            language="zh-CN",
            course_context_ref=str(blueprint_id),
        ),
        textbook_sources=_textbook_sources(context),
        teaching_strategy=TeachingStrategy(
            intro="\n".join(_real_texts(bp.get("objectives"))),
            concept_progression=_real_texts(bp.get("concepts")),
            derivation_chain=_real_texts(bp.get("theorems")),
            checks_for_understanding=_real_texts(bp.get("inquiries")),
        ),
        outline=outline,
        selected_examples=selected_examples,
        slides=slides,
        # v16：实际被页面引用的图形资产（候选序确定性；按 id 去重，无则空表）
        assets=[a for _k, _p, a in figure_candidates if a.id in figure_assets_used],
        speaker_notes={s.id: s.speaker_note or "" for s in slides if s.speaker_note},
        change_log=[
            ChangeLogEntry(
                revision=1,
                ts=now,
                kind="create",
                summary=(
                    f"从 approved blueprint revision {blueprint_revision} 投影（run_hash={run_hash[:12]}…）"
                    + (f"；{unresolved} 项蓝图内容为「（待确认）」占位符，未排入版面（请教师补齐）" if unresolved else "")
                ),
            )
        ],
        projection=DeckProjectionRef(
            projector=PROJECTOR_VERSION,
            approved_blueprint_id=blueprint_id,
            approved_blueprint_revision=blueprint_revision,
            context_snapshot_hash=context.snapshot_hash,
            prompt_release_id=lineage.prompt_release_id,
            ai_run_id=lineage.ai_run_id,
            model=lineage.model,
            run_hash=run_hash,
            body_hash="0" * 64,  # 构造占位（pattern 64hex）；构造后立即回填真实 body_hash
            projected_at=now,
        ),
    )
    deck.projection.body_hash = body_hash_of(deck, exclude=("projection",))
    return deck


# ── 只读校验助手 ─────────────────────────────────────────────────────────────


def is_locator_ref(value: str | None) -> bool:
    """SourceLocator v1/legacy 形状校验（新写入零 page_refs/pNN 的守门）。"""
    if not value:
        return False
    return _LOCATOR_V1_RE.match(value) is not None


__all__ = [
    "PROJECTOR_VERSION",
    "ProjectionRunLineage",
    "ProjectionInputs",
    "compute_projection_hash",
    "body_hash_of",
    "normalize_blueprint",
    "project_lesson_plan_spec",
    "project_deck_spec",
    "is_locator_ref",
]
