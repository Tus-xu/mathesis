"""AI 数学课堂会话路由（F9+ 双师课堂：OpenMAIC 融合改造第一阶段）

两段式生成（对齐 OpenMAIC outlines→scene-content 结构，收敛为高中数学专属）：
1. 大纲（outline）：以课程预处理产物（章节/知识点/知识卡）为输入，生成 slide_count 页
   数学课堂大纲：导入→概念/公式→例题→变式→小结，逐页带旁白与建议用时。
2. 逐页内容（content）：每个 outline 生成 blocks（text/latex/example/note/geometry）+ 旁白。
   geometry 块为 MathFigure3D 受控场景（立体几何/圆锥曲线/函数图像等可交互配图），
   曲线表达式由后端安全采样为折线点集，前端只渲染不执行。

纪律（复用 course_router 治理经验）：
- kp_code 只能取自课程锚定（白名单），禁止编造；
- 公式一律 LaTeX（$...$），数学对象校验交给渲染层；
- 输出必须是 JSON，解析失败走确定性兜底（不空手返回）。

端点：
- POST /api/classroom/sessions — 创建会话（后台生成，幂等策略：每请求新会话）
- GET  /api/classroom/sessions — 我的会话列表
- GET  /api/classroom/sessions/{id} — 会话详情（outlines + slides + status）
"""

from __future__ import annotations

import ast as _ast
import asyncio
import math as _math
import re as _re
import uuid
from datetime import UTC, datetime
from string import Template as _Template
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domains.classroom.events import (
    clear_session_events,
    publish_session_event,
    subscribe_session_events,
    unsubscribe_session_events,
)
from app.domains.classroom.openmaic_adapter import (
    build_openmaic_document,
)
from app.domains.classroom.rag_orchestrator import (
    attach_classroom_grounding,
    attach_textbook_association_when_no_visual,
    build_classroom_retrieval_plan,
    build_right_trapezoid_pyramid_coordinate_witness,
    derive_explicit_length_facts,
    prefer_verified_coordinate_witness,
    retrieve_classroom_evidence,
)
from app.gateway.auth import get_current_user
from app.gateway.schemas import ApiResponse
from app.models.classroom import ClassroomSession
from app.models.course import COURSE_STATUS_READY, Course
from app.models.database import background_session_factory, get_db
from app.models.file import FileAsset
from app.models.knowledge_point import KnowledgePoint
from app.services.geogebra_figure import build_ggb_payload, generate_ggb

if TYPE_CHECKING:
    pass

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/classroom", tags=["classroom"])

# V7：通用数学建模引擎（IR中间层 + SymPy 精算 + 5步通用讲解模板）
# 设计哲学：不识别具体题目，只识别数学对象大类；不记忆标准答案，只做符号化建模
# 参考：stem-tutor-agent 4层验证链 / do-the-math IR中间层 / math_agent SymPy集成
# ==================== Prompt 安全格式化 ====================
# 由于 CONTENT Prompt 包含大量 geometry_claims JSON 示例（单花括号），
# 直接用 str.format 会把 {"coordinates":...} 误当成占位符解析 KeyError。
# 故所有 Prompt 改用 string.Template（仅 ${VAR} / $VAR 是占位符）。


def _safe_prompt_format(template_str: str, **kwargs) -> str:
    """基于 string.Template 的安全替换。
    - 缺失变量替换为空串（保证 prompt 不崩溃）；
    - 任意类型传入值统一 str() 后，把其中的单个 $ 转义为 $$
      （避免 OCR 文本/大纲/知识卡里的 LaTeX $ $ 被误当占位符）。"""

    class _SafeDict(dict):
        def __missing__(self, key):
            logger.warning("prompt_placeholder_missing", name=key)
            return ""

    escaped = {}
    for k, v in kwargs.items():
        # 统一转字符串，再对 $ 转义：Template 语义下 $$ = 一个字面 $
        s = "" if v is None else str(v)
        escaped[k] = s.replace("$", "$$")
    return _Template(template_str).substitute(_SafeDict(**escaped))


# ==================== 常量 ====================
MIN_SLIDES = 8
MAX_SLIDES = 15
_MODE_LABELS = {"sync": "同步课堂", "review": "考前复习", "topic": "专题精讲"}

# 高中数学课堂大纲 prompt（数学专属：章节目奏 + 公式 LaTeX + 考点纪律）
_MATH_OUTLINE_PROMPT = """\
你是高中数学主讲老师。基于给定课程的【章节/知识点/知识卡】生成一节课的 AI 课堂大纲，共 ${slide_count} 页幻灯片。

【课程章节】
${chapters}

【知识点白名单（kp_codes 只能从此表选择，禁止编造）】
${kp_table}

【课程知识卡（核心概念/公式）】
${knowledge_cards}

【课堂模式】${mode_label}

【大纲 JSON 契约】
{
  "title": "课堂标题（≤20字）",
  "slides": [
    {
      "order": 1,
      "type": "slide",
      "title": "页标题（≤12字）",
      "subtitle": "页副题（≤20字，可空）",
      "kp_code": "从白名单选择，無匹配填空串",
      "key_points": ["要点1", "要点2", "要点3"],
      "narration": "旁白（≤120字，口语化，面向学生讲解）",
      "minutes": 2
    }
  ]
}

【课堂结构纪律】
1. 第一页必须是"课堂导入"：回顾旧知、抛出本节课要解决的问题；
2. 中间页按学习节奏组织：概念→公式→例题→变式/易错，禁止堆砌；
3. 最后一页必须是"课堂小结"：核心公式 + 易错提醒 + 课后行动（练什么考点）；
4. 每页 2-3 个要点，每页预计用时 2-4 分钟；全课合计约 ${total_minutes} 分钟；
5. ${count_rule}
6. **【内容完整度硬门槛（全课）】**——共 ${slide_count} 页（最少值：图≥${min_figure_pages}页、例题≥${min_example_pages}页、公式≥${min_latex_pages}页）：
   - 至少 **${min_figure_pages} 页** 必须包含图形块（立体几何用 geometry、圆锥曲线/解几用 geometry+curves、函数题用 plot2d）；
   - 至少 **${min_example_pages} 页** 必须包含 example 块（完整例题/推导+分析+答案）；
   - 至少 **${min_latex_pages} 页** 必须包含 latex 块（核心公式/中间推导式/最终结论）；
   - 禁止"导入/小结以外的页只含 text 块"；第 2 页到第 ${slide_count_minus_1} 页（正文）必须同时命中 ≥3 种块类型：**text + 至少 2 种非text块**（非text包括 latex / example / geometry / plot2d / note）；常见正确组合：text+latex+geometry、text+latex+example、text+example+plot2d、text+example+note+latex；**正文页出现 "text+只有1种非text" 或 "全text" 被视为课堂不完整，直接打 needs_review**。
7. 大纲 JSON 的**每页需附加 `required_blocks` 和 `figure_kind` 字段**（与 title/narration 同级），以便内容生成阶段强制执行：
   - `required_blocks`: ["text","latex","geometry"]（非导入/小结页至少写 3 种：text 必选，再从 latex/example/geometry/plot2d/note 中选 ≥2 种与当前页主题匹配）；
   - `figure_kind`: 本页需要的图形类型（`none` / `geometry_3d_solid` / `geometry_conic_curve` / `plot2d_function`）：
     * 立体几何课：中间 6-8 页写 `geometry_3d_solid`，导入/小结写 `none`；
     * 圆锥曲线/解析几何课：中间 5-7 页写 `geometry_conic_curve`，导数/单调性页写 `plot2d_function`；
     * 函数/导数课：例题页和图像分析页写 `plot2d_function`；
     * 禁止中间 60% 以上的页写 `none`（会被视为"回避图形"直接失败）。
8. 只输出 JSON，不要其他文字。"""

