"""平台上下文（P0.5，AI 管家补丁）

承载"AI 全局知晓平台"的两块基础：
1. ROLE 枚举：端隔离（student/teacher/researcher/guest），从 JWT active_role 透传。
2. 平台地图（P9）：全部页面/技能的注册表，供模型识别"用户说打开XX → 输出 action"。
   静态常量 + GET /api/features 动态下发（v1.2 补丁 D：前端注册即进地图，免发版）。
"""

from __future__ import annotations

import json
import re

# ==================== ROLE 枚举（端隔离） ====================
VALID_ROLES = ("student", "teacher", "researcher", "guest")

_ROLE_LABELS = {
    "student": "学生端",
    "teacher": "教师端",
    "researcher": "科研端",
    "guest": "游客端",
}


def normalize_role(role: str | None) -> str:
    """归一化角色：非法/空 → student（默认端）"""
    return role if role in VALID_ROLES else "student"


def role_label(role: str) -> str:
    return _ROLE_LABELS.get(role, role)


# ==================== 平台地图（P9） ====================
# 结构与前端 config/features.js 对齐；type: route 可跳转 / unconfigured 未配置（模型输出提示而非跳转）
# skill: 对话技能（点亮）；page: 页面直达。per_roles: 可访问角色（缺省全部学生端）
_PLATFORM_MAP: list[dict] = [
    # ---- 首页 ----
    {
        "key": "home",
        "name": "首页",
        "to": "/student/home",
        "aliases": ["首页", "主页", "回到首页", "工作台", "主页面"],
        "type": "page",
    },
    # ---- 学习业务 ----
    {
        "key": "tasks",
        "name": "教师任务",
        "to": "/student/tasks",
        "aliases": ["任务", "教师任务", "作业任务"],
        "type": "page",
    },
    # 闭环迭代13：刷题/模拟试卷统一收口到练题中心（旧 /student/practice、/student/exam 路由保留但不再跳转）
    {
        "key": "lab",
        "name": "练题中心",
        "to": "/student/practice-lab",
        "aliases": ["练题中心", "练题", "专项训练", "模拟训练", "刷题训练", "训练中心"],
        "type": "page",
    },
    {
        "key": "practice",
        "name": "专项训练",
        "to": "/student/practice-lab",
        "aliases": ["刷题", "练习", "做题", "练薄弱", "薄弱训练", "专题训练"],
        "type": "page",
        "params": "?mode=special&kp_code=",
    },
    {
        "key": "exam",
        "name": "模拟考试",
        "to": "/student/practice-lab",
        "aliases": ["模拟试卷", "模拟考", "试卷", "全真模拟", "做套卷", "套卷"],
        "type": "page",
        "params": "?mode=exam",
    },
    {
        "key": "error-book",
        "name": "错题本",
        "to": "/student/error-book",
        "aliases": ["错题本", "错题", "我的错题"],
        "type": "page",
    },
    {
        "key": "mastery",
        "name": "学情报告",
        "to": "/student/mastery",
        "aliases": ["学情", "学情报告", "掌握度", "我的掌握"],
        "type": "page",
    },
    {
        "key": "graph",
        "name": "知识图谱",
        "to": "/student/graph",
        "aliases": ["知识图谱", "图谱", "知识点地图"],
        "type": "page",
    },
    {
        "key": "classes",
        "name": "我的班级",
        "to": "/student/classes",
        "aliases": ["班级", "我的班级"],
        "type": "page",
    },
    {
        "key": "memories",
        "name": "记忆管理",
        "to": "/student/memories",
        "aliases": ["记忆", "记忆管理", "记忆库"],
        "type": "page",
    },
    {
        "key": "profile",
        "name": "个人设置",
        "to": "/student/profile",
        "aliases": ["设置", "个人设置", "资料"],
        "type": "page",
    },
    # 迭代20 联调修复：资源中心此前不在平台地图，「打开资源中心」既不触发 open_page
    # （模型只能谎称"功能还在开发中"），又因动作词护栏不出视频卡——两头落空。
    # 补为可跳转 page；前端 DialogView PATH_MAP 将 /student/resources 映射到 /resources。
    # 注意别名不含裸「视频」/「推荐」——防止「推荐几个B站的三角函数学习视频」这类
    # 视频诉求被误判成跳页（视频诉求应走对话内视频卡）。
    {
        "key": "resources",
        "name": "资源中心",
        "to": "/student/resources",
        "aliases": ["资源中心", "资源库", "学习资源"],
        "type": "page",
    },
    # ---- 场景与更多 ----
    {
        "key": "classroom",
        "name": "双师课堂",
        "to": "/student/classroom",
        "aliases": ["双师课堂", "课堂", "直播课", "双师"],
        "type": "page",
    },
    {"key": "voice", "name": "语音讲解", "aliases": ["语音讲解", "语音"], "type": "unconfigured"},
    {
        "key": "dyn-visual",
        "name": "可视化讲解",
        "aliases": ["可视化讲解", "可视化"],
        "type": "unconfigured",
    },
    {
        "key": "derivation",
        "name": "推导检查",
        "aliases": ["推导检查", "推导"],
        "type": "unconfigured",
    },
    {"key": "replay", "name": "课堂回溯", "aliases": ["课堂回溯", "回溯"], "type": "unconfigured"},
    {
        "key": "recommend",
        "name": "资源推荐",
        "aliases": ["资源推荐", "推荐资源"],
        "type": "unconfigured",
    },
    {
        "key": "daily-path",
        "name": "每日任务",
        "aliases": ["每日任务", "学习路径", "路径"],
        "type": "unconfigured",
    },
    {
        "key": "guest",
        "name": "游客演示",
        "aliases": ["游客演示", "游客模式"],
        "type": "unconfigured",
    },
    # ---- 对话技能（点亮） ----
    {
        "key": "socratic",
        "name": "苏格拉底解题",
        "aliases": ["解题", "苏格拉底", "讲题", "教我", "socratic"],
        "type": "skill",
        "skill_key": "socratic",
    },
    {
        "key": "quiz_gen",
        "name": "智能出题",
        "aliases": ["出题", "组卷", "智能出题", "生成题目"],
        "type": "skill",
        "skill_key": "quiz_gen",
    },
    {
        "key": "chat",
        "name": "自由对话",
        "aliases": ["对话", "聊天", "chat"],
        "type": "skill",
        "skill_key": "chat",
    },
]

