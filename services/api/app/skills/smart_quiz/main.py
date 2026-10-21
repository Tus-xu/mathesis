"""smart_quiz skill — 智能出题（F2 / PATCH-09，M2 重构版）

出题质量纪律（三闸真落地）：
1. 字段闸：题干/答案齐全，选择题 4 个选项、答案在选项内、选项去重
2. 自检闸：模型随题返回 self_check 五检（答案代回验证/计算复核/无歧义/难度匹配/考纲内），
   关键项 False 判失败；缺失记 note 跳过（兼容旧产出）
3. 格式闸：$ 公式配对检查 + 答案表达式纯本地 sympify 格式校验（仅 warning 不重出）

M2 重构（D3）：删除"LLM 现场编写 sympy_check_code 并沙箱执行"的机检闸——
实测其误判率高于能抓住的错误率，且每题烧 5s 沙箱 + 触发整题重出循环（71s/题）。
答案正确性长期保障：self_check 硬项 + 用户侧一键纠错反馈闭环（真实错题数据回流）。

失败处理：带具体原因反馈重生成 1 次 → 仍失败则诚实降级（绝不出错题）。
多题：消息含"几道/两道/N 道"时最多出 3 道，逐题过闸，部分未过闸的题略去并明示。
题库联动：RAG 检索到相似真题时以其为原型出变式题（best-effort，失败不影响主流程）。

输出 quiz_set block（信封 card 事件），前端渲染题卡。
SSE 事件纪律：yield token/status/card/_result_meta/error。
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator
from typing import Any

import structlog

from app.skills.base import SkillContext, SkillExecutor

logger = structlog.get_logger(__name__)

# 出题 prompt（PATCH-09 §9.3，迭代02 增加 self_check 五检与题库原型块）
QUIZ_PROMPT = """\
你是数学出题专家。根据指定知识点和难度生成 1 道数学题。

【输入】
- 知识点：{kp_name}
- 难度：{difficulty}（easy/medium/hard）
- 题型：{q_type}（choice/blank/solution）
{kb_block}{extra_spec}{theme_block}
【难度量表（难度锚点定义，迭代05 A-P0-3；对标自评 difficulty_match，禁止送分题充中等）】
- easy：单一概念直接应用，1~2 步可解（如公式直接代入计算）
- medium：需 2~4 步推理，含 1 个典型陷阱或概念简单综合
- hard：需 ≥5 步综合推理，含隐藏条件/分类讨论/跨知识点综合（对标高考中档偏后/压轴水平）
- 校准警示（自评 difficulty 必须保守，宁高勿低）：竞赛/压轴思路 ≠ easy；解题步骤超出档位锚点上限就升档；
  拿不准两档之间时往高评——把难题标低比标高危害更大

【高考真题风格规范（对标高考命题质量）】
- 题干规范完整：条件完备、表述清晰无歧义，符合高考命题语言习惯，不漏必要条件
- 【配图纪律（必须遵守）】：禁止使用"如图/如图所示/见下图"等指代表述——题目必须文字自包含；
  立体/解析几何与函数图像题用坐标、边长、角度、位置关系、方程等文字完整描述图形
  （如"底面 ABCD 是边长为 2 的正方形，侧面 PAD 是等边三角形且垂直底面"），
  系统会依据你的文字描述自动生成配图给学生；不得输出 graph 字段（该字段已废弃，勿填）
- 选择题：4 个选项平行同质（形式、量纲、取值风格一致，不能一眼排除）；干扰项对应典型错解——
  算错符号、漏讨论一种情况、用错公式会得到的那些答案
- 填空题：答案唯一、可机检（数值或最简解析式），不出答案不唯一的开放填空。
  answer 字段尽量用纯数学表达式：数字/分数(3/4)/根式(\\sqrt{{2}})/幂(e^2) 等；
  多值答案用逗号分隔或区间/集合记号（如 "2,-2" 或 "[1,3]"），避免"或"、"±"、"x="等口语描述
- 解答题：2~3 个递进小问（后问承接前问），answer_analysis 给出分步给分点说明（每个关键步骤一个给分点）
- 知识范围仅限指定知识点所属的高中课标内容，禁止超纲定理/方法
  （如洛必达法则、泰勒展开、竞赛不等式技巧等非课标内容不得作为考点或必需解法）

【输出严格 JSON 格式】
{{
  "q_type": "{q_type}",
  "question_text": "题目正文（公式用 $...$ LaTeX）",
  "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
  "answer": "正确答案",
  "answer_analysis": "解题过程（分步骤，每步用 [[STEP]] 标记）",
  "kp_codes": ["{kp_code}"],
  "difficulty": "{difficulty}",
  "self_check": {{
    "answer_verified": true,
    "computation_double_checked": true,
    "no_ambiguity": true,
    "difficulty_match": true,
    "in_syllabus": true,
    "note": "自检说明（50 字内）"
  }},
  "graph": "废弃字段。当前系统不支持题目配图渲染，一律不要输出此字段；几何题用文字完整描述图形"
}}

【出题纪律】
- 选择题 4 个选项，干扰项合理（对应典型错解）
- 填空题答案唯一或有限可枚举
- 解答题给完整解题步骤 + \\boxed{{}} 终答
- 公式只用 $...$ / $$...$$ 分隔符
- 不出偏题怪题，不超纲（知识范围与风格要求见上方【高考真题风格规范】）
- 【解析干净化红线（v1.9）】answer_analysis 是**给学生看的标准解析**：只写解题步骤与知识点依据，
  严禁出现任何命题过程性语言——如"原题/参考答案/重新审视/经核算/本题设计/为符合…格式/
  题目要求…个选项/干扰项/陷阱设计/调整为/改为…"等；发现前面写错了就在心里改，最终只输出正确版本
- 【答案一致性红线（v1.9）】选择题 answer_analysis 的最后一步必须落到"故选 X"，
  且 X 必须与 answer 字段完全一致；推导结论与 answer 打架的题宁可不要输出
- self_check 填法：answer_verified / computation_double_checked 看你是否用代回法独立核对过答案，
  核对过就填 true；题目难不是填 false 的理由。no_ambiguity 仅当题目条件真的歧义、矛盾或缺失
  导致无法唯一求解时才填 false——条件多、计算复杂、题目难都不算歧义，应填 true。
  difficulty_match 按上方难度量表与校准警示保守填写，宁高勿低
- 只输出 JSON，不要其他文字
- 解析简明扼要，控制在 300 字以内
"""

# 题库原型注入块（RAG 命中相似真题时拼接）
KB_BLOCK = """\
【题库参考题】（来源：{source}）
{draft}
请以该题为原型生成一道变式题：更换数字/情境，保持考点与难度一致，不要照抄原题。
若该参考题与上述指定知识点不符，忽略参考题，直接按指定知识点原创。
"""

# ========== 基于用户原题的变式链（M2 重构增量，队长核心需求） ==========
# 场景：学生贴一道自己的题（或刚做完的题），要求"出变式/来几道类似的/举一反三"。
# 与 KB_BLOCK 的区别：原题来自用户本人（可能是作业/试卷/错题），不是题库检索。
USER_QUESTION_DETECT_RE = r"(已知|设|若|在[^，。\s]{1,12}中|如图|求|求证|证明|求值|函数|数列|方程|不等式|向量|平面|直线|圆|椭圆|双曲线|抛物线|复数|集合|概率|导数|积分|极限)"

# 变式链（基于用户原题）必须能看到「一道题」，而不是仅仅出现「函数/导数」这种学科词。
# 2026-10-21 修复：「请针对函数单调性与极值先诊断再出 3 道变式」曾被当成用户贴了原题，
# 复用了上一题的题干并输出「基于你刚才那道题」——实际没有原题。
_CONCRETE_PROBLEM_RE = re.compile(
    r"已知|设\s|求[解值证]?|求证|证明|计算|若.{0,40}(?:则|求)"
    r"|[A-Za-z]\s*\([^)]*\)\s*[=<>≤≥]"
    r"|\d+\s*[xXyY]|\\frac|\\sqrt|\b(?:sin|cos|tan|log|ln)\b"
)

_DIAGNOSIS_WORDS = ("诊断", "薄弱", "最弱", "掌握度", "学情", "短板", "漏洞")

# 变式链单题 prompt（v1.6 逐题渐进生成：一次 1 道，过好闸立刻发卡，学生实时看到进度——
# 旧版单次调用憋 ~47s 零反馈，学生误以为卡死中途取消，实测实锤）
VARIANT_CHAIN_ITEM_PROMPT = """\
你是数学变式出题专家。用户给出了一道原题，请基于它生成 1 道变式题（变式链第 {idx}/{total} 道），
用于对话内轻练——学生直接点选 ABCD 作答、即时判分，所以**必须出选择题**。

【原题】
{question}

【审题纪律（先读懂再出题，否则必产废题）】
- 原题文本可能来自拍照识别（OCR），含识别噪声（⊥ 看成 I、字母看错、符号粘连）。
  先按最合理的数学解读一次性订正原题，再基于订正后的题意出变式；
  **禁止在解析里反复推敲识别错误、禁止输出"矛盾/修正/重新分析/假设错误"等审题挣扎过程**——
  出现这些字样说明你还没读懂题，应重新审题而不是继续写。
- 图形关系必须逐条明确：平行四边形 ABCD 的对边是 AB∥CD、AD∥BC——先列出"谁平行谁、
  谁垂直谁"再推理，**严禁臆造题目没有的平行/垂直关系**（如把 AE 误当作平行四边形的边）。
- 变式题条件必须自洽可解：出完题后自查一遍条件组合是否矛盾，矛盾就换条件重出。

【本题定位】
- 目标难度：{difficulty_cn}（{difficulty_hint}）
- 推荐变式方式：{variant_way}（也可自选更合适的方式）
- 变式方式参考：换数字/参数｜换情境包装｜条件与结论互换（逆向）｜增加条件/分类讨论｜综合延伸
{prev_block}
【选择题硬性纪律（逐条必须满足，否则视为废题不要输出）】
- q_type 一律 "choice"；options 恰好 4 项，以 "A. "/"B. "/"C. "/"D. " 开头，选项互不相同、互不包含
- answer 只填正确选项的**字母**（"A"/"B"/"C"/"D"）；**正确选项的位置必须随机**，禁止习惯性放在 A
  （数值类选项按大小排序后正确答案落在哪算哪，不要为了放 A 而调整数值）
- **先把题做对了再出题**：正确选项必须是你独立验算（代回题干条件逐一验证）后的结论；
  干扰项必须是学生的典型错解（如符号看错、公式记混、漏分类），不是随手编的数
- 禁止出现：恒等式（答案不唯一）、多解/无解、条件自相矛盾、答案与推导打架——拿不准就换一道有把握的
- answer_analysis 分步写（每步 [[STEP]]），最后一步必须落到"故选 X"，且 X 与 answer 完全一致
- 【三处一致红线（最高优先级）】先独立解出答案的**数值/表达式**，再匹配到选项字母；
  answer 字段、answer_analysis 结尾"故选 X"、self_check.note 里引用的选项，三处必须是同一个字母。
  严禁"验算是 A、答案键写 B、解析选 C"式的错位——系统会用独立盲解复核，错位即废题
- 【几何/图像题表述纪律】禁止"如图/如图所示"；立体几何、解析几何、函数图像类题干
  用文字完整描述图形要素（顶点名、棱/边长、垂直平行关系、方程、坐标系），系统会依据
  你的文字描述自动生成配图给学生看——文字描述越完整，配图越准确
- 【解析干净化红线】answer_analysis 只写给学生看的解题步骤与依据，严禁命题过程性语言
  （"原题/参考答案/重新审视/经核算/本题设计/为符合…格式/干扰项/调整为/改为…"等一律禁止）
- 保持考点一致：必须与原题同一知识点；禁止照抄原题
- 【去雷同红线】若【已出过的变式】非空：新题必须更换核心考查对象（不同函数/数列/图形/情境），
  禁止仅把已出题目的数字换一个再出——同一函数表达式换个定义域也算雷同
- self_check 五项**如实**填写；没把握就换题重出，严禁把没验证的题标 true