# 逐页内容 prompt（复用大纲的故事线 + 知识卡上下文，生成可渲染 blocks）
_MATH_CONTENT_PROMPT = """\
你是高中数学主讲老师。为下面这一页幻灯片写出可渲染的课堂内容。

【本页大纲】
（大纲中已明确本页必须包含的 blocks 种类与图形种类，你必须严格遵守 required_blocks 字段，缺一类即视为课堂不完整，直接失败）
${outline_json}

【大纲字段释义（内容页必须严格遵守）】
- required_blocks：本页 blocks 必须覆盖的 kind 列表（按顺序出现即可）。例如 ["text","latex","geometry"] 意味着 blocks 中**必须同时包含** kind=text、kind=latex、kind=geometry 三种块，少任何一种都不行。
- figure_kind：本页图形类型要求。
  * `geometry` / `geometry_3d_solid` → blocks 必须有 1 个 kind=geometry（立体几何写 solids、解几写 curves+solids=[]）；
  * `geometry_conic_curve` → blocks 必须有 kind=geometry 且 figure.curves 含圆锥曲线（parametric/function）；
  * `plot2d_function` → blocks 必须有 1 个 kind=plot2d（函数/导数图）；
  * `none` → 图形可省略，但 latex/example 必须覆盖 required_blocks 剩余种类。
- **正文页（非导入/小结）禁止出现「blocks 种类数 < 3」或「只有 text 块」或「required_blocks 有缺」的情况**（会被内容完整度门扣为 needs_review）。

【LaTeX 纪律（违反视为本页失败，会触发整页重写）】
1. 所有数学表达式必须包裹在行内 $$...$$ 或展示 $$$$...$$$$ 定界符中（渲染后即学生看到的单/双美元符定界公式），一个公式都不能裸奔；
2. 只用标准 LaTeX 命令：分数用 \\frac{a}{b}、根式用 \\sqrt{a}、上下标用 ^ 与 _；
3. **严禁** frac(a){b}、v(a^2-b^2)、√(a)、x^2/a^2 这类无定界伪记号出现在任何正文里；
4. text/note/analysis 的中文叙述里禁止出现 ^、_、\\ 等裸 LaTeX 符号——数学要么完整包进定界符，要么改写成中文表述；
5. kind=latex 块的 latex 字段写纯公式本体，不要自带美元符定界符。

【课程知识卡参考（概念/公式/例题语境）】
${knowledge_cards}

【输出的 JSON（只输出 JSON）】
{
  "blocks": [
    {"kind": "theorem", "title": "定理/定义名（≤12字）", "body": "定理完整陈述（≤160字，条件与结论分行用；分隔，数学一律 $$...$$ 行内定界）"},
    {"kind": "text", "text": "一句话讲解（≤80字，口语化）"},
    {"kind": "latex", "latex": "LaTeX 公式本体（不含美元符，如 y = ax^2 + bx + c, \\\\; a \\\\neq 0）"},
    {"kind": "example", "question": "例题题干（≤80字，完整题目条件，数学 $$...$$）", "analysis": "分步解答（用 ①②③ 编号逐步写，每步注明定理/公式名，≤400字，数学 $$...$$ 行内定界）", "answer": "答案/结论（≤60字，数学 $$...$$）"},
    {"kind": "table", "caption": "表格标题（≤20字，可空）", "headers": ["x", "f'(x)", "f(x)"], "rows": [["(-∞,-1)", "+", "增"], ["-1", "0", "极大值"], ["(-1,1)", "−", "减"]]},
    {"kind": "note", "text": "易错点/记忆口诀/本页结论（≤60字）"},
    {"kind": "plot2d", "expr": "x^3 - 3*x", "x0": -3, "x1": 3, "marks": [{"x": -1, "label": "极大"}, {"x": 1, "label": "极小"}], "regions": [{"x0": -3, "x1": -1, "color": "#ef4444", "label": "增"}, {"x0": -1, "x1": 1, "color": "#3b82f6", "label": "减"}, {"x0": 1, "x1": 3, "color": "#ef4444", "label": "增"}], "caption": "f(x)=x³−3x 图像与单调区间"},
    {"kind": "geometry", "caption": "配图说明（可空，≤20字）", "figure": {
      "grid": true, "axes": true,
      "solids": [
        {"kind": "polyhedron", "vertices": [{"name": "A", "pos": [-2, -1, 0]}, {"name": "B", "pos": [2, -1, 0]}, {"name": "C", "pos": [2, 1, 0]}], "edges": [["A","B"], ["B","C"], ["C","A"]]},
        {"kind": "cylinder", "base": [0, 0, 0], "top": [0, 0, 3], "radius": 1.2}
      ],
      "curves": [{"kind": "parametric", "expr": ["2*cos(t)", "2*sin(t)", "0"], "t0": 0, "t1": 6.28}],
      "segments": [{"a": [-2, -1, 0], "b": [2, 1, 0], "dashed": true, "label": "对角线"}]
    }}
  ],
  "narration": "本页讲稿（150~260字，像老师在课堂说话：先衔接上一页，再点出本页要解决的问题，然后带学生走一遍关键步骤，最后一句收束。口语化、有节奏、禁止罗列要点式）",
  "math_claims": {
    "f_expr": "x^3 - 3*x",
    "fprime_expr": "3*x^2 - 3",
    "critical_points": [-1, 1],
    "increasing_intervals": [[-100, -1], [1, 100]],
    "decreasing_intervals": [[-1, 1]],
    "max_eval_point": -1,
    "max_eval_second_deriv": -6,
    "substitutions": {"a": -2}
  },
  "geometry_claims": {
    "coordinates": {"S": [0,0,2], "A": [0,0,0], "B": [2,0,0], "C": [2,2,0], "D": [0,2,0], "E": [0,0,1]},
    "plane_points": {"SBD": ["S", "B", "D"]},
    "distance": {"point": "E", "plane": "SBD", "value": 0.5774, "latex": "\\\\frac{1}{\\\\sqrt{3}}"},
    "conclusion": "点 E 到平面 SBD 的距离为 1/√3"
  }
}


geometry_claims 完整字段契约（V5 扩充：二面角 + 解析几何断言）：
{
  "coordinates": {"A":[x,y,z], ...},
  "plane_points": {"ACE":["A","C","E"], ...},
  "distance":     {"point":"E", "plane":"SBD", "value":0.5774, "latex":"\\frac{1}{\\sqrt{3}}"},
  "perpendicular":{"line":["A","E"], "plane":"ACE"},
  "dihedral": {
    "plane1": "DCA", "plane2": "ACE",
    "value": 0.5774,
    "latex": "\\dfrac{\\sqrt{3}}{3}"
  },
  "conic": {
    "a": 2.0, "b": 1.0, "c": 1.732,
    "theta": 1.047,
    "latex": "\\dfrac{x^2}{a^2} + \\dfrac{y^2}{b^2} = 1"
  },
  "tangent_point": {
    "k": -0.5,
    "x": 0.8944, "y": 0.8944,
    "latex": "P\\left(\\dfrac{-a^2k}{\\sqrt{a^2k^2+b^2}},\\;\\dfrac{b^2}{\\sqrt{a^2k^2+b^2}}\\right)"
  },
  "inner_point": {
    "x": 0.87, "y": 0.209,
    "latex": "I\\left(c\\cos\\theta,\\;\\dfrac{bc\\sin\\theta}{a+c}\\right)"
  },
  "distance_max": {
    "value": 1.0,
    "latex": "d_{\\max} = a - b",
    "method": "P(a\\cos\\phi,b\\sin\\phi) \\to 距离 = |a\\cos^2\\phi-b\\sin^2\\phi| \\leq a-b"
  },
  "conclusion": "总结语句：如二面角余弦为 √3/3、P为第一象限切点、内心在 ξ²/c²+η²/(bc/(a+c))²=1 椭圆上"
}
【纪律】
- **【教学编排质量（决定课堂观感，与数学正确性同级）】**：
  * 概念/定理页推荐组合：theorem（定理完整陈述）+ text（通俗解读）+ latex（符号化公式）+ 图形 + note（易错）；
  * 例题页推荐组合：example（题干完整 + analysis 用 ①②③ 分步、每步标注所用定理/公式名）+ latex（关键中间式）+ plot2d/geometry（配图）+ table（若涉及单调性/符号讨论，必须给 x / f'(x) / f(x) 三行表）+ note（结论一句话）；
  * example.analysis 是给学生在页面上逐步看的板书解答：**禁止只写一句思路**，要 3 步以上、每步一行、关键计算写完整（如 f'(x)=3x²−3=3(x−1)(x+1)）；
  * 正文中每页最后放一个 note 块写本页结论（"结论：..."），小结页的 note 写课后行动；
  * narration 必须是"连续讲课的话"，150~260 字，禁止写成要点罗列。
- blocks 至少 2 个：必须包含 text；公式页要含 latex；例题页要含 example；
- **【关键】结构化数学断言（数学验证硬门槛）**：凡是本页含 `latex`/`example`/`plot2d`/`geometry` 块（数学承载页），JSON 顶层必须同时携带对应断言，供后端做自洽数学验证（缺断言 → 页面标记"需要复核"，不得显示"已验证"）：
  * 涉及**函数/导数/单调性/极值**（含 plot2d 块）→ 必须给 `math_claims`：f_expr 用 x 为变量；fprime_expr 可与题干形式不同（验证器做符号等价比对）；critical_points 是 f'=0 的实数根；increasing_intervals/decreasing_intervals 的端点用 -100/100 表示无穷；max_eval_point 为极值点、max_eval_second_deriv 为该点 f'' 的值（判断极大/极小）；含参题（如"求 a"）把参数解出的值写入 substitutions，并把验算点放入 max_eval_point/max_eval_second_deriv；
  * 涉及**立体几何**（含 geometry 块）→ 必须给 `geometry_claims`：coordinates 给出关键点坐标（**键名必须与图 solids.vertices[*].name 字段完全一致，严禁用 v0/v1/v2/v3 代替 A/B/C/D/E/S/P/V 等真实顶点名**）；plane_points 用 3 个点名定义关键平面；distance 给出点到平面距离的 value（数值，如 0.5774，或 "1/sqrt(3)"）与 latex；需要证明线面垂直时给 perpendicular: {"line": [点A, 点B], "plane": "平面名"}；题干给出线线垂直时必须给 line_perpendicular: [{"line1": [点A,点B], "line2": [点C,点D]}]，后端会独立验算方向向量点积；**同时给 `metrics`**（题设度量，后端用坐标表核对，不符即失败）：lengths: {"AB": 2, "AD": 2}（关键边长）、angle_deg: {"BAD": 60}（含角底面的夹角）、apex_height: 2（棱锥顶点到底面高度）；
  * 涉及**解析几何**（含 curves 曲线块的 geometry / 含 example 推导圆锥曲线结论）→ 必须给 `geometry_claims` 的对应字段：**只要题目中出现椭圆/双曲线/抛物线方程，就必须写 conic{a,b,c,theta,latex}（a,b,c 从题干直接读出，符号题保留符号或取归一化演示值如 a=2,b=1）；涉及切线写 tangent_point；涉及轨迹/内心/重心写 inner_point；涉及距离/最值写 distance_max/distance_min**。禁止"推导了一整页椭圆切线结论但 geometry_claims 为空"——空断言 = 该页未通过验证 = needs_review。
  * 纯文字页（导入/小结，仅 text/note 块）可以不带断言；
  * **数值必须与题目条件吻合，禁止编造**——验证器会用 sympy/几何计算独立核对。
- **【关键】求真纪律（数学验证门是硬约束，断言与题目矛盾则整课失败）**：
  * `max_eval_second_deriv` 必须用 f_expr 代入 substitutions 后的函数独立重新计算（如 f''(x)=6x-2a，a=-2 时代入 x=-1 得 -6+4=-2），禁止凭感觉给数；
  * 立体几何中题目给出的线面垂直**未必成立**：必须用坐标点积先验证（线的方向向量与平面法向量平行才是垂直）。若命题在给定条件下不成立（如正方形底四棱锥中"BD⊥平面SAB"就不成立，因为 BD 与平面法向(0,1,0)不平行），禁止断言它——应指出原命题需修正，并改为证明成立的正确命题（如 BD⊥平面SAC），结论与坐标表必须一致；
  * `perpendicular` 是可选项：拿不准时不要写，避免与坐标表冲突导致整课失败；`distance` 必须与 coordinates/plane_points 推导一致。
  * `distance.value` 必须用你自己给出的 coordinates 按公式 |n·(P0) − c| / |n| 计算（n 为平面法向量、c 为平面常数），写**算出来的结果**（如保留 4 位小数），禁止估算或凭感觉给数——后端会用同一坐标表复核。
  * **建系规范**（立体几何一律优先用标准建系，坐标越简单越不易错）：把底面顶点放在坐标轴上，A(0,0,0) 为原点、底面两条邻边沿 x/y 轴、高沿 z 轴；如底面正方形边长为 s：A(0,0,0), B(s,0,0), C(s,s,0), D(0,s,0)，顶点 S(0,0,h)；中点坐标取半（如 SA 中点 E(0,0,h/2)）。**不要随意缩放**：SA/边长按题目给的值写（如 SA=AD=2 → s=2, h=2），distance.value 也随之是题目刻度下的真实距离。
  * **菱形/含角底面的建系**：AB=AD=2 且 ∠BAD=60° 的菱形 → A(0,0,0), B(2,0,0), D(2cos60°, 2sin60°, 0)=(1,√3,0)，C 由平行四边形法则 C=B+D=(3,√3,0)，顶点 V(0,0,h)；坐标里必须体现夹角，否则后续距离全错。矩形底面 → A(0,0,0), B(AB,0,0), D(0,AD,0), C(AB,AD,0)。
  * **二面角 · 通用计算纪律**（立体几何求二面角的页**必须**写 dihedral 断言，后端会用两个平面法向量独立计算核对）：
    ① 先在 plane_points 里用题目中的**真实点名字**定义两个半平面：如 "PQR":["P","Q","R"], "PQS":["P","Q","S"]（棱是两点PQ，两个面各自第三个点不同）；
    ② 在 dihedral.plane1 / dihedral.plane2 中写相同的平面名；
    ③ dihedral.value 必须写你按 n1·n2/(|n1||n2|) 用 coordinates 里的点坐标手算出来的余弦**绝对值**（通常取锐角）；禁止"凭感觉写0.5或√3/3"；
    ④ 若棱不是水平/竖直方向，**不要用目测**——一律用三点叉乘法向量。
  * **立体几何 · 通用三步证明链方法论**（任意立几题通用）：
    Step1 (建系)：**从题目条件严格推导坐标**。把"题目给出的线面垂直/面面垂直/平行四边形/边长/夹角"全部写进坐标表推导过程，禁止"默认正方形边长为2""随便令A在原点就完事"——坐标必须满足|AB|=题给值、∠BAD=题给夹角。推荐：
      - 有线面垂直/面面垂直 ⇒ 取公共棱为一轴，垂直向量沿另一轴；
      - 平行四边形：由 向量CD=BE，D=C+(E-B) 推导；
      - 棱锥顶点：由"侧棱相等/垂直底面"推出。
      建系完成后，**立即验算所有题给度量**：|AB|、∠ABC、线面点积=0、面内共线等，**在 narration 或 text 块中写出至少 1 条验算语句**（如 "由坐标得AB向量=(2,0,0)，|AB|=2，与题意一致"、"向量 AE·向量 CD = (0,0,h)·(-1,√3,0)=0，故 AE⊥CD"），再将正确坐标写入 geometry_claims.coordinates。若不匹配**必须修正坐标**，不允许带着错误坐标进入后续。
    Step2 (证明)：线线垂直/线面垂直/线线平行一律用**向量点积=0 / 叉乘=0 / 方向向量共线**严格验证，禁止"由图可知""显然"。写出完整的向量计算过程，**在 narration 或 example.analysis 中显式写出 1-2 条关键点积计算**（如 "向量 n1·n2 = (1,0,0)·(0,1,0) = 0 ⇒ 两平面法向垂直，故二面角=90°"）。
    Step3 (度量计算)：
      - 点到平面距离：严格按 d=|n·(P-P0)|/|n|，其中 n=(B-A)×(C-A) 为平面ABC法向量，P0是面上任一点；
      - 二面角：求两个半平面各自的法向量n1,n2（都由棱上两点+面内第三点叉乘得到），cosθ=|n1·n2|/(|n1||n2|) 取绝对值写 dihedral.value；
      - 体积/面积：直接用向量标积/叉积计算。
    **所有数值必须由 coordinates 独立算出，严禁凭常识估算 0.5/√2/√3/√3/3。**
  * **解析几何 · 通用证明链方法论**（圆锥曲线/切线/轨迹/最值 通用）：
    **通用工具包（必须在每一步显式使用，禁止跳步；在 narration / example.analysis 中同步写出定理名+推导过程）**：
    ① 参数化：椭圆上任意点 M 写成 (a·cosθ, b·sinθ)；双曲线上点写 (a·secθ, b·tanθ)；抛物线上点写 (t²/(2p), t)；消参求轨迹时写出坐标与参数关系再消去参数；
    ② 切线方程：用**隐函数求导**得到斜率 k = dy/dx = -(b²x)/(a²y)（椭圆 x²/a²+y²/b²=1）；或用"判别式 Δ=0"联立直线与椭圆得到切线条件 y=kx±√(a²k²+b²)；切点坐标通式 P(-a²k/D, b²/D), D=√(a²k²+b²)；
    ③ 距离/最值：点到直线距离 d=|Ax₀+By₀+C|/√(A²+B²) 代入**参数化的点**得到关于参数 θ 的表达式，用**辅助角公式** A·cosθ+B·sinθ = √(A²+B²)·sin(θ+φ) 求最大/最小值；或用**柯西不等式** (u₁²+u₂²)(v₁²+v₂²)≥(u₁v₁+u₂v₂)²；对 t 的有理函数 f(t)=N(t)/D(t) 求最值可直接求导 f'(t)=0 找极值点；
    ④ 焦点三角形/内心/重心/垂心：严格用标准公式——内心 I=(|F₁F₂|·M + |MF₂|·F₁ + |MF₁|·F₂)/perimeter（加权平均）；焦半径 |MF₁|=a±c·cosθ（椭圆）；
    ⑤ 轨迹问题：设动点 (ξ,η) 为条件中定义的点，写出 (ξ,η) 与原曲线上参数点 M 的关系，然后消参得到 ξ,η 满足的方程 f(ξ,η)=0；
    ⑥ **显式引用每一个定理名**："由隐函数求导得"、"由焦半径公式"、"由角平分线定理"、"由柯西不等式"、"由辅助角公式"、"由判别式 Δ=0"、"由加权平均公式"，严禁模糊使用"易得""可知"；
    ⑦ **每步验算在 narration / text / example 中同步写出**（例："代入参数化 P(a·cosφ,b·sinφ) 到距离公式，得 d=|b·cosφ·sinφ + a·sinφ·cosφ|/√(...) = |a·cos²φ - b·sin²φ| ≤ a-b，由辅助角公式当 φ=0 时取等号"），避免推导与断言脱节。
    **配套断言契约（通用）**：
    - 只要是圆锥曲线题，**conic{a,b,c,theta} 必须出现在该题≥1 页的 geometry_claims 中**（a,b,c 由题干直接读）；
    - 涉及切线：写 tangent_point{k,x,y,latex}（斜率→切点通式代入即得）；
    - 涉及焦点三角形内心/重心：写 inner_point{x,y,latex} 或相应几何点；
    - 涉及距离/式子最大值：写 distance_max{value, latex, method}（method里写用的不等式/导数方法名+关键推导一行）。
    **数值校验（通用）**：断言值（切点坐标/内心坐标/距离最大值）必须和 conic 参数一致——distance_max若你写 max=a-b 必须由你的推导过程（辅助角/柯西）严格得到，严禁"凭印象写 a-b 而不推导"。
  * **证明步骤必须"步步显化"**：每一步定理名/公式名要写出来（"焦半径公式"、"柯西不等式"、"角平分线定理"、"辅助角公式"、"平行四边形向量法则"），禁止跳步；禁止"显然""易得"。
- **【关键】图形类型选择纪律**（每页最多 1 个图形块，严格按主题选择；选错图形类型=图形纪律失败）：
  * **函数/导数/数列/概率统计/三角函数图像/不等式**（单变量 y=f(x) 图像题型）→ 用 `kind="plot2d"`（二维函数图，expr 为 f(x) 表达式）；marks 标临界点/极值点/零点，regions 用颜色区分单调增减区间；**绝对不要用 geometry 块画函数图**；
  * **立体几何（多面体/棱柱/棱锥/旋转体/截面/二面角/线面角/异面直线）** → 用 `kind="geometry"`（3D 受控场景）；**绝对不要用 plot2d 画立体几何**；
  * **解析几何/圆锥曲线/椭圆/双曲线/抛物线/轨迹/切线/弦长/焦点三角形** → **必须用 `kind="geometry"`（3D 受控场景的 2D 曲线模式）+ `figure.curves` 画圆锥曲线本体**，再配合 `marks`/`segments`/`planes` 画焦点、动直线、切线、动点；**圆锥曲线题禁止用 plot2d 画函数图像代替曲线本体**（plot2d 只能画 y=f(x) 函数图，无法正确表达椭圆/双曲线/抛物线的二次曲线）；
  * 平面向量/平面几何基本图 → 若涉及点线位置关系用 `kind="geometry"` 加 `segments`/`marks`；若涉及函数关系再用 plot2d；
  * 不需要图形辅助的页面（纯公式推导/文字说明）→ 不加任何图形块；
  * **圆锥曲线/立体几何课的图形分布硬门槛**：10 页幻灯片中至少 4 页含符合主题的 geometry 图（圆锥曲线图或立体几何图），导入页和小结页允许无图，但中间例题/分析/计算页**必须有图**。
- **【关键】figure.solids 必含几何体纪律**（仅当使用 geometry 块时）：
  * **立体几何题页面**：每页必须包含至少 1 个 solids 元素，**绝对不允许只给 segments/curves 而不给出任何实体**；主题含"多面体/棱柱/棱锥/正方体/长方体/三棱锥/四棱锥/几何体家族"等 → solids 至少 1 个 polyhedron/prism/pyramid（按题目实际给的体型写，用于展示当前题的几何体本体）。
  * **解析几何 / 圆锥曲线题页面**：figure.curves 中必须包含曲线本体的 parametric / function 表达式；**solids 可以为空数组 `[]`**（因为平面曲线题不需要 3D 实体），但必须显式给出 solids=[] 字段（前端解析契约），同时必须**至少包含一个 geometry.curves 条目**（即椭圆/双曲线/抛物线本身），并配合 segments 画焦点连线/切线/动直线/渐近线，marks 画焦点、切点、动点。
  * **解析几何 curves 规范**（圆锥曲线题强制遵守）：
    - 椭圆 $$\frac{x^2}{a^2}+\frac{y^2}{b^2}=1$$：写 `{"kind":"parametric", "expr":["a*cos(t)", "b*sin(t)", "0"], "t0":0, "t1":6.283}`，samples≤160；
    - 双曲线 $$\frac{x^2}{a^2}-\frac{y^2}{b^2}=1$$（右支）：`expr:["a*cosh(t)","b*sinh(t)","0"], t0:-1.5, t1:1.5`，左支再补一条；或用 parametric: `["a/cos(t)", "b*tan(t)", "0"], t0:0.2, t1:2.94`（避开±π/2）；
    - 抛物线 $$y^2=2px$$：`{"kind":"function", "axis":"y_axis", "expr":"y^2/(2*p)", "y0":-4, "y1":4}`（或按 x=f(y) 参数化）；
    - 圆 $$x^2+y^2=r^2$$：`["r*cos(t)","r*sin(t)","0"], t0:0, t1:6.283`；
    - **用符号（a,b,p,r,t）写表达式字符串，前端渲染时会把符号按参数代入**（若写具体数值则直接渲染）。
  * 主题含"旋转体/圆柱/圆锥/球/圆台"等立体几何 → solids 至少 1 个 cylinder/cone/sphere；
  * **solids 为空数组仅允许在「纯 2D 曲线解析几何题」页面使用**——其他任何场景（立体几何/平面向量几何）都必须有 solids 元素。
- figure 契约（所有坐标绝对值 ≤50，数字用普通数字）：
  * solids：box（center+size=[宽,深,高]）、polyhedron（vertices=[{name,pos}] + edges=棱的两点名）、prism（bottom/top=上下底面点列表）、pyramid（base=底点列表+apex）、cylinder/cone（base+top/apex+radius）、sphere（center+radius）；opacity 0.1~0.7；
  * curves：parametric 用 expr=["x(t)","y(t)","z(t)"] + t0/t1（samples≤160）；function 用 expr 如 "x^2-2*x" + x0/x1；数学表达式只允许变量 t 或 x，函数白名单 sin/cos/tan/asin/acos/atan/sqrt/abs/exp/log/ln/pi/e，运算符 + - * / ** （%）；
  * planes：截面/辅助平面，points=3 个空间点；
  * segments：辅助线段 {a,b,dashed,label}，dashed=true 表示不可见棱（虚线）；
  * camera 可选：{pos, target}；
- **【关键】几何体朝向与位置纪律**（避免观感差、互相遮挡、躺地上）：
  * **Y 轴是"向上"方向**：cylinder/cone 的轴向必须沿 Y 轴（即 top.y > base.y），圆柱/圆锥要"立"在地面上；**绝对不要把 height 写在 X 或 Z 方向**（会导致圆柱躺着，视觉错误）。
  * 所有坐标使用「分散铺开」策略：单个几何体的最长边长建议 2-4 个单位（推荐 3）；
  * 多个几何体同时出现时，X 方向间隔至少 3 个单位（如棱柱放在 x=-4，棱锥放在 x=4），避免互相遮挡；
  * 旋转体（cylinder/cone/sphere）半径推荐 1.0-1.5，cylinder/cone 的高推荐 2-3（base.y=0，top.y=2~3）；
  * 棱柱/棱锥：底面边长 2-3（推荐正多边形），顶面 y 坐标 = 底面 y + 3（上下底面高度差 3）；
  * 多面体（polyhedron）：8 顶点（如三棱柱/四棱柱）时所有 12 条棱必须显式列出；6 顶点（楔形/三棱锥）列出全部 9 条棱；不要漏写 edges，否则会渲染失败；
- 多面体（棱柱/棱锥/正方体等）优先用 polyhedron 完整给出顶点（A₁ 写作 "A1"）与所有棱；旋转体用 cylinder/cone/sphere；
- 名词/公式必须与高中数学一致，禁止编造定理；LaTeX 必须语法正确；
- 只输出 JSON。"""


# ==================== 图形场景（MathFigure3D DSL） ====================
# kind="geometry" 块的 figure 为受控场景 JSON：前端只消费已采样折线点集，
# 曲线表达式在此用受限求值器（ast 白名单）采样成坐标点，前端不执行任何表达式。

_MATH_FUNCS = {
    "sin": _math.sin,
    "cos": _math.cos,
    "tan": _math.tan,
    "asin": _math.asin,
    "acos": _math.acos,
    "atan": _math.atan,
    "sqrt": _math.sqrt,
    "abs": abs,
    "exp": _math.exp,
    "log": _math.log,
    "ln": _math.log,
    "pi": _math.pi,
    "e": _math.e,
}
_COLOR_RE = _re.compile(r"^#[0-9a-fA-F]{6}$")
_FIGURE_COLORS = [
    "#4f8ef7",
    "#ef6b5b",
    "#4cc49c",
    "#c084fc",
    "#f59e0b",
    "#38bdf8",
    "#fb7185",
    "#a3e635",
]
_FUNC_COLOR = "#ef4444"
_LINE_COLOR = "#64748b"
_PLANE_COLOR = "#f59e0b"
_MAX_COORD = 50.0


def _safe_math_eval(expr: object, variables: dict) -> float | None:
    """受限数学表达式求值：仅白名单函数 + 四则/幂/取模/一元运算，禁 eval/属性/下标。

    返回 float；任何非法输入/非有限值返回 None。
    """
    if not isinstance(expr, str) or len(expr) > 200 or not expr.strip():
        return None
    try:
        # ^ 语义对齐前端 GraphBlock：幂运算（Python 原生 ^ 是异或）
        tree = _ast.parse(expr.strip().replace("^", "**"), mode="eval")
    except SyntaxError:
        return None

    def node_val(n: _ast.AST):
        if isinstance(n, _ast.Expression):
            return node_val(n.body)
        if isinstance(n, _ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, _ast.Name):
            if n.id in variables:
                return variables[n.id]
            return _MATH_FUNCS.get(n.id)
        if isinstance(n, _ast.UnaryOp) and isinstance(n.op, (_ast.UAdd, _ast.USub)):
            v = node_val(n.operand)
            if not isinstance(v, (int, float)):
                return None
            return v if isinstance(n.op, _ast.UAdd) else -v
        if isinstance(n, _ast.BinOp) and isinstance(
            n.op, (_ast.Add, _ast.Sub, _ast.Mult, _ast.Div, _ast.Pow, _ast.FloorDiv, _ast.Mod)
        ):
            lv, rv = node_val(n.left), node_val(n.right)
            if not isinstance(lv, (int, float)) or not isinstance(rv, (int, float)):
                return None
            if isinstance(n.op, _ast.Add):
                return lv + rv
            if isinstance(n.op, _ast.Sub):
                return lv - rv
            if isinstance(n.op, _ast.Mult):
                return lv * rv
            if isinstance(n.op, _ast.Div):
                return lv / rv if rv != 0 else None
            if isinstance(n.op, _ast.Pow):
                return lv**rv
            if isinstance(n.op, _ast.FloorDiv):
                return lv // rv if rv != 0 else None
            return lv % rv if rv != 0 else None
        if isinstance(n, _ast.Call):
            fn = node_val(n.func)
            if not callable(fn) or len(n.args) != 1:
                return None
            arg = node_val(n.args[0])
            if not isinstance(arg, (int, float)):
                return None
            try:
                v = fn(arg)
            except (ValueError, OverflowError, ZeroDivisionError):
                return None
            return float(v) if isinstance(v, (int, float)) else None
        return None

    try:
        v = node_val(tree)
        return v if isinstance(v, (int, float)) and _math.isfinite(v) else None
    except (RecursionError, TypeError):
        return None


def _to_pt(v) -> list | None:
    """坐标三元组归一化：必须为 3 个 ≤50 的有限数字，否则 None。"""
    if not isinstance(v, (list, tuple)) or len(v) < 3:
        return None
    out = []
    for c in v[:3]:
        try:
            f = float(c)
        except (TypeError, ValueError):
            return None
        if not _math.isfinite(f) or abs(f) > _MAX_COORD:
            return None
        out.append(round(f, 4))
    return out


# 几何体最小可读尺寸（再小就看不见了，LLM 偶尔会输出半径 0.1 之类导致观感极差）
_MIN_SOLID_SIZE = 0.8
# 旋转体最小半径
_MIN_RADIUS = 0.55
# 单个几何体最长边期望长度（自适应取景目标）。
# 适度放大即可（4.0 太大——会把 LLM 输出的紧凑坐标全推到 ±5，相机距离
# 跟着上去，物体反而显得更小）。3.0 是经验值，物体占视野约 60% 时观感最好。
_TARGET_SPAN = 3.0
# 触发自适应缩放的最大跨度：只对 span < 2.0 的"挤在原点"坐标做放大。
# span 已经 ≥ 2.0 的不处理（避免把合理尺寸再次放大）。
_SCALE_BELOW = 2.0


