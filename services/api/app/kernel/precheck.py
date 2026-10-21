"""L1-1 全局状态机 · 预检管线（迭代15 B7）

状态机主链：入站 → 预检 → 路由 → 执行 → 封装 → 输出。
本模块是「预检」阶段：意图路由（L0/L2/L3）之前的确定性有序检测，
各阶段按固定优先级短路，保证同一句消息永远得到同一个结论（L1-3 确定性）。

阶段优先级（方案 04 第二部分 L1-1，B-C0 精简版全采）：

    情绪 emotion > 考试 exam > 变式 variant > 复习 review > 求解 solve > 闲聊 smalltalk

六分类路由表（B-C4 全采，预检与 LLM 路由的分工）：

    | 类别 | 确定性信号 | 去向 |
    |------|-----------|------|
    | 情绪 | 强负向词库（规则先行）+ LLM 双判（B8：搭车路由 FC 调用，零额外延迟） | chat + emotion 参数（B8 完整层：三段式/轮数上限/连续受挫干预/正反馈） |
    | 考试 | match_practice_intent | open_page 练题中心（迭代14 手法，不在对话手写整套卷） |
    | 变式 | 变式触发词 + 讲解/pinned 护栏 | smart_quiz 变式链 / socratic 讲解（v1.11+B2 护栏） |
    | 复习 | 到期复习/任务查询 | chat 管家数据注入链路（_butler_lookup，现状保持，本阶段仅占位排序） |
    | 求解 | 承接词/题干结构信号 | socratic_solver（router L0 承接词 + L2 FC，不在预检重复） |
    | 闲聊 | 默认兜底 | chat（router 默认分支） |

参考架构（05/05b 调研 + B7/B8 补充）：
- Rasa rule_policy：显式规则优先于 ML 策略，与本管线「确定性预检 > LLM 路由」同构；
- Rasa FallbackClassifier 两段式兜底：对应 router L3 低置信澄清（B7 起澄清选项候选感知）；
- OpenAI Swarm routines：每类意图一个确定性入口（handoff），对应六分类表；
- facebookresearch/EmpatheticDialogues：情绪先标注再回应——情绪阶段永远最先（求助优先于任务）。
- ESConv（ACL 2021）：探索→安抚→行动三阶段 + 八策略频率（提问最高频、情感反映必备）；
- MultiESC（EMNLP 2022）：策略随轮数推进（lookahead 轻量规则版：R1-R2 三段式，R3+ 收束回流）；
- FailedESConv 负样本判据：情绪强度不降=失败对话 → 轮数上限 2 轮的实证依据。

注意：情绪检测必须保守——含实质任务内容的轻度吐槽（"这题好烦怎么做"）不劫持，
只有强痛苦信号或无任务内容的情绪表达才优先响应。
"""

import re
from dataclasses import dataclass

import structlog

from app.kernel.router import RouteDecision, _has_task_substance
from app.services.platform_context import match_practice_intent

logger = structlog.get_logger()

# 「把这道题加入错题本 + 错因」是真实写操作，必须确定性进 chat 执行，
# 不能交给模型自由发挥（历史实现只有 seed 文案，没有落库）。
_ERROR_BOOK_ADD_RE = re.compile(
    r"(?:加入|收录|记入|放进|存进|添加).{0,10}错题本|错题本.{0,10}(?:加入|收录|记入|放进|存进|添加)"
)

# ------------------------------------------------------------------ #
# 阶段 1 · 情绪（L1-5 种子：词库规则先行；LLM 双判/连续受挫干预在 B8）
# ------------------------------------------------------------------ #

# 强痛苦信号：命中即最优先响应（即使消息带任务内容，先处理人再处理题）
_STRONG_DISTRESS = (
    "不想学",
    "不想活",
    "放弃学习",
    "想放弃",
    "崩溃了",
    "要崩溃",
    "考砸了",
    "考废了",
    "抑郁",
    "焦虑得",
    "压力好大",
    "受不了了",
    "撑不住",
    "讨厌数学",
    "恨数学",
)

# 轻度受挫：仅当消息无实质任务内容时才判定为情绪求助
# （"这题好烦怎么做"仍走解题——题比情绪具体时先解题）
_MILD_FRUSTRATION = (
    "好烦",
    "烦死",
    "难过",
    "沮丧",
    "心累",
    "emo",
    "学不进去",
    "没信心",
    "自卑",
)

