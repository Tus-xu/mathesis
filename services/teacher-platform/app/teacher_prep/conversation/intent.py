"""R17 意图解析：自然语言 → 结构化 PrepIntent（deltas），schema 校验后才是意图。

铁律（DECISIONS.md §4/§6/§8）：
- LLM 输出是不可信输入：JSON 解析 + schema 校验（extra=forbid / HTML 禁令 / UUID 形状）三重闸门；
- 任何失败（provider 不可用 / 超时 / 校验失败）→ 显式 `IntentParseFailure`，
  由编排层降级为带标签可编辑 basic mode；不沉默、不编造、不假成功；
- intent 是 deltas：None 字段 = 保持现状；应用增量不整表覆盖；
- 解析 prompt 有界（当前选择投影 + 指令 + 输出 schema），不含任何密钥。
"""

from __future__ import annotations

import inspect
import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from .schema import PrepIntent, canonical_json, intent_snapshot_hash

LlmGenerate = Callable[[str], str | Awaitable[str]]

# 脱敏 provider 失败标签（机器可读；不带任何模型原文/secret）
ProviderErrorLabel = Literal[
    "provider_unavailable", "provider_timeout", "provider_error",
    "intent_invalid", "quota_exceeded",
]


@dataclass(frozen=True)
class IntentParseFailure:
    """意图解析失败（带标签可编辑 basic mode 的触发依据）。"""

    error_label: str
    detail: str = ""


class IntentValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedIntent:
    intent: PrepIntent
    snapshot_hash: str


def detect_casual_chat(message: str) -> str | None:
    """确定性闲聊检测：返回闲聊类型标签（如 'greeting'/'ack'/'chat'）或 None。

    命中即走 casual_chat 分支（orchestrator 直接生成温暖回复，不触发蓝图投影）。
    关键词命中/较长句子一律返回 None → 交给 LLM 意图解析。

    这是修复"AI 回复太公式化/复读教材"的根因 —— 任何输入都强制解析为备课意图
    导致 LLM 硬往 JSON 里填字段，回复自然就机械了。
    """
    text = message.strip().lower()
    if not text:
        return None

    # 包含数学/备课关键词 → 绝对不是闲聊
    _MATH_KEYWORDS = frozenset((
        '数学', '备课', '教案', '课件', 'ppt', '椭圆', '双曲线', '抛物线', '函数',
        '三角', '数列', '立体', '几何', '概率', '向量', '导数', '积分', '方程',
        '不等式', '集合', '映射', '复数', '矩阵', '课时', '讲课', '教学', '题目',
        '例题', '习题', '上课', '教材', '章节', '知识点',
    ))
    for kw in _MATH_KEYWORDS:
        if kw in text:
            return None

    # 精确匹配的问候/寒暄短语
    _EXACT_GREETINGS = {
        '你好', '您好', '嗨', 'hi', 'hello', '哈喽', '哈罗', '在吗', '在不在',
        '老师好', '早上好', '下午好', '晚上好', '早安', '晚安',
        '谢谢', '感谢', '多谢', '好的', '行', '可以', '没问题', 'ok', 'okay',
        'yes', '对', '没错', '嗯', '哦', '啊', '收到', '明白', '知道了',
        '怎么样', '好吗', '最近怎么样', '吃了吗',
    }
    if text in _EXACT_GREETINGS:
        return 'greeting' if any(g in text for g in ('你好', '您好', 'hi', 'hello', '嗨')) else 'ack'

    # 极短中文短语（≤4 字），不含任何标点或空格 —— 大概率是打招呼
    if len(text) <= 4 and not re.search(r'[\s，。！？、；：""''（）【】《》]', text):
        # 排除明显的数字/纯符号
        if not re.fullmatch(r'[\d\W_]+', text):
            return 'short_greeting'

    return None


# ── R2 对话去表单机：确定性「提问」与「确认」检测（与闲聊快路径同法）───────────
# 问知识点/问考法/质疑不是备课参数变更，绝不能被硬套成 modify → 整包蓝图重投影
# （那正是"每句话都变成大卡"的表单机根因）。保守命中：宁可漏判（回落 LLM 意图
# 解析），不误判（把改需求当提问丢掉）。