def _orient_vertical(base, top, *, prefer_y=True):
    """把 cylinder/cone 的轴向"立起来"为 Y 轴（向上）。

    LLM 经常把圆柱/圆锥的 height 写在了 Z 方向上（base.y == top.y），
    导致几何体"躺"在地面上——观感极差。判定规则：
    - 若 Y 分量明显小于水平分量（X 或 Z），把轴旋转到 +Y 方向，
      高度取原轴长；水平位移保持。
    - 若 Y 分量已经是主导（向上/向下），不处理。
    返回新的 (base, top)，可能与原值相同。
    """
    if not base or not top:
        return base, top
    ax = [top[i] - base[i] for i in range(3)]
    h = (ax[0] ** 2 + ax[1] ** 2 + ax[2] ** 2) ** 0.5
    if h < 1e-6:
        return base, top
    abs_y, abs_xz = abs(ax[1]), (ax[0] ** 2 + ax[2] ** 2) ** 0.5
    # Y 方向已经是主导（>=45° 偏上/下）— 不动
    if abs_y >= abs_xz:
        return base, top
    # 轴太"平"了 → 旋转到 +Y（保持长度、保持 base 不动）
    sign = 1.0 if prefer_y else -1.0
    new_axis = [0.0, h * sign, 0.0]
    new_top = [round(base[i] + new_axis[i], 4) for i in range(3)]
    return base, new_top


def _scale_to_target(
    points3: list[list[float]], center: list[float] | None = None
) -> list[list[float]]:
    """把"挤在原点"的点集整体缩放到目标跨度，保持相对位置。

    经验：LLM 给 [-0.5, 0.5] 这种紧凑坐标时会被放大；给 [-4, 4] 这种合理
    坐标时**不再**处理（避免破坏相对位置）。
    """
    if not points3:
        return points3
    if center is None:
        center = [sum(p[i] for p in points3) / len(points3) for i in range(3)]
    span = 0.0
    for p in points3:
        for i in range(3):
            span = max(span, abs(p[i] - center[i]))
    # 已经很合理（≥ 2.0）或退化点：原样返回
    if span < 1e-6 or span >= _SCALE_BELOW:
        return points3
    k = _TARGET_SPAN / span
    return [[round(center[i] + (p[i] - center[i]) * k, 4) for i in range(3)] for p in points3]


def _auto_edges(verts: list[dict]) -> list[list[str]]:
    """LLM 漏写 edges 时的兜底：每个顶点连到距离最近的 K 个顶点，去重。

    K 选 3 时对立方体/正多面体效果最好（每个顶点连出 3 条棱）。
    8 顶点以下（4-6 顶点）也可得到合理的多面体骨架。
    """
    n = len(verts)
    if n < 4 or n > 30:
        return []
    K = 3 if n >= 6 else 2
    edges: set[tuple[str, str]] = set()
    for i in range(n):
        pi = verts[i]["pos"]
        dists: list[tuple[float, int]] = []
        for j in range(n):
            if j == i:
                continue
            pj = verts[j]["pos"]
            d = (pi[0] - pj[0]) ** 2 + (pi[1] - pj[1]) ** 2 + (pi[2] - pj[2]) ** 2
            dists.append((d, j))
        dists.sort()
        for _, j in dists[:K]:
            a, b = verts[i]["name"], verts[j]["name"]
            key = (a, b) if a < b else (b, a)
            edges.add(key)
    return [[a, b] for a, b in edges]


def _sample_curve(curve: dict) -> list | None:
    """把 parametric/function 曲线采样为折线点集（前端不再求值）。"""
    kind = curve.get("kind")
    if not isinstance(curve, dict) or kind not in ("parametric", "function"):
        return None
    try:
        samples = min(max(int(curve.get("samples") or 120), 24), 400)
    except (TypeError, ValueError):
        samples = 120
    if kind == "function":
        expr = curve.get("expr")
        try:
            x0, x1 = float(curve.get("x0") or -4), float(curve.get("x1") or 4)
        except (TypeError, ValueError):
            x0, x1 = -4.0, 4.0
        if x1 <= x0 or abs(x0) > _MAX_COORD or abs(x1) > _MAX_COORD:
            x0, x1 = -4.0, 4.0
        pts = []
        for i in range(samples + 1):
            x = x0 + (x1 - x0) * i / samples
            y = _safe_math_eval(expr, {"x": x})
            if y is None or abs(y) > _MAX_COORD:
                continue
            pts.append([round(x, 4), round(y, 4), 0.0])
        return pts or None
    exprs = curve.get("expr")
    if not (isinstance(exprs, (list, tuple)) and len(exprs) == 3):
        return None
    try:
        t0 = float(curve.get("t0") or 0)
        t1 = float(curve.get("t1") or (2 * _math.pi))
    except (TypeError, ValueError):
        t0, t1 = 0.0, 2 * _math.pi
    if t1 <= t0 or abs(t0) > 100 or abs(t1) > 100:
        t0, t1 = 0.0, 2 * _math.pi
    pts = []
    for i in range(samples + 1):
        t = t0 + (t1 - t0) * i / samples
        row = []
        for ex in exprs:
            v = _safe_math_eval(ex, {"t": t})
            if v is None or abs(v) > _MAX_COORD:
                row = []
                break
            row.append(round(v, 4))
        if row:
            pts.append(row)
    return pts or None


def _norm_color(value: object, default: str) -> str:
    return str(value or default) if _COLOR_RE.match(str(value or "")) else default


def _normalize_figure(raw) -> dict | None:
    """把 LLM 输出的 figure 收敛为前端 MathFigure3D 契约；非法项剔除/丢弃。"""
    if not isinstance(raw, dict):
        return None
    out: dict = {}
    caption = str(raw.get("caption") or "").strip()[:40]
    if caption:
        out["caption"] = caption
    out["grid"] = raw.get("grid", True) is not False
    out["axes"] = raw.get("axes", True) is not False

    camera = raw.get("camera")
    if isinstance(camera, dict):
        cam = {}
        pos, tgt = camera.get("pos"), camera.get("target")
        if isinstance(pos, (list, tuple)) and len(pos) >= 3:
            p = _to_pt(pos)
            if p:
                cam["pos"] = p
        if isinstance(tgt, (list, tuple)) and len(tgt) >= 3:
            p = _to_pt(tgt)
            if p:
                cam["target"] = p
        if cam:
            out["camera"] = cam

    # ---- solids ----
    solids: list[dict] = []
    for i, s in enumerate((raw.get("solids") or [])[:8]):
        if not isinstance(s, dict):
            continue
        kind = str(s.get("kind") or "box")
        if kind not in ("box", "polyhedron", "prism", "pyramid", "cylinder", "cone", "sphere"):
            continue
        color = _norm_color(s.get("color"), _FIGURE_COLORS[i % len(_FIGURE_COLORS)])
        try:
            opacity = min(max(float(s.get("opacity") or 0.35), 0.1), 0.8)
        except (TypeError, ValueError):
            opacity = 0.35
        item: dict = {"kind": kind, "color": color, "opacity": round(opacity, 2)}
        ok = True
        if kind == "box":
            center = _to_pt(s.get("center"))
            size = _to_pt(s.get("size") or s.get("dimensions"))
            if s.get("center") is not None and center is None:
                ok = False
            elif size and all(0 < abs(v) <= _MAX_COORD for v in size):
                # 最小可读尺寸保护：过小的几何体放大到 _MIN_SOLID_SIZE
                item["center"] = center or [0.0, 0.0, 0.0]
                item["size"] = [max(round(abs(v), 4) or 1.0, _MIN_SOLID_SIZE) for v in size]
            else:
                ok = False
        elif kind == "polyhedron":
            # 顶点：LLM 经常给空 name 或重复 name（A,A,A,A,B,B,B,B），
            # 统一重命名为 v0..vN，并把原 name 存为 _label（用于显示标签），
            # 保证 edges 一定能匹配上。
            raw_verts = []
            for pt in (s.get("vertices") or [])[:30]:
                if isinstance(pt, dict) and "pos" in pt:
                    p = _to_pt(pt.get("pos"))
                    if p:
                        raw_verts.append({"name": str(pt.get("name") or "")[:8], "pos": p})
            if raw_verts:
                scaled_pos = _scale_to_target([v["pos"] for v in raw_verts])
                verts = [
                    {"name": f"v{i}", "pos": scaled_pos[i], "_label": raw_verts[i]["name"]}
                    for i in range(len(raw_verts))
                ]
                item["vertices"] = verts
                names = {v["name"] for v in verts}
                # 原 name → 下标 映射（处理 LLM 用的 A/B/C 等字母）。
                # 同时支持整数下标引用（LLM 可能写 [0, 4] 这种）。
                orig_to_idx = {v["name"]: i for i, v in enumerate(raw_verts)}
                edges = []
                for e in (s.get("edges") or [])[:60]:
                    if not (isinstance(e, (list, tuple)) and len(e) == 2):
                        continue
                    a_raw, b_raw = str(e[0]), str(e[1])
                    ai = orig_to_idx.get(a_raw)
                    bi = orig_to_idx.get(b_raw)
                    # 整数下标兜底
                    if ai is None and a_raw.isdigit():
                        ai = int(a_raw)
                    if bi is None and b_raw.isdigit():
                        bi = int(b_raw)
                    if ai is None or bi is None:
                        continue
                    if not (0 <= ai < len(verts) and 0 <= bi < len(verts)):
                        continue
                    ea, eb = f"v{ai}", f"v{bi}"
                    if ea in names and eb in names and ea != eb:
                        edges.append([ea, eb])
                # LLM 经常漏写 edges 或写得不完整：用 KNN 自动连边兜底
                if not edges and len(verts) >= 4:
                    auto = _auto_edges(verts)
                    if auto:
                        edges = auto
                if edges:
                    item["edges"] = edges
                # 顶点 labels（用于显示 A/B/C 等角标）：保留 LLM 原始命名（去重），
                # 跳过空字符串和 v\d+ 自动命名（避免双重显示）
                seen = set()
                labels = []
                for i, rv in enumerate(raw_verts):
                    orig = rv["name"]
                    if not orig or (orig.startswith("v") and orig[1:].isdigit()):
                        continue
                    if orig in seen:
                        continue
                    seen.add(orig)
                    labels.append({"pos": verts[i]["pos"], "text": orig[:8]})
                if labels:
                    item["labels"] = labels
            else:
                ok = False
        elif kind == "prism":
            bottom, top = s.get("bottom"), s.get("top")
            if (
                isinstance(bottom, (list, tuple))
                and isinstance(top, (list, tuple))
                and len(bottom) >= 3
                and len(bottom) == len(top)
            ):
                b_pts = [_to_pt(x) for x in bottom]
                t_pts = [_to_pt(x) for x in top]
                if all(b_pts) and all(t_pts):
                    # 上下底面 + 顶面整体一起算 span，再统一缩放
                    # （避免上下底面分别缩放导致中心错位）
                    all_pts = b_pts + t_pts
                    all_center = [sum(p[i] for p in all_pts) / len(all_pts) for i in range(3)]
                    scaled_all = _scale_to_target(all_pts, center=all_center)
                    item["bottom"] = scaled_all[: len(b_pts)]
                    item["top"] = scaled_all[len(b_pts) :]
                else:
                    ok = False
            else:
                ok = False
        elif kind == "pyramid":
            base, apex = s.get("base"), s.get("apex")
            if (
                isinstance(base, (list, tuple))
                and len(base) >= 3
                and isinstance(apex, (list, tuple))
            ):
                b_pts = [_to_pt(x) for x in base[:8]]
                ap = _to_pt(apex)
                if all(b_pts) and ap:
                    # 底面/顶点一起缩放，保持锥形
                    all_pts = b_pts + [ap]
                    scaled = _scale_to_target(all_pts)
                    item["base"] = scaled[: len(b_pts)]
                    item["apex"] = scaled[-1]
                else:
                    ok = False
            else:
                ok = False
        elif kind in ("cylinder", "cone"):
            base = _to_pt(s.get("base"))
            top = _to_pt(s.get("top") or s.get("apex"))
            try:
                r = float(s.get("radius") or 1)
            except (TypeError, ValueError):
                r = 1.0
            if not (0 < r <= _MAX_COORD):
                r = 1.0
            if base:
                # 半径过小（< _MIN_RADIUS）放大到 _MIN_RADIUS；height < _MIN_SOLID_SIZE 也拉长
                r2 = max(round(r, 4), _MIN_RADIUS)
                # LLM 经常把圆柱/圆锥的 height 写在 Z 方向（base.y == top.y），
                # 看起来像"躺"在地上。先尝试把轴向旋转为 Y 轴向上（更符合"立着"的视觉直觉）。
                if top:
                    axis = [top[i] - base[i] for i in range(3)]
                    h = (axis[0] ** 2 + axis[1] ** 2 + axis[2] ** 2) ** 0.5
                    if h < _MIN_SOLID_SIZE * 1.2:
                        # 高度过短：方向向上拉伸（保持 base 不动）
                        dir_ = [0.0, 1.0, 0.0] if axis[1] >= 0 else [0.0, -1.0, 0.0]
                        top = [
                            round(base[i] + dir_[i] * max(_MIN_SOLID_SIZE * 2, r2 * 2), 4)
                            for i in range(3)
                        ]
                    else:
                        # 高度合理时：检测轴向是否"太平"，是则旋转为 Y
                        base, top = _orient_vertical(base, top)
                    item["base"] = base
                    item["top"] = top
                else:
                    # cylinder/cone 必须有 top/apex（避免退化）
                    item["base"] = base
                    item["top"] = [
                        round(base[0], 4),
                        round(base[1] + max(_MIN_SOLID_SIZE * 2, r2 * 2), 4),
                        round(base[2], 4),
                    ]
                item["radius"] = r2
            else:
                ok = False
        elif kind == "sphere":
            center = _to_pt(s.get("center"))
            try:
                r = float(s.get("radius") or 1)
            except (TypeError, ValueError):
                r = 1.0
            if not (0 < r <= _MAX_COORD):
                r = 1.0
            if s.get("center") is not None and center is None:
                ok = False
            else:
                # 半径过小放大
                item["center"] = center or [0.0, 0.0, 0.0]
                item["radius"] = max(round(r, 4), _MIN_RADIUS)
        labels = []
        for lb in (s.get("labels") or [])[:12]:
            if isinstance(lb, dict) and "text" in lb:
                p = _to_pt(lb.get("pos"))
                if p:
                    labels.append({"pos": p, "text": str(lb["text"])[:8]})
        if labels:
            item["labels"] = labels
        if ok:
            solids.append(item)
    if solids:
        out["solids"] = solids

    # ---- curves（统一为折线点集） ----
    curves: list[dict] = []
    for c in (raw.get("curves") or [])[:4]:
        if not isinstance(c, dict):
            continue
        color = _norm_color(c.get("color"), _FUNC_COLOR)
        pts = None
        if isinstance(c.get("points"), (list, tuple)) and c["points"]:
            pts = [p for p in (_to_pt(p) for p in c["points"][:200]) if p]
        elif c.get("kind") in ("parametric", "function"):
            pts = _sample_curve(c)
        if not pts or len(pts) < 2:
            continue
        curves.append(
            {
                "kind": "polyline",
                "points": pts,
                "closed": bool(c.get("closed")),
                "color": color,
            }
        )
    if curves:
        out["curves"] = curves

    # ---- planes（截面/辅助平面，3 点定义） ----
    planes: list[dict] = []
    for pl in (raw.get("planes") or [])[:3]:
        if not isinstance(pl, dict):
            continue
        src = pl.get("points")
        if not (isinstance(src, (list, tuple)) and len(src) == 3):
            continue
        three = [_to_pt(p) for p in src]
        if all(three):
            try:
                op = min(max(float(pl.get("opacity") or 0.22), 0.08), 0.5)
            except (TypeError, ValueError):
                op = 0.22
            planes.append(
                {
                    "points": three,
                    "color": _norm_color(pl.get("color"), _PLANE_COLOR),
                    "opacity": round(op, 2),
                }
            )
    if planes:
        out["planes"] = planes

    # ---- segments（辅助线段/边，dashed=不可见棱） ----
    segments: list[dict] = []
    raw_segments = (raw.get("segments") or [])[:10]
    for sg in raw_segments:
        if not isinstance(sg, dict):
            continue
        a, b = _to_pt(sg.get("a")), _to_pt(sg.get("b"))
        if not (a and b):
            continue
        segments.append(
            {
                "a": a,
                "b": b,
                "dashed": bool(sg.get("dashed")),
                "color": _norm_color(sg.get("color"), _LINE_COLOR),
                "label": str(sg.get("label") or "").strip()[:8] or None,
            }
        )
    if segments:
        out["segments"] = segments

    if not any(k in out for k in ("solids", "curves", "planes", "segments")):
        return None
    return out


# 兜底几何体生成工具（仅保留几何体构造函数；关键字驱动的自动注入已按 V4 移除）
def _box_solid(size=(2.0, 2.0, 2.0), center=(0.0, 0.0, 0.0), color="#4f8ef7", opacity=0.35):
    return {
        "kind": "box",
        "color": color,
        "opacity": opacity,
        "center": list(center),
        "size": list(size),
    }


def _cube_polyhedron(origin=(0.0, 0.0, 0.0), edge=2.0, color="#4f8ef7", opacity=0.35):
    """正方体（8 顶点 12 棱），origin 为最小角点。"""
    a, b, c = origin
    s = edge
    verts = [
        {"name": "A", "pos": [a, b, c]},
        {"name": "B", "pos": [a + s, b, c]},
        {"name": "C", "pos": [a + s, b + s, c]},
        {"name": "D", "pos": [a, b + s, c]},
        {"name": "A1", "pos": [a, b, c + s]},
        {"name": "B1", "pos": [a + s, b, c + s]},
        {"name": "C1", "pos": [a + s, b + s, c + s]},
        {"name": "D1", "pos": [a, b + s, c + s]},
    ]
    edges = [
        ["A", "B"],
        ["B", "C"],
        ["C", "D"],
        ["D", "A"],
        ["A1", "B1"],
        ["B1", "C1"],
        ["C1", "D1"],
        ["D1", "A1"],
        ["A", "A1"],
        ["B", "B1"],
        ["C", "C1"],
        ["D", "D1"],
    ]
    return {
        "kind": "polyhedron",
        "color": color,
        "opacity": opacity,
        "vertices": verts,
        "edges": edges,
    }


def _prism_solid(
    n_gon=3, base_radius=1.2, height=2.5, center=(0.0, 0.0, 0.0), color="#4f8ef7", opacity=0.35
):
    """正 n 棱柱：上下底面正多边形。center 为几何中心。"""
    cx, cy, cz = center
    bottom = []
    top = []
    for i in range(n_gon):
        ang = 2 * _math.pi * i / n_gon
        x = cx + base_radius * _math.cos(ang)
        y = cy - height / 2
        z = cz + base_radius * _math.sin(ang)
        bottom.append([round(x, 4), round(y, 4), round(z, 4)])
        top.append([round(x, 4), round(y + height, 4), round(z, 4)])
    return {"kind": "prism", "color": color, "opacity": opacity, "bottom": bottom, "top": top}


def _pyramid_solid(
    n_gon=4, base_radius=1.2, height=2.5, center=(0.0, 0.0, 0.0), color="#ef6b5b", opacity=0.35
):
    """正 n 棱锥。center 为底面中心。"""
    cx, cy, cz = center
    base = []
    for i in range(n_gon):
        ang = 2 * _math.pi * i / n_gon
        x = cx + base_radius * _math.cos(ang)
        z = cz + base_radius * _math.sin(ang)
        base.append([round(x, 4), round(cy, 4), round(z, 4)])
    apex = [round(cx, 4), round(cy + height, 4), round(cz, 4)]
    return {"kind": "pyramid", "color": color, "opacity": opacity, "base": base, "apex": apex}


def _cylinder_solid(
    base=(0.0, 0.0, 0.0), top=(0.0, 2.0, 0.0), radius=1.0, color="#4f8ef7", opacity=0.4
):
    return {
        "kind": "cylinder",
        "color": color,
        "opacity": opacity,
        "base": list(base),
        "top": list(top),
        "radius": radius,
    }


def _cone_solid(
    base=(0.0, 0.0, 0.0), top=(0.0, 2.5, 0.0), radius=1.0, color="#ef6b5b", opacity=0.4
):
    return {
        "kind": "cone",
        "color": color,
        "opacity": opacity,
        "base": list(base),
        "top": list(top),
        "radius": radius,
    }