【输出严格 JSON 对象】（单个对象，不是数组）
{{
  "q_type": "choice",
  "question_text": "题干（公式用 $...$ LaTeX，文字自包含，禁止"如图"）",
  "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
  "answer": "A",
  "answer_analysis": "分步解析（每步 [[STEP]]，最后一步"故选 X"）",
  "kp_codes": ["{kp_code}"],
  "difficulty": "{difficulty}",
  "variant_note": "一句话说明本变式怎么变的（如：换数字 / 条件互换 / 加分类讨论）",
  "self_check": {{
    "answer_verified": true,
    "computation_double_checked": true,
    "no_ambiguity": true,
    "difficulty_match": true,
    "in_syllabus": true,
    "note": "自检说明"
  }}
}}
{retry_block}只输出 JSON 对象，不要其他文字。\
"""

# 变式链逐题定位表（v1.6）
_CHAIN_DIFFICULTY_CN = {"easy": "基础", "medium": "进阶", "hard": "挑战"}
_CHAIN_DIFFICULTY_HINT = {
    "easy": "≈原题难度或略低，1~2 步可解",
    "medium": "比原题高半档，2~4 步推理",
    "hard": "比原题高 1 档，含 1 个综合点或分类讨论",
}
_CHAIN_VARIANT_WAYS = (
    "换数字/参数（保持题型与考点结构不变）",
    "换情境包装 或 条件与结论互换（逆向变式）",
    "增加条件/分类讨论 或 综合延伸（最高难度档）",
)

# 出题配图门控：题目必须“需要看图”才生成题目配图。
# 纯计算题（求导/求值/解方程）即使含“函数”二字也不配图——
# 旧实现把原题图的视觉描述注入每道变式，导致变式上方出现无关的分段函数图。
_QUIZ_FIGURE_REF_RE = re.compile(
    r"如图|图示|图中|图象|图像|图形|曲线|抛物线|双曲线|椭圆|圆|三角形|"
    r"正方体|长方体|棱锥|棱柱|球|三视图|坐标|几何|截面|外接|内切|焦点|"
    r"渐近线|切线|相交|交点"
)


def _quiz_question_needs_figure(question: str) -> bool:
    """题目是否真的需要一张配图（视觉/几何语义），而不是纯代数计算。"""
    return bool(question and _QUIZ_FIGURE_REF_RE.search(question))


def _chain_difficulty_targets(count: int) -> list[str]:
    """变式链逐题难度目标：1 道≈原题档；2 道基础+进阶；3 道及以上 基础→进阶→挑战"""
    if count <= 1:
        return ["medium"]
    if count == 2:
        return ["easy", "medium"]
    return ["easy", "medium", "hard"][:count]


# 大题规格注入块（用户点名「大题/高考大题/压轴」时拼接；防止单问小题充大题）
SOLUTION_BIG_SPEC = """\
【大题规格（用户要求高考大题/压轴题，必须遵守）】
- 必须出 2~3 个递进小问的综合解答题（高考 12 分制规格）：第 (1) 问奠基、直接考查核心概念；
  第 (2) 问综合提升、与第 (1) 问有逻辑承接；可选第 (3) 问拓展
- question_text 中用 (1)(2)(3) 明确标注各小问
- answer_analysis 按小问组织完整解答过程，并分别给出各小问答案
- 禁止用单问小题冒充大题
"""

# 带失败原因的重新生成反馈（迭代05：区分不同失败原因的修正指引）
RETRY_FEEDBACK = """\
上一次生成的题目未通过质量检查，问题如下：
{failures}
请针对性修正后重新生成（保持知识点、难度、题型不变）。

【修正指引】
- 若失败原因是"自检未通过：answer_verified / computation_double_checked"：
  请用代回法（把答案代回题干条件逐一验证）独立核对答案，确认无误后再把自检项填 true。
- 若失败原因涉及难度不匹配（difficulty_match=false 或 difficulty 字段与难度量表不符）：
  对照难度量表与校准警示重新自评——解题步骤超出当前档位锚点上限的，要么把 difficulty 字段升高一档，
  要么简化题目使其真正匹配目标难度。
- 若失败原因是"答案不在选项内/选项重复/选项数不对"：检查选择题四个选项与 answer 字段的一致性。
- 只输出 JSON。
"""

# KP 自由抽取 prompt（KP_MAP 未命中时，小 JSON 抽知识点 + 情境主题）
KP_EXTRACT_PROMPT = """\
从用户的出题请求中抽取：
1. kp_name：知识点名称（高中数学范围，如：复数、平面向量、圆锥曲线、导数应用、函数、概率统计）
2. theme：用户指定的情境/主题（如"原神"、"NBA"、"三国"；没有则为空字符串）
只输出 JSON：{{"kp_name": "...", "theme": "..."}}；若无法判断知识点输出 {{"kp_name": "", "theme": ""}}。
用户消息：{message}
"""

# 情境主题包装块（用户指定主题时注入出题 prompt；考点不变，仅换题目背景）
# v2.0：增加内容安全白/黑名单 + 真实融合纪律 + 避免生硬凑题
THEME_BLOCK = """
【情境包装（必须遵守）v2.0】
- 用户要求以「{theme}」为背景出题。

【主题白名单（允许作为情境包装）】
游戏（王者/原神/我的世界/Minecraft 等）、动漫/影视/综艺、传统体育项目（NBA/CBA/足球/乒乓等）、
历史人物/朝代/事件、地理/旅行/校园生活、科幻/太空/AI/前沿科技、传统美食/节俗、艺术/音乐/文学经典。
——以上题材可安全使用，题目背景可自由发挥。

【主题黑名单（直接礼貌拒绝）】
赌博/博彩/赌场/彩票、色情/低俗/擦边、毒品/酒精/香烟等成瘾物、自残/自杀/伤害他人、
宗教/民族/政治敏感人物与争议、血腥暴力/恐怖袭击、未成年人不当情境。
——若 theme 命中以上任一，直接拒绝并说明"该主题不适合作为题目背景"，不强行出题。

【真实融合纪律（最重要）】
- 情境描述要真实自然：使用该题材中真实存在的人物名/物品名/地点名（如原神用「提瓦特/可莉/甘雨/雷电将军」，
  不要凭空编造不存在的"原神角色"），数值设定要符合该题材的世界观物理常识。