# 生成 P9 注入文本（一次构建，进程内缓存）
_P9_TEXT_CACHE: str | None = None


def build_platform_map_text() -> str:
    """平台地图注入文本（P9）：模型据此识别功能直达。格式：
    【平台功能】名称（别名/路径）..."""
    global _P9_TEXT_CACHE
    if _P9_TEXT_CACHE is not None:
        return _P9_TEXT_CACHE
    lines = [
        "以下是平台可访问的全部功能与对应路径。"
        "区分两种情况："
        "① 用户仅询问内容（如「我有哪些错题」、「最近做错的题」、「我的学情」、「今天任务」）——"
        "系统已提供【管家查询】真实数据，直接在回复中列出，不要跳转、不要假装打开页面；"
        "② 用户明确要求打开/跳转/整理某个功能（如「打开错题本」、「带我去双师课堂」）——"
        "用自然语言确认（例如：好的，马上帮你打开错题本），系统会自动完成跳转。"
        "任何情况下都绝不要在回复中输出 JSON、路径或代码，只输出自然语言。"
        "未配置功能用自然语言说明暂未开放即可。"
    ]
    for item in _PLATFORM_MAP:
        if item["type"] == "page":
            path = item["to"] + (item.get("params") or "")
            alias = "、".join(item["aliases"])
            lines.append(f"- {item['name']}（{alias}）：{path}")
        elif item["type"] == "skill":
            lines.append(
                f"- 技能·{item['name']}（{'、'.join(item['aliases'])}）：/student/chat 点亮 {item['skill_key']}"
            )
        else:
            lines.append(f"- {item['name']}（{'、'.join(item['aliases'])}）：未配置，仅提示")
    _P9_TEXT_CACHE = "【平台功能】\n" + "\n".join(lines)
    return _P9_TEXT_CACHE


def platform_map_payload(role: str = "student") -> list[dict]:
    """按角色过滤后的平台地图（供 GET /api/features 下发；guest 只留公开项）"""
    if role == "guest":
        return [
            i
            for i in _PLATFORM_MAP
            if i.get("type") == "page" and i["key"] in ("practice", "exam", "profile")
        ]
    return list(_PLATFORM_MAP)


# 功能直达触发词（用户消息须含其一 + 命中功能名才输出 action）
_ACTION_VERBS = (
    "打开",
    "跳转",
    "进入",
    "前往",
    "带我去",
    "打开一下",
    "帮我打开",
    "切换到",
    "去一下",
    "整理",
    "管理一下",
    "帮我整理",
    "回到",
    "返回",
)

# 文件名/资料名中的「错题」「整理」不是页面指令：匹配前先剥离，防真机事故
# 「请基于我上传的《三角函数恒等变换_错题整理.md》帮我梳理知识点」误触发「打开错题本」。
_FILENAME_RE = re.compile(
    r"[^\s，。；、：:（）()\[\]{}<>《》「」“”\"']+\.(?:md|markdown|pdf|docx?|pptx?|txt|tex|png|jpe?g|webp|csv|xlsx?)\b",
    re.I,
)
# 内容任务（读资料/总结/讲解/引导）优先于页面直达；只有显式导航词才跳页。
_CONTENT_TASK_RE = re.compile(
    r"资料|文档|文件|上传|附件|知识点|梳理|总结|归纳|阅读|基于(?:我|这|上述|以下)?(?:的)?(?:资料|文档|材料|附件)"
)
_EXPLICIT_NAV_RE = re.compile(r"打开|跳转|进入|前往|带我去|切换到|回到|返回|去一下")


def strip_document_refs(text: str) -> str:
    """剥离消息中的文件名，避免文件名内的「错题/整理」被误读成页面指令。"""
    return _FILENAME_RE.sub(" ", text or "")