def _sphere_solid(center=(0.0, 0.0, 1.0), radius=1.0, color="#4cc49c", opacity=0.4):
    return {
        "kind": "sphere",
        "color": color,
        "opacity": opacity,
        "center": list(center),
        "radius": radius,
    }


def _fallback_solids_for_title(title: str) -> list[dict]:
    """V4 契约：禁止标题关键词驱动的默认 3D 图形。

    图形只能由当前题目与推导步骤的结构化数据（LLM 输出的 geometry.solids）驱动；
    标题关键词不再用于注入默认几何体。本函数恒返回空列表——
    图形不可靠时由调用方显示"需要确认图形条件"，绝不渲染默认实体。
    """
    _ = title
    return []


def _ensure_solids(fig: dict, title: str) -> dict:
    """V4 契约：不在代码层按标题注入默认 3D 实体。

    仅作透传（保持函数形态兼容历史调用）；LLM 未给出有效 solids 时，
    由 _gen_slide_content 判定为"图形不可靠需确认"，不注入任何默认几何体。
    """
    _ = title
    return fig


def _normalize_plot2d(raw: dict) -> dict | None:
    """二维函数图块归一化（复用前端 GraphBlock）。

    契约：{kind:"plot2d", expr, x0, x1, marks:[{x,label}], regions:[{x0,x1,color,label}], caption}
    后端用 visual_spec.sample_2d 校验表达式可采样（非空），透传原始字段给前端渲染。
    表达式不可求值或采样为空则丢弃该块（页面仍有 text 兜底）。
    """
    if not isinstance(raw, dict):
        return None
    from app.domains.classroom.visual_spec import sample_2d

    expr = str(raw.get("expr") or "").strip()
    if not expr or len(expr) > 200:
        return None
    try:
        x0 = float(raw.get("x0") if raw.get("x0") is not None else -4)
        x1 = float(raw.get("x1") if raw.get("x1") is not None else 4)
    except (TypeError, ValueError):
        x0, x1 = -4.0, 4.0
    if x1 <= x0 or abs(x0) > _MAX_COORD or abs(x1) > _MAX_COORD:
        x0, x1 = -4.0, 4.0
    # 采样校验：表达式必须能产生至少 3 个有限点（否则丢弃）
    pts = sample_2d(expr, x0, x1, samples=60)
    if not pts or len(pts) < 3:
        return None
    out: dict = {"kind": "plot2d", "expr": expr[:200], "x0": round(x0, 4), "x1": round(x1, 4)}
    # 标记点（临界点/极值点/零点）
    marks = []
    for m in (raw.get("marks") or [])[:8]:
        if not isinstance(m, dict):
            continue
        try:
            mx = float(m.get("x"))
        except (TypeError, ValueError):
            continue
        if not _math.isfinite(mx) or abs(mx) > _MAX_COORD:
            continue
        marks.append({"x": round(mx, 4), "label": str(m.get("label") or "")[:12]})
    if marks:
        out["marks"] = marks
    # 单调区间着色（增/减区间）
    regions = []
    for r in (raw.get("regions") or [])[:6]:
        if not isinstance(r, dict):
            continue
        try:
            rx0 = float(r.get("x0"))
            rx1 = float(r.get("x1"))
        except (TypeError, ValueError):
            continue
        if rx1 <= rx0:
            continue
        regions.append(
            {
                "x0": round(rx0, 4),
                "x1": round(rx1, 4),
                "color": _norm_color(r.get("color"), _FUNC_COLOR),
                "label": str(r.get("label") or "")[:8],
            }
        )
    if regions:
        out["regions"] = regions
    caption = str(raw.get("caption") or "").strip()[:60]
    if caption:
        out["caption"] = caption
    return out


# ==================== Schemas ====================


class SessionCreateRequest(BaseModel):
    """OpenMAIC 语义：给出 topic 即可生成课堂；course_id 仅作可选增强上下文。"""

    course_id: uuid.UUID | None = None
    topic: str | None = None
    description: str | None = None  # 补充要求：重点讲解/易错点/例题量等
    slide_count: int = Field(default=10, ge=MIN_SLIDES, le=MAX_SLIDES)
    mode: str = Field(default="sync", pattern="^(sync|review|topic)$")
    source_type: str = Field(
        default="topic", pattern="^(topic|photo|file)$"
    )  # 来源类型（历史筛选）
    source_ref: dict | None = (
        None  # 原件留存 {filename, page, region, raw_meta, status, retry_reason}
    )

    @model_validator(mode="after")
    def _need_source(self) -> SessionCreateRequest:
        if self.course_id is None and not (self.topic or "").strip():
            raise ValueError("course_id 与 topic 至少提供一个（OpenMAIC 语义：输入主题即可生成）")
        return self


class SessionItem(BaseModel):
    session_id: str
    course_id: str
    title: str
    mode: str
    slide_count: int
    status: str
    created_at: str | None = None


def resolve_generation_topic(session_title: str | None, parsed_source_text: str | None) -> str:
    """优先使用原件解析出的完整题干，避免受会话标题长度限制。

    ``classroom_sessions.title`` 仅用于列表展示，长度受数据库字段限制；拍题中的
    多个小问则属于生成与验收的原始输入，必须始终从解析产物取得。没有可审计的
    解析文本时才退回到用户输入/会话标题。
    """
    source = str(parsed_source_text or "").strip()
    if source:
        return source[:6000]
    return str(session_title or "").strip()[:6000]


async def _load_parsed_source_text(source_ref: dict[str, Any], db: AsyncSession) -> str:
    """读取题图/文件的可审计文本产物；不从 OCR 条件反向拼造题干。"""
    for key in ("source_text", "extracted_text", "content"):
        value = source_ref.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:6000]

    file_id = source_ref.get("file_id")
    if not file_id:
        return ""
    try:
        result = await db.execute(
            select(FileAsset.content)
            .where(
                FileAsset.file_id == uuid.UUID(str(file_id)),
                FileAsset.asset_type.in_(("markdown", "text")),
                FileAsset.deleted_at.is_(None),
                FileAsset.content.is_not(None),
            )
            .order_by(FileAsset.page_no.asc().nulls_last())
        )
        parts = [str(content).strip() for content in result.scalars().all() if str(content).strip()]
        return "\n\n".join(parts)[:6000]
    except (TypeError, ValueError):
        logger.warning("classroom_source_file_id_invalid", file_id=str(file_id)[:80])
        return ""


# ==================== 生成管线 ====================


async def _gen_outline(
    course: Course | None,
    slide_count: int,
    mode: str,
    kp_table: str,
    knowledge_cards: str,
    topic: str = "",
    description: str = "",
    condition_ledger: list[str] | None = None,
) -> tuple[bool, list, str]:
    """大纲生成（LLM → JSON；失败返回确定性兜底大纲）。
    OpenMAIC 语义：course 可空——主题直接生成，课程产物仅作增强上下文。"""
    from app.providers.router import get_model_router
    from app.skills.smart_quiz.main import parse_quiz_json

    confirmed_conditions = [
        str(item).strip()[:240] for item in (condition_ledger or []) if str(item).strip()
    ][:12]
    # 仅将题干等式链机械化简成长度事实；不从图形名称或模型猜测任何未知量。
    from app.domains.classroom.rag_orchestrator import derive_explicit_length_facts

    coordinate_facts = derive_explicit_length_facts(confirmed_conditions)
    condition_context = ""
    if confirmed_conditions:
        condition_context = (
            "\n\n【已确认题目条件：逐条保留，不得替换、简化或补造】\n- "
            + "\n- ".join(confirmed_conditions)
        )
    if coordinate_facts:
        facts_text = "，".join(
            f"{segment}={value:g}" for segment, value in coordinate_facts.items()
        )
        condition_context += (
            "\n【由上述等式链机械归约的长度事实：只可使用这些已推出的数值】\n" + facts_text
        )

    if course is not None and (course.preprocess_result or {}).get("chapters"):
        chapters = "；".join(
            f"{ch.get('title')}（{ch.get('summary') or ''}）"
            for ch in (course.preprocess_result or {}).get("chapters") or []
        )
    else:
        # OpenMAIC 语义：course 可选。当课程存在但预处理产物（章节）为空时，
        # 以课程标题作为主题兜底，避免 LLM 脱离所选课程自由发挥（如二次函数课生成导数课）。
        chapters = (topic or (course.title if course else "") or "").strip()
        if (description or "").strip():
            chapters += f"；补充要求：{description.strip()}"
        chapters = chapters or "（自由生成：请围绕主题组织课堂结构）"
    chapters += condition_context
    count_rule = f"slide_count 必须恰好 {slide_count} 页（第一页导入、最后一页小结，中间 {slide_count - 2} 页正文）"
    import math as _pm

    prompt = _safe_prompt_format(
        _MATH_OUTLINE_PROMPT,
        slide_count=slide_count,
        total_minutes=slide_count * 3,
        slide_count_minus_1=slide_count - 1,
        min_figure_pages=_pm.ceil(slide_count * 0.4),
        min_example_pages=_pm.ceil(slide_count * 0.2),
        min_latex_pages=_pm.ceil(slide_count * 0.3),
        chapters=chapters[:3000],
        kp_table=kp_table or "（无）",
        knowledge_cards=knowledge_cards[:2000],
        mode_label=_MODE_LABELS.get(mode, "同步课堂"),
        count_rule=count_rule,
    )
    router_llm = get_model_router()
    try:
        result = await router_llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
            max_tokens=6000,
            request_id=f"classroom-outline-{uuid.uuid4().hex[:12]}",
            scene="classroom_outline",
        )
        data = parse_quiz_json(result.get("content", "")) or {}
    except Exception as e:
        logger.warning("classroom_outline_llm_failed", error=str(e)[:200])
        data = {}

    slides = data.get("slides") or []
    if not slides:
        # 确定性兜底：按章节/主题切页（第一页导入/最后一页小结）
        slides = _fallback_outline(course, slide_count, topic)
    # 白名单过滤 + 强制 count
    valid_codes = {c.split(":")[0] for c in kp_table.splitlines() if ":" in c}
    import math as _pm2

    _mc_fg = _pm2.ceil(slide_count * 0.4)  # 至少图形页数
    keep = []
    for i, s in enumerate(slides[:slide_count]):
        order = i + 1
        is_intro = order == 1
        is_outro = order == slide_count
        user_req = s.get("required_blocks")
        user_fk = s.get("figure_kind")
        accepted_figure_kinds = {
            "none",
            "geometry_3d_solid",
            "geometry_conic_curve",
            "plot2d_function",
            "geometry",
        }
        requested_fk = (
            user_fk if isinstance(user_fk, str) and user_fk in accepted_figure_kinds else None
        )
        # required_blocks 与 figure_kind：正文页强制要求 3 种以上块类型 + 指定图形
        if is_intro or is_outro:
            req = ["text"]
            fk = "none"
        else:
            # 中间页按序号分配：前 _mc_fg 个中间页写 geometry_conic_curve / geometry_3d_solid（由题干在生成时决定），
            # 其余中间页写 plot2d_function / none 交替，但保证 none 不超过 30%。
            # 这里用 heuristic：order 是否为图形候选页（前_mc_fg个中间页 + 其他每隔一页）
            should_figure = (2 <= order < 2 + _mc_fg) or (
                (order - 2) % 2 == 0 and order < slide_count - 1
            )
            # figure_kind 是视觉契约：函数页只要求 plot2d，立几/解析几何页才要求 geometry。
            # 不能把两种不相干的图形同时硬塞给模型，否则会造成无效重试和错误配图。
            fk = requested_fk or ("geometry" if should_figure else "none")
            if fk == "plot2d_function":
                req = ["text", "latex", "plot2d"]
            elif fk in {"geometry", "geometry_3d_solid", "geometry_conic_curve"}:
                req = ["text", "latex", "geometry"]
            else:
                req = ["text", "latex", "example"]
        # LLM 可以补充块类型，但不能把后端设定的正文最低结构降级。
        # 例如 text+geometry 仍必须保留 latex/example 等第二种非文本块。
        if isinstance(user_req, list):
            # required_blocks 是前端可渲染的 block.kind；figure_kind（如
            # plot2d_function）只是视觉契约，绝不能当成 block.kind 再要求模型生成。
            # 否则 materializer 永远无法满足该项，导致无意义重试并最终判失败。
            renderable_block_kinds = {
                "text",
                "latex",
                "geometry",
                "plot2d",
                "example",
                "note",
                "table",
                "theorem",
            }
            extras = [str(x) for x in user_req if str(x) in renderable_block_kinds]
            if fk == "plot2d_function":
                extras = [x for x in extras if x != "geometry"]
            elif fk in {"geometry", "geometry_3d_solid", "geometry_conic_curve"}:
                extras = [x for x in extras if x != "plot2d"]
            req = list(dict.fromkeys(req + extras))[:5]
        keep.append(
            {
                "order": order,
                "type": "slide",
                "title": str(s.get("title") or f"第 {order} 页")[:12],
                "subtitle": str(s.get("subtitle") or "")[:20],
                "kp_code": (
                    str(s.get("kp_code") or "")
                    if str(s.get("kp_code") or "") in valid_codes
                    else ""
                ),
                "key_points": [str(x)[:40] for x in (s.get("key_points") or [])][:3],
                "narration": str(s.get("narration") or "")[:120],
                "minutes": min(max(int(s.get("minutes") or 3), 2), 5),
                "required_blocks": req,
                "figure_kind": fk,
                "source_conditions": confirmed_conditions,
                "coordinate_facts": coordinate_facts,
            }
        )
    title = (
        str(data.get("title") or (course.title if course else topic))[:40]
        or (topic or course.title if course else "")[:40]
    )
    return True, keep, title


def _fallback_outline(course: Course | None, slide_count: int, topic: str = "") -> list:
    """确定性兜底：第一页导入、最后一页小结，中间按章节/主题切页。"""
    if course is not None:
        chapters = (course.preprocess_result or {}).get("chapters") or []
        cards = (course.preprocess_result or {}).get("knowledge_cards") or []
    else:
        chapters = []
        cards = []
    mid = slide_count - 2
    slides = [
        {
            "order": 1,
            "title": "课堂导入",
            "subtitle": course.title if course else topic,
            "kp_code": "",
            "key_points": ["回顾旧知", "提出本节课问题", "明确学习目标"],
            "narration": "同学们好，今天我们一起来学习这一课。",
            "minutes": 2,
        }
    ]
    for i in range(mid):
        ch = (
            chapters[i]
            if i < len(chapters)
            else {"title": (topic or f"知识点 {i + 1}"), "summary": ""}
        )
        card = cards[i] if i < len(cards) else {}
        slides.append(
            {
                "order": i + 2,
                "title": str(ch.get("title") or f"知识点 {i + 1}")[:12],
                "subtitle": str(card.get("title") or "")[:20],
                "kp_code": "",
                "key_points": [
                    str(card.get("title") or ch.get("title") or "本节要点")[:40],
                    "公式与例题",
                    "易错提醒",
                ],
                "narration": str(ch.get("summary") or f"接下来我们学习{ch.get('title')}")[:120],
                "minutes": 3,
            }
        )
    slides.append(
        {
            "order": len(slides) + 1,
            "title": "课堂小结",
            "subtitle": "核心公式 + 课后行动",
            "kp_code": "",
            "key_points": ["本节课核心公式", "常见易错点", "课后练什么"],
            "narration": "我们来总结一下今天的内容，并布置课后练习。",
            "minutes": 2,
        }
    )
    return slides


async def _gen_slide_content(
    outline: dict, knowledge_cards: str, verification_feedback: str = ""
) -> dict:
    """单页内容生成（V4 执行门版）。

    纪律：
      1. 先调用 LLM 生成，用 parse_quiz_json 提取 blocks；
      2. 若 outline.required_blocks 指定了块种类，LLM 返回必须覆盖所有种类。
         不满足时**再重试最多 2 次**（每次重试注入缺失种类清单，温度升高）；
      3. 仍不满足时程序化补块：
         - 缺 geometry → 由 figure_kind/geometry_claims 坐标构建或放入可渲染的骨架；
         - 缺 plot2d → 放 plot2d 骨架（空 regions/marks，保证种类不缺）；
         - 缺 latex → 从文字要点包装成 LaTeX 展示块（不引入伪造推导，仅展示名词/变量）；
         - 缺 example → 放入题目原文复述 + 提示"跟做：学生独立写出关键方程"的交互块；
         - 缺 note → 写入"易错提示/小结"文本。
    """
    from app.providers.router import get_model_router
    from app.skills.smart_quiz.main import parse_quiz_json

    router_llm = get_model_router()

    req_blocks = [str(x) for x in (outline.get("required_blocks") or []) if str(x)]
    fig_kind = str(outline.get("figure_kind") or "none")
    _ = fig_kind  # used in closure _programmatic_patch_blocks; 保留给后续补丁判定用
    order = outline.get("order", 1)
    title = outline.get("title") or "本节"
    kps = outline.get("key_points") or []
    bounded_feedback = str(verification_feedback or "").strip()[:600]

    def _cover_ok(items: list[dict]) -> tuple[bool, set[str]]:
        have = {b.get("kind") for b in items if isinstance(b, dict)}
        missing = {r for r in req_blocks if r not in have}
        return (len(missing) == 0), missing

    async def _try_llm(
        temperature: float, extra_instruction: str = ""
    ) -> tuple[list[dict], dict[str, Any]] | None:
        prompt_args: dict[str, Any] = {
            "outline_json": outline,
            "knowledge_cards": knowledge_cards[:1500],
        }
        if extra_instruction:
            prompt_args["extra_instruction"] = extra_instruction
        try:
            prompt_text = _safe_prompt_format(_MATH_CONTENT_PROMPT, **prompt_args)
        except Exception:
            prompt_text = _safe_prompt_format(
                _MATH_CONTENT_PROMPT, outline_json=outline, knowledge_cards=knowledge_cards[:1500]
            )
        if extra_instruction and "${extra_instruction}" not in _MATH_CONTENT_PROMPT:
            # prompt 模板无占位：追加到末尾
            prompt_text = (
                prompt_text.rstrip() + "\n\n【本页强制补充指令】\n" + extra_instruction + "\n"
            )
        if bounded_feedback:
            prompt_text += (
                "\n【上一轮独立数学验证发现】\n"
                + bounded_feedback
                + "\n请严格以本页已确认题目条件重新推导并重建坐标/图形。"
                "这不是答案提示；不得照抄或补造条件，必须给出可由题设验证的内容。\n"
            )
        try:
            result = await router_llm.chat(
                messages=[{"role": "user", "content": prompt_text}],
                temperature=temperature,
                max_tokens=4000,
                request_id=f"classroom-slide-{uuid.uuid4().hex[:12]}",
                scene="classroom_slide",
            )
            data = parse_quiz_json(result.get("content", "")) or {}
        except Exception as e:
            logger.warning("classroom_slide_llm_retry_failed", order=order, error=str(e)[:200])
            return None
        return _materialize_blocks(data, outline), data

    # ===== 第 1 次 + 重试 =====
    # 迭代20 门禁修复：所有页面统一 3 次尝试。原「无 req_blocks 只试 1 次」在
    # 供应商限流（429/瞬时超时）下一次抖动即整页失败——验收实测 8 页并发下
    # 5 页 failed 触发 provider_outage 整课判失败。无 req_blocks 的页面首次
    # 成功即 break（:1519），重试预算只在真失败时消耗，成功路径零额外成本。
    # 重试间加退避 sleep（0.6s/1.2s），给限流窗口留恢复时间——原实现立即重试，
    # 在限流场景下等于把同一个 429 撞三遍。
    max_tries = 3
    last_items: list[dict] = []
    last_data: dict[str, Any] = {}
    for i in range(max_tries):
        if i > 0:
            await asyncio.sleep(0.6 * i)
        t = 0.3 + 0.15 * i
        instr = ""
        if i > 0 and last_items:
            _, miss = _cover_ok(last_items)
            instr = (
                f"上一轮缺失以下块种类：{sorted(miss)}。"
                f"本轮必须在 blocks 中为每种缺失种类各写至少 1 个合法块："
                f"geometry 必须含 solids/curves；plot2d 必须含 expr 和 x0,x1；"
                f"latex 必须含 latex 字段（公式，如定义/公式/表达式）；"
                f"example 必须含 question+analysis+answer 三字段（题干/分析/解答要点）；"
                f"note 必须含 text 字段（易错提示/小结 1-2 句）。"
                "禁止只写 text 块敷衍。"
            )
        generated = await _try_llm(t, instr)
        if generated is None:
            continue  # 本轮抛错：重试
        items, data = generated
        last_items = items
        last_data = data
        if not req_blocks:
            break  # 无强制要求：首过即可
        ok, _ = _cover_ok(items)
        if ok:
            break  # 已覆盖：退出重试
    else:
        # ===== 重试耗尽仍不满足：程序化补块 =====
        logger.warning(
            "classroom_slide_required_blocks_uncov_after_retries",
            order=order,
            title=title[:30],
            req=req_blocks,
        )
        ok, miss = _cover_ok(last_items)
        if not ok:
            last_items = _programmatic_patch_blocks(last_items, outline, miss)

    items = last_items
    # ===== 上限：限制块数，避免过长 =====
    items = items[:9]
    # 讲稿优先取内容模型为该页现写的口语化讲解（更贴合 blocks），大纲旁白兜底
    content_narration = str(last_data.get("narration") or "").strip()
    outline_narration = str(outline.get("narration") or "").strip()
    narration = (content_narration or outline_narration)[:320] or (
        f"{title}讲解要点：" + ("；".join(kps[:3]) if kps else "")
    )
    out: dict[str, Any] = {
        "blocks": items,
        "narration": narration,
        # 结构化断言与渲染 blocks 同源，必须一并传给页级验证器。
        "math_claims": (
            last_data.get("math_claims") if isinstance(last_data.get("math_claims"), dict) else {}
        ),
        "geometry_claims": (
            last_data.get("geometry_claims")
            if isinstance(last_data.get("geometry_claims"), dict)
            else {}
        ),
    }
    return out


