"""防护层（kernel/guard.py）

输入检查（长度/注入/敏感词）+ 输出校验（citation/越权字段）。
规则集中维护，命中→清洗+日志。
"""

import re
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

# 注入检测规则（集中维护）
INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(
        r"忽略(以上|上面|之前|前面|所有)(所有|全部|之前)?(的)?(指令|提示|规则|要求|设定)",
        re.IGNORECASE,
    ),
    re.compile(
        r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?)", re.IGNORECASE
    ),
    re.compile(r"你(现在|从现在)是(?!.*数学)", re.IGNORECASE),
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"(假装|扮演|角色扮演).{0,10}(不是|没有).{0,10}(限制|规则)", re.IGNORECASE),
    re.compile(r"jailbreak", re.IGNORECASE),
    re.compile(r"DAN\s*mode", re.IGNORECASE),
    # 角色替换类注入
    re.compile(
        r"(假设|假定|想象)(你|自己).{0,10}(没有|不受|去掉|移除).{0,10}(限制|边界|规则|约束)",
        re.IGNORECASE,
    ),
    re.compile(r"(进入|开启|激活)(开发者|管理员|调试|debug)\s*(模式|状态)", re.IGNORECASE),
    re.compile(r"(忘记|忘掉|清除)(你|你是).{0,10}(助手|设定|角色)", re.IGNORECASE),
    re.compile(r"(不受|无需遵守|无需服从).{0,10}(角色|身份|限制|规则)", re.IGNORECASE),
    re.compile(r"你现在是(?!.*数学).{0,20}(助手|AI|机器人)", re.IGNORECASE),
    re.compile(
        r"(以|用)\s*(JSON|json)\s*格式.{0,10}(输出|返回|显示).{0,10}(配置|设定|提示词|prompt)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(输出|显示|告诉|透露).{0,10}(你的|系统).{0,10}(配置|设定|提示词|prompt|内部)",
        re.IGNORECASE,
    ),
]

# 敏感词列表（基础）
SENSITIVE_WORDS: list[str] = [
    "赌博",
    "色情",
    "毒品",
    "暴力恐怖",
]

# 最大输入长度
MAX_INPUT_LENGTH = 4000


@dataclass
class GuardResult:
    """防护检查结果"""

    safe: bool = True
    cleaned_message: str = ""
    reason: str = ""
    injection_detected: bool = False