- 数学考点、难度、解题逻辑与普通题完全一致——**情境包装不改变题目难度和考点**。
- 严禁生搬硬套导致题意歧义；若主题与考点结合生硬，宁可退回中性情境也不硬凑。
- 严禁为贴合主题而降低题目质量（如改错数学公式/编造超纲设定/出含糊不清的题）。
- 题目要自包含：所有人物名/数值/设定在题干中要完整呈现，不依赖外部常识。
"""

# 知识点词汇表（PLAN §7.4.5「KP 编码词汇断层」指定修法，B3）：
# ① 静态粗粒度考点词——question_bank.kp_codes 的码本（题库 544/241/197 行真题归属三类；
#    消费侧未知码本就回退原码，安全），保持不变供题库兜底检索；
# ② 动态结构节点——出题请求时从 knowledge_points 加载（load_kp_map）：只取「册级根节点 +
#    节名 ^\d+\. 的小节」（R4 §5 实测口径：182 个小节 + 5 个册级），排除「探究/思考/归纳」
#    等 25 个栏目噪声名（它们会误命中任何含"思考/探究"的用户消息），使题卡携带真实
#    MATH-PEP-* KP code，与学情/教材域词汇闭合。
_KP_MAP = {
    "function": "函数",
    "trig": "三角函数",
    "derivative": "导数",
    "probability": "概率统计",
    "sequence": "数列",
    "geometry": "立体几何",
    "analytic": "解析几何",
    "exponential": "指数对数",
    # 阶段2 决策1：父类扩码（题库已有 544/241/197 行真题归属三类；消费侧未知码本就回退原码，安全）
    "set_logic": "集合逻辑",
    "complex": "复数",
    "inequality": "不等式",
}

#: 兼容别名（既有引用读静态码本；动态词表一律走 load_kp_map）
KP_MAP = _KP_MAP

# 结构节点过滤（R4 §5 口径）：节名 ^\d+\. 的小节；册级根节点以 parent_id IS NULL 判定
_STRUCTURAL_SECTION_NAME_RE = re.compile(r"^\d+\.")


async def load_kp_map(ctx: SkillContext) -> dict[str, str]:
    """出题请求时加载知识点词汇表：静态粗粒度词 ∪ knowledge_points 结构节点。

    结构节点过滤（PLAN §7.4.5）：只取册级根（parent_id IS NULL）+ 节名 ``^\\d+\\.``
    的小节；排除「探究/思考」等栏目噪声名。DB 不可用/查询失败 → fail-open 回退静态
    词表（与出题链路既有 fail-open 口径一致，绝不因字典查询阻塞出题）。
    """
    merged = dict(_KP_MAP)
    db = getattr(ctx, "db", None)
    if db is None:
        return merged
    try:
        from sqlalchemy import text as _sql_text

        rows = (
            await db.execute(_sql_text("SELECT code, name, parent_id FROM knowledge_points"))
        ).all()
        for code, name, parent_id in rows:
            name = (name or "").strip()
            if not code or not name:
                continue
            if parent_id is None or _STRUCTURAL_SECTION_NAME_RE.match(name):
                merged.setdefault(code, name)
        logger.info(
            "smart_quiz.kp_map_loaded",
            static=len(_KP_MAP),
            total=len(merged),
        )
    except Exception as e:  # noqa: BLE001 - 字典加载失败不阻塞出题
        logger.warning("smart_quiz.kp_map_load_failed", error=str(e)[:120])
    return merged


# self_check 中判失败的硬项；其余软项仅记 note
_SELF_CHECK_HARD_KEYS = ("answer_verified", "computation_double_checked", "no_ambiguity")
_SELF_CHECK_SOFT_KEYS = ("difficulty_match", "in_syllabus")


def _source_label(
    ai_generated: bool, source: str | None, kb_ref: str | None, verified: bool
) -> str:
    """阶段3 来源透明：题卡逐题来源标注（题库优先改编 / AI 兜底）。"""
    if kb_ref:
        return f"改编自 {kb_ref} 真题"
    if not ai_generated:
        return "题库真题"
    return "AI 生成 · 已过五闸验证" if verified else "AI 生成 · 质量闸校验中"


# 卡片选项字母序（须与前端 QuizSetCard 的 LETTERS 一致）
_BANK_LETTERS = "ABCDEFGH"


def _bank_options_to_card(options: Any, answer: Any) -> tuple[list[str], str]:
    """题库 options/answer → 卡片契约（options 数组 + 字母答案）。

    QuestionBank.options 是 {"A": "...", ...} 形态的 dict、answer 是 option key；
    而前端 QuizSetCard 以数组下标渲染 A/B/C/D，并按 answer 字母判分（isRightOption
    的 `/^[A-F]$/i` 分支）。因此必须在此归一：选项按 key 排序成数组，answer 命中
    某个 key 时还原为其下标字母；非字母 key（如 "1"/"2"）或文本答案原样透传，
    由前端文本归一比较兜住，避免选择题退化成无选项渲染或判分错位。
    """
    if isinstance(options, dict) and options:
        keys = sorted(str(k) for k in options)
        opts = [str(options[k]) for k in keys]
        raw = str(answer or "").strip()
        for i, key in enumerate(keys[: len(_BANK_LETTERS)]):
            if raw.upper() == key.strip().upper():
                return opts, _BANK_LETTERS[i]
        return opts, raw
    if isinstance(options, list):
        return [str(o) for o in options], str(answer or "").strip()
    return [], str(answer or "").strip()


def _is_fully_verified(notes: list[str]) -> bool:
    """自检闸缺失（跳过）的题不得标 verified（迭代15 L0-4 闸口收紧）。

    修复前：模型未返回 self_check → 记 note 跳过 → 卡片仍 verified=True——
    实测无解题带"已验证"标记出闸（事故级）。现在 verified 只在五检真实运行时成立。
    """
    return not any("自检闸跳过" in n for n in notes or [])


# v1.9 解析命题草稿语言特征（命中即闸 3.5 判失败重出）：
# 模型把"设计/纠错题目"的内心戏写进学生可见的 answer_analysis 的实锤用语
_ANALYSIS_META_RE = re.compile(
    r"参考答案|重新审视|经核算|为符合|题目要求|题目设计|本题设计|设计为|干扰项|保持与原题|原题参考"
)


# v1.11 乱码修复：JSON 合法转义字母恰好是 LaTeX 常用命令首字母
# （\frac \binom \tan \nu \rho…），模型偶发单反斜杠时 strict loads 会"成功"，
# 但 \f 被解码成 \x0c（\b→\x08、\t→\x09、\r→\x0d）——题干静默乱码（实机实锤）。
# 解析成功后递归检查字符串值，命中即判腐蚀弃用，落入修复候选重解析。
# （\n 对应的 \x0a 字面换行是 answer_analysis 的合法内容，豁免。）
_ESCAPE_CORRUPT_CHARS = ("\x08", "\x09", "\x0c", "\x0d")


def _has_escape_corruption(value: Any) -> bool:
    if isinstance(value, str):
        return any(c in value for c in _ESCAPE_CORRUPT_CHARS)
    if isinstance(value, dict):
        return any(_has_escape_corruption(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_escape_corruption(v) for v in value)
    return False


def _json_loads_lenient(text: str) -> Any | None:
    """宽松 JSON 解析（v1.4/v1.5）：直接 loads → strict=False（容忍字符串内字面换行/控制字符）
    → 非法反斜杠补全后同样两档。数学 LLM 输出常含 LaTeX 单反斜杠（\\sqrt \\frac 不是
    合法 JSON 转义）和 answer_analysis 分步解析的字面换行，严格 loads 必炸。

    反斜杠修复纪律：先整体跳过合法的已转义 `\\\\`（LaTeX 换行符，如 `\\\\ge` `\\\\times`），
    只把"孤立的非法单反斜杠"补成 `\\\\`——否则会把合法的 `\\\\ge` 错补成 `\\\\\\ge` 直接炸解析。
    v1.11 增补：`\\` + 两个 ASCII 字母也补全（\\frac \\to \\nu 等 LaTeX 命令特征——
    它们虽是合法 JSON 转义，但在数学 JSON 里几乎一定是 LaTeX 单反斜杠漏转义）。
    """
    candidates = [text]
    fixed = re.sub(
        r"\\\\|\\(?=[a-zA-Z][a-zA-Z])|\\(?![\\/bfnrtu\"])",
        lambda m: m.group(0) if len(m.group(0)) == 2 else "\\\\",
        text,
    )
    if fixed != text:
        candidates.append(fixed)
    for candidate in candidates:
        for strict in (True, False):
            try:
                result = json.loads(candidate, strict=strict)
            except (json.JSONDecodeError, ValueError):
                continue
            # 原文档解析"成功"但含 \f \b \t \r 转义腐蚀 → 弃用，落入修复候选重解析
            if candidate is text and _has_escape_corruption(result):
                break
            return result
    return None


def parse_quiz_json(raw: str) -> dict | None:
    """从 LLM 输出解析 JSON（skill 与 student_router 判分/出题复用）"""
    # 尝试直接解析（含宽松兜底）
    result = _json_loads_lenient(raw)
    if result is not None:
        return result

    # 尝试提取 ```json ... ``` 块
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    if match:
        result = _json_loads_lenient(match.group(1))
        if result is not None:
            return result

    # 尝试找 { ... } 块
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        result = _json_loads_lenient(match.group(0))
        if result is not None:
            return result

    return None


async def recent_seed_question(db: Any, conversation_id: Any, *, limit: int = 12) -> str | None:
    """本会话最近题目种子（v1.11 共享助手：smart_quiz 变式回落 / socratic 讲解回落复用）。

    学生单说「举一反三」或「基于刚才的题讲解」时消息里没有题干——回落取最近上下文补齐：
    ① 最近一条含 quiz_set 题卡的 assistant 消息 → 第一道题题干（题卡才是学生"刚才"刚做的）；
    ② 最近一次引导解题（tutor_session）题干。
    两者都有时取时间更近的；都查不到返回 None（调用方走原有兜底，绝不幻觉编题）。
    """
    if db is None or not conversation_id:
        return None
    try:
        from sqlalchemy import select

        from app.models.message import Message
        from app.models.tutor_session import TutorSession

        quiz_q, quiz_ts = None, None
        rows = await db.execute(
            select(Message.envelope, Message.created_at)
            .where(
                Message.conversation_id == conversation_id,
                Message.role == "assistant",
                Message.deleted_at.is_(None),
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        for envelope, created_at in rows:
            for block in (envelope or {}).get("blocks") or []:
                data = block.get("data") or {}
                if block.get("type") == "card" and data.get("type") == "quiz_set":
                    items = data.get("items") or []
                    stem = (items[0].get("question_text") or "").strip() if items else ""
                    if stem:
                        quiz_q, quiz_ts = stem, created_at
                        break
            if quiz_q:
                break

        tutor_q, tutor_ts = None, None
        trows = await db.execute(
            select(TutorSession.question_text, TutorSession.updated_at)
            .where(
                TutorSession.conversation_id == conversation_id,
                TutorSession.deleted_at.is_(None),
            )
            .order_by(TutorSession.updated_at.desc())
            .limit(1)
        )
        trow = trows.first()
        if trow and (trow[0] or "").strip():
            tutor_q, tutor_ts = trow[0].strip(), trow[1]

        if quiz_q and tutor_q:
            if quiz_ts is not None and tutor_ts is not None:
                return quiz_q if quiz_ts >= tutor_ts else tutor_q
            return quiz_q if quiz_ts is not None else tutor_q
        return quiz_q or tutor_q
    except Exception as e:
        logger.info("smart_quiz.recent_seed_failed", error=str(e)[:120])
        return None


async def generate_quiz_item(
    llm: Any,
    *,
    kp_code: str,
    kp_name: str,
    difficulty: str,
    q_type: str,
    request_id: str,
    kb_block: str = "",
    retry_feedback: str = "",
    extra_spec: str = "",
    theme_block: str = "",  # v1.3：情境主题包装（如"原神"），考点不变仅换背景
    temperature: float = 0.8,
) -> tuple[dict | None, str]:
    """调用 LLM 生成 1 道题并解析 JSON。

    返回 (quiz_data, raw)：JSON 解析失败为 (None, raw)；LLM 异常向上抛出。
    供 SmartQuizExecutor 与 student_router 题组生成复用。
    kb_block：题库原型注入（SmartQuizExecutor RAG 命中时使用）。
    retry_feedback：上一次质量检查失败原因（重生成时传入）。
    extra_spec：额外规格约束（如 SOLUTION_BIG_SPEC 大题多小问规格）。
    theme_block：情境主题包装（用户指定"有关X的数学题"时注入）。
    """
    prompt = QUIZ_PROMPT.format(
        kp_code=kp_code,
        kp_name=kp_name,
        difficulty=difficulty,
        q_type=q_type,
        kb_block=kb_block,
        extra_spec=extra_spec,
        theme_block=theme_block,
    )
    if retry_feedback:
        prompt = prompt + "\n\n" + retry_feedback
    result = await llm.chat(
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        # 迭代05：4000→6000（难度量表/自我校检加长后 4000 会被截断，实测 output_tokens=4000 打满致 JSON 截断）
        max_tokens=6000,
        request_id=request_id,
        scene="smart_quiz",
    )
    raw = result.get("content", "")
    return parse_quiz_json(raw), raw


async def run_quiz_gates(quiz_data: dict) -> tuple[bool, list[str], list[str]]:
    """质量四闸验证（迭代05：提取为模块级函数，skill chat 路径与 practice/start 路径共用，A-P0-1）

    返回 (passed, failures, notes)：
    - failures 非空即未通过（用于重生成反馈与降级原因）
    - notes 记录跳过的闸（如模型未返回 self_check / 无机检代码），随卡片透传

    闸 1 良定性五检（SSOT §4.7 本地机检终闸硬条款，迭代05 B-P1-6 补全）：
    题干非空 / 答案非空 / 选项数=4 / 选项去重 / answer∈options / blank 可 sympify。
    """
    failures: list[str] = []
    notes: list[str] = []

    # 闸 1: 基本字段完整性 + 良定性五检
    q_text = (quiz_data.get("question_text") or "").strip()
    answer = str(quiz_data.get("answer") or "").strip()
    if not q_text:
        failures.append("题目文本缺失")
    if not answer:
        failures.append("标准答案缺失")
    # 配图纪律（双保险，与 QUIZ_PROMPT 约束对应）：题干依赖图片 → 整题重生成。
    # 系统现在能依据文字描述自动生成配图（v3.2），所以仍禁止"如图"指代，
    # 但反馈要教模型怎么改：用文字完整描述图形，配图由系统生成
    if re.search(r"如图|如图所示|见下图|见右图|见左图|见上图|如下图中|下图所示", q_text):
        failures.append(
            "题干使用了'如图/如图所示'等指代表述：请删掉该表述，改用文字完整描述图形"
            "（顶点名、边长/方程、垂直平行等位置关系），配图由系统依据描述自动生成"
        )
    q_type = quiz_data.get("q_type") or ""
    options = quiz_data.get("options") or []
    if q_type == "choice" and len(options) != 4:
        failures.append(f"选择题选项数应为 4，实际 {len(options)}")
    if q_type == "choice" and len(options) == 4:
        # 选项去重（良定性五检之一）
        norm_opts = [re.sub(r"\s+", "", str(o)) for o in options]
        if len(set(norm_opts)) < 4:
            failures.append("选择题选项存在重复")
        # answer ∈ options（良定性五检之一：整体归一相等或选项首字母命中）
        ans_norm = re.sub(r"\s+", "", answer).upper()
        if ans_norm:
            opt_hit = any(
                ans_norm == re.sub(r"\s+", "", str(o)).upper()
                or (
                    ans_norm[0] in "ABCDEF"
                    and re.sub(r"\s+", "", str(o)).upper().startswith(ans_norm[0])
                )
                for o in options
            )
            if not opt_hit:
                failures.append("选择题答案不在选项内")
    if q_type == "blank" and answer and not re.search(r"[一-鿿]", answer):
        # blank 答案格式校验（M2 重构：纯本地 sympify，不再沙箱执行 LLM 写的代码）
        # 多值填空按"或/逗号"拆解逐值 sympify；区间/描述性答案为合法表达跳过
        sympify_answer = answer.replace("π", "pi").replace("×", "*").replace("÷", "/")
        if re.search(r"[±√∈∉⊂⊆≠≤≥∪∩∞°]", sympify_answer) or not re.search(r"[0-9]", sympify_answer):
            notes.append("填空答案含区间/描述性表达，跳过格式校验")
        else:
            try:
                from sympy import sympify as _sympify

                for part in re.split(r"或|或者|[，,;；]", sympify_answer):
                    part = part.strip()
                    if not part:
                        continue
                    if "=" in part:
                        part = part.split("=", 1)[1].strip()
                    _sympify(part.strip("$"))
            except Exception:
                # 格式解析失败仅提示（不重出）——表达式格式多样的合法答案存在
                notes.append("填空答案 sympify 格式校验未过（仅提示，不重出）")
    if failures:
        return False, failures, notes

    # 闸 2: self_check 五检（硬项 False 判失败；缺失记 note 跳过）
    self_check = quiz_data.get("self_check")
    if isinstance(self_check, dict):
        for key in _SELF_CHECK_HARD_KEYS:
            if self_check.get(key) is False:
                failures.append(f"自检未通过：{key}")
        for key in _SELF_CHECK_SOFT_KEYS:
            if self_check.get(key) is False:
                notes.append(f"自检提示：{key} 为 false（{self_check.get('note') or '无说明'}）")
    else:
        notes.append("模型未返回 self_check，自检闸跳过")

    # 闸 3: 公式合法性（题干 + 选项 $ 配对）
    from app.providers.latex_check import check_formula_pairing

    joined = q_text + " " + " ".join(str(o) for o in options)
    if "$" in joined and not check_formula_pairing(joined):
        failures.append("公式配对检查未通过")

    # 闸 3.5（v1.9）：解析干净化 + 答案一致性（学生端"廉价感"实锤治理）
    # a) 命题草稿语言：模型把"设计题目时的自我纠错"写进 answer_analysis
    #    （实锤样例："原题参考答案为(2)(3)…经核算…为符合单选题格式将C改为错误命题"）
    analysis_text = str(quiz_data.get("answer_analysis") or "")
    if analysis_text:
        meta_m = _ANALYSIS_META_RE.search(analysis_text)
        if meta_m:
            failures.append(
                f"解析含命题过程性语言（命中「{meta_m.group(0)}」）：answer_analysis 只写给学生看的解题步骤"
            )
    # b) 选择题答案一致性：解析最终结论（故选 X）与 answer 字母打架 → 判失败重出
    #    （实锤样例：answer=C 但解析推导出"正确选项为 D"）
    if q_type == "choice" and re.fullmatch(r"[A-D]", answer.upper()):
        concl = re.findall(
            r"(?:故选|正确选项为|正确答案为|答案为|答案是|应选)\s*[:：]?\s*([A-D])(?![A-D])",
            analysis_text.upper(),
        )
        if concl and concl[-1] != answer.upper():
            failures.append(f"解析最终结论（{concl[-1]}）与标准答案（{answer.upper()}）不一致")

    # 闸 4（M2 重构）：答案格式静态校验（纯本地 sympify，仅 warning 不触发重出）。
    # 旧的"LLM 现场编写 sympy_check_code 并沙箱执行"机检闸已删除——实测其误判率高于
    # 能抓住的错误率（LLM 写的验证代码本身质量参差），且每题烧 5s 沙箱 + 重出循环。
    # 答案正确性的长期保障改为：self_check 硬项 + 用户侧一键纠错反馈闭环（真实错题数据回流）。
    if q_type in ("blank", "choice") and answer and not re.search(r"[一-鿿]", answer):
        try:
            from sympy import sympify as _sympify

            probe = answer.replace("π", "pi").replace("×", "*").replace("÷", "/")
            if not re.search(r"[±√∈∉⊂⊆≠≤≥∪∩∞°]", probe):
                first_part = re.split(r"或|或者|[，,;；]", probe)[0].strip()
                if "=" in first_part:
                    first_part = first_part.split("=", 1)[1].strip()
                if first_part:
                    _sympify(first_part.strip("$"))
        except Exception:
            notes.append("答案表达式 sympify 格式校验未过（仅提示，不重出）")

    return not failures, failures, notes


# ========== 闸 5：答案键独立黑盒复核（N2：2026-08 极限题判分键 D/A 错位事故防复发） ==========
# 事故：模型出"求 a 的值"的选择题，把解题中间量（极限值 3）当成所问量写进答案键，
# 且解析自圆其说（推导 a=-1 后仍"故选 D"），闸 3.5b 的文本一致性检查抓不住。
# 防线：不带标准答案独立重解一遍，choice 比对选项字母，blank 用 sympy 等价比对。

KEY_VERIFY_SYSTEM = """\
你是独立阅卷老师，负责复核题目标准答案是否正确。你会拿到一道题的题干与选项，**没有标准答案**。
请完全独立地解题，只输出一个 JSON 对象：
{"option": "A|B|C|D 或 null", "value": "你求出的答案（数值/表达式原文，不是选项字母）", "note": "一句话关键依据"}