def _conic_circle_curve(cx: float, cy: float, r: float, color: str) -> dict | None:
    """以 (cx,cy) 为圆心、r 为半径的参数圆 → 折线点集（figure.curves 契约）。"""
    if not (r > 1e-6) or not _math.isfinite(r):
        return None
    pts = []
    for i in range(97):
        t = 2 * _math.pi * i / 96
        x = cx + r * _math.cos(t)
        y = cy + r * _math.sin(t)
        if abs(x) > _MAX_COORD or abs(y) > _MAX_COORD:
            return None
        pts.append([round(x, 4), round(y, 4), 0.0])
    return {"kind": "polyline", "points": pts, "closed": True, "color": color}


def _point_label_solid(name: str, pos: list[float], color: str = "#ef4444") -> dict:
    """把一个命名点渲染为可看见的实心球（带字母标签）。"""
    return {
        "kind": "sphere",
        "center": [round(pos[0], 4), round(pos[1], 4), round(pos[2], 4)],
        "radius": 0.16,
        "color": color,
        "opacity": 0.95,
        "labels": [
            {"pos": [round(pos[0], 4), round(pos[1] + 0.35, 4), round(pos[2], 4)], "text": name[:8]}
        ],
    }


def _build_conic_focus_figure(conic: dict, geo: dict) -> dict | None:
    """由 geometry_claims.conic{a,b,c} 确定性构建圆锥曲线焦点三角形图。

    全部数据来自该页已通过验证的结构化断言（a/b/c/theta/inner_point），
    属于"经真实题目条件支持"的构造，不是标题关键词默认图：
    - 椭圆：参数曲线本体 + 焦点 F1/F2 + 焦点三角形 M-F1-F2（M 取断言 theta 或 60°）；
      若带 inner_point 断言，画出内心 I 与内切圆（r = Δ/s，由三角形边长独立计算）。
    - 双曲线：两支参数曲线 + 焦点。
    - 抛物线：y²=2px 本体 + 焦点。
    """
    try:
        a = float(conic.get("a"))
        b = float(conic.get("b"))
    except (TypeError, ValueError):
        return None
    if not (_math.isfinite(a) and _math.isfinite(b)) or a <= 0 or b <= 0:
        return None
    try:
        c = float(conic.get("c"))
    except (TypeError, ValueError):
        c = _math.sqrt(a * a - b * b)
    if not _math.isfinite(c):
        return None
    theta = None
    try:
        tv = conic.get("theta")
        if tv is not None:
            theta = float(tv)
    except (TypeError, ValueError):
        theta = None
    kind = str(conic.get("kind") or "").strip()
    latex = str(conic.get("latex") or "")
    # 类型判定：双曲线 c²=a²+b² → c>a；椭圆 c²=a²−b² → c<a（比 latex 子串更可靠）；
    # 抛物线方程不含 x² 项（"y^2=2px"），含 x^2 的一律不是抛物线。
    is_hyperbola = kind == "hyperbola" or abs(c) > a + 1e-9
    is_parabola = (kind == "parabola") or (
        not is_hyperbola and "y^2" in latex.replace(" ", "") and "x^2" not in latex.replace(" ", "")
    )

    curves: list[dict] = []
    foci: list[list[float]] = []
    span = max(abs(a), abs(b), abs(c)) * 1.6 + 1.0

    if is_parabola:
        p = b if b > 1e-6 else 1.0
        pts = []
        for i in range(161):
            y = -span + 2 * span * i / 160
            x = y * y / (2 * p)
            if abs(x) > _MAX_COORD:
                continue
            pts.append([round(x, 4), round(y, 4), 0.0])
        if len(pts) < 3:
            return None
        curves.append({"kind": "polyline", "points": pts, "closed": False, "color": "#4f8ef7"})
        foci.append([p / 2, 0.0, 0.0])
    elif is_hyperbola:
        for sign in (1, -1):
            pts = []
            for i in range(81):
                t = -1.3 + 2.6 * i / 80
                x = sign * a * _math.cosh(t)
                y = b * _math.sinh(t)
                if abs(x) > _MAX_COORD or abs(y) > _MAX_COORD:
                    continue
                pts.append([round(x, 4), round(y, 4), 0.0])
            if len(pts) >= 2:
                curves.append(
                    {"kind": "polyline", "points": pts, "closed": False, "color": "#4f8ef7"}
                )
        if len(curves) < 1:
            return None
        foci.append([-abs(c), 0.0, 0.0])
        foci.append([abs(c), 0.0, 0.0])
    else:  # 椭圆（默认）
        pts = []
        for i in range(161):
            t = 2 * _math.pi * i / 160
            x = a * _math.cos(t)
            y = b * _math.sin(t)
            pts.append([round(x, 4), round(y, 4), 0.0])
        curves.append({"kind": "polyline", "points": pts, "closed": True, "color": "#4f8ef7"})
        foci.append([-abs(c), 0.0, 0.0])
        foci.append([abs(c), 0.0, 0.0])

    solids: list[dict] = []
    segments: list[dict] = []
    focus_names = ["F1", "F2"] if len(foci) > 1 else ["F"]
    for name, p in zip(focus_names, foci, strict=False):
        solids.append(_point_label_solid(name, p, "#ef6b5b"))

    # 焦点三角形（仅椭圆且两焦点）：M 取断言 theta 或默认 60°
    m_pt: list[float] | None = None
    if len(foci) == 2 and not is_hyperbola:
        th = theta if (theta is not None and _math.isfinite(theta)) else _math.pi / 3
        m_pt = [round(a * _math.cos(th), 4), round(b * _math.sin(th), 4), 0.0]
        f1, f2 = foci
        for p in (f1, f2):
            segments.append(
                {"a": m_pt, "b": [round(p[0], 4), 0.0, 0.0], "dashed": False, "color": "#4cc49c"}
            )
        segments.append(
            {
                "a": [round(f1[0], 4), 0.0, 0.0],
                "b": [round(f2[0], 4), 0.0, 0.0],
                "dashed": True,
                "color": "#94a3b8",
            }
        )
        solids.append(_point_label_solid("M", m_pt, "#4cc49c"))
        # 内心 I 与内切圆：由三角形三边长独立计算（r = Δ/s），仅当断言给出 inner_point 时画
        inner = geo.get("inner_point") if isinstance(geo.get("inner_point"), dict) else None
        if inner is not None and m_pt is not None:
            try:
                ix, iy = float(inner.get("x")), float(inner.get("y"))
            except (TypeError, ValueError):
                ix = iy = None
            if ix is not None and iy is not None and _math.isfinite(ix) and _math.isfinite(iy):
                side_f2f1 = abs(f2[0] - f1[0])
                side_mf1 = _math.hypot(m_pt[0] - f1[0], m_pt[1])
                side_mf2 = _math.hypot(m_pt[0] - f2[0], m_pt[1])
                per = side_f2f1 + side_mf1 + side_mf2
                if per > 1e-6:
                    # 内心坐标 = 边长加权平均（对顶点权重=对边边长）
                    w1, w2, wm = side_mf2, side_mf1, side_f2f1
                    cx = (w1 * f1[0] + w2 * f2[0] + wm * m_pt[0]) / per
                    cy = (w1 * 0.0 + w2 * 0.0 + wm * m_pt[1]) / per
                    s = per / 2
                    area = 0.5 * side_f2f1 * abs(m_pt[1])
                    r_in = area / s if s > 1e-9 else 0
                    solids.append(_point_label_solid("I", [cx, cy, 0.0], "#c084fc"))
                    circ = _conic_circle_curve(cx, cy, r_in, "#c084fc")
                    if circ:
                        curves.append(circ)

    fig: dict = {"grid": True, "axes": True, "solids": solids, "curves": curves}
    if segments:
        fig["segments"] = segments
    # 不写 camera：交给前端 fitCamera 按包围盒自动取景（自定义机位容易过近裁切）
    return fig


def _figure_from_geometry_claims(geo: dict, *, allow_solid: bool = True) -> dict | None:
    """几何断言驱动的确定性兜底图：圆锥曲线 → 焦点三角形图；立几坐标 → 多面体。

    allow_solid=False（2D 主题页）时拒绝坐标点多面体路径——断言里的散坐标
    对非立几题毫无意义，硬画必出"不属于本题"的图。
    """
    if not isinstance(geo, dict):
        return None
    conic = geo.get("conic")
    if isinstance(conic, dict) and conic.get("a") is not None:
        fig = _build_conic_focus_figure(conic, geo)
        if fig:
            return fig
    if not allow_solid:
        return None
    try:
        from app.domains.classroom.visual_spec import solid_from_coordinates

        fig = solid_from_coordinates(geo.get("coordinates"))
        if fig and fig.get("solids"):
            return fig
    except Exception:
        pass
    return None


def _plot2d_from_math_claims(claims: dict) -> dict | None:
    """函数断言驱动的确定性兜底图（plot2d 契约）。

    LLM 给出的 plot2d 采样失败时，用同页已验证的 math_claims（f_expr +
    临界点 + 单调区间）直接构建图像，保证"函数页必有图"且图文同源。
    """
    if not isinstance(claims, dict):
        return None
    expr = str(claims.get("f_expr") or "").strip()
    if not expr or len(expr) > 200:
        return None
    from app.domains.classroom.visual_spec import sample_2d

    pts = sample_2d(expr, -4, 4, samples=60)
    if not pts or len(pts) < 3:
        return None
    ys = [p[1] for p in pts]
    y_pad = max(1.0, (max(ys) - min(ys)) * 0.35)
    out: dict = {"kind": "plot2d", "expr": expr[:200], "x0": -4.0, "x1": 4.0}
    marks = []
    for cp in (claims.get("critical_points") or [])[:8]:
        try:
            cx = float(cp)
        except (TypeError, ValueError):
            continue
        if _math.isfinite(cx) and abs(cx) <= _MAX_COORD:
            marks.append({"x": round(cx, 4), "label": "极值"})
    if marks:
        out["marks"] = marks
    regions = []
    for iv, label, color in (
        (claims.get("increasing_intervals"), "增", "#ef4444"),
        (claims.get("decreasing_intervals"), "减", "#3b82f6"),
    ):
        for r in (iv or [])[:6]:
            if not (isinstance(r, (list, tuple)) and len(r) == 2):
                continue
            try:
                rx0, rx1 = float(r[0]), float(r[1])
            except (TypeError, ValueError):
                continue
            if rx1 <= rx0:
                continue
            regions.append(
                {"x0": max(-100, rx0), "x1": min(100, rx1), "color": color, "label": label}
            )
    if regions:
        out["regions"] = regions
    out["caption"] = f"y = {expr} 图像"
    _ = y_pad
    return out


def _normalize_table(raw: dict) -> dict | None:
    """table 块归一化：{caption, headers:[...], rows:[[...]]}；空表丢弃。"""
    if not isinstance(raw, dict):
        return None
    headers = [str(h).strip()[:24] for h in (raw.get("headers") or [])[:8] if str(h).strip()]
    rows = []
    for r in (raw.get("rows") or [])[:8]:
        if not isinstance(r, (list, tuple)):
            continue
        cells = [str(c).strip()[:40] for c in r[: len(headers) or 8]]
        if any(cells):
            rows.append(cells)
    if not headers or not rows:
        return None
    out: dict = {"kind": "table", "headers": headers, "rows": rows}
    caption = str(raw.get("caption") or "").strip()[:60]
    if caption:
        out["caption"] = caption
    return out


def _materialize_blocks(data: dict, outline: dict) -> list[dict]:
    """把 LLM 返回的 data.blocks 转成后端规范 item 列表（收敛 geometry/plot2d/example）。

    图形兜底纪律（学生端零警告）：
    1. LLM 图形数据合法 → 直接使用；
    2. 不合法 → 用同页"已验证结构化断言"确定性重建（coordinates/conic/math_claims）；
    3. 仍无 → 静默省略图形（页面继续以文字/公式呈现），绝不向学生展示"未渲染图形"类警告。
    """
    blocks = data.get("blocks") or []
    items: list[dict] = []
    need_figure_fallback = False
    geo = data.get("geometry_claims") if isinstance(data.get("geometry_claims"), dict) else None
    for b in blocks:
        if not isinstance(b, dict):
            continue
        kind = str(b.get("kind") or "text")
        if kind == "geometry":
            fig = _normalize_figure(b.get("figure"))
            if fig and (fig.get("solids") or fig.get("curves") or fig.get("segments")):
                items.append(
                    {
                        "kind": "geometry",
                        "figure": fig,
                        "caption": str(b.get("caption") or "")[:40],
                    }
                )
            else:
                # LLM 图形不合法：记录待兜底，由断言驱动重建；断言也无 → 静默省略
                need_figure_fallback = True
            continue
        if kind == "plot2d":
            plot = _normalize_plot2d(b)
            if plot:
                items.append(plot)
            else:
                fallback = _plot2d_from_math_claims(data.get("math_claims") or {})
                if fallback:
                    items.append(fallback)
                # 兜底失败静默省略：不再插入"本页需要函数图像"警告
            continue
        if kind == "table":
            tbl = _normalize_table(b)
            if tbl:
                items.append(tbl)
            continue
        if kind == "theorem":
            title = str(b.get("title") or "").strip()[:40]
            body = str(b.get("body") or b.get("text") or "").strip()[:600]
            if body:
                items.append({"kind": "theorem", "title": title, "body": body})
            continue
        detail = {
            k: str(b[k])[:400]
            for k in ("text", "latex", "question", "analysis", "answer")
            if b.get(k) is not None
        }
        if kind == "example" and "question" not in detail:
            kind = "text"
        items.append({"kind": kind, **detail})
    # 本页出现过 geometry 块但数据不合法：用断言重建（立几坐标/圆锥曲线），不行就静默省略。
    # 重建方向必须与大纲 figure_kind 一致：2D 主题（conic/plot2d/none）绝不拿坐标点
    # 硬凑多面体——宁可本页无图，也不给学生一张"不属于本题"的图。
    fig_kind = str((outline or {}).get("figure_kind") or "none")
    allow_solid_rebuild = fig_kind in ("geometry", "geometry_3d_solid")
    if need_figure_fallback and not any(b.get("kind") in ("geometry", "figure") for b in items):
        fig = _figure_from_geometry_claims(geo or {}, allow_solid=allow_solid_rebuild)
        if fig and (fig.get("solids") or fig.get("curves")):
            items.append(
                {
                    "kind": "geometry",
                    "figure": fig,
                    "caption": "由本页已验证的题目条件生成",
                }
            )
    # LLM 整页漏掉 geometry 块但断言里有圆锥曲线/坐标（大纲要求图形页）：同样补齐
    if geo and not any(b.get("kind") in ("geometry", "figure", "plot2d") for b in items):
        conic = geo.get("conic")
        has_coords = (
            isinstance(geo.get("coordinates"), dict) and len(geo.get("coordinates") or {}) >= 4
        )
        conic_ok = isinstance(conic, dict) and conic.get("a") is not None
        if conic_ok or (allow_solid_rebuild and has_coords):
            fig = _figure_from_geometry_claims(geo, allow_solid=allow_solid_rebuild)
            if fig and (fig.get("solids") or fig.get("curves")):
                items.append(
                    {
                        "kind": "geometry",
                        "figure": fig,
                        "caption": "由本页已验证的题目条件生成",
                    }
                )
    return items


def _programmatic_patch_blocks(items: list[dict], outline: dict, missing: set[str]) -> list[dict]:
    """保留真实生成内容，禁止为满足版式要求伪造数学或图形块。

    重试耗尽仍缺种类时原样返回（不塞默认立方体/y=0/通用公式）。学生端不做
    任何"缺块"告警展示——图形缺口由 _materialize_blocks 的断言驱动兜底，
    其余缺口静默降级，页面照常以已有内容呈现。
    """
    return list(items or [])


def _append_grounded_ggb_block(
    slide: dict,
    *,
    ggb: dict | None,
    evidence: dict | None,
    verification_status: str,
) -> bool:
    """仅把已验证内容对应的安全 GeoGebra 构造附到课堂页。"""
    if verification_status != "verified" or not isinstance(evidence, dict):
        return False
    if evidence.get("status") != "grounded" or not evidence.get("citations"):
        return False
    if not isinstance(ggb, dict) or not isinstance(ggb.get("commands"), list):
        return False
    commands = [str(c).strip() for c in ggb["commands"] if str(c).strip()]
    if not commands:
        return False
    blocks = slide.get("blocks")
    if not isinstance(blocks, list) or any(
        isinstance(block, dict) and block.get("kind") == "ggb" for block in blocks
    ):
        return False
    payload = build_ggb_payload(
        commands,
        str(ggb.get("view") or "2d"),
        caption=str(slide.get("title") or "交互图形")[:120],
    )
    # 同页只保留一个主图，避免旧自绘图与交互画布重复争抢学习画布。
    blocks[:] = [
        block
        for block in blocks
        if not (isinstance(block, dict) and block.get("kind") in {"geometry", "plot2d", "figure"})
    ]
    blocks.append(
        {
            "kind": "ggb",
            "caption": payload["caption"],
            "ggb": payload,
            # 命令白名单通过不等于图与题完全一致，继续显式要求教学复核。
            "visual_verification": {
                "status": "needs_review",
                "reason": "命令已通过安全校验；请以题干条件和教材依据复核图形关系。",
                "citations": list(evidence.get("citations") or [])[:4],
            },
        }
    )
    return True