# 情绪标签（供 chat 共情提示与后续 L1-5 干预统计）
_EMOTION_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("挫败", ("学不会", "考砸", "考废", "没信心", "自卑")),
    ("焦虑", ("焦虑", "压力", "撑不住", "受不了")),
    ("厌倦", ("不想学", "讨厌数学", "恨数学", "放弃", "学不进去")),
    ("低落", ("难过", "沮丧", "心累", "emo", "好烦", "烦死", "崩溃", "抑郁", "不想活")),
)

# 正向信号（迭代15 B8，B-C3「正反馈夸行为不夸人格」）：学生报喜/进步/感谢。
# 与轻度受挫同一护栏：仅当消息无实质任务内容时才判定
# （"我做对了，再出一道更难的"仍走出题——题比情绪具体时先解题）。
_POSITIVE_SIGNALS = (
    "我做对了",
    "做出来了",
    "终于懂了",
    "我懂了",
    "全对",
    "都会了",
    "考好了",
    "进步了",
    "太开心了",
    "好开心",
    "谢谢老师",
)

# 合法情绪标签全集（router LLM 双判参数的枚举校验复用，脏值一律丢弃）
EMOTION_LABELS: frozenset[str] = frozenset([label for label, _ in _EMOTION_LABELS] + ["喜悦"])

# 求解承接词/讲解意图（v1.11 护栏②用；自 agent_router 迁入内核，行为不变）
EXPLAIN_INTENT_RE = re.compile(r"讲解|讲透|讲讲|讲一讲|教我|帮我理解|带我做|怎么做|怎么解|思路")
CONTEXT_REF_RE = re.compile(r"刚才|刚刚|这道|这题|那道|原题|上述|该题|错题")


def detect_emotion(message: str) -> str | None:
    """情绪词库检测（规则先行）。返回情绪标签或 None。

    强痛苦信号无条件命中（L1-1：情绪求助永远最先响应）；
    轻度受挫词只在消息无实质任务内容时命中（避免劫持解题请求）。
    B8：正向信号（报喜/进步/感谢）同样受任务实质护栏约束，命中返回「喜悦」。
    """
    t = "".join((message or "").split())
    if not t:
        return None
    strong = any(w in t for w in _STRONG_DISTRESS)
    mild = any(w in t for w in _MILD_FRUSTRATION)
    if strong or (mild and not _has_task_substance(t)):
        for label, words in _EMOTION_LABELS:
            if any(w in t for w in words):
                return label
        return "低落"
    if any(w in t for w in _POSITIVE_SIGNALS) and not _has_task_substance(t):
        return "喜悦"
    return None


def count_emotion_streak(recent_messages: list[dict] | None) -> int:
    """连续情绪轮数（迭代15 B8，MultiESC 策略推进的轻量规则版）。

    recent_messages 为 working_memory 的活动线程窗口（含当前消息——主链路中
    当前 user 消息先于 skill 落库）。跳过最新一条 user 消息（本轮已由调用方判定
    为情绪轮），向前数连续命中 detect_emotion 的 user 消息条数，+1 即本轮轮数。
    窗口缺省时按第 1 轮处理（保守走完整三段式）。
    """
    if not recent_messages:
        return 1
    skipped_current = False
    streak = 0
    for m in reversed(recent_messages):
        if m.get("role") != "user":
            continue
        if not skipped_current:
            skipped_current = True
            continue
        if detect_emotion(str(m.get("content") or "")):
            streak += 1
        else:
            break
    return 1 + min(streak, 9)


# ------------------------------------------------------------------ #
# 阶段 3 · 变式/讲解确定性路由（自 agent_router._variant_route_decision 迁入）
# ------------------------------------------------------------------ #