【铁律】
1. 先看清题目问的是什么量（问参数 a 就给 a 的值，问极限值就给极限值），
   绝不把题目所问的量偷换成解题过程中的中间量；
2. 先推导出答案本身，再匹配选项字母；涉及计算务必逐步核算，不要心算跳步；
3. 选择题 option 必须是 A/B/C/D 之一；确实无法确定时 option 填 null 并在 note 说明；
4. 只输出 JSON，禁止输出任何其他文字。
"""

KEY_VERIFY_USER = """【题目】
{question}

【选项】
{options}

请独立作答并输出 JSON。"""


async def blind_solve_choice(
    question: str, options: list, llm: Any, request_id: str
) -> dict | None:
    """不带标准答案盲解一道选择题，返回 {option, value, note} 或 None（复核与答案对齐共用）"""
    options_block = "\n".join(f"{chr(65 + i)}. {o}" for i, o in enumerate(options))
    try:
        result = await llm.chat(
            messages=[
                {"role": "system", "content": KEY_VERIFY_SYSTEM},
                {
                    "role": "user",
                    "content": KEY_VERIFY_USER.format(
                        question=question[:2000], options=options_block
                    ),
                },
            ],
            temperature=0.1,
            max_tokens=600,
            request_id=request_id,
            scene="smart_quiz_key_verify",
        )
        raw = (result or {}).get("content") or ""
    except Exception:
        return None
    data: Any = None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
            except (json.JSONDecodeError, TypeError):
                data = None
    return data if isinstance(data, dict) else None


def _match_option_by_value(value: str, options: list) -> str:
    """按求值文本匹配选项字母（精确/归一化两级）；匹配不到返回空串"""
    if not value:
        return ""
    v_clean = re.sub(r"[\s$\\]|left|right|dfrac|tfrac|cdot|text", "", value)
    for i, opt in enumerate(options or []):
        o_clean = re.sub(r"[\s$\\]|left|right|dfrac|tfrac|cdot|text", "", str(opt))
        o_clean = re.sub(r"^[A-D][.、．:：]", "", o_clean)
        if v_clean and (v_clean == o_clean or v_clean in o_clean or o_clean in v_clean):
            return chr(65 + i)
    return ""


async def align_choice_answer(
    quiz_data: dict, failures: list[str], llm: Any, request_id: str
) -> bool:
    """答案-选项对齐自愈（v3.2，闸后修复而非弃题）。

    实锤事故：模型把解答题转选择题时"独立验算 √3/3 选 A、答案键 B、解析结论 C"三处错位。
    本函数只处理"答案一致性"类失败：收集三个独立信号——
      ① 盲解复核的选项字母；② 盲解求值文本按 sympy/归一化匹配的选项；③ 解析"故选 X"结论——
    取多数派重写 answer 并改写解析结尾，使 answer / 解析 / 复核三者一致。
    信号 <2 个或互相矛盾（无多数）→ 不对齐（返回 False 走针对性重试）。
    """
    joined = "；".join(failures or [])
    if not ("不一致" in joined or "独立复核" in joined):
        return False
    options = quiz_data.get("options") or []
    if len(options) != 4:
        return False
    solved = await blind_solve_choice(
        str(quiz_data.get("question_text") or ""), options, llm, request_id or "quiz-align"
    )
    votes: list[str] = []
    if solved:
        opt = str(solved.get("option") or "").strip().upper()[:1]
        if opt in ("A", "B", "C", "D"):
            votes.append(opt)
        by_value = _match_option_by_value(str(solved.get("value") or ""), options)
        if by_value:
            votes.append(by_value)
    concl = re.findall(
        r"(?:故选|正确选项为|正确答案为|答案为|应选)\s*[:：]?\s*([A-D])(?![A-D])",
        str(quiz_data.get("answer_analysis") or "").upper(),
    )
    if concl:
        votes.append(concl[-1])
    if len(votes) < 2 or len(set(votes)) != 1:
        return False
    target = votes[0]
    key_opt = str(quiz_data.get("answer") or "").strip().upper()[:1]
    if target == key_opt:
        return True  # 信号一致且与 answer 相同：原失败来自解析/复核文本，重过闸即可
    quiz_data["answer"] = target
    analysis = str(quiz_data.get("answer_analysis") or "")
    rewritten = re.sub(
        r"(故选|正确选项为|正确答案为|答案为|应选)\s*[:：]?\s*[A-D]",
        f"故选 {target}",
        analysis,
    )
    if rewritten == analysis or not re.search(r"故选", rewritten):
        rewritten = rewritten.rstrip() + f"\n故选 {target}。"
    quiz_data["answer_analysis"] = rewritten
    self_check = quiz_data.get("self_check")
    if isinstance(self_check, dict):
        self_check["answer_verified"] = True
        self_check["computation_double_checked"] = True
        if isinstance(self_check.get("note"), str):
            self_check["note"] = (
                self_check["note"] + f"（已对齐：独立复核与解析结论一致为 {target}）"
            )[:200]
    return True


async def verify_answer_key(quiz_data: dict, llm: Any, request_id: str) -> tuple[bool, str, str]:
    """答案键独立黑盒复核。返回 (passed, failure_reason, note)。

    - choice：复核器给出的选项字母与 answer 字母不一致 → 判失败（走重出/弃题）；
    - blank：复核器 value 与 answer 做 sympy 等价比对，不等价 → 判失败；文本型答案跳过；
    - 复核器自身故障（调用异常/输出非法）不拦题，记 note——复核器抖动不应拒绝出题；
      但一旦复核出不一致，必须判失败（错答案键直接损害学习，宁严勿松）。
    """
    q_type = quiz_data.get("q_type") or ""
    answer = str(quiz_data.get("answer") or "").strip()
    question = str(quiz_data.get("question_text") or "").strip()
    if q_type not in ("choice", "blank") or not question or not answer:
        return True, "", ""
    options = quiz_data.get("options") or []
    solved = await blind_solve_choice(question, options, llm, request_id)
    if solved is None:
        return True, "", "答案复核器调用失败/输出非法（放行）"

    solver_value = str(solved.get("value") or "").strip()
    solver_opt = str(solved.get("option") or "").strip().upper()[:1]
    if q_type == "choice":
        key_opt = answer.upper()[:1]
        if solver_opt in ("A", "B", "C", "D") and solver_opt != key_opt:
            return (
                False,
                (
                    f"独立复核答案为 {solver_opt}，与标准答案 {key_opt} 不一致"
                    f"（复核求值：{solver_value[:40] or '未给出'}），疑似答案键错位"
                ),
                "",
            )
        return True, "", ""
    # blank：文本型跳过；数值/表达式型 sympy 等价比对
    if re.search(r"[一-鿿]", answer) or not solver_value:
        return True, "", ""
    try:
        from app.providers.sandbox import check_equivalence

        eq = await check_equivalence(solver_value, answer, timeout_ms=3000)
        if eq.get("verdict") == "wrong":
            return (
                False,
                (
                    f"独立复核答案为 {solver_value[:40]}，与标准答案 {answer[:40]} 不等价，"
                    "疑似答案键错位"
                ),
                "",
            )
    except Exception:
        return True, "", "答案复核等价判定异常（放行）"
    return True, "", ""


class SmartQuizExecutor(SkillExecutor):
    """智能出题 skill executor"""

    manifest = {
        "id": "smart_quiz",
        "name": "智能出题",
        "description": (
            "根据知识点和难度生成数学练习题（选择/填空/解答），或基于用户给出的原题生成"
            "难度递进的变式链。当用户要求出题、来一道题、练习题、组卷、出变式题、"
            "举一反三、来几道类似的题、想刷某知识点的题时使用。"
        ),
        "version": "2.0.0",
        "roles": ["student", "teacher"],
        "presentation": "card",
        "examples_positive": [
            "出一道三角函数的题",
            "给我一道中等难度的导数题",
            "出选择题",
            "来几道数列练习题",
        ],
        "examples_negative": ["帮我解题", "这道题怎么做"],
        "fallback": "chat",
    }

    async def run(self, params: dict[str, Any], ctx: SkillContext) -> AsyncGenerator[dict, None]:
        """主执行流"""
        message = params.get("message", "") or params.get("question", "")

        # ===== 变式链分支（M2 重构增量）：用户贴了完整题目 + 要求出变式 → 基于用户原题生成变式链 =====
        # 触发词：变式/变形/来几道类似的/举一反三/再来一题/类似的题/换个数字 等；
        # 且消息中含题目特征（已知/设/求/函数/数列/方程/…）。
        # 若命中，走独立 prompt 生成变式链；不命中则走下方原有出题逻辑（零影响）。
        image_file_ids = params.get("image_file_ids") or []
        if self._detect_user_variant(message):
            async for ev in self._run_user_variant_chain(
                message, ctx, image_file_ids=image_file_ids
            ):
                yield ev
            return

        # 迭代10 v1.4 变式回落：触发词命中但当前消息无题目特征（如引导完成后单说
        # 「举一反三」）时，回落取本会话最近题目作种子出变式（v1.11：种子含题卡题干，
        # 不再只看 tutor_session——此前题卡场景拿到旧引导种子，变式跑偏成无关题），
        # 避免退化成无关随机出题；无种子再走原有出题逻辑
        if message and any(t in message for t in self._VARIANT_TRIGGERS):
            seed = await recent_seed_question(
                getattr(ctx, "db", None), getattr(ctx, "conversation_id", None)
            )
            if seed:
                async for ev in self._run_user_variant_chain(
                    message, ctx, seed=seed, image_file_ids=image_file_ids
                ):
                    yield ev
                return

        # 解析出题参数（KP_MAP 未命中时 LLM 小 JSON 抽取，best-effort）
        kp_code, kp_name, difficulty, q_type, theme = await self._parse_request(message, ctx)
        count = _parse_count(message)
        # 迭代15 B8（L1-5 连续受挫干预，B-C3）：连错 ≥2 → 难度降一档 + 基础巩固指令。
        # 计数由判分事件总线维护（services/quiz_streak.py，Redis fail-open）
        try:
            from app.services.quiz_streak import (
                apply_frustration_relief,
                get_quiz_wrong_streak,
            )

            _wrong_streak = await get_quiz_wrong_streak(getattr(ctx, "user_id", None))
            difficulty, _relief_note = apply_frustration_relief(difficulty, _wrong_streak)
        except Exception:
            _relief_note = ""
        # 情境主题包装块（用户指定主题如"原神"时注入；考点不变，仅换背景）
        theme_block = ""
        if theme:
            theme_block = "\n" + THEME_BLOCK.format(theme=theme)
        theme_block += _relief_note
        # 大题规格：用户点名「大题/压轴」的解答题必须多小问（防止单问小题充大题）
        extra_spec = ""
        if q_type == "solution" and ("大题" in message or "压轴" in message):
            extra_spec = SOLUTION_BIG_SPEC

        yield {
            "type": "status",
            "data": {
                "stage": "generating",
                "text": (
                    f"正在出题（{kp_name}·{_difficulty_cn(difficulty)}"
                    + (f"，共 {count} 道" if count > 1 else "")
                    + "）..."
                ),
            },
        }

        # 题库检索（best-effort）：命中相似真题则以其为原型出变式
        kb_ref = await self._retrieve_kb_prototype(kp_name, difficulty, message, ctx)
        kb_block = ""
        if kb_ref:
            yield {
                "type": "status",
                "data": {"stage": "generating", "text": "找到题库中的相似真题，正在出变式题..."},
            }
            kb_block = KB_BLOCK.format(source=kb_ref["source"], draft=kb_ref["content"])

        # 生成 + 三闸（每题：失败带反馈重生成 1 次；全部失败 → 诚实降级）
        try:
            items: list[dict] = []
            item_notes: list[list[str]] = []
            last_failures: list[str] = []
            bank_fallback = False  # v3.4：AI 全灭后是否由题库兜底供题（决定来源标注口径）
            for idx in range(count):
                if count > 1:
                    yield {
                        "type": "status",
                        "data": {
                            "stage": "generating",
                            "text": f"正在出第 {idx + 1}/{count} 题...",
                        },
                    }
                passed_item: dict | None = None
                passed_notes: list[str] = []
                failures: list[str] = []
                try:
                    for attempt in range(
                        3
                    ):  # v3.2: 2→3 次（首次 + 带反馈重试 2 次），几何/计算题答案错位率实测偏高
                        retry_feedback = ""
                        if attempt > 0:
                            reason = failures or ["JSON 解析失败，未输出合法 JSON"]
                            yield {
                                "type": "status",
                                "data": {
                                    "stage": "regenerating",
                                    "text": "质量检查未通过，正在修正重出...",
                                },
                            }
                            retry_feedback = RETRY_FEEDBACK.format(failures="；".join(reason))
                        quiz_data, raw = await generate_quiz_item(
                            ctx.llm,
                            kp_code=kp_code,
                            kp_name=kp_name,
                            difficulty=difficulty,
                            q_type=q_type,
                            request_id=ctx.request_id,
                            kb_block=kb_block,
                            retry_feedback=retry_feedback,
                            extra_spec=extra_spec,
                            theme_block=theme_block,
                            temperature=0.8 if attempt == 0 else 0.5,
                        )
                        if not quiz_data:
                            failures = ["JSON 解析失败"]
                            continue
                        passed, failures, notes = await self._three_gates(quiz_data, ctx)
                        # 大题规格软闸：点名大题但未分小问 → 判不通过并带反馈重出
                        if (
                            passed
                            and extra_spec
                            and "(1)" not in quiz_data.get("question_text", "")
                        ):
                            passed = False
                            failures = [
                                "未按大题规格分小问：question_text 必须用 (1)(2) 标注 2~3 个递进小问"
                            ]
                        # v3.2 答案对齐自愈：闸因"答案/解析/复核不一致"拦截时，
                        # 先用盲解多数派对齐 answer 再重过闸（救"验算A/键B/解析C"错位），不盲目重出
                        if not passed and await align_choice_answer(
                            quiz_data, failures, ctx.llm, ctx.request_id
                        ):
                            passed, failures, notes = await self._three_gates(quiz_data, ctx)
                            if (
                                passed
                                and extra_spec
                                and "(1)" not in quiz_data.get("question_text", "")
                            ):
                                passed = False
                                failures = [
                                    "未按大题规格分小问：question_text 必须用 (1)(2) 标注 2~3 个递进小问"
                                ]
                        if passed:
                            passed_item = quiz_data
                            passed_notes = notes
                            break
                        logger.info("smart_quiz.gate_failed", attempt=attempt, failures=failures)
                except Exception as e:
                    # 单题异常（如 LLM 瞬时故障）不拖垮整批：跳过该题，已通过的题保留
                    logger.warning("smart_quiz.item_error", idx=idx, error=str(e)[:150])
                    failures = [f"生成异常：{type(e).__name__}"]
                    yield {
                        "type": "status",
                        "data": {
                            "stage": "generating",
                            "text": f"第 {idx + 1} 题生成异常，跳过该题...",
                        },
                    }
                if passed_item is not None:
                    items.append(passed_item)
                    item_notes.append(passed_notes)
                else:
                    last_failures = failures
                    logger.info("smart_quiz.item_failed", idx=idx, failures=failures)

            if not items:
                # v3.4 题库兜底（ADR-11「AI 五闸拦截后题库补位」的正式接线）：
                # 生成全灭时不再直接拒答——结构化题库的真题自带官方答案与解析，
                # 天然可信；且绝不伪装成 AI 生成（ai_generated=False / 「题库真题」标签）。
                bank_items = await self._fallback_from_bank(
                    kp_code=kp_code,
                    q_type=q_type,
                    difficulty=difficulty,
                    count=count,
                    ctx=ctx,
                )
                if bank_items:
                    items = bank_items
                    item_notes = [["题库原题（未经 AI 改写）"] for _ in bank_items]
                    bank_fallback = True
                    logger.info(
                        "smart_quiz.bank_fallback_served", kp_code=kp_code, count=len(bank_items)
                    )
                    yield {
                        "type": "status",
                        "data": {
                            "stage": "generating",
                            "text": "AI 命题未通过质量闸，已从题库调取同类真题（附官方答案与解析）。",
                        },
                    }
                else:
                    # 题库也无同类题 → 诚实降级：绝不出错题
                    reason = "；".join(last_failures) if last_failures else "JSON 解析失败"
                    logger.info("smart_quiz.degraded", reason=reason[:200])
                    if count > 1:
                        degrade_text = (
                            "这几道题我反复出稿都没能通过质量检查"
                            f"（{reason[:80]}），题库也没找到同知识点的题，为了保证不把错题给你，"
                            "这次就不出了。你可以换个知识点或难度再让我试试。"
                        )
                    else:
                        degrade_text = (
                            "这道题我出了两稿都没能通过质量检查"
                            f"（{reason[:80]}），题库也没找到同知识点的题，为了保证不把错题给你，"
                            "这次就不出了。你可以换个知识点或难度再让我试试。"
                        )
                    yield {"type": "token", "data": {"text": degrade_text}}
                    yield {
                        "type": "_result_meta",
                        "data": {"confidence": 0.2, "degraded": True, "skill": "smart_quiz"},
                    }
                    return

            # P1-3 防幻觉评分（迭代18）：已过闸题目逐题评分（B 难度漂移 + C 知识锚定），
            # 分数随卡片下发并落 ai_quality_scores 流水表；评分链路任何失败都不影响出题。
            try:
                from app.services.hallucination_score import persist_scores, score_many

                score_rows = await score_many(
                    items,
                    kp_code=kp_code,
                    kp_name=kp_name,
                    expected_difficulty=difficulty,
                )
                for i, row in enumerate(score_rows):
                    items[i]["hallucination_score"] = {
                        "total_score": row["total_score"],
                        "b_hit": row["b_hit"],
                        "c_similarity": row["c_similarity"],
                        "c_deduction": row["c_deduction"],
                        "note": row["note"],
                    }
                await persist_scores(
                    score_rows,
                    scene="smart_quiz_chat",
                    request_id=getattr(ctx, "request_id", None),
                )
            except Exception as _score_err:
                logger.warning("smart_quiz.score_failed", error=str(_score_err)[:150])

            # 构造输出文本（v1.9：只发一句引导语，不再全量 dump 题干+答案——
            # 旧版 token 与卡片内容完全重复，且 <details> 里答案/解析直接外漏、
            # [[STEP]] 标记裸奔，是学生感知"廉价感"的实锤根因；作答闭环全部在卡片内完成）
            has_choice = any(quiz_data.get("options") for quiz_data in items)
            has_long = any(not quiz_data.get("options") for quiz_data in items)
            # 注意：引导语不用 **加粗**——CJK 标点紧邻 ** 触发 CommonMark  flank 规则，
            # 闭合失败会原样显示星号（实测"**【极限·中等】**出好了"裸奔）
            header = (
                f"【{kp_name}·{_difficulty_cn(difficulty)}】共 {len(items)} 道"
                if count > 1
                else f"【{kp_name}·{_difficulty_cn(difficulty)}】"
            )
            if has_choice and not has_long:
                hint = "出好了 👇 直接点选项作答，我马上判分"
            elif has_long and not has_choice:
                hint = "出好了 👇 在卡片里写答案（可拍照上传手写解答），提交即判分"
            else:
                hint = "出好了 👇 选择题点选项即判分，解答题写步骤（可拍照上传）提交判分"
            output = f"{header}{hint}"
            diagnosis = await self._diagnosis_prefix(message, ctx)
            if diagnosis:
                output = diagnosis + output
            if len(items) < count:
                output += (
                    f"\n\n> 注：另有 {count - len(items)} 道未通过质量检查，已略去，绝不出错题。"
                )
            yield {"type": "token", "data": {"text": output}}

            # v3.3 配图：只在题目确实需要看图时生成，并作为 item.figure 随题卡下发，
            # 由题卡在题干内部渲染「题目配图」；不再先发 figure 事件悬浮在题卡之前。
            fig_by_idx: dict[int, dict] = {}
            for _idx, _fig_item in enumerate(items[:2]):
                _fig_payload = await self._quiz_figure_payload(
                    str(_fig_item.get("question_text", "")),
                    str(_fig_item.get("answer_analysis", "")),
                    ctx,
                )
                if _fig_payload:
                    fig_by_idx[_idx] = _fig_payload

            # 发 card 事件（前端可渲染题卡）
            card_data = {
                "type": "quiz_set",
                "items": [
                    {
                        "item_no": i + 1,
                        "q_type": quiz_data.get("q_type", q_type),
                        "question_text": quiz_data.get("question_text", ""),
                        "options": quiz_data.get("options", []),
                        "answer": quiz_data.get("answer", ""),
                        **({"figure": fig_by_idx[i]} if i in fig_by_idx else {}),
                        # v1.9：解析随卡片下发（[[STEP]] 转编号步骤），判分后卡片内展开
                        "answer_analysis": _format_step_analysis(
                            quiz_data.get("answer_analysis", "")
                        ),
                        "kp_code": kp_code,
                        "kp_name": kp_name,  # v1.3：下发中文知识点名，前端优先展示（英文 code 仅作数据用）
                        # 兜底题为题库原题，难度以题自身标注为准（难度放宽后不得误标为请求难度）
                        "difficulty": quiz_data.get("difficulty") or difficulty,
                        "ai_generated": not bank_fallback,
                        # 迭代15：verified 如实——自检闸跳过的题不得标已验证
                        "verified": _is_fully_verified(item_notes[i]),
                        "self_check": quiz_data.get("self_check"),
                        "gate_notes": item_notes[i],
                        "hallucination_score": quiz_data.get("hallucination_score"),
                        "source": "bank" if bank_fallback else ("kb_variant" if kb_ref else "ai"),
                        "kb_ref": kb_ref["source"] if kb_ref else None,
                        "source_label": _source_label(
                            not bank_fallback,
                            "bank" if bank_fallback else None,
                            kb_ref["source"] if kb_ref else None,
                            _is_fully_verified(item_notes[i]),
                        ),
                    }
                    for i, quiz_data in enumerate(items)
                ],
            }
            yield {"type": "card", "data": card_data}

            # F11：透传题目配图契约（LLM JSON 可选 "graph" 字段；多题取首个带图者，
            # 契约校验在 gateway 统一做，无该字段时行为与现状一致）
            result_meta: dict[str, Any] = {
                "confidence": 0.85,
                "skill": "smart_quiz",
                "kp_code": kp_code,
                "source": "bank" if bank_fallback else ("kb_variant" if kb_ref else "ai"),
                "quality": {
                    "avg_score": (
                        round(
                            sum(
                                it.get("hallucination_score", {}).get("total_score", 100.0)
                                for it in items
                            )
                            / len(items),
                            2,
                        )
                        if items
                        else None
                    ),
                    "scored": len(items),
                },
            }
            graph_payload = next(
                (q.get("graph") for q in items if isinstance(q.get("graph"), dict)),
                None,
            )
            if graph_payload is not None:
                result_meta["graph"] = graph_payload

            yield {
                "type": "_result_meta",
                "data": result_meta,
            }

        except Exception as e:
            logger.error("smart_quiz_failed", error=str(e))
            yield {
                "type": "token",
                "data": {"text": f"出题服务异常: {type(e).__name__}: {str(e)[:150]}"},
            }
            yield {"type": "_result_meta", "data": {"confidence": 0.1, "degraded": True}}

    async def _parse_request(
        self, message: str, ctx: SkillContext
    ) -> tuple[str, str, str, str, str]:
        """从用户消息解析出题参数：KP_MAP 命中直接用；未命中 LLM 小 JSON 抽知识点。
        返回 (kp_code, kp_name, difficulty, q_type, theme)。"""
        # 难度识别（注意"中等难度"含"难"字，先排除中等再判难）
        difficulty = "medium"
        if "简单" in message or "基础" in message or "easy" in message or "低" in message:
            difficulty = "easy"
        elif (
            ("难" in message and "中等" not in message)
            or "hard" in message
            or "挑战" in message
            or "压轴" in message
        ):
            difficulty = "hard"

        # 题型识别
        q_type = "choice"
        if "填空" in message:
            q_type = "blank"
        elif "解答" in message or "大题" in message:
            q_type = "solution"
        elif "判断" in message:
            # 判断题归入 choice（迭代05 C-P2-7：契约 q_type 仅 choice/blank/solution 三枚举）
            q_type = "choice"

        # 知识点识别（长名先匹配，避免"函数"抢走"三角函数"）。
        # PLAN §7.4.5：动态词表 = 静态粗粒度码本 + knowledge_points 结构节点
        # （182 小节 + 5 册级），使消息命中结构节点时题卡携带真实 KP code。
        kp_map = await load_kp_map(ctx)

        # 用户用「」/《》点名的主题是最强信号：不能因为词表里只有「函数」就降级成泛函数。
        # 2026-10-21 修复：'请针对「函数单调性与极值」…出 3 道变式' 曾生成三角函数题。
        quoted = re.search(r"[「『《]([^」』》]{2,30})[」』》]", message)
        if quoted:
            topic = quoted.group(1).strip()
            is_filename = bool(
                re.search(
                    r"\.(?:md|markdown|pdf|docx?|pptx?|txt|tex|png|jpe?g|webp|csv|xlsx?)$",
                    topic,
                    re.I,
                )
            )
            if topic and not is_filename and not _CONCRETE_PROBLEM_RE.search(topic):
                best_code = ""
                best_name = ""
                for code, name in sorted(kp_map.items(), key=lambda x: -len(x[1])):
                    if topic == name:
                        best_code, best_name = code, name
                        break
                    if not best_name and topic in name:
                        best_code, best_name = code, name
                    elif not best_name and name in topic:
                        # 词表只有更粗的「函数」时，保留用户点名的完整主题
                        best_code, best_name = code, topic
                return (
                    best_code or "custom",
                    best_name or topic,
                    difficulty,
                    q_type,
                    self._extract_theme(message),
                )

        for code, name in sorted(kp_map.items(), key=lambda x: -len(x[1])):
            if name in message or code in message:
                return code, name, difficulty, q_type, self._extract_theme(message)

        # KP_MAP 未命中 → LLM 小 JSON 抽取（best-effort，失败回退默认）
        kp_name, theme = await self._extract_kp_via_llm(message, ctx)
        if kp_name:
            return "custom", kp_name, difficulty, q_type, theme
        return "function", "函数", difficulty, q_type, theme

    @staticmethod
    def _extract_theme(message: str) -> str:
        """从"有关X的数学题/关于X的/以X为背景/X主题"中提取主题词（best-effort，无则空串）"""
        import re as _re

        # 常见句式：有关X的数学题 / 关于X的题 / 以X为背景 / 围绕X出题 / X主题 / 帮我出X主题的题
        # 主题 = "有关/关于/围绕/以"后到第一个"的/为"前的短词（≤12 字，含中英文）
        for pat in (
            _re.compile(
                r"(?:有关|关于|围绕|以)([\u4e00-\u9fffA-Za-z0-9]{1,12}?)(?=的|为|主题|背景|$)"
            ),
            _re.compile(
                r"(?:帮我出|出)(?:一道|一题|个)?([\u4e00-\u9fffA-Za-z0-9]{1,12}?)(?=主题|为背景)"
            ),
            _re.compile(r"([\u4e00-\u9fffA-Za-z0-9]{1,12}?)(?=主题|为背景)"),
        ):
            m = pat.search(message)
            if m and m.group(1):
                theme = m.group(1).strip()
                # 截掉尾部动词/名词（"出题/出一/主题的"等），只留主题核心
                for tail in (
                    "出一题",
                    "出一道",
                    "出一",
                    "出题",
                    "主题的",
                    "主题",
                    "背景",
                    "的题",
                    "题",
                ):
                    if theme.endswith(tail) and len(theme) > len(tail):
                        theme = theme[: -len(tail)]
                        break
                # 过滤掉"数学/函数"等考点词（它们是知识点不是主题）
                if theme and theme not in ("数学", "函数", "高中", "高考", "题", "出题", "一"):
                    return theme
        return ""

    async def _extract_kp_via_llm(self, message: str, ctx: SkillContext) -> tuple[str, str]:
        """LLM 抽取知识点名称 + 情境主题；任何失败返回 ("", "")"""
        if not message.strip() or ctx.llm is None:
            return "", ""
        try:
            result = await ctx.llm.chat(
                messages=[{"role": "user", "content": KP_EXTRACT_PROMPT.format(message=message)}],
                temperature=0.0,
                max_tokens=120,
                request_id=ctx.request_id,
                scene="smart_quiz_kp",
            )
            data = parse_quiz_json(result.get("content", ""))
            name = ""
            theme = ""
            if data:
                raw_name = data.get("kp_name")
                if isinstance(raw_name, str) and raw_name.strip() and len(raw_name.strip()) <= 20:
                    name = raw_name.strip()
                raw_theme = data.get("theme")
                if (
                    isinstance(raw_theme, str)
                    and raw_theme.strip()
                    and len(raw_theme.strip()) <= 20
                ):
                    theme = raw_theme.strip()
            if name:
                logger.info("smart_quiz.kp_extracted", kp_name=name, theme=theme)
            return name, theme
        except Exception as e:
            logger.warning("smart_quiz.kp_extract_failed", error=str(e)[:120])
            return "", ""

    async def _retrieve_question_bank_prototype(
        self, kp_name: str, ctx: SkillContext
    ) -> dict | None:
        """question_bank 母题预检：知识点词 + 变式触发词对题干 ILIKE，真题随机抽一。

        901 行小表全扫毫秒级，无新依赖；命中的是带官方答案解析的高考真题，
        作为出题原型（KB_BLOCK 底稿）优先级高于知识库切片。
        """
        from sqlalchemy import text

        kw = (kp_name or "").strip()
        if not kw:
            return None
        sql = text("""
            SELECT stem, analysis, source, q_type
            FROM question_bank
            WHERE deleted_at IS NULL AND scope = 'student'
              AND (stem ILIKE :pat OR analysis ILIKE :pat)
            ORDER BY random()
            LIMIT 1
            """)
        row = (await ctx.db.execute(sql, {"pat": f"%{kw}%"})).first()
        if row is None:
            return None
        stem, analysis, source, q_type = row
        content = f"【{q_type}】{stem}\n【解析】{(analysis or '')[:600]}"
        logger.info(
            "smart_quiz.qb_hit",
            source=source,
            kp=kw,
        )
        return {"content": content[:800], "source": f"高考真题·{source}"}

    async def _guess_kp_from_text(self, text_src: str, ctx: SkillContext) -> str | None:
        """从种子题文本做确定性 kp 匹配（变式链题库兜底的定位依据）。

        匹配 knowledge_points 的 name/aliases（文本包含即命中），取名字最长者
        （更具体）。零 LLM、零幻觉；不命中返回 None（由调用方走诚实路径）。
        """
        t = (text_src or "").strip()[:400]
        if not t:
            return None
        from sqlalchemy import text

        try:
            row = await ctx.db.execute(
                text(
                    "SELECT kp.code FROM knowledge_points kp "
                    "WHERE :t ILIKE '%' || kp.name || '%' "
                    "   OR EXISTS (SELECT 1 FROM unnest(kp.aliases) AS a "
                    "              WHERE :t ILIKE '%' || a || '%') "
                    "ORDER BY length(kp.name) DESC NULLS LAST LIMIT 1"
                ),
                {"t": t},
            )
            return row.scalar() or None
        except Exception as e:  # noqa: BLE001
            logger.warning("smart_quiz.kp_guess_failed", error=str(e)[:120])
            return None

    async def _fallback_from_bank(
        self, *, kp_code: str, q_type: str, difficulty: str, count: int, ctx: SkillContext
    ) -> list[dict]:
        """AI 三闸全灭时的题库兜底供题（ADR-11），返回卡片 item 结构的列表。

        复用 ``question_supply.supply_questions`` 的全部治理过滤：scope=student、
        排除竞赛/超纲标记、kp 双向粒度展开、难度不足时放宽、hash 环回轮换——
        因此不会把 research 端素材或超纲题供给学生，也不会重复同一题。

        只供**未经 AI 改写**的题库原题（调用方据返回值标 ai_generated=False 与
        「题库真题」来源），绝不把题库内容伪装成 AI 生成。
        best-effort：任何异常均返回空列表，由调用方走原有诚实降级。
        """
        if not kp_code:
            return []
        try:
            from app.skills.question_supply import supply_questions

            rows = await supply_questions(
                ctx.db,
                kp_codes=[kp_code],
                q_type=q_type or None,
                difficulty=difficulty or None,
                count=max(1, min(count, 3)),
                # 必须开启：把「题干/答案非空 + 选择题答案能匹配到选项 key」下推到 SQL。
                # 否则可能供出 answer 与 options 不匹配的坏题——前端 isRightOption 恒为
                # false，学生选对也被判错，比直接拒答更糟。
                publishable_only=True,
            )
        except Exception as e:
            logger.warning("smart_quiz.bank_fallback_failed", error=str(e)[:150])
            return []

        if not rows:
            logger.info("smart_quiz.bank_fallback_empty", kp_code=kp_code)
            return []

        fallback: list[dict] = []
        for row in rows:
            options, answer = _bank_options_to_card(row.options, row.answer)
            if not (row.stem or "").strip() or not answer:
                continue  # 题库坏行：宁缺勿滥，不因凑数把残缺题给学生
            fallback.append(
                {
                    "question_text": row.stem,
                    "options": options,
                    "answer": answer,
                    "answer_analysis": row.analysis or "",
                    "difficulty": row.difficulty,
                    "q_type": row.q_type,
                    "self_check": None,
                }
            )
        return fallback

    async def _retrieve_kb_prototype(
        self, kp_name: str, difficulty: str, message: str, ctx: SkillContext
    ) -> dict | None:
        """母题检索（题库优先 → RAG 回落，best-effort：任何异常都返回 None）

        v3.2：question_bank 已导入高考真题（901+，scripts/import_gaokao_bench.py），
        先按知识点词对题库题干做 ILIKE 预检（真题优先级高于知识库切片，且自带
        官方答案解析）；题库未命中再走 chunks RAG 语义检索。
        """
        try:
            hit = await self._retrieve_question_bank_prototype(kp_name, ctx)
            if hit:
                return hit
        except Exception as e:
            logger.info("smart_quiz.qb_prototype_failed", error=str(e)[:120])
        if ctx.rag is None:
            return None
        try:
            query = f"{kp_name} {message}".strip()[:200]
            result = await ctx.rag.retrieve(
                query,
                db=ctx.db,
                conversation_history=[],
                conversation_id=ctx.conversation_id,
                request_id=ctx.request_id,
                scope="student",  # 端隔离：出题原型不得引用教师用书/超纲资料
            )
            if not getattr(result, "answerable", False) or not result.chunks:
                return None
            top = result.chunks[0]
            # 相关度门槛：原型必须足够贴题（BGE-M3 实测强相关 ≥0.55；
            # 低于此值的多为跑偏切片——若以其为原型会把考点带偏，如要极限给导数）
            if getattr(top, "raw_score", 0.0) < 0.55:
                logger.info(
                    "smart_quiz.kb_prototype_low_rel",
                    score=round(getattr(top, "raw_score", 0.0), 3),
                )
                return None
            content = (top.content or "").strip()
            # 原型门槛：足够长且像一道题（含已知/求/题字样）
            if len(content) < 50 or not re.search(r"已知|求|证明|如图|题", content):
                return None
            logger.info(
                "smart_quiz.kb_hit",
                chunk_id=top.chunk_id,
                score=round(top.score, 3),
                source=top.doc_title,
            )
            return {"content": content[:800], "source": top.doc_title or "题库"}
        except Exception as e:
            logger.warning("smart_quiz.kb_retrieve_failed", error=str(e)[:120])
            return None

    def _parse_quiz_json(self, raw: str) -> dict | None:
        """从 LLM 输出解析 JSON（委托模块级 parse_quiz_json）"""
        return parse_quiz_json(raw)

    async def _describe_variant_figure(self, image_file_ids: list, ctx) -> str | None:
        """om5：变式链读学生上传的原题图（多模态 MiMo）→ 结构化图形描述。
        用于"如图"类错题的变式出题与配图；任何失败返回 None（退化为纯文字）。"""
        import uuid as _uuid

        import httpx

        from app.config import settings as _settings
        from app.services.geogebra_figure import resolve_image_data_uri

        if not (_settings.deepseek_api_key and _settings.deepseek_base_url):
            return None
        data_uri = None
        for fid in (image_file_ids or [])[:1]:
            try:
                data_uri = await resolve_image_data_uri(
                    ctx.db, _uuid.UUID(str(fid)), _uuid.UUID(ctx.user_id)
                )
            except Exception:
                data_uri = None
            if data_uri:
                break
        if not data_uri:
            return None
        prompt = (
            "这是高中数学题的原题图。请只描述你看到的事实，不要解题、不要推理：\n"
            "1. 图形类型与全部顶点/关键点的字母标注；\n"
            "2. 各点的相对位置（上下左右前后）；\n"
            "3. 哪些线实线、哪些虚线；\n"
            "4. 图中标注的长度、角度、记号。\n"
            "用简洁中文分点输出，不超过 200 字。"
        )
        channel = _settings.vision_channel()
        if channel is None:
            return None
        _vision_url, _vision_key, _vision_model = channel
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                resp = await c.post(
                    (
                        _vision_url
                        if "/chat/completions" in _vision_url
                        else _vision_url.rstrip("/").removesuffix("/chat/completions")
                        + "/chat/completions"
                    ),
                    headers={"Authorization": f"Bearer {_vision_key}"},
                    json={
                        "model": _vision_model,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": data_uri}},
                                ],
                            }
                        ],
                        "temperature": 0.1,
                        "max_tokens": 500,
                    },
                )
            resp.raise_for_status()
            return ((resp.json().get("choices") or [{}])[0].get("message") or {}).get(
                "content"
            ) or None
        except Exception as e:
            logger.info("smart_quiz.variant_figure_desc_failed", error=str(e)[:140])
            return None

    async def _quiz_figure_payload(
        self,
        question: str,
        analysis: str,
        ctx: SkillContext,
        figure_desc: str | None = None,
    ) -> dict | None:
        """出题配图（v3.2）：只有题目确实需要看图时，才生成随题下发的“题目配图”。

        旧实现把 figure 作为独立 SSE 事件在题卡**之前**发射，且变式链会把原题图的
        视觉描述强行注入每道变式，导致纯计算变式上方出现一张与原题相关、与本题无关的
        “步骤 1”图（2026-10-21 用户反馈）。现在改为：
        - 只在题目含图形/图象/几何等视觉语义时生成；
        - 配图作为 quiz item.figure 随题卡下发，前端渲染在题干内部（题目配图），
          不再悬浮在题卡之前；
        - 不设 step_no（它不是讲解步骤），也不提供 GGB“动态演示”入口。
        任何失败静默跳过——配图绝不阻断出题。
        """
        try:
            from app.services.figure_renderer import render_figure_frames
            from app.skills.socratic_solver.figures import parse_figure_plan, should_plan_figures
            from app.skills.socratic_solver.prompts import (
                FIGURE_PLANNER_RETRY,
                FIGURE_PLANNER_SYSTEM,
                FIGURE_PLANNER_USER,
            )

            if not _quiz_question_needs_figure(question) or not should_plan_figures(question):
                return None
            steps = [
                {"assertion": s.strip()}
                for s in re.split(r"【STEP】|\[\[STEP\]\]|\n+", analysis or "")
                if s.strip()
            ][:9] or [{"assertion": question}]
            steps_block = "\n".join(f"第{i}步：{s['assertion']}" for i, s in enumerate(steps, 1))
            raw = ""
            error: str | None = "first"
            figure_desc_block = ""
            if figure_desc:
                figure_desc_block = (
                    "\n【原题图描述（视觉模型读学生上传的原题图所得，构图必须与该描述一致，"
                    "顶点相对位置/虚实线以此为准）】\n" + figure_desc + "\n"
                )
            for attempt in range(2):
                user = (
                    FIGURE_PLANNER_RETRY.format(question=question, error=error or "")
                    if attempt > 0
                    else FIGURE_PLANNER_USER.format(question=question, steps_block=steps_block)
                )
                if figure_desc_block:
                    user = user.replace("【分步参考解】", figure_desc_block + "\n【分步参考解】")
                result = await ctx.llm.chat(
                    messages=[
                        {"role": "system", "content": FIGURE_PLANNER_SYSTEM},
                        {"role": "user", "content": user},
                    ],
                    temperature=0.2,
                    max_tokens=1400,
                    request_id=getattr(ctx, "request_id", None) or "quiz-figure",
                    scene="socratic_figure_plan",
                )
                raw = (result or {}).get("content") or ""
                items, error = parse_figure_plan(raw, len(steps))
                if error is None:
                    break
            if error is not None or not items:
                return None
            payload = render_figure_frames(
                items[0]["figure"], step_no=None, caption="", frame_limit=1
            )
            payload["role"] = "question"
            return payload
        except Exception as e:
            logger.info("smart_quiz.figure_skipped", error=str(e)[:140])
            return None

    async def _three_gates(
        self, quiz_data: dict, ctx: SkillContext
    ) -> tuple[bool, list[str], list[str]]:
        """三闸验证（委托模块级 run_quiz_gates，迭代05 单一实现）
        + 闸 5 答案键独立黑盒复核（N2：判分键错位事故防复发）"""
        passed, failures, notes = await run_quiz_gates(quiz_data)
        if passed and getattr(ctx, "llm", None) is not None:
            ok, reason, note = await verify_answer_key(
                quiz_data, ctx.llm, getattr(ctx, "request_id", "") or "quiz-key-verify"
            )
            if note:
                notes.append(note)
            if not ok:
                passed, failures = False, [reason]
        return passed, failures, notes

    # ------------------------------------------------------------------ #
    #  v2.1 变式链：基于用户原题生成难度递进的变式（M2 重构增量）
    # ------------------------------------------------------------------ #
    _VARIANT_TRIGGERS = (
        "变式",
        "变形",
        "类似的题",
        "类似的",
        "举一反三",
        "再来一题",
        "再来一道",
        "换个数字",
        "换个数",
        "换种出法",
        "换种考法",
        "同类题",
        "变着法",
        "改编一下",
        "照这个出",
        "以这题",
        "照这道题",
        "基于这道题",
    )

    @staticmethod
    def _detect_user_variant(message: str) -> bool:
        """检测是否「用户贴了完整题目 + 要求出变式」。
        需要同时满足：含变式触发词 + 含可识别的题干结构（方程/函数式/求/证明等），
        而不是只出现「函数/导数」这类学科词。
        """
        if not message or not any(t in message for t in SmartQuizExecutor._VARIANT_TRIGGERS):
            return False
        return bool(_CONCRETE_PROBLEM_RE.search(message))

    async def _diagnosis_prefix(self, message: str, ctx: SkillContext) -> str:
        """「先诊断再出题」时，把真实学情画像放到题卡之前；无数据/失败则诚实省略。"""
        if not any(w in message for w in _DIAGNOSIS_WORDS):
            return ""
        db = getattr(ctx, "db", None)
        uid = getattr(ctx, "user_id", None)
        if db is None or not uid:
            return ""
        try:
            from app.services.learning_profile import get_learning_profile_service

            card = await get_learning_profile_service().build_profile_card_text(
                db, uid, role=getattr(ctx, "user_role", None) or "student"
            )
            if not card:
                return ""
            return "先看你的学情诊断：\n" + card + "\n\n"
        except Exception as e:  # noqa: BLE001 - 诊断失败不阻断出题
            logger.info("smart_quiz.diagnosis_failed", error=str(e)[:120])
            return ""

    async def _recent_tutor_question(self, ctx: SkillContext) -> str | None:
        """回落取本会话最近一次引导解题的题干作变式种子（迭代10 v1.4）。

        对话内引导完成/查看答案后，学生单说「举一反三」时消息里没有题目，
        用 tutor_sessions 里最近的题干补齐上下文；查不到返回 None（走原有出题逻辑）。
        """
        try:
            from sqlalchemy import select

            from app.models.tutor_session import TutorSession

            conv_id = getattr(ctx, "conversation_id", None)
            db = getattr(ctx, "db", None)
            if not conv_id or db is None:
                return None
            rows = await db.execute(
                select(TutorSession.question_text)
                .where(
                    TutorSession.conversation_id == conv_id,
                    TutorSession.deleted_at.is_(None),
                )
                .order_by(TutorSession.updated_at.desc())
                .limit(1)
            )
            q = rows.scalar_one_or_none()
            return (q or "").strip() or None
        except Exception as e:
            logger.info("smart_quiz.variant_seed_failed", error=str(e)[:120])
            return None

    async def _run_user_variant_chain(
        self,
        message: str,
        ctx: SkillContext,
        *,
        seed: str | None = None,
        image_file_ids: list[str] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """变式链主流程（v1.6 逐题渐进版）：原题 → 逐道 LLM 出变式（每道过好闸立刻发卡）
        → 学生实时看到第 i/N 道进度与题卡逐道出现，不再整链憋几十秒零反馈。

        v1.11：种子显式传入（seed 参数），不再拼进 message 靠"取最后一行"反推——
        旧提取 `lines[-1]` 对「种子\n触发词」三段式会错把触发词当原题。
        无 seed 时从消息剔除纯触发词短行，保留题干行（题干可跨多行，join 保留）。"""
        import uuid

        q = (seed or "").strip()
        # om5：带原图附件的"如图"错题 → 视觉读图一次，描述注入变式出题与配图
        figure_desc = (
            await self._describe_variant_figure(image_file_ids or [], ctx)
            if image_file_ids
            else None
        )
        if not q:
            lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
            stem_lines = [
                ln
                for ln in lines
                if not (len(ln) <= 30 and any(t in ln for t in self._VARIANT_TRIGGERS))
            ]
            q = "\n".join(stem_lines or lines)
        q = q[:400]

        count = _parse_count(message)
        yield {
            "type": "status",
            "data": {
                "stage": "generating",
                "text": f"已收到你的原题，正在逐道生成 {count} 道难度递进的变式（举一反三）...",
            },
        }
        # token 引导语先到位（对齐愿景 03 对话内轻练），题卡逐道跟上
        yield {
            "type": "token",
            "data": {
                "text": f"基于你刚才那道题，给你 {count} 道难度递进的变式"
                "——一道一道来，直接点选项作答，我马上判分 👇"
            },
        }

        # v1.6 逐题渐进生成：一次出 1 道、过好闸立刻发卡（修复"47s 零反馈被当成卡死取消"）。
        # 单题失败带原因反馈重出 1 次，仍失败跳过该题——不再整链陪葬。
        targets = _chain_difficulty_targets(count)
        chain_id = uuid.uuid4().hex[:12]
        passed = 0
        prev_stems: list[str] = []
        for i, target in enumerate(targets):
            yield {
                "type": "status",
                "data": {
                    "stage": "generating",
                    "text": f"正在出第 {i + 1}/{len(targets)} 道变式（{_CHAIN_DIFFICULTY_CN[target]}档）...",
                },
            }
            fig_block = (
                (
                    "\n【原题图描述（视觉模型读学生上传的原题图所得，出变式时图形信息以此为准，"
                    "变式题面对图形的文字描述必须与该描述自洽）】\n" + figure_desc + "\n"
                )
                if figure_desc
                else ""
            )
            quiz_data, gate_notes = await self._gen_one_variant(
                q, i, len(targets), target, prev_stems, ctx, figure_desc_block=fig_block
            )
            if quiz_data is None:
                # 供应商降级（2026-09-14）：豆包 mini 生成+闸门全灭 → 换讯飞 235B 再战一轮。
                # 换供应商不降闸门：质量红线不变，只是给生成侧另一次机会。
                quiz_data, gate_notes = await self._gen_one_variant(
                    q,
                    i,
                    len(targets),
                    target,
                    prev_stems,
                    ctx,
                    figure_desc_block=fig_block,
                    scene="smart_quiz_variant_fb",
                )
            if quiz_data is None:
                yield {
                    "type": "status",
                    "data": {
                        "stage": "generating",
                        "text": f"第 {i + 1} 道质量检查没过（已换模型重试），已跳过（不把错题给你）",
                    },
                }
                continue
            passed += 1
            prev_stems.append(str(quiz_data.get("question_text", ""))[:240])
            # v3.3 配图：只在变式题本身含图形语义时生成，并放进题卡题干内部；
            # figure_desc 只在该变式确实需要读图时才随 planner 下发，避免把原题图
            # 强行套到「求导数」这类纯计算变式上（2026-10-21 用户反馈根因）。
            _variant_fig = await self._quiz_figure_payload(
                str(quiz_data.get("question_text", "")),
                str(quiz_data.get("answer_analysis", "")),
                ctx,
                figure_desc=figure_desc,
            )
            yield {
                "type": "card",
                "data": {
                    "type": "quiz_set",
                    "mode": "chat_light",
                    "chain": "variant",
                    "chain_id": chain_id,
                    "chain_total": len(targets),
                    "source": "user_variant",
                    "items": [
                        {
                            "item_no": passed,
                            "q_type": "choice",
                            "question_text": quiz_data.get("question_text", ""),
                            "options": quiz_data.get("options", []),
                            "answer": quiz_data.get("answer", ""),
                            "answer_analysis": quiz_data.get("answer_analysis", ""),
                            **({"figure": _variant_fig} if _variant_fig else {}),
                            "kp_code": "custom",
                            "kp_name": "变式链",
                            "difficulty": quiz_data.get("difficulty", target),
                            "ai_generated": True,
                            # 迭代15：verified 如实（同主路径口径）
                            "verified": _is_fully_verified(gate_notes),
                            "gate_notes": gate_notes,
                            "source": "user_variant",
                            "source_label": _source_label(
                                True, "user_variant", None, _is_fully_verified(gate_notes)
                            ),
                            "variant_note": quiz_data.get("variant_note", ""),
                        }
                    ],
                },
            }

        if not passed:
            # 题库兜底（2026-09-14，三层兜底的最后一层）：学生不可能接受"不出题"——
            # 用种子题文本做确定性 kp 匹配 → 供给同知识点真题（标注"题库真题"，绝不伪装 AI 生成）。
            bank_kp = await self._guess_kp_from_text(q, ctx)
            bank_items: list[dict] = []
            if bank_kp:
                bank_items = await self._fallback_from_bank(
                    kp_code=bank_kp, q_type="choice", difficulty="medium", count=2, ctx=ctx
                )
            if bank_items:
                yield {
                    "type": "status",
                    "data": {
                        "stage": "generating",
                        "text": "AI 变式未通过质量检查，已从题库为你挑选同知识点真题 👇",
                    },
                }
                yield {
                    "type": "card",
                    "data": {
                        "type": "quiz_set",
                        "mode": "chat_light",
                        "chain": "variant",
                        "chain_id": chain_id + "b",
                        "chain_total": len(bank_items),
                        "source": "bank",
                        "items": [
                            {
                                "item_no": bi + 1,
                                "q_type": "choice",
                                "question_text": it.get("question_text", ""),
                                "options": it.get("options", []),
                                "answer": it.get("answer", ""),
                                "answer_analysis": it.get("answer_analysis", ""),
                                "kp_code": bank_kp,
                                "kp_name": "变式链·题库真题",
                                "difficulty": it.get("difficulty", "medium"),
                                "ai_generated": False,
                                "verified": False,
                                "source": "bank",
                                "source_label": "题库真题 · 同知识点巩固",
                            }
                            for bi, it in enumerate(bank_items)
                        ],
                    },
                }
                passed = len(bank_items)
            else:
                yield {
                    "type": "token",
                    "data": {
                        "text": "变式链质量检查未通过（保证不把错题给你），这次就不出了。"
                        "你可以换个说法，或直接说「出一道导数中等题」按知识点出题。"
                    },
                }
                yield {"type": "_result_meta", "data": {"confidence": 0.2, "degraded": True}}
                return

        yield {
            "type": "status",
            "data": {"stage": "generating", "text": f"{passed} 道变式已全部出好，开始作答吧 ✍️"},
        }
        yield {
            "type": "_result_meta",
            "data": {
                "confidence": 0.85,
                "skill": "smart_quiz",
                "source": "user_variant",
            },
        }

    async def _gen_one_variant(
        self,
        question: str,
        idx: int,
        total: int,
        target: str,
        prev_stems: list[str],
        ctx: SkillContext,
        figure_desc_block: str = "",
        scene: str = "smart_quiz_variant",
    ) -> tuple[dict | None, list[str]]:
        """生成 1 道变式并过三闸 + 答案字母归一；失败带原因反馈重出 1 次，仍失败返回 (None, [])。"""
        retry_block = ""
        for attempt in range(
            3
        ):  # v3.2: 2→3 次（首次 + 带反馈重试 2 次），几何/计算题答案错位率实测偏高
            if prev_stems:
                prev_lines = "\n".join(f"{j + 1}. {s}" for j, s in enumerate(prev_stems))
                prev_block = f"【已出过的变式（禁止重复/雷同）】\n{prev_lines}\n"
            else:
                prev_block = ""
            prompt = VARIANT_CHAIN_ITEM_PROMPT.format(
                question=question + (figure_desc_block or ""),
                idx=idx + 1,
                total=total,
                difficulty=target,
                difficulty_cn=_CHAIN_DIFFICULTY_CN.get(target, target),
                difficulty_hint=_CHAIN_DIFFICULTY_HINT.get(target, ""),
                variant_way=_CHAIN_VARIANT_WAYS[min(idx, len(_CHAIN_VARIANT_WAYS) - 1)],
                prev_block=prev_block,
                kp_code="custom",
                retry_block=retry_block,
            )
            try:
                result = await ctx.llm.chat(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7,
                    max_tokens=6000,  # v3.2: 4000→6000（几何题审题+完整解析+自检在 4000 处实测截断致 JSON 坏）
                    request_id=ctx.request_id,
                    scene=scene,
                )
                raw = result.get("content", "")
            except Exception as e:
                logger.error("smart_quiz.variant_llm_error", error=str(e)[:120])
                return None, []

            data = parse_quiz_json(raw)
            if isinstance(data, list):
                data = data[0] if data and isinstance(data[0], dict) else None
            if not isinstance(data, dict):
                data = _json_loads_lenient(raw)
                if isinstance(data, list):
                    data = data[0] if data and isinstance(data[0], dict) else None
            if not isinstance(data, dict):
                logger.info(
                    "smart_quiz.variant_parse_failed",
                    raw=raw[:800],
                    raw_len=len(raw),
                    tail=raw[-200:],
                )
                retry_block = (
                    "【上次生成未通过质量检查】\n输出不是合法 JSON 对象，请只输出严格 JSON。\n"
                )
                continue

            ok, failures, notes = await self._three_gates(data, ctx)
            letter = _normalize_choice_letter(data) if ok else ""
            # v3.2 答案对齐自愈：闸判"答案不一致"或答案无法归一为字母时，
            # 先盲解多数派对齐 answer 再重过闸（救"验算A/键B/解析C"三处错位），不直接弃题
            if ok and not letter:
                failures = ["answer 无法归一为选项字母（与解析结论/独立复核可能错位）"]
                ok = False
            if not ok and await align_choice_answer(data, failures, ctx.llm, ctx.request_id):
                ok, failures, notes = await self._three_gates(data, ctx)
                letter = _normalize_choice_letter(data) if ok else ""
            # v1.9 雷同闸：核心公式集与已出变式有交集 → 判失败重出
            # （实锤样例：3 道变式全是 f(x)=cosx+1/cosx 换定义域——核心对象没换就是假变式）
            if (
                ok
                and letter
                and _is_clone_variant(str(data.get("question_text") or ""), prev_stems)
            ):
                ok = False
                failures = [
                    "与已出变式雷同：核心公式/考查对象未更换，必须换一个不同的函数/数列/图形/情境"
                ]
            if ok and letter:
                data["q_type"] = "choice"
                data["answer"] = letter
                data["answer_analysis"] = _format_step_analysis(data.get("answer_analysis", ""))
                return data, notes
            reasons = failures or ["answer_not_letter"]
            logger.info(
                "smart_quiz.variant_gate_failed", idx=idx, attempt=attempt, failures=reasons
            )
            retry_block = (
                "【上次生成未通过质量检查】\n"
                + "；".join(reasons)
                + "\n请针对性修正后重新生成（保持难度定位不变）。\n"
            )
        return None, []


def _difficulty_cn(d: str) -> str:
    return {"easy": "基础", "medium": "中等", "hard": "提高"}.get(d, d)


def _normalize_choice_letter(item: dict) -> str:
    """把变式题答案归一成选项字母 A-D（v1.5 对话内轻练判分依赖字母）。

    兼容模型常见写法：已是字母（"A"/"b"）、带选项前缀（"A. $2^n$"）、
    "故选 C"话术、或直接给选项文本（与 4 个选项逐一比对）。都找不到返回空串。
    """
    ans = str(item.get("answer") or "").strip()
    opts = item.get("options") or []
    if not ans:
        return ""
    m = re.match(r"^([A-Da-d])(?=[.、．\s]|$)", ans)
    if m:
        return m.group(1).upper()
    m = re.search(r"故?选\s*[:：]?\s*([A-Da-d])", ans)
    if m:
        return m.group(1).upper()

    def _norm(s: Any) -> str:
        s = re.sub(r"^[A-Da-d][.、．]?\s*", "", str(s))
        return re.sub(r"\s+", "", s).lower()

    a = _norm(ans)
    if a:
        for i, o in enumerate(opts[:4]):
            if _norm(o) == a:
                return "ABCD"[i]
    return ""


def _stem_formulas(text: str) -> set[str]:
    """提取题干 $...$ 公式并归一化：去 LaTeX 命令与非算数字符，保留数字与运算符。
    换数字/换参数的合法变式归一化后仍不同（数字保留），不会误伤。"""
    out: set[str] = set()
    for m in re.findall(r"\$([^$]+)\$", str(text)):
        f = re.sub(r"\\[a-zA-Z]+", "", m)
        f = re.sub(r"[^0-9A-Za-z=+\-*/^()]", "", f)
        if len(f) >= 5:
            out.add(f)
    return out


def _is_clone_variant(question_text: str, prev_stems: list[str]) -> bool:
    """雷同判定：新题核心公式集与任一已出变式有交集（归一化公式相等）即雷同。"""
    cur = _stem_formulas(question_text)
    if not cur:
        return False
    return any(cur & _stem_formulas(prev) for prev in prev_stems)


def _format_step_analysis(text: str) -> str:
    """answer_analysis 里的 [[STEP]] 标记转成编号步骤（markdown 加粗小标题），无标记原文返回。"""
    if not text or "[[STEP]]" not in text:
        return text or ""
    parts = [p.strip() for p in str(text).split("[[STEP]]") if p.strip()]
    return "\n\n".join(f"**第 {i} 步** {p}" for i, p in enumerate(parts, 1))


def _parse_count(message: str) -> int:
    """出题数量：默认 1；'两道/2道'→2；'几道/几题/三道/一些/一组'→3；显式数字封顶 3（控制耗时）"""
    m = re.search(r"(\d+)\s*道", message)
    if m:
        return max(1, min(3, int(m.group(1))))
    if "两道" in message:
        return 2
    if (
        "几道" in message
        or "几题" in message
        or "三道" in message
        or "一些" in message
        or "一组" in message
    ):
        return 3
    return 1