async def _attach_grounded_geogebra_visual(
    slide: dict,
    *,
    outline: dict,
    evidence: dict,
    db: AsyncSession,
    user_id: str,
) -> str:
    """复用已有 GeoGebra 服务生成可交互图，不足时不绘制默认图。

    OpenMAIC 哲学对齐：默认关闭（classroom_enable_ggb=False）——自绘图形
    （MathFigure3D 受控场景 + 确定性构造器）零额外 LLM 调用且离线可渲染；
    ggb 依赖外网 CDN 渲染器且每页多一次 4000-token 调用，仅显式开启时使用。
    """
    if not settings.classroom_enable_ggb:
        return "not_requested"
    figure_kind = str(outline.get("figure_kind") or "none")
    if figure_kind == "none":
        return "not_requested"
    if (slide.get("verification_result") or {}).get("status") != "verified":
        return "math_not_verified"
    if evidence.get("status") != "grounded" or not evidence.get("citations"):
        return "no_textbook_evidence"

    relevant_blocks: list[str] = []
    for block in slide.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        for key in ("text", "latex", "question", "analysis", "answer", "caption", "expr"):
            if block.get(key):
                relevant_blocks.append(str(block[key]).strip())
    source_conditions = [
        str(item).strip() for item in (outline.get("source_conditions") or []) if str(item).strip()
    ]
    condition_context = "\n".join(f"- {item}" for item in source_conditions)
    lesson_context = "\n".join(item for item in relevant_blocks if item)[:1800]
    geometry_claims = slide.get("geometry_claims")
    if (
        isinstance(geometry_claims, dict)
        and geometry_claims.get("source") == "confirmed_right_trapezoid_coordinate_witness"
    ):
        lesson_context += "\n【后端已验证坐标见证：GeoGebra 必须使用这些点，禁止重新估计】\n" + str(
            geometry_claims.get("coordinates") or {}
        )
    if condition_context:
        lesson_context = (
            "【已确认题目条件，图形必须逐条满足】\n"
            + condition_context
            + "\n\n【已验证讲解与板书】\n"
            + lesson_context
        )
    if not lesson_context:
        return "no_verified_visual_context"

    try:
        ggb = await generate_ggb(
            f"课堂主题：{slide.get('title') or ''}\n已验证讲解与板书：\n{lesson_context}",
            figure_hint=(
                f"仅构造本页 {figure_kind} 所需对象；必须对应已验证的讲解与教材依据，"
                "不要补题干没有给出的长度、角度、点或默认立体。"
            ),
            interactive=True,
            user_id=user_id,
            db=db,
        )
    except Exception as exc:
        logger.warning("classroom_geogebra_generation_failed", error=str(exc)[:160])
        return "generation_failed"

    if _append_grounded_ggb_block(
        slide,
        ggb=ggb,
        evidence=evidence,
        verification_status=(slide.get("verification_result") or {}).get("status", ""),
    ):
        return "attached"
    return "no_safe_visual"


# ==================== 课堂语音（对齐 OpenMAIC：神经语音而非浏览器 TTS） ====================

# 音色映射：UI 键 → 硅基流动 CosyVoice2 音色（当前通道，质量最好）
_TTS_VOICES = {
    "xiaoxiao": "FunAudioLLM/CosyVoice2-0.5B:anna",  # 女·温柔
    "xiaoyi": "FunAudioLLM/CosyVoice2-0.5B:bella",  # 女·活泼
    "yunxi": "FunAudioLLM/CosyVoice2-0.5B:benjamin",  # 男·沉稳
    "yunyang": "FunAudioLLM/CosyVoice2-0.5B:alex",  # 男·播音
}
_TTS_VOICE_DEFAULT = "xiaoxiao"
# 进程内音频缓存（页级文本重复朗读不重复合成）；MD5(text+voice) → mp3 bytes
_TTS_CACHE: dict[str, bytes] = {}
_TTS_CACHE_MAX = 64


class ClassroomTtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    voice: str = Field(default="xiaoxiao", max_length=24)


async def _tts_siliconflow(text: str, voice_full: str) -> bytes | None:
    """硅基流动 CosyVoice2 合成（当前 DEEPSEEK_API_KEY 即硅基流动凭证）"""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://api.siliconflow.cn/v1/audio/speech",
                json={
                    "model": "FunAudioLLM/CosyVoice2-0.5B",
                    "input": text,
                    "voice": voice_full,
                    "response_format": "mp3",
                },
                headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            )
            if r.status_code == 200 and r.content[:3] in (b"ID3", b"\xff\xfb", b"\xff\xf3"):
                return r.content
            logger.warning(
                "classroom_tts_siliconflow_bad_response", status=r.status_code, body=r.text[:120]
            )
    except Exception as e:
        logger.warning("classroom_tts_siliconflow_failed", error=str(e)[:140])
    return None


async def _tts_edge(text: str, voice_full: str) -> bytes | None:
    """edge-tts 备选（微软神经语音，需出网可达 bing 语音服务）"""
    try:
        import edge_tts

        communicate = edge_tts.Communicate(text[:2000], voice_full, rate="+6%")
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf) or None
    except Exception as e:
        logger.warning("classroom_tts_edge_failed", error=str(e)[:140])
        return None


@router.post("/tts")
async def classroom_tts(req: ClassroomTtsRequest, user: dict = Depends(get_current_user)):
    """课堂讲稿神经语音合成（返回 audio/mpeg；不可用时前端回退浏览器 TTS）。

    契约变更（FE→BE）：本端点此前无鉴权，现要求 Bearer Token——与全站端点纪律一致。
    """
    import hashlib as _hashlib

    voice_key = req.voice if req.voice in _TTS_VOICES else _TTS_VOICE_DEFAULT
    key = _hashlib.md5(f"{voice_key}|{req.text}".encode()).hexdigest()
    cached = _TTS_CACHE.get(key)
    if cached:
        return Response(content=cached, media_type="audio/mpeg")

    audio = await _tts_siliconflow(req.text, _TTS_VOICES[voice_key])
    if not audio:
        edge_voice = {
            "xiaoxiao": "zh-CN-XiaoxiaoNeural",
            "xiaoyi": "zh-CN-XiaoyiNeural",
            "yunxi": "zh-CN-YunxiNeural",
            "yunyang": "zh-CN-YunyangNeural",
        }.get(voice_key, _TTS_VOICE_DEFAULT.replace("zh-CN-", "zh-CN-"))
        audio = await _tts_edge(req.text, edge_voice)
    if not audio:
        return ApiResponse(code=50002, message="语音合成不可用", data=None)

    if len(_TTS_CACHE) >= _TTS_CACHE_MAX:
        _TTS_CACHE.pop(next(iter(_TTS_CACHE)))
    _TTS_CACHE[key] = audio
    return Response(content=audio, media_type="audio/mpeg")


# ==================== 分层练习（效果图右栏：基础/进阶/挑战） ====================

_PRACTICE_PROMPT = """\
你是高中数学命题老师。基于本节课的内容，为课后「分层练习」出题：基础、进阶、挑战三档，每档 ${per_tier} 道选择题。

【本节课主题】
${topic}

【本节课页面结构】
${outline_summary}

【本课关键公式（出题必须围绕它们）】
${formulas}

【出题 JSON 契约（只输出 JSON）】
{
  "basic": [
    {"question": "题干（≤80字，可直接计算/概念辨析）", "options": ["选项A内容", "选项B内容", "选项C内容", "选项D内容"], "answer": "C", "analysis": "解析（≤120字，含所用公式/定理）"}
  ],
  "advanced": [ ...同结构... ],
  "challenge": [ ...同结构... ]
}

【命题纪律】
- basic：单一知识点直接套用（概念辨析/一步计算）；
- advanced：2 步以上综合，或需要分类讨论；
- challenge：本课知识的综合/变式/易错陷阱题；
- 四个选项互不重复、干扰项要有典型错因；answer 取 "A"/"B"/"C"/"D"；
- 全部为客观单选题；数学必须正确；只输出 JSON。"""


_PRACTICE_RETRY_ATTEMPTS = 2


async def _gen_practice(topic: str, outlines: list, slides: list, per_tier: int = 3) -> dict | None:
    """课堂分层练习生成；短暂的模型/网络失败会重试一次。"""
    from app.providers.router import get_model_router
    from app.skills.smart_quiz.main import parse_quiz_json

    outline_summary = "\n".join(
        f"{o.get('order')}. {o.get('title')}" for o in (outlines or [])[:15] if isinstance(o, dict)
    )
    formulas: list[str] = []
    for sld in (slides or [])[:15]:
        if not isinstance(sld, dict):
            continue
        for b in sld.get("blocks") or []:
            if isinstance(b, dict) and b.get("kind") == "latex" and b.get("latex"):
                formulas.append(str(b["latex"])[:120])
        if len(formulas) >= 8:
            break
    prompt = _safe_prompt_format(
        _PRACTICE_PROMPT,
        topic=(topic or "")[:600],
        outline_summary=outline_summary or "（无）",
        formulas="\n".join(f"- {f}" for f in formulas) or "（无）",
        per_tier=per_tier,
    )

    def _norm_tier(items) -> list[dict]:
        out: list[dict] = []
        for q in (items or [])[: per_tier + 2]:
            if not isinstance(q, dict):
                continue
            question = str(q.get("question") or "").strip()[:300]
            raw_options = q.get("options")
            if isinstance(raw_options, (list, tuple)):
                options = [str(o).strip()[:120] for o in raw_options[:4]]
            else:
                options = [str(q.get(f"option_{k}") or "").strip()[:120] for k in "ABCD"]
            ans_raw = str(q.get("answer") or "").strip().upper()
            m = _re.search(r"[ABCD]", ans_raw)
            answer_idx = "ABCD".index(m.group(0)) if m else -1
            analysis = str(q.get("analysis") or "").strip()[:400]
            if (
                not question
                or len(options) != 4
                or any(not o for o in options)
                or not (0 <= answer_idx <= 3)
            ):
                continue
            out.append(
                {
                    "question": question,
                    "options": options,
                    "answer": answer_idx,
                    "analysis": analysis,
                }
            )
            if len(out) >= per_tier:
                break
        return out

    router_llm = get_model_router()
    for attempt in range(_PRACTICE_RETRY_ATTEMPTS):
        try:
            result = await router_llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.35,
                max_tokens=4000,
                request_id=f"classroom-practice-{uuid.uuid4().hex[:12]}",
                scene="classroom_practice",
            )
            data = parse_quiz_json(result.get("content", "")) or {}
            practice = {
                tier: _norm_tier(data.get(tier)) for tier in ("basic", "advanced", "challenge")
            }
            if any(practice.values()):
                return practice
            raise ValueError("practice response contains no renderable questions")
        except Exception as e:
            logger.warning("classroom_practice_llm_failed", attempt=attempt + 1, error=str(e)[:200])
            if attempt + 1 < _PRACTICE_RETRY_ATTEMPTS:
                await asyncio.sleep(0.5 * (attempt + 1))
    return None


def _fallback_practice(topic: str, outlines: list, slides: list) -> dict:
    """AI 暂不可用时的可作答保底练习，绝不让课堂停在空白加载态。"""
    title = str((outlines or [{}])[0].get("title") or topic or "本节课核心概念").strip()[:80]
    primary = str(topic or title).strip()[:80]
    distractors = ["与本节无关的结论", "未经题目条件支持的推断", "跳过推导直接猜测答案"]

    def question(prefix: str, analysis: str) -> dict:
        return {
            "question": prefix,
            "options": [primary, *distractors],
            "answer": 0,
            "analysis": analysis,
        }

    return {
        "basic": [
            question(
                "本节课首先要掌握的核心主题是什么？",
                f"本节课堂围绕“{primary}”展开，先回忆相关定义与条件。",
            )
        ],
        "advanced": [
            question(
                f"面对“{title}”相关题目，第一步应优先做什么？",
                "先明确题目条件和本节核心概念，再选择对应方法。",
            )
        ],
        "challenge": [
            question(
                f"复盘本节“{title}”时，哪种学习方式最可靠？",
                "用题目条件逐步推导并检查每一步，而不是跳步猜结论。",
            )
        ],
    }


def _set_practice_generation_state(
    session: ClassroomSession, status: str, error: str | None = None
) -> None:
    verification = dict(session.verification or {})
    verification["practice_generation"] = {"status": status, "error": error}
    session.verification = verification


_active_practice_generations: set[str] = set()


async def _run_practice_generation(session_id: str) -> None:
    """独立补齐练习题；ready 课堂不因练习慢而不可进入。"""
    if session_id in _active_practice_generations:
        return
    _active_practice_generations.add(session_id)
    try:
        async with background_session_factory() as db:
            session = await db.get(ClassroomSession, uuid.UUID(session_id))
            if session is None or session.deleted_at is not None or session.status != "ready":
                return
            practice = await _gen_practice(
                session.title, list(session.outlines or []), list(session.slides or [])
            )
            state, error = "ready", None
            if practice is None:
                practice = _fallback_practice(
                    session.title, list(session.outlines or []), list(session.slides or [])
                )
                state, error = "fallback", "AI 练习生成暂不可用，已切换为本课保底复习题。"
            session.practice = practice
            session.practice_stats = {}
            _set_practice_generation_state(session, state, error)
            await db.commit()
            _emit(session_id, "practice", {"practice": practice, "status": state, "error": error})
    except Exception as e:
        logger.error(
            "classroom_practice_generation_failed", session_id=session_id, error=str(e)[:200]
        )
        try:
            async with background_session_factory() as db:
                session = await db.get(ClassroomSession, uuid.UUID(session_id))
                if session:
                    _set_practice_generation_state(session, "failed", str(e)[:200])
                    await db.commit()
            _emit(
                session_id,
                "practice",
                {"practice": None, "status": "failed", "error": str(e)[:200]},
            )
        except Exception:
            pass
    finally:
        _active_practice_generations.discard(session_id)


async def _gen_single_slide(
    outline: dict,
    *,
    knowledge_cards: str,
    evidence: dict,
    coordinate_witness: dict | None,
    user_id: str,
    db: AsyncSession,
) -> tuple[dict, str, str]:
    """生成单页内容 + 数学验证闭环 + 图形附加（页间无依赖，可并发执行）。

    返回 (slide_dict, vr_status, vr_detail)。db 仅用于 GeoGebra 附加的
    短事务，由调用方提供独立会话（并发安全）。
    """
    from app.domains.classroom.math_verifier import verify_slide

    content = await _gen_slide_content(outline, knowledge_cards)
    slide_dict = {
        "order": outline["order"],
        "title": outline["title"],
        "subtitle": outline.get("subtitle") or "",
        "kp_code": outline.get("kp_code") or "",
        "minutes": outline.get("minutes") or 3,
        "blocks": content["blocks"],
        "narration": content["narration"],
        "key_points": outline.get("key_points") or [],
        # V5：大纲硬注入的强制字段随页持久化，前端/验收/完整度门直接读取
        "required_blocks": list(outline.get("required_blocks") or []),
        "figure_kind": str(outline.get("figure_kind") or "none"),
        "source_conditions": list(outline.get("source_conditions") or []),
        # V6：页级断言汇总（由 _v6_postprocess 保证数量下限）
        "math_claims": dict(content.get("math_claims") or {}),
        "geometry_claims": dict(content.get("geometry_claims") or {}),
        "source_evidence": {
            "status": evidence["status"],
            "citations": evidence["citations"],
        },
    }
    # 确定性坐标见证来自 OCR 已确认条件，并已由现有 math_verifier 复核；
    # 仅覆盖需要空间图形的页面，正文论证仍由模型基于这份见证生成。
    slide_dict["geometry_claims"] = prefer_verified_coordinate_witness(
        slide_dict["geometry_claims"], coordinate_witness
    )

    # 数学验证（LaTeX 结构 + 结构化断言自洽校验）。
    # 失败页自动重试（最多 2 次）：LLM 一次性算术/坐标笔误是随机噪声，
    # 重试不注入任何答案，验证语义不变（仍由通用验证器裁定）。
    vr = None
    verification_feedback = ""
    for attempt in range(3):
        if attempt:
            content = await _gen_slide_content(
                outline,
                knowledge_cards,
                verification_feedback=verification_feedback,
            )
            slide_dict["blocks"] = content["blocks"]
            slide_dict["narration"] = content["narration"]
            slide_dict["math_claims"] = dict(content.get("math_claims") or {})
            slide_dict["geometry_claims"] = dict(content.get("geometry_claims") or {})
            slide_dict["geometry_claims"] = prefer_verified_coordinate_witness(
                slide_dict["geometry_claims"], coordinate_witness
            )
        vr = verify_slide(slide_dict)
        if vr["status"] != "failed":
            break
        verification_feedback = str(vr.get("detail") or "数学验证失败，请逐条复核题设。")

    # 多类型算术校准（distance / dihedral / tangent_point / inner_point / distance_max）
    # 按坐标表/公式独立计算出的校准值写回 geometry_claims，透明记录
    gc = slide_dict.get("geometry_claims")
    if isinstance(gc, dict):
        # 优先使用 autofixes 列表（V5+ 多类型）
        fix_list = vr.get("autofixes")
        if isinstance(fix_list, list) and fix_list:
            for fx in fix_list:
                t = fx.get("type")
                if t == "distance":
                    dist = gc.get("distance")
                    if isinstance(dist, dict):
                        dist["value"] = fx["value"]
                        dist["autofixed"] = True
                        if fx.get("note"):
                            dist["autofix_note"] = fx["note"]
                elif t == "dihedral":
                    dist = gc.get("dihedral")
                    if isinstance(dist, dict):
                        dist["value"] = fx["value"]
                        dist["autofixed"] = True
                        if fx.get("note"):
                            dist["autofix_note"] = fx["note"]
                elif t == "tangent_point":
                    tp = gc.get("tangent_point")
                    if isinstance(tp, dict):
                        tp["x"] = fx["x"]
                        tp["y"] = fx["y"]
                        tp["autofixed"] = True
                        if fx.get("note"):
                            tp["autofix_note"] = fx["note"]
                elif t == "inner_point":
                    ip = gc.get("inner_point")
                    if isinstance(ip, dict):
                        ip["x"] = fx["x"]
                        ip["y"] = fx["y"]
                        ip["autofixed"] = True
                        if fx.get("note"):
                            ip["autofix_note"] = fx["note"]
                elif t == "distance_max":
                    dm = gc.get("distance_max")
                    if isinstance(dm, dict):
                        dm["value"] = fx["value"]
                        dm["autofixed"] = True
                        if fx.get("note"):
                            dm["autofix_note"] = fx["note"]
        # 兼容：老调用方可能只返回单个 autofix（distance 类型）
        elif vr.get("autofix"):
            dist = gc.get("distance")
            if isinstance(dist, dict):
                dist["value"] = vr["autofix"]["value"]
                dist["autofixed"] = True
    slide_dict["verification_result"] = {
        "status": vr["status"],
        "detail": vr.get("detail", ""),
    }
    # 复用现有 GeoGebra（MathMover 同类）执行器：只把已通过数学验证、
    # 且有教材出处的页面交给图形生成器；任何失败都不补默认图。
    slide_dict["visual_generation"] = await _attach_grounded_geogebra_visual(
        slide_dict,
        outline=outline,
        evidence=evidence,
        db=db,
        user_id=user_id,
    )
    attach_textbook_association_when_no_visual(
        slide_dict,
        evidence,
        visual_generation=str(slide_dict["visual_generation"]),
    )
    return slide_dict, str(vr["status"]), str(vr.get("detail", ""))


_REGEN_PENDING_TTL_SECONDS = 600  # 单页重生成 pending 锁 TTL（= 8min 看门狗 + 余量）
# 进程内"正在生成"登记：防同一会话被重复拉起；配合详情接口的 auto-resume，
# 进程重启后用户一刷新页面即可自动续生成（对齐 OpenMAIC 断点续生成模式）。
_active_generations: set[str] = set()
_RESUME_STALE_SECONDS = 90  # updated_at 超过此秒数未变且任务不在册 → 判定为中断


def _emit(session_id: str, event_type: str, data: dict | None = None) -> None:
    """向 SSE 订阅者推送生成事件（OpenMAIC 式进度可见化；无订阅者时零成本）。"""
    try:
        publish_session_event(session_id, event_type, data)
    except Exception:  # 事件推送绝不拖垮生成管线
        logger.warning("classroom_event_publish_failed", session_id=session_id, type=event_type)