def variant_route_decision(message: str, pinned: list | None = None) -> RouteDecision | None:
    """变式触发词确定性前置路由（迭代10 v1.4；v1.11 讲解优先；B2 pinned 护栏）。

    「举一反三/再来一题/变式…」必须进 smart_quiz 变式链（skill 内含题干回落：
    当前消息无题时自动取本会话最近题卡/引导题干作种子）。不做此前置时，
    LLM 意图路由会把两个字数的触发词误判成 chat 闲聊。

    两条护栏：
    ① 前端按钮显式 pinned socratic + 讲解意图 → 确定性走 pinned（讲解按钮不被劫持）；
    ② 讲解意图 + 指代既有题目/自带题干 → 先 socratic 引导讲解，先讲再练。
    """
    from app.skills.smart_quiz.main import USER_QUESTION_DETECT_RE, SmartQuizExecutor

    if not message or not any(t in message for t in SmartQuizExecutor._VARIANT_TRIGGERS):
        return None
    if pinned:
        if (
            "socratic_solver" in pinned
            and EXPLAIN_INTENT_RE.search(message)
            and (CONTEXT_REF_RE.search(message) or re.search(USER_QUESTION_DETECT_RE, message))
        ):
            return RouteDecision(
                skill_id="socratic_solver", confidence=0.99, params={"question": message}
            )
        return None
    if EXPLAIN_INTENT_RE.search(message) and (
        CONTEXT_REF_RE.search(message) or re.search(USER_QUESTION_DETECT_RE, message)
    ):
        return RouteDecision(
            skill_id="socratic_solver", confidence=0.97, params={"question": message}
        )
    return RouteDecision(skill_id="smart_quiz", confidence=0.98, params={"question": message})


# ------------------------------------------------------------------ #
# 预检主入口
# ------------------------------------------------------------------ #


@dataclass
class PrecheckResult:
    """预检命中结果。

    kind="route"           → decision 直接作为路由结果（跳过 LLM 路由）
    kind="practice_intent" → 考试/练题中心跳转（由网关统一落库 + open_page）
    """

    kind: str
    stage: str
    decision: RouteDecision | None = None
    practice_intent: dict | None = None
    page_item: dict | None = None  # kind="page_intent"：平台地图页面项（打开/跳转 XX）


def run_precheck(message: str, *, pinned: list | None = None) -> PrecheckResult | None:
    """L1-1 预检管线主入口：按固定优先级执行各确定性阶段，首命中即短路。

    返回 None 表示预检未命中，消息继续走 LLM 意图路由（求解/闲聊由路由层判别）。
    全程本地规则，零模型调用、零 IO，耗时 <1ms；同句消息结果必然一致。
    """
    if not message or not message.strip():
        return None

    # ① 情绪：求助永远最先响应（先于考试/变式一切任务意图）
    emotion = detect_emotion(message)
    if emotion:
        logger.info("precheck.hit", stage="emotion", emotion=emotion)
        return PrecheckResult(
            kind="route",
            stage="emotion",
            decision=RouteDecision(
                skill_id="chat",
                confidence=0.9,
                params={"question": message, "emotion": emotion},
            ),
        )

    # ② 考试/练题中心强意图：open_page 跳转（迭代14 手法）
    intent = match_practice_intent(message)
    if intent:
        logger.info("precheck.hit", stage="exam", intent=intent.get("key"))
        return PrecheckResult(kind="practice_intent", stage="exam", practice_intent=intent)

    # ②b 平台页面直达（迭代18 修复）：动作词 + 平台地图页面名 → open_page 跳转。
    # 原先放在 chat skill 流式生成之后（先 LLM 回复再跳转），模型还会幻觉
    # "已为你打开 XX"；提到预检后零 LLM 调用、确定性命中，杜绝幻觉。
    from app.services.platform_context import match_platform_item

    page_item = match_platform_item(message)
    if page_item:
        logger.info("precheck.hit", stage="page", page=page_item.get("key"))
        return PrecheckResult(kind="page_intent", stage="page", page_item=page_item)

    # ③ 错题本录入：确定性写操作（后续 chat skill 解析题干/错因并落库）
    if _ERROR_BOOK_ADD_RE.search(message):
        logger.info("precheck.hit", stage="error_book")
        return PrecheckResult(
            kind="route",
            stage="error_book",
            decision=RouteDecision(
                skill_id="chat",
                confidence=0.99,
                params={"question": message, "error_book_intent": True},
            ),
        )

    # ④ 变式/讲解确定性路由
    decision = variant_route_decision(message, pinned=pinned)
    if decision:
        logger.info("precheck.hit", stage="variant", skill_id=decision.skill_id)
        return PrecheckResult(kind="route", stage="variant", decision=decision)

    # ④ 复习：到期复习/任务查询走 chat 管家数据注入（_butler_lookup），不改路由，占位保持排序契约
    # ⑤ 求解：承接词/题干由 router L0/L2 判别，预检不重复
    # ⑥ 闲聊：router 默认兜底
    return None