def match_platform_item(text: str) -> dict | None:
    """本地功能意图匹配：需同时满足「动作词 + 功能名/别名」，防误触发。

    命中 page 类型 → 返回 item（chat skill 据此 yield action 事件）；
    skill 类型不在此处触发（技能点亮走技能注册逻辑）。

    2026-10-21 修复：文件名中的「错题整理.md」曾同时命中动作词「整理」与
    页面别名「错题」，把「基于资料梳理知识点」误吞成打开错题本。匹配前剥离
    文件名；含资料/总结/讲解等内容任务且无显式导航词时一律不跳页。
    """
    t = strip_document_refs(text.strip())
    if not t.strip():
        return None
    if _CONTENT_TASK_RE.search(t) and not _EXPLICIT_NAV_RE.search(t):
        return None
    has_verb = any(v in t for v in _ACTION_VERBS)
    if not has_verb:
        return None
    tl = t.lower()
    for item in _PLATFORM_MAP:
        if item["type"] != "page":
            continue
        if item["key"] in tl or any(a.lower() in tl for a in item["aliases"]):
            return item
    return None


# ==================== 练题中心强意图（闭环迭代13） ====================
# 无需动作词（"打开/跳转"）也触发：用户说"来一场 60 分钟全真模拟 / 做套卷 / 练薄弱"，
# 直接跳转练题中心对应模式，而不是让模型在对话里手写整套试卷。
# 对话内出题（含"几道/变式"）明确除外——那仍是 smart_quiz 的职责。

# 对话内出题标记：命中任一 → 不走练题中心跳转
_INLINE_QUIZ_WORDS = ("几道", "几题", "出几道", "来几道", "一道题", "变式", "出一题", "来一题")

# 疑问/指代标记（迭代10 v1.4）：命中任一 → 不跳转，放行给对话模型回答。
# 场景：「这套模拟卷第3题怎么做」「模拟考和专项有什么区别」「模拟卷有哪些题型」
# 是在**咨询/讨论**而非要求跳转，纯关键词拦截会把问题误吞成跳页。
_QUESTION_WORDS = (
    "怎么做",
    "如何做",
    "怎么解",
    "怎么算",
    "怎么写",
    "怎么样",
    "怎么用",
    "区别",
    "差异",
    "哪个",
    "哪些",
    "哪种",
    "哪套",
    "哪道",
    "为什么",
    "为何",
    "是什么",
    "什么意思",
    "有没有",
    "能不能",
    "可以吗",
    "是多少",
    "好不好",
    "行吗",
    "呢",
    "吗",
    "？",
    "?",
    "讲解",
    "讲讲",
    "讲一下",
    "解析",
    "教我",
    "复习",
)
# 指代卷中具体题目：「这套模拟卷第3题」「卷子第二道」
_QUESTION_REF_RE = re.compile(r"第\s*[0-9０-９一二三四五六七八九十百]+\s*[题道小问]")

# (地图 key, 名称, 跳转路径, 参数, 触发词元组, 确认语)
_PRACTICE_INTENT_RULES: list[tuple] = [
    (
        "exam",
        "模拟考试",
        "/student/practice-lab",
        "?mode=exam&auto=1",
        (
            "模拟考",
            "模拟考试",
            "全真模拟",
            "模拟试卷",
            "做套卷",
            "来套卷",
            "来一套卷",
            "组一套卷",
            "来一份卷",
            "模拟卷",
            "来场模拟",
            "模考",
            "整套卷",
        ),
        "好的，马上为你打开练题中心，生成一套全真模拟卷",
    ),
    (
        "special",
        "专项训练",
        "/student/practice-lab",
        "?mode=special&auto=1",
        (
            "专项训练",
            "练薄弱",
            "薄弱训练",
            "专题训练",
            "刷题训练",
            "训练中心",
            "来一组专项",
            "去练题",
            "开始练题",
        ),
        "好的，马上为你打开练题中心，按你的薄弱点出题训练",
    ),
]


def match_practice_intent(text: str) -> dict | None:
    """练题中心强意图检测（闭环迭代13，chat skill 前置拦截用）：
    - 命中「模拟考试/专项训练」类词 → 返回 open_page 意图（含确认语）
    - 含「几道/变式」等对话内出题词 → 返回 None（交给 smart_quiz）
    - 含疑问/指代特征（迭代10 v1.4）→ 返回 None（是在咨询讨论，放行给对话模型）
    """
    t = text.strip()
    if not t:
        return None
    if any(w in t for w in _INLINE_QUIZ_WORDS):
        return None
    if _QUESTION_REF_RE.search(t) or any(w in t for w in _QUESTION_WORDS):
        return None
    for kind, name, to, params, kws, confirm in _PRACTICE_INTENT_RULES:
        if any(k in t for k in kws):
            return {
                "key": kind,
                "name": name,
                "to": to,
                "params": params,
                "confirm_text": confirm,
            }
    return None


def to_json() -> str:
    """供接口/测试直接消费的完整地图 JSON"""
    return json.dumps(_PLATFORM_MAP, ensure_ascii=False)