async def _run_generation(session_id: str) -> None:
    """后台生成任务（两段式；OpenMAIC 语义：topic 即可生成，course 为可选上下文）

    通用链路：所有题目（无论主题）都经过相同的「大纲→逐页内容→数学验证」流程。
    无金标准特例分支、无关键词路由、无预置内容放行。
    断点续生成：若 outlines 已持久化（上次进程重启被中断），跳过 Step1 直接重出内容页。
    """
    if session_id in _active_generations:
        return
    _active_generations.add(session_id)
    try:
        await _run_generation_inner(session_id)
    finally:
        _active_generations.discard(session_id)


async def _run_generation_inner(session_id: str) -> None:
    try:
        async with background_session_factory() as db:
            session = await db.get(ClassroomSession, uuid.UUID(session_id))
            if session is None:
                return
            _emit(session_id, "status", {"status": "generating", "stage": "outline"})

            # 可选课程上下文（OpenMAIC：不依赖课程预处理，有则增强，无则按主题生成）
            course = None
            if session.course_id is not None:
                course = await db.get(Course, session.course_id)
                if course is None or course.deleted_at:
                    session.status = "failed"
                    session.error = "课程不存在"
                    await db.commit()
                    _emit(session_id, "status", {"status": "failed", "error": session.error})
                    return
                if course.status != COURSE_STATUS_READY:
                    session.status = "failed"
                    session.error = "课程预处理未完成，请稍后再试"
                    await db.commit()
                    _emit(session_id, "status", {"status": "failed", "error": session.error})
                    return

            source_ref = dict(session.source_ref or {})
            parsed_source_text = await _load_parsed_source_text(source_ref, db)
            generation_topic = resolve_generation_topic(session.title, parsed_source_text)
            parse_quality = source_ref.get("parse_quality")
            parse_quality = parse_quality if isinstance(parse_quality, dict) else None
            condition_ledger = [
                str(item).strip()[:240]
                for item in ((parse_quality or {}).get("conditions") or [])
                if str(item).strip()
            ][:12]
            diagram_entities = (
                (parse_quality or {}).get("diagram_entities")
                or (parse_quality or {}).get("diagramEntities")
                or {}
            )
            if isinstance(diagram_entities, dict):
                diagram_entities = diagram_entities.get("items") or []
            if isinstance(diagram_entities, list):
                condition_ledger = list(
                    dict.fromkeys(
                        condition_ledger
                        + [
                            str(item).strip()[:240]
                            for item in diagram_entities
                            if str(item).strip()
                        ]
                    )
                )[:16]
            coordinate_facts = derive_explicit_length_facts(condition_ledger)
            coordinate_witness = build_right_trapezoid_pyramid_coordinate_witness(
                condition_ledger, coordinate_facts
            )
            if coordinate_witness is not None:
                from app.domains.classroom.math_verifier import verify_geometry_claims

                if verify_geometry_claims(coordinate_witness)["status"] != "verified":
                    coordinate_witness = None
            retrieval_plan = build_classroom_retrieval_plan(generation_topic, parse_quality)
            evidence = await retrieve_classroom_evidence(retrieval_plan, db=db)
            session.source_ref = attach_classroom_grounding(source_ref, evidence)
            if evidence["status"] == "blocked":
                session.status = "failed"
                session.error = "题图存在未确认条件，请核对题意后重新生成课堂"
                session.verification = {
                    "overall": "needs_confirmation",
                    "per_slide": [],
                    "textbook_evidence": {
                        "status": evidence["status"],
                        "citations": evidence["citations"],
                        "block_reason": evidence.get("block_reason"),
                    },
                }
                await db.commit()
                _emit(
                    session_id,
                    "status",
                    {
                        "status": "failed",
                        "error": session.error,
                        "verification_overall": "needs_confirmation",
                    },
                )
                logger.info(
                    "classroom_photo_conditions_need_confirmation",
                    session_id=session_id,
                    block_reason=evidence.get("block_reason"),
                )
                return

            # kp 白名单表（只允许从高中数学知识点表选择）
            kp_rows = await db.execute(select(KnowledgePoint.code, KnowledgePoint.name))
            kp_table = "\n".join(f"{code}: {name}" for code, name in kp_rows.all())
            cards = ((course.preprocess_result or {}) if course else {}).get(
                "knowledge_cards"
            ) or []
            course_cards = "\n".join(
                f"- {c.get('title')}: {c.get('content')}"
                for c in cards
                if c.get("title") or c.get("content")
            )
            knowledge_cards = (
                "\n\n".join(
                    part
                    for part in [
                        course_cards,
                        (
                            "【已确认题目条件：逐条保留，不得替换、简化或补造】\n- "
                            + "\n- ".join(condition_ledger)
                            if condition_ledger
                            else ""
                        ),
                        (
                            "【后端已验证的坐标建系见证：只能据此坐标讲解和绘图，不得改写】\n"
                            + str(coordinate_witness)
                            if coordinate_witness is not None
                            else ""
                        ),
                        evidence.get("prompt_context") or "",
                    ]
                    if part
                )
                or "（本课未关联教材或未检索到教材片段：请直接依据题目条件与高中数学通用知识讲解，确保数学正确即可，无需引用教材出处。）"
            )

            # Step1 大纲（OpenMAIC：无课程时以主题为纲直接生成）
            # 断点续生成：上次中断时大纲已持久化 → 复用大纲直接重出内容页
            existing_outlines = session.outlines or []
            if existing_outlines:
                outlines = existing_outlines
                title = session.title
                session.slide_count = len(outlines)
                logger.info(
                    "classroom_generation_resumed",
                    session_id=session_id,
                    outlines=len(outlines),
                )
            else:
                # 在 LLM 重写 session.title 前保存原始输入，供 RAG 引用与题目追溯。
                _, outlines, title = await _gen_outline(
                    course,
                    session.slide_count,
                    session.mode,
                    kp_table,
                    knowledge_cards,
                    topic=generation_topic or (session.title if course is None else ""),
                    condition_ledger=condition_ledger,
                )
                session.status = "generating"
                session.outlines = outlines
                session.title = title
                # 大纲实际页数回写（LLM 可能与请求数±1），保证进度分母一致
                session.slide_count = len(outlines)
            session.slides = []  # 续跑时清掉上次的部分页，逐页重建
            session.engine = "openmaic_rag_v1"
            await db.commit()
            # OpenMAIC 式进度可见化：大纲定稿立即推送（前端逐条动画展示，不等首页内容）
            _emit(session_id, "title", {"title": session.title})
            _emit(
                session_id,
                "outlines",
                {"outlines": outlines, "slide_count": len(outlines)},
            )
            _emit(session_id, "status", {"status": "generating", "stage": "content"})

            # Step2 并发生成各页：页间相互独立（各自的内容生成 + 数学验证闭环），
            # 信号量限流防止打爆免费档并发上限；结果按 order 保序渐进落库，
            # 前端轮询可见逐页出现（与旧串行版进度语义一致，耗时约降 3~4 倍）。
            import asyncio as _asyncio

            slides: list[dict | None] = [None] * len(outlines)
            per_slide_verification: list[dict | None] = [None] * len(outlines)
            has_failed = False
            failed_detail = ""

            gen_sem = _asyncio.Semaphore(8)

            async def _gen_page_task(idx: int, page_outline: dict):
                async with gen_sem:
                    try:
                        async with background_session_factory() as pdb:
                            # 任务级看门狗：网络半开/系统休眠唤醒后 httpx 读超时可能
                            # 失效，单页 8 分钟强制判失败，编排绝不无限挂起。
                            slide_dict, vstatus, vdetail = await asyncio.wait_for(
                                _gen_single_slide(
                                    page_outline,
                                    knowledge_cards=knowledge_cards,
                                    evidence=evidence,
                                    coordinate_witness=coordinate_witness,
                                    user_id=str(session.user_id),
                                    db=pdb,
                                ),
                                timeout=480.0,
                            )
                            return idx, slide_dict, vstatus, vdetail
                    except TimeoutError:
                        logger.warning(
                            "classroom_page_task_timeout",
                            order=page_outline.get("order"),
                        )
                        fallback = {
                            "order": page_outline["order"],
                            "title": page_outline.get("title")
                            or f"第 {page_outline.get('order')} 页",
                            "subtitle": page_outline.get("subtitle") or "",
                            "kp_code": page_outline.get("kp_code") or "",
                            "minutes": page_outline.get("minutes") or 3,
                            "blocks": [],
                            "narration": "",
                            "key_points": page_outline.get("key_points") or [],
                            "required_blocks": list(page_outline.get("required_blocks") or []),
                            "figure_kind": str(page_outline.get("figure_kind") or "none"),
                            "source_conditions": list(page_outline.get("source_conditions") or []),
                            "math_claims": {},
                            "geometry_claims": {},
                            "source_evidence": {
                                "status": evidence["status"],
                                "citations": evidence["citations"],
                            },
                            "verification_result": {
                                "status": "failed",
                                "detail": "页面生成超时（AI 通道无响应），请重新生成本页",
                            },
                        }
                        return idx, fallback, "failed", "页面生成超时（AI 通道无响应）"
                    except Exception as page_exc:
                        # 单页彻底失败不拖垮任务编排：记为 failed 验证页，终态统一判失败
                        logger.warning(
                            "classroom_page_task_failed",
                            order=page_outline.get("order"),
                            error=str(page_exc)[:200],
                        )
                        fallback = {
                            "order": page_outline["order"],
                            "title": page_outline.get("title")
                            or f"第 {page_outline.get('order')} 页",
                            "subtitle": page_outline.get("subtitle") or "",
                            "kp_code": page_outline.get("kp_code") or "",
                            "minutes": page_outline.get("minutes") or 3,
                            "blocks": [],
                            "narration": "",
                            "key_points": page_outline.get("key_points") or [],
                            "required_blocks": list(page_outline.get("required_blocks") or []),
                            "figure_kind": str(page_outline.get("figure_kind") or "none"),
                            "source_conditions": list(page_outline.get("source_conditions") or []),
                            "math_claims": {},
                            "geometry_claims": {},
                            "source_evidence": {
                                "status": evidence["status"],
                                "citations": evidence["citations"],
                            },
                            "verification_result": {
                                "status": "failed",
                                "detail": f"页面生成异常：{str(page_exc)[:120]}",
                            },
                        }
                        return idx, fallback, "failed", f"页面生成异常：{str(page_exc)[:120]}"

            done_prefix = 0
            finished: dict[int, tuple[dict, str, str]] = {}
            for coro in _asyncio.as_completed(
                [_gen_page_task(i, o) for i, o in enumerate(outlines)]
            ):
                idx, slide_dict, vstatus, vdetail = await coro
                finished[idx] = (slide_dict, vstatus, vdetail)
                # 单页完成即推送（OpenMAIC scene 逐页入列语义）：
                # SSE 订阅者立刻看到这一页，DB 仍按前缀有序落库（契约不变）
                _emit(
                    session_id,
                    "slide",
                    {
                        "index": idx,
                        "order": slide_dict.get("order", idx + 1),
                        "completed": len(finished),
                        "total": len(outlines),
                        "slide": slide_dict,
                    },
                )
                # 前缀齐一页落一页：slides 数组始终按 order 有序（前端按序号取页）
                while done_prefix in finished:
                    sdict, vst, vdt = finished.pop(done_prefix)
                    slides[done_prefix] = sdict
                    per_slide_verification[done_prefix] = {
                        "idx": sdict["order"],
                        "status": vst,
                        "detail": vdt,
                    }
                    if vst == "failed" and not has_failed:
                        has_failed = True
                        failed_detail = f"第{sdict['order']}页验证失败：{vdt}"
                    done_prefix += 1
                session.slides = [s for s in slides if s is not None]  # 新 list 触发 JSONB 变更检测
                session.verification = {
                    "overall": (
                        "failed"
                        if has_failed
                        else (
                            "needs_review"
                            if any(
                                v["status"] == "needs_review" for v in per_slide_verification if v
                            )
                            else "verified"
                        )
                    ),
                    "per_slide": [v for v in per_slide_verification if v],
                    "textbook_evidence": {
                        "status": evidence["status"],
                        "citations": evidence["citations"],
                    },
                }
                await db.commit()

            if has_failed:
                # 过半页面失败 = 生成通道整体异常（如 DNS/供应商故障），
                # 不再以"ready + 满屏失败页"呈现，直接判失败并给出可行动文案；
                # 少量失败页仍走 OpenMAIC「失败页重试」模式（ready + 单页重出）。
                failed_pages = sum(
                    1 for v in per_slide_verification if v and v.get("status") == "failed"
                )
                if failed_pages * 2 >= len(slides):
                    session.status = "failed"
                    session.error = (
                        f"AI 生成通道异常：{failed_pages}/{len(slides)} 页内容未能生成"
                        "（多为网络波动），请稍后重新生成"
                    )
                    await db.commit()
                    _emit(session_id, "status", {"status": "failed", "error": session.error})
                    logger.warning(
                        "classroom_session_failed_provider_outage",
                        session_id=session_id,
                        failed_pages=failed_pages,
                        total=len(slides),
                    )
                    return
                # OpenMAIC「失败页重试」模式：单页验证失败不再打死整课。
                # 课程照常进入 ready，失败页保留标记（per_slide.status=failed），
                # 前端在该页显著提示并提供「重新生成本页」入口。
                logger.warning(
                    "classroom_session_ready_with_failed_pages",
                    session_id=session_id,
                    detail=failed_detail,
                )

            # ===== 内容完整度门（惩罚"全text/低完整度"的投机课堂）=====
            import math as _pmc

            sc = len(slides)
            min_fig = _pmc.ceil(sc * 0.4)
            min_ex = _pmc.ceil(sc * 0.2)
            min_lat = _pmc.ceil(sc * 0.3)
            completeness_issues = []
            fig_pages = 0
            ex_pages = 0
            lat_pages = 0
            incomplete_body_pages = 0
            for idx, sld in enumerate(slides):
                order = sld.get("order", idx + 1)
                blocks = sld.get("blocks") or []
                kinds = {b.get("kind") for b in blocks if isinstance(b, dict)}
                has_geometry = "geometry" in kinds or "ggb" in kinds
                has_plot2d = "plot2d" in kinds
                has_latex = "latex" in kinds
                has_example = "example" in kinds
                if has_geometry or has_plot2d:
                    fig_pages += 1
                if has_example:
                    ex_pages += 1
                if has_latex:
                    lat_pages += 1
                is_body = order != 1 and order != sc
                if is_body:
                    # 正文页：必须 text + 至少2种非text
                    non_text_count = len(kinds - {"text"})
                    if ("text" not in kinds) or non_text_count < 2:
                        incomplete_body_pages += 1
                        completeness_issues.append(f"第{order}页正文缺非text种类（当前{kinds}）")
            if fig_pages < min_fig:
                completeness_issues.append(f"图形页不足：{fig_pages}/{min_fig}")
            if ex_pages < min_ex:
                completeness_issues.append(f"例题页不足：{ex_pages}/{min_ex}")
            if lat_pages < min_lat:
                completeness_issues.append(f"公式页不足：{lat_pages}/{min_lat}")
            if incomplete_body_pages > 0:
                completeness_issues.append(f"{incomplete_body_pages}页正文块种类不足")
            content_completeness_pass = len(completeness_issues) == 0
            # ===== 与单页数学验证结果合并 =====
            per_slide_needs_review = any(
                v["status"] == "needs_review" for v in per_slide_verification
            )
            if not content_completeness_pass:
                # 在per_slide末尾追加一条完整度说明，便于前端展示
                per_slide_verification = list(per_slide_verification) + [
                    {
                        "idx": 0,
                        "status": "needs_review",
                        "detail": "内容完整度：" + "；".join(completeness_issues),
                    }
                ]

            # 先宣布 ready 并落库（用户立刻进课堂，OpenMAIC 语义：首页就绪即可开课），
            # 分层练习随后补生成、补推送——练习失败/耗时不再拖慢进课堂。
            session.status = "ready"
            session.generated_at = datetime.now(UTC)
            session.error = None
            overall = (
                "failed"
                if has_failed
                else (
                    "needs_review"
                    if (per_slide_needs_review or not content_completeness_pass)
                    else "verified"
                )
            )
            session.verification = {
                "overall": overall,
                "per_slide": per_slide_verification,
                "content_completeness": {
                    "pass": content_completeness_pass,
                    "issues": completeness_issues,
                    "figure_pages": fig_pages,
                    "example_pages": ex_pages,
                    "latex_pages": lat_pages,
                    "min_figure_pages": min_fig,
                    "min_example_pages": min_ex,
                    "min_latex_pages": min_lat,
                },
                "textbook_evidence": {
                    "status": evidence["status"],
                    "citations": evidence["citations"],
                },
            }
            await db.commit()
            _emit(
                session_id,
                "status",
                {"status": "ready", "verification_overall": overall},
            )
            logger.info("classroom_session_ready", session_id=session_id, slides=len(slides))

            # 练习异步补齐：课堂先可进入；AI 失联时仍会落本课保底练习，绝不无限等待。
            _set_practice_generation_state(session, "pending")
            await db.commit()
            _emit(session_id, "status", {"status": "ready", "stage": "practice"})
            asyncio.get_running_loop().create_task(_run_practice_generation(session_id))
    except Exception as e:
        logger.error("classroom_generation_failed", session_id=session_id, error=str(e)[:300])
        _emit(session_id, "status", {"status": "failed", "error": str(e)[:300]})
        try:
            async with background_session_factory() as db:
                s = await db.get(ClassroomSession, uuid.UUID(session_id))
                if s:
                    s.status = "failed"
                    s.error = str(e)[:300]
                    await db.commit()
        except Exception:
            pass


# ==================== 端点 ====================