_QUESTION_PATTERNS = re.compile(
    r"(怎么考|常怎么考|考点是什么|是什么[？?]?$|为什么|怎么理解|怎么讲|有哪些|包括哪些|"
    r"有什么|区别是什么|和.{0,12}的区别|怎么做|能不能讲讲|讲讲|说说|解释一下|什么叫)",
)
# 含修改/推进动词 → 是备课指令不是纯提问（回落正常意图链路）
_COMMAND_KEYWORDS = re.compile(
    r"(改|换|调整|删|减|加|增加|补充|精简|压缩|重新|新建|继续|确认|生成|按这个|改成|换成|"
    r"课时|分钟|重点|难点|例题|环节|流程|安排|教案|课件|ppt)",
)


def detect_question(message: str) -> bool:
    """是否是纯提问（问知识点/考法/概念），应回 prose 答案而不是重发卡片。"""
    text = (message or "").strip()
    if not text or len(text) > 60:
        return False
    if not _QUESTION_PATTERNS.search(text):
        return False
    return _COMMAND_KEYWORDS.search(text) is None


_CONFIRM_PATTERNS = re.compile(
    r"^(好的?|行|可以|没问题|嗯|哦|ok|okay)?[，,、\s]*"
    r"(就)?(按这个(方向)?继续|继续(生成|吧|下一步)?|确认|就这么(定|办)|按这个来|采用(这个|这份)(方案)?|"
    r"就这样吧|可以继续|同意)[，,。！!~\s]*$",
    re.IGNORECASE,
)


def detect_confirm(message: str) -> bool:
    """是否是「确认既有提案、继续」类短语（触发真实 confirm 状态迁移，不再重投影）。"""
    text = (message or "").strip()
    if not text or len(text) > 20:
        return False
    return bool(_CONFIRM_PATTERNS.match(text))


# 质疑/反馈（"课时安排太满了"）：有针对方案的意见但没有给出可执行的新参数 →
# prose 回应 + 给出可选调整方向，不硬套 modify 重投影（投影 prompt 看不到这句话，
# 重投影只会生成同样的方案 = AI 没在听）。
_FEEDBACK_PATTERNS = re.compile(
    r"(太满|太多|太赶|太浅|超了|来不及|不太对|不满意|不太合适|等等|可是|可是我觉得|我觉得.{0,12}(太|不))",
)
_FEEDBACK_IMPERATIVE = re.compile(
    r"(改成|换成|删掉|删去|减掉|去掉|加上|增加|补充|精简|压缩|调整为|重新(生成|备|拟)|帮我(改|换|删|加|减)|"
    r"把.{0,8}(改|换|删|加|减|调整))",
)


def detect_feedback(message: str) -> bool:
    """是否是对既有方案的质疑/反馈（应回 prose，不重发大卡）。"""
    text = (message or "").strip()
    if not text or len(text) > 60:
        return False
    if not _FEEDBACK_PATTERNS.search(text):
        return False
    return _FEEDBACK_IMPERATIVE.search(text) is None


def dialog_fast_intent(message: str) -> ParsedIntent | None:
    """R2 对话去表单机：闲聊 / 确认 / 提问 / 质疑的确定性快路径（零 LLM）。

    同步路径与受理后台任务**都必须**先走这里（background 曾直连 parse_intent_with
    漏掉快路径 → ⑤质疑被硬套 modify 整包重投影）。命中即返回对话意图；
    未命中返回 None（回落 LLM 意图解析）。
    """
    casual_label = detect_casual_chat(message)
    if casual_label is not None:
        casual = PrepIntent(intent_type="casual_chat", note=f"casual:{casual_label}")
        return ParsedIntent(intent=casual, snapshot_hash=intent_snapshot_hash(casual))
    if detect_confirm(message):
        confirm = PrepIntent(intent_type="confirm", note="dialog:confirm")
        return ParsedIntent(intent=confirm, snapshot_hash=intent_snapshot_hash(confirm))
    if detect_question(message):
        question = PrepIntent(intent_type="question", note="dialog:question")
        return ParsedIntent(intent=question, snapshot_hash=intent_snapshot_hash(question))
    if detect_feedback(message):
        feedback = PrepIntent(intent_type="question", note="dialog:feedback")
        return ParsedIntent(intent=feedback, snapshot_hash=intent_snapshot_hash(feedback))
    return None


