"""Butler 主图状态与 L0 意图路由（规则层，词典逐条对齐 mock teacherV3Server.ts:980-995）。

real = L0 规则 + L1 小模型分类 + L2 大模型 FC 三层（§3.2）；M2-C 交付 L0 + L1 FC
挂点（gateway.tools，无 key 时走 fallback 文案）。路由决策写日志（B5 L5 验收要看）。
"""
from __future__ import annotations

import logging
import re
from typing import Any, TypedDict

logger = logging.getLogger(__name__)

INTENTS = (
    "quiz_import",  # 图片+存题库 → write 工具确认流
    "find_resource",
    "draw_figure",
    "deck_generate",
    "insert_request",
    "web_search",
    "formula_parse",
    "math_chat",  # 数学问答（mock 的 fallback 升级为真 LLM 流式）
    "chitchat",
)

# 与 mock 同义的正则词典（优先级即列表序；quiz_import 需 has_images 由调用方与标志合并）
_L0_RULES: list[tuple[str, re.Pattern]] = [
    ("quiz_import_text", re.compile(r"存(入|到).{0,6}(题库|题目|错题)|入库")),
    ("find_resource", re.compile(r"找.{0,10}(一些|几道|道|个)?题|找(个|点|些)?(素材|资源)|推荐.{0,10}(题|素材|视频)")),
    ("draw_figure", re.compile(r"画.{0,16}(图|动图|演示)|动态图|构图")),
    ("deck_generate", re.compile(r"做.{0,16}?(课件|ppt|演示文稿)|生成.{0,12}?(课件|ppt)|备课")),
    ("insert_request", re.compile(r"插入|放到|放进|加到|添加到")),
    ("web_search", re.compile(r"联网|搜索|查一查|最新")),
    ("formula_parse", re.compile(r"根号|平方|分之|阶乘|绝对值|正弦|余弦|正切|对数|极限|导数")),
]
_INSERT_NEGATIVE = re.compile(r"课件|第\s*\d+\s*页|页面|画布")
_DRAW_NEGATIVE = re.compile(r"课件|ppt")


class ButlerState(TypedDict, total=False):
    session_id: str
    teacher_id: str
    message: str
    has_images: bool
    force_web_search: bool
    ctx: dict[str, Any]  # 前端捎带 {route_title, deck_id, slide_index, selection...}
    history: list[dict]  # 多轮上下文 [{role:user|assistant, text}]
    intent: str
    meta: dict[str, Any]
    decision: dict[str, Any]  # interrupt 恢复后的教师裁决
    messages: list  # ToolMessage 通道（updates → tool_result 事件）
    executed: bool


def route_intent(message: str, *, has_images: bool = False, force_web_search: bool = False) -> str:
    """L0 规则路由（mock teacherV3Server.ts:981-995 同义移植，优先级保序）。"""
    text = message.lower()
    if has_images and re.search(r"存(入|到).{0,6}(题库|题目|错题)|入库", text):
        return "quiz_import"
    if _L0_RULES[0][1].search(text) and has_images:  # 存题库 且 带图（mock 的第二分支）
        return "quiz_import"
    if _L0_RULES[1][1].search(text):  # find_resource 优先于 deck（'备课找题'进资源台）
        return "find_resource"
    if _L0_RULES[2][1].search(text) and not _DRAW_NEGATIVE.search(text):
        return "draw_figure"
    if _L0_RULES[3][1].search(text):
        return "deck_generate"
    if _L0_RULES[4][1].search(text) and _INSERT_NEGATIVE.search(text):
        return "insert_request"
    if force_web_search or _L0_RULES[5][1].search(text):
        return "web_search"
    if _L0_RULES[6][1].search(message):  # 公式词按原文（含大写 Sin 等）匹配
        return "formula_parse"
    return "math_chat"