@router.post("/sessions")
async def create_session(
    req: SessionCreateRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建 AI 数学课堂会话（OpenMAIC 语义：topic 即可；course_id 可选增强）"""

    course = None
    if req.course_id is not None:
        course = await db.get(Course, req.course_id)
        if course is None or course.deleted_at:
            return ApiResponse(code=40400, message="课程不存在", data=None)
        if course.status != COURSE_STATUS_READY:
            return ApiResponse(code=40901, message="课程预处理未完成，请稍后再试", data=None)

    # 无课程时以 topic 为标题（自由生成，对齐 OpenMAIC 首页输入即生成）
    session = ClassroomSession(
        course_id=req.course_id,
        user_id=uuid.UUID(user["sub"]),
        title=(course.title if course else (req.topic or "").strip())[:180] or "数学课堂",
        mode=req.mode,
        slide_count=req.slide_count,
        status="generating",
        source_type=req.source_type,
        source_ref=req.source_ref,
    )
    db.add(session)
    await db.commit()
    # 注意：不用 BackgroundTasks——本环境已证实其不触发（见 course_router 同款注释），
    # 任务型生成统一 create_task，请求返回即开跑（修"点击生成后干等 ~90s"）。
    import asyncio as _aio

    _aio.get_running_loop().create_task(_run_generation(str(session.id)))
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "session_id": str(session.id),
            "course_id": str(course.id) if course else None,
            "title": session.title,
            "slide_count": session.slide_count,
            "mode": session.mode,
            "status": "generating",
        },
    )


@router.get("/sessions")
async def list_sessions(
    status: str | None = None,
    source_type: str | None = None,
    kp_code: str | None = None,
    date_from: str | None = None,
    limit: int = 50,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """我的课堂会话列表（历史闭环：四态展示 + 筛选 + 排除软删除）。

    筛选维度：status（generating/ready/failed）、source_type（topic/photo/file）、
    kp_code（知识点）、date_from（ISO 日期，仅返回此日期之后）。
    """
    user_id = uuid.UUID(user["sub"])
    stmt = (
        select(ClassroomSession)
        .where(ClassroomSession.user_id == user_id)
        .where(ClassroomSession.deleted_at.is_(None))
    )
    if status:
        stmt = stmt.where(ClassroomSession.status == status)
    if source_type:
        stmt = stmt.where(ClassroomSession.source_type == source_type)
    if date_from:
        try:
            from datetime import datetime as _dt

            since = _dt.fromisoformat(date_from)
            stmt = stmt.where(ClassroomSession.created_at >= since)
        except ValueError:
            pass
    stmt = stmt.order_by(ClassroomSession.created_at.desc()).limit(min(max(limit, 1), 100))
    rows = await db.execute(stmt)
    items = []
    for s in rows.scalars().all():
        # kp_code 筛选：knowledge_points JSONB 数组包含该 code
        if kp_code:
            kps = s.knowledge_points or []
            if kp_code not in kps:
                continue
        items.append(
            {
                "session_id": str(s.id),
                "course_id": str(s.course_id) if s.course_id else None,
                "title": s.title,
                "mode": s.mode,
                "slide_count": s.slide_count,
                "status": s.status,
                "error": s.error,
                "source_type": s.source_type,
                "source_ref": s.source_ref,
                "knowledge_points": s.knowledge_points or [],
                "slides_generated": len(s.slides or []),
                "verification_overall": (
                    (s.verification or {}).get("overall") if s.verification else None
                ),
                "progress": s.progress or {},
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "generated_at": s.generated_at.isoformat() if s.generated_at else None,
            }
        )
    return ApiResponse(code=0, message="ok", data={"total": len(items), "items": items})


@router.get("/sessions/{session_id}/openmaic-document")
async def get_openmaic_document(
    session_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """导出本节课的 OpenMAIC ``{stage, scenes}`` 文档，不重新生成内容。"""
    session = await db.get(ClassroomSession, session_id)
    if session is None or str(session.user_id) != user["sub"] or session.deleted_at is not None:
        return ApiResponse(code=40400, message="会话不存在", data=None)
    evidence = (session.verification or {}).get("textbook_evidence") or {}
    return ApiResponse(
        code=0,
        message="ok",
        data=build_openmaic_document(
            session_id=str(session.id),
            title=session.title,
            mode=session.mode,
            slides=list(session.slides or []),
            evidence=evidence,
        ),
    )


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """会话详情（outlines + slides + 验证 + 进度 + 笔记 + 问答摘要 + 来源）"""
    session = await db.get(ClassroomSession, session_id)
    if session is None or str(session.user_id) != user["sub"]:
        return ApiResponse(code=40400, message="会话不存在", data=None)
    if session.deleted_at is not None:
        return ApiResponse(code=40400, message="会话已删除", data=None)
    # 断点续生成（OpenMAIC auto-resume）：会话卡在 generating、任务不在册且
    # 久未更新（如进程重启导致后台任务丢失）→ 用户一刷新即自动续跑。
    if session.status == "generating" and str(session.id) not in _active_generations:
        updated = getattr(session, "updated_at", None)
        stale = (
            updated is None or (datetime.now(UTC) - updated).total_seconds() > _RESUME_STALE_SECONDS
        )
        if stale:
            import asyncio as _aio

            logger.info("classroom_generation_auto_resume", session_id=str(session.id))
            # 只 create_task 不预登记：_run_generation 自登记；预登记会让它自判重复而直接退出
            _aio.get_running_loop().create_task(_run_generation(str(session.id)))
    practice_state = (session.verification or {}).get("practice_generation", {}).get("status")
    if (
        session.status == "ready"
        and session.practice is None
        and practice_state != "failed"
        and str(session.id) not in _active_practice_generations
    ):
        _set_practice_generation_state(session, "pending")
        await db.commit()
        asyncio.get_running_loop().create_task(_run_practice_generation(str(session.id)))
    course = await db.get(Course, session.course_id) if session.course_id else None
    # V4 契约：不再对历史 figure 按标题注入默认 3D 实体（如实返回存储内容）
    slides_out = list(session.slides or [])
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "session_id": str(session.id),
            "course_id": str(session.course_id) if session.course_id else None,
            "course_title": course.title if course else session.title,
            "title": session.title,
            "mode": session.mode,
            "slide_count": session.slide_count,
            "status": session.status,
            "engine": session.engine,
            "error": session.error,
            "outlines": session.outlines or [],
            "slides": slides_out,
            "verification": session.verification or {},
            "practice": session.practice,
            "practice_stats": session.practice_stats or {},
            "progress": session.progress or {},
            "notes": session.notes or "",
            "qa_summary": session.qa_summary or {},
            "knowledge_points": session.knowledge_points or [],
            "source_type": session.source_type,
            "source_ref": session.source_ref,
            "content_version": session.content_version,
            "generated_at": session.generated_at.isoformat() if session.generated_at else None,
            "created_at": session.created_at.isoformat() if session.created_at else None,
        },
    )


@router.get("/sessions/{session_id}/events")
async def stream_session_events(
    session_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """生成进度 SSE（OpenMAIC 生成可见化的推送通道）。

    事件流：status/title/outlines/slide/practice → done。连接即回放历史事件
    （刷新/重连不丢进度），再直播增量；进入终态（ready/failed）且练习事件
    已过或超时后发送 done 关闭。生成任务卡死时走与详情接口一致的 auto-resume。
    """

    def _frame(event_type: str, data: dict | str) -> str:
        import json as _json

        payload = data if isinstance(data, str) else _json.dumps(data, ensure_ascii=False)
        return f"event: {event_type}\ndata: {payload}\n\n"

    session = await db.get(ClassroomSession, session_id)
    if session is None or str(session.user_id) != user["sub"]:
        return ApiResponse(code=40400, message="会话不存在", data=None)
    if session.deleted_at is not None:
        return ApiResponse(code=40400, message="会话已删除", data=None)

    # 与详情接口同款 auto-resume：SSE 重连也能救活丢失的后台任务
    if session.status == "generating" and str(session.id) not in _active_generations:
        updated = getattr(session, "updated_at", None)
        stale = (
            updated is None or (datetime.now(UTC) - updated).total_seconds() > _RESUME_STALE_SECONDS
        )
        if stale:
            logger.info("classroom_generation_auto_resume_sse", session_id=str(session.id))
            asyncio.get_running_loop().create_task(_run_generation(str(session.id)))

    sid = str(session.id)
    history, queue = subscribe_session_events(sid)

    async def _gen():
        try:
            for ev in history:
                yield _frame(ev["type"], ev["data"])
            terminal_seen = any(
                e["type"] == "status" and e["data"].get("status") in ("ready", "failed")
                for e in history
            )
            practice_seen = any(e["type"] == "practice" for e in history)
            idle_deadline = 180.0  # 终态后等练习事件的最长窗口
            idle_seconds = 0.0
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    idle_seconds += 15.0
                    if terminal_seen and idle_seconds >= idle_deadline:
                        break
                    yield ": ping\n\n"
                    if idle_seconds >= 900.0:  # 生成端彻底失联的兜底断开
                        break
                    continue
                idle_seconds = 0.0
                if ev["type"] == "done":
                    break
                if ev["type"] == "status" and ev["data"].get("status") in ("ready", "failed"):
                    terminal_seen = True
                    if ev["data"].get("status") == "failed":
                        yield _frame(ev["type"], ev["data"])
                        break
                if ev["type"] == "practice":
                    practice_seen = True
                yield _frame(ev["type"], ev["data"])
                if terminal_seen and practice_seen:
                    break
            yield _frame("done", {})
        finally:
            unsubscribe_session_events(sid, queue)

    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ==================== 历史课堂闭环端点（进度/笔记/克隆/软删除） ====================


class ProgressUpdateRequest(BaseModel):
    """学习进度更新（继续学习闭环）。"""

    slide_index: int | None = None  # 当前页码（0-based）
    page_check: dict | None = None  # {idx: "ok"|"again"} 当堂检测作答
    completed_at: str | None = None  # ISO 时间，完成时刻


class NotesUpdateRequest(BaseModel):
    """笔记更新（服务端持久化，Markdown/纯文本）。"""

    notes: str = Field(default="", max_length=20000)


class QaAppendRequest(BaseModel):
    """问答追加（助教抽屉问答写入课堂历史）。"""

    role: str = Field(pattern="^(user|assistant)")
    text: str = Field(default="", max_length=2000)
    error_summary: str | None = None  # 错因摘要（可选）


@router.patch("/sessions/{session_id}/progress")
async def update_progress(
    session_id: uuid.UUID,
    req: ProgressUpdateRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新学习进度（继续学习闭环：断点续学 + 当堂检测记录）。"""
    session = await db.get(ClassroomSession, session_id)
    if session is None or str(session.user_id) != user["sub"]:
        return ApiResponse(code=40400, message="会话不存在", data=None)
    if session.deleted_at is not None:
        return ApiResponse(code=40400, message="会话已删除", data=None)
    progress = dict(session.progress or {})
    if req.slide_index is not None:
        progress["slide_index"] = req.slide_index
    if req.page_check is not None:
        pc = dict(progress.get("page_check") or {})
        pc.update(req.page_check)
        progress["page_check"] = pc
    if req.completed_at is not None:
        progress["completed_at"] = req.completed_at
    session.progress = progress
    await db.commit()
    return ApiResponse(code=0, message="ok", data={"progress": progress})


@router.patch("/sessions/{session_id}/notes")
async def update_notes(
    session_id: uuid.UUID,
    req: NotesUpdateRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新课堂笔记（服务端持久化，浏览器本地仅作离线草稿）。"""
    session = await db.get(ClassroomSession, session_id)
    if session is None or str(session.user_id) != user["sub"]:
        return ApiResponse(code=40400, message="会话不存在", data=None)
    if session.deleted_at is not None:
        return ApiResponse(code=40400, message="会话已删除", data=None)
    session.notes = req.notes
    await db.commit()
    return ApiResponse(code=0, message="ok", data={"notes": session.notes})


@router.post("/sessions/{session_id}/qa")
async def append_qa(
    session_id: uuid.UUID,
    req: QaAppendRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """追加问答记录到课堂历史（助教抽屉问答 + 错因摘要闭环）。"""
    session = await db.get(ClassroomSession, session_id)
    if session is None or str(session.user_id) != user["sub"]:
        return ApiResponse(code=40400, message="会话不存在", data=None)
    if session.deleted_at is not None:
        return ApiResponse(code=40400, message="会话已删除", data=None)
    qa = dict(session.qa_summary or {})
    messages = list(qa.get("messages") or [])
    messages.append({"role": req.role, "text": req.text, "at": datetime.now(UTC).isoformat()})
    qa["messages"] = messages[-200:]  # 保留最近 200 条
    if req.error_summary is not None:
        qa["error_summary"] = req.error_summary[:500]
    session.qa_summary = qa
    await db.commit()
    return ApiResponse(code=0, message="ok", data={"qa_summary": qa})


class PracticeAnswerRequest(BaseModel):
    """分层练习作答上报（正确率环形图数据源）。"""

    tier: str = Field(pattern="^(basic|advanced|challenge)$")
    question_index: int = Field(ge=0, le=19)
    correct: bool


@router.post("/sessions/{session_id}/practice-answer")
async def answer_practice(
    session_id: uuid.UUID,
    req: PracticeAnswerRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上报分层练习作答结果，累加 {tier: {total, correct}} 统计。"""
    session = await db.get(ClassroomSession, session_id)
    if session is None or str(session.user_id) != user["sub"]:
        return ApiResponse(code=40400, message="会话不存在", data=None)
    if session.deleted_at is not None:
        return ApiResponse(code=40400, message="会话已删除", data=None)
    stats = dict(session.practice_stats or {})
    tier_stats = dict(stats.get(req.tier) or {})
    tier_stats["total"] = int(tier_stats.get("total") or 0) + 1
    if req.correct:
        tier_stats["correct"] = int(tier_stats.get("correct") or 0) + 1
    stats[req.tier] = tier_stats
    session.practice_stats = stats
    await db.commit()
    return ApiResponse(code=0, message="ok", data={"practice_stats": stats})


@router.post("/sessions/{session_id}/slides/{order}/regenerate")
async def regenerate_slide(
    session_id: uuid.UUID,
    order: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """单页重新生成（OpenMAIC「失败页重试」模式）：不重跑整课，只重出指定页。

    复用该页大纲（required_blocks/figure_kind/source_conditions）重新走
    内容生成 + 数学验证闭环；完成后原位替换 slides[order-1] 并同步验证汇总。
    """
    session = await db.get(ClassroomSession, session_id)
    if session is None or str(session.user_id) != user["sub"]:
        return ApiResponse(code=40400, message="会话不存在", data=None)
    if session.deleted_at is not None:
        return ApiResponse(code=40400, message="会话已删除", data=None)
    if session.status == "generating":
        return ApiResponse(code=40901, message="课堂生成中，请稍后再试", data=None)
    outline = next(
        (o for o in (session.outlines or []) if int(o.get("order") or 0) == order),
        None,
    )
    if outline is None:
        return ApiResponse(code=40400, message="页码不存在", data=None)
    verification = dict(session.verification or {})
    regeneration = dict(verification.get("slide_regeneration") or {})
    # pending 锁带时间戳 + TTL 回收：进程重启/任务挂死留下的孤儿锁
    # 超过 _REGEN_PENDING_TTL_SECONDS 自动失效，页面永不被锁死。
    entry = regeneration.get(str(order)) or {}
    if entry.get("status") == "pending":
        since = entry.get("pending_since")
        if not since:
            # 无时间戳的 pending 必然出自旧版本代码（新代码必写 pending_since）
            # → 视为孤儿锁直接回收；时间损坏（解析失败）才保守拒绝。
            logger.warning(
                "classroom_slide_regen_legacy_lock_reclaimed",
                session_id=str(session.id),
                order=order,
            )
        else:
            try:
                age = (datetime.now(UTC) - datetime.fromisoformat(str(since))).total_seconds()
                if 0 <= age < _REGEN_PENDING_TTL_SECONDS:
                    return ApiResponse(code=40901, message="本页正在重新生成，请稍候", data=None)
                logger.warning(
                    "classroom_slide_regen_stale_lock_reclaimed",
                    session_id=str(session.id),
                    order=order,
                    age_seconds=int(age),
                )
            except ValueError:
                return ApiResponse(code=40901, message="本页正在重新生成，请稍候", data=None)
        logger.warning(
            "classroom_slide_regen_stale_lock_reclaimed",
            session_id=str(session.id),
            order=order,
            age_seconds=entry.get("pending_since"),
        )
    regeneration[str(order)] = {
        "status": "pending",
        "error": None,
        "pending_since": datetime.now(UTC).isoformat(),
    }
    verification["slide_regeneration"] = regeneration
    session.verification = verification
    await db.commit()
    _emit(str(session.id), "slide_regeneration", {"order": order, "status": "pending"})
    # BackgroundTasks 在本环境不触发（同 create_session 注释）→ create_task
    asyncio.get_running_loop().create_task(
        _regen_single_slide_task(str(session.id), order, str(user["sub"]))
    )
    return ApiResponse(code=0, message="ok", data={"status": "regenerating", "order": order})


async def _regen_single_slide_task(session_id: str, order: int, user_id: str) -> None:
    try:
        async with background_session_factory() as db:
            session = await db.get(ClassroomSession, uuid.UUID(session_id))
            if session is None or session.deleted_at is not None:
                return
            outline = next(
                (o for o in (session.outlines or []) if int(o.get("order") or 0) == order),
                None,
            )
            if outline is None:
                return
            ev = (session.verification or {}).get("textbook_evidence") or {}
            evidence = {
                "status": ev.get("status", "unavailable"),
                "citations": ev.get("citations", []),
                "prompt_context": "",
            }
            # 重生成上下文：该页大纲携带的已确认题目条件（无整课知识卡，避免超预算）
            conditions = [
                str(c).strip() for c in (outline.get("source_conditions") or []) if str(c).strip()
            ]
            knowledge_cards = (
                "【已确认题目条件：逐条保留，不得替换或补造】\n- " + "\n- ".join(conditions)
                if conditions
                else ""
            )
            # 重生成同样上看门狗：8 分钟无结果即放弃并广播失败事件
            slide_dict, vstatus, vdetail = await asyncio.wait_for(
                _gen_single_slide(
                    {**outline, "order": order},
                    knowledge_cards=knowledge_cards,
                    evidence=evidence,
                    coordinate_witness=None,
                    user_id=user_id,
                    db=db,
                ),
                timeout=480.0,
            )
            slides = list(session.slides or [])
            idx = order - 1
            if 0 <= idx < len(slides):
                slides[idx] = slide_dict
            else:
                slides.append(slide_dict)
            session.slides = slides
            verification = dict(session.verification or {})
            per_slide = [v for v in (verification.get("per_slide") or []) if v.get("idx") != order]
            per_slide.append({"idx": order, "status": vstatus, "detail": vdetail})
            statuses = {v["status"] for v in per_slide}
            verification["per_slide"] = per_slide
            verification["overall"] = (
                "failed"
                if "failed" in statuses
                else ("needs_review" if "needs_review" in statuses else "verified")
            )
            regeneration = dict(verification.get("slide_regeneration") or {})
            regeneration[str(order)] = {"status": "ready", "error": None}
            verification["slide_regeneration"] = regeneration
            session.verification = verification
            await db.commit()
            _emit(
                session_id,
                "slide_regeneration",
                {"order": order, "status": "ready", "slide": slide_dict},
            )
            logger.info(
                "classroom_slide_regenerated", session_id=session_id, order=order, status=vstatus
            )
    except Exception as e:
        logger.error(
            "classroom_slide_regen_failed", session_id=session_id, order=order, error=str(e)[:200]
        )
        try:
            async with background_session_factory() as db:
                session = await db.get(ClassroomSession, uuid.UUID(session_id))
                if session:
                    verification = dict(session.verification or {})
                    regeneration = dict(verification.get("slide_regeneration") or {})
                    regeneration[str(order)] = {"status": "failed", "error": str(e)[:200]}
                    verification["slide_regeneration"] = regeneration
                    session.verification = verification
                    await db.commit()
            _emit(
                session_id,
                "slide_regeneration",
                {"order": order, "status": "failed", "error": str(e)[:200]},
            )
        except Exception:
            pass


@router.post("/sessions/{session_id}/clone")
async def clone_session(
    session_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """复制为新课（保留原课内容快照，生成新会话；不重新生成，直接复用 slides）。"""
    src = await db.get(ClassroomSession, session_id)
    if src is None or str(src.user_id) != user["sub"]:
        return ApiResponse(code=40400, message="会话不存在", data=None)
    if src.deleted_at is not None:
        return ApiResponse(code=40400, message="会话已删除", data=None)
    if src.status != "ready":
        return ApiResponse(code=40901, message="源会话未就绪，无法复制", data=None)
    new_session = ClassroomSession(
        course_id=src.course_id,
        user_id=uuid.UUID(user["sub"]),
        title=f"{src.title}（副本）"[:180],
        mode=src.mode,
        slide_count=src.slide_count,
        status="ready",
        outlines=src.outlines or [],
        slides=src.slides or [],
        engine=src.engine,
        knowledge_points=src.knowledge_points or [],
        source_type=src.source_type,
        content_version=src.content_version,
        verification=src.verification or {},
        practice=src.practice,
        practice_stats={},
        generated_at=datetime.now(UTC),
    )
    db.add(new_session)
    await db.commit()
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "session_id": str(new_session.id),
            "title": new_session.title,
            "status": "ready",
        },
    )


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: uuid.UUID,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """软删除课堂会话（历史课堂闭环：保留数据，列表不展示）。"""
    session = await db.get(ClassroomSession, session_id)
    if session is None or str(session.user_id) != user["sub"]:
        return ApiResponse(code=40400, message="会话不存在", data=None)
    if session.deleted_at is not None:
        return ApiResponse(code=0, message="ok", data={"deleted": True})  # 幂等
    session.deleted_at = datetime.now(UTC)
    await db.commit()
    clear_session_events(str(session.id))
    return ApiResponse(code=0, message="ok", data={"deleted": True})