class Guard:
    """防护层"""

    async def check_input(self, message: str, ctx: dict) -> GuardResult:
        """输入检查：
        - 长度 > 4000 截断
        - 注入模式命中 → 清洗 + 日志
        - 敏感词拦截
        """
        user_id = ctx.get("user_id", "unknown")
        log = logger.bind(user_id=user_id)

        # 1. 长度截断
        if len(message) > MAX_INPUT_LENGTH:
            message = message[:MAX_INPUT_LENGTH]
            log.info("guard.input_truncated", original_len=len(message))

        # 2. 注入检测
        injection_detected = False
        for pattern in INJECTION_PATTERNS:
            if pattern.search(message):
                injection_detected = True
                log.warning("guard.injection_detected", pattern=pattern.pattern[:50])
                # 注入检测命中时直接拦截，不让模型处理
                return GuardResult(
                    safe=False,
                    cleaned_message=message,
                    reason="检测到提示注入尝试",
                    injection_detected=True,
                )

        # 3. 敏感词检测（仅日志警告，不再硬拦截——交给模型拒绝）
        for word in SENSITIVE_WORDS:
            if word in message:
                log.warning("guard.sensitive_word", word=word)
                # 不再硬拦截，让模型自行拒绝
                break

        return GuardResult(
            safe=True,
            cleaned_message=message,
            injection_detected=injection_detected,
        )

    async def check_output(
        self,
        text: str,
        ctx: dict,
        *,
        valid_chunk_ids: list[str] | None = None,
        degraded: bool = False,
    ) -> str:
        """输出校验：
        - degraded 模式：移除所有【N】引用标记（防止伪造引用）
        - citation chunk_id 必须 ∈ 本次召回集，否则删标记
        - 越权字段扫描（白名单）
        """
        # 0. 降级模式：移除所有【N】标记，防止伪造引用
        if degraded:
            text = self._strip_degraded_citations(text)

        # 1. Citation 校验：验证【N】标记对应的 chunk_id 是否合法
        if valid_chunk_ids is not None:
            text = self._validate_citations(text, valid_chunk_ids)

        # 2. 越权字段扫描
        text = self._scan_privilege_escalation(text, ctx)

        # 3. 系统提示词泄露检测
        text = self._scan_prompt_leakage(text)

        # 4. 迭代18：清洗模型自带免责尾巴（小米 MiMo 末尾追加
        #    "|部分内容可能由AI生成"，落库/历史回显兜底清洗）
        text = self._strip_ai_disclaimer_tail(text)

        return text

    _AI_DISCLAIMER_TAIL_RE = re.compile(r"[\s｜|]*部分内容可能由AI生成[。.!！]*\s*$")

    def _strip_ai_disclaimer_tail(self, text: str) -> str:
        """移除回复末尾的 AI 免责声明尾巴（provider 层已流式清洗，此处兜底落库）"""
        if not text:
            return text
        cleaned = self._AI_DISCLAIMER_TAIL_RE.sub("", text)
        if cleaned != text:
            logger.info("guard.ai_disclaimer_stripped")
        return cleaned

    _DEGRADED_CITATION_RE = re.compile(r"【\d+】")

    def _strip_degraded_citations(self, text: str) -> str:
        """降级模式下移除所有【N】引用标记，防止伪造教材来源"""
        cleaned, n = self._DEGRADED_CITATION_RE.subn("", text)
        if n > 0:
            logger.warning(
                "guard.degraded_citation_stripped",
                count=n,
            )
        return cleaned

    def _validate_citations(self, text: str, valid_chunk_ids: list[str]) -> str:
        """验证 citation 标记的合法性

        如果文本中的【N】标记对应的 chunk 不在召回集中，删除该标记。
        注意：这里的 N 是序号（1,2,3...），不是 chunk_id。
        主链路会传入有效的序号范围。
        """
        # 找出所有【N】标记
        citation_pattern = re.compile(r"【(\d+)】")
        matches = citation_pattern.findall(text)

        if not matches:
            return text

        # 验证序号是否在有效范围内
        max_valid = len(valid_chunk_ids)
        for num_str in matches:
            num = int(num_str)
            if num > max_valid or num < 1:
                # 删除无效引用标记
                text = text.replace(f"【{num_str}】", "")
                logger.warning("guard.invalid_citation", citation_num=num, max_valid=max_valid)

        return text

    def _scan_privilege_escalation(self, text: str, ctx: dict) -> str:
        """越权字段扫描

        检查输出是否包含不应暴露的字段（其他用户的 ID、内部字段等）。
        """
        user_id = ctx.get("user_id", "")

        # 检查是否泄露 UUID 格式的 user_id（非当前用户的）
        uuid_pattern = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
        uuids_in_text = uuid_pattern.findall(text)

        for uid in uuids_in_text:
            if uid != user_id:
                # 可能是泄露的其他用户 ID，替换为 [REDACTED]
                text = text.replace(uid, "[ID]")
                logger.warning("guard.uuid_leak", leaked_id=uid[:8])

        return text

    # 系统提示词泄露检测关键词
    _PROMPT_LEAK_PATTERNS: list[re.Pattern] = [
        re.compile(r"(我的|系统)(提示词|提示语|prompt|system\s*prompt)是.{5,}", re.IGNORECASE),
        re.compile(r"(对话设定|系统配置|内部配置|内部设定)如下", re.IGNORECASE),
    ]

    def _scan_prompt_leakage(self, text: str) -> str:
        """检测输出是否包含系统提示词泄露"""
        for pattern in self._PROMPT_LEAK_PATTERNS:
            if pattern.search(text):
                logger.warning("guard.prompt_leak_detected")
                # 替换为安全回复
                return "抱歉，我无法透露系统配置或内部设定信息。有什么数学问题我可以帮你吗？"
        return text


# ---- 全局单例 ----
_guard: Guard | None = None


def get_guard() -> Guard:
    """获取全局 Guard 单例"""
    global _guard
    if _guard is None:
        _guard = Guard()
    return _guard