def build_intent_prompt(
    *,
    user_message: str,
    current: dict[str, Any] | None,
    available_sources: list[dict[str, Any]] | None = None,
    available_sections: list[dict[str, Any]] | None = None,
) -> str:
    """有界意图解析 prompt：当前选择投影（或空）+ 用户自然语言 + 输出契约。

    `available_*` 是已授权教材/章节选项（有则给，LLM 只能从中选 id）；
    不可用时不编造选项，输出 null 字段由编排层以选择卡兜底。
    """
    lines = [
        "你是高中数学教师的备课助手。把教师的一句话自然语言意图解析为结构化 JSON。",
        "规则：",
        "- 只输出一个 JSON 对象，不要输出任何其它文字/代码围栏。",
        "- 字段语义为增量（delta）：无法确定的字段输出 null（表示“保持现状/待定”），绝不猜测或编造。",
        "- 教材(textbook_id)/章节(section_id)/班级(class_id)必须是 UUID 字符串。",
        "- 选教材(textbook_id)时必须让教师提到的知识点落在该源 topics（章节导览）范围内；"
        "若没有任何源的话题覆盖该知识点，宁可选 null（交给选择卡），绝不猜测册次。",
        "- 知识点(knowledge_points)是字符串数组；教学目标(objectives)是 {text, source_ref?} 数组。",
        "- 意图类型(intent_type)六选一：",
        "  * new_prep — 新建备课（提到“新建/重新备课/第一次/从零开始”）",
        "  * continue_prep — 继续上次（提到“继续/接着昨天的/上次那个”）",
        "  * modify — 修改既有参数（默认，对备课内容有具体要求）",
        "  * casual_chat — 闲聊/问候/寒暄（纯打招呼、致谢、闲聊，不含备课内容）——此时所有业务字段输出 null",
        "  * question — 问知识点/问考法/问概念（如“怎么考”“是什么”“为什么”），只要答案不要改方案——此时所有业务字段输出 null",
        "  * confirm — 确认既有教学方案、同意继续（如“按这个方向继续”“确认”）——此时所有业务字段输出 null",
        "- 禁止输出 HTML、禁止输出组件名、禁止输出任何可执行代码。",
        "- 输出契约（字段可为 null）：",
        '  {"intent_type":"new_prep|continue_prep|modify|casual_chat|question|confirm","textbook_id":null,"section_id":null,'
        '"class_id":null,"knowledge_points":null,"lesson_scope":null,"lesson_hours":null,'
        '"objectives":null,"note":null,"confidence":null,"warnings":[]}',
    ]
    if current:
        lines.append("当前备课选择（保持现状的字段输出 null）：")
        lines.append(canonical_json(current))
    if available_sources:
        lines.append("可选教材（只允许从中选 textbook_id）：")
        lines.append(canonical_json(available_sources))
    if available_sections:
        lines.append("可选章节（只允许从中选 section_id）：")
        lines.append(canonical_json(available_sections))
    lines.append("教师消息：")
    lines.append(user_message)
    return "\n".join(lines)


def _strip_code_fence(raw: str) -> str:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        first_nl = raw.find("\n")
        if first_nl >= 0:
            raw = raw[first_nl + 1 :]
        if raw.endswith("```"):
            raw = raw[:-3]
    return raw.strip()


def _dump_intent_raw(raw: str, reason: str) -> None:
    """意图解析失败时落盘 LLM 原始输出（**仅诊断**，需显式开启开关，默认惰性）。

    生产/CI 不设该环境变量 → 不产生任何文件与 IO。本轮靠它定位到
    「救援模型回填章节标题而非 UUID」与「45 分钟被填成 lesson_hours=45」两个真实缺陷。
    """
    if not os.environ.get("PREP_DEBUG_INTENT_RAW"):
        return
    try:
        import pathlib

        pathlib.Path(os.environ["PREP_DEBUG_INTENT_RAW"]).write_text(
            f"reason: {reason}\nlen: {len(raw)}\n---\n{raw}", encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 —— 诊断路径绝不影响主流程
        pass


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _coerce_option_id(value: Any, options: list[dict[str, Any]] | None) -> str | None:
    """模型给出的 id 规范化：UUID 原样通过；**标题/名称**→ 服务端选项 id（唯一命中才接受）。

    2026-09-23（救援通道实测）：换模型（GLM）后模型会回填章节**标题**（如
    `"1.2.1 椭圆的标准方程"`）而非 UUID，schema 直接拒绝 → basic_mode。
    此处只在**服务端已下发的真实选项**里做确定性匹配（精确优先，其次唯一包含），
    找不到或命中多条 → 返回 None（deltas 语义：不设该字段 → 走选择卡，诚实降级）。
    不猜、不编造 id。
    """
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v:
        return None
    if _UUID_RE.match(v):
        return v
    if not options:
        return None
    exact = [o for o in options if isinstance(o, dict) and str(o.get("label") or "").strip() == v]
    if len(exact) == 1:
        return str(exact[0].get("id"))
    partial = [
        o
        for o in options
        if isinstance(o, dict) and v and v in str(o.get("label") or "") and str(o.get("id") or "")
    ]
    if len(partial) == 1:
        return str(partial[0].get("id"))
    return None


def parse_and_validate_intent(
    raw: str,
    *,
    available_sources: list[dict[str, Any]] | None = None,
    available_sections: list[dict[str, Any]] | None = None,
) -> ParsedIntent:
    """JSON 解析 + schema 校验；失败抛 IntentValidationError（不成为意图）。"""
    cleaned = _strip_code_fence(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        _dump_intent_raw(cleaned, f"json: {exc}")
        raise IntentValidationError(f"LLM 输出非合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        _dump_intent_raw(cleaned, f"root: {type(data).__name__}")
        raise IntentValidationError(f"LLM 输出根节点必须是 JSON 对象，实际 {type(data).__name__}")
    # 宽容规范化（2026-09-23，救援通道实测）：模型可能多包一层 {"intent": {...}} 或
    # 携带 schema 外解释字段（reason/confidence/…）。与 node_ops.parse_node_revision 同法：
    # **只保留 schema 已知键**（数据面仍是 fail-closed：未知键被丢弃、类型错误照旧拒绝）。
    if "intent" in data and isinstance(data.get("intent"), dict) and "lesson_hours" not in data:
        data = data["intent"]
    known = set(PrepIntent.model_fields)
    data = {k: v for k, v in data.items() if k in known}
    # id 形态规范化（UUID 原样；标题 → 服务端选项 id；无法匹配 → 丢弃字段走选择卡）
    if "textbook_id" in data:
        coerced = _coerce_option_id(data.get("textbook_id"), available_sources)
        if coerced:
            data["textbook_id"] = coerced
        else:
            data.pop("textbook_id", None)
    if "section_id" in data:
        coerced = _coerce_option_id(data.get("section_id"), available_sections)
        if coerced:
            data["section_id"] = coerced
        else:
            data.pop("section_id", None)
    if "class_id" in data and not _UUID_RE.match(str(data.get("class_id") or "")):
        data.pop("class_id", None)
    # 课时数确定性归一（2026-09-23 实测）：救援模型会把「45 分钟」填成 lesson_hours=45
    # → 首页出现「第45课时 · 2025分钟」这类荒谬元信息。课时预算是 45 分钟/课时，
    # 故 >6 的值必然是把**分钟**当课时：按 45 分钟/课时折算（四舍五入，下限 1）。
    # 只在明显越界时介入，正常 1~6 原样通过（不改教师真实意图）。
    hours = data.get("lesson_hours")
    if isinstance(hours, int) and not isinstance(hours, bool) and hours > 6:
        data["lesson_hours"] = max(1, round(hours / 45))
    try:
        intent = PrepIntent.model_validate(data)
    except ValidationError as exc:
        issues = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        _dump_intent_raw(cleaned, f"schema: {'; '.join(issues[:5])}")
        raise IntentValidationError(f"意图 schema 校验失败（{len(issues)} 项）：{'; '.join(issues[:5])}") from exc
    return ParsedIntent(intent=intent, snapshot_hash=intent_snapshot_hash(intent))


async def parse_intent(
    llm_generate: LlmGenerate,
    *,
    user_message: str,
    current: dict[str, Any] | None = None,
    available_sources: list[dict[str, Any]] | None = None,
    available_sections: list[dict[str, Any]] | None = None,
) -> ParsedIntent | IntentParseFailure:
    """编排入口：prompt → LLM → 校验。任何异常 → IntentParseFailure（显式标签）。"""
    prompt = build_intent_prompt(
        user_message=user_message,
        current=current,
        available_sources=available_sources,
        available_sections=available_sections,
    )
    try:
        result = llm_generate(prompt)
        if inspect.isawaitable(result):
            result = await result
        return parse_and_validate_intent(
            str(result),
            available_sources=available_sources,
            available_sections=available_sections,
        )
    except IntentValidationError as exc:
        return IntentParseFailure(error_label="intent_invalid", detail=str(exc))
    except TimeoutError:
        return IntentParseFailure(error_label="provider_timeout", detail="意图解析超时")
    except Exception as exc:  # noqa: BLE001 —— provider 网络/5xx/网关异常统一脱敏标签
        name = type(exc).__name__
        if "timeout" in name.lower() or isinstance(exc, TimeoutError):
            return IntentParseFailure(error_label="provider_timeout", detail=name)
        return IntentParseFailure(error_label="provider_unavailable", detail=name)


def apply_intent(
    current: dict[str, Any] | None,
    intent: PrepIntent,
) -> dict[str, Any]:
    """intent deltas → 有效选择（None 字段保持现状；list 字段整体替换）。

    返回规范选择字典：{source_id, section_id, class_id, knowledge_points,
    lesson_scope, lesson_hours, objectives:[{text, source_ref?}], intent_hash}。
    """
    base: dict[str, Any] = {
        "source_id": None,
        "section_id": None,
        "class_id": None,
        "knowledge_points": [],
        "lesson_scope": None,
        "lesson_hours": None,
        "objectives": [],
    }
    if current:
        for key in ("source_id", "section_id", "class_id", "knowledge_points", "lesson_scope", "lesson_hours", "objectives"):
            if current.get(key) is not None:
                base[key] = current[key]

    if intent.textbook_id is not None and intent.textbook_id != base["source_id"]:
        # 换册：旧册 section 不可跨书沿用（唯一允许清空已确认信息的场景；编排层必须
        # 在 prose 中明说"已换册、原章节不再沿用"，见 _handle_parsed_intent）。
        base["source_id"] = intent.textbook_id
        if intent.section_id is None:
            base["section_id"] = None
    elif intent.textbook_id is not None:
        base["source_id"] = intent.textbook_id
    if intent.section_id is not None:
        base["section_id"] = intent.section_id
    if intent.class_id is not None:
        base["class_id"] = intent.class_id
    if intent.knowledge_points is not None:
        base["knowledge_points"] = list(intent.knowledge_points)
    if intent.lesson_scope is not None:
        base["lesson_scope"] = intent.lesson_scope
    if intent.lesson_hours is not None:
        base["lesson_hours"] = intent.lesson_hours
    if intent.objectives is not None:
        base["objectives"] = [
            {"text": o.text, "source_ref": o.source_ref} for o in intent.objectives
        ]
    base["intent_hash"] = intent_snapshot_hash(intent)
    return base


def selection_complete(selection: dict[str, Any]) -> bool:
    """选择是否满足建上下文的最小集合（教材+章节；班级可选——E-3，未绑定降级画像）。"""
    return bool(selection.get("source_id") and selection.get("section_id"))


__all__ = [
    "LlmGenerate",
    "ParsedIntent",
    "IntentParseFailure",
    "IntentValidationError",
    "detect_casual_chat",
    "detect_question",
    "detect_confirm",
    "detect_feedback",
    "dialog_fast_intent",
    "build_intent_prompt",
    "parse_intent",
    "parse_and_validate_intent",
    "apply_intent",
    "selection_complete",
]
