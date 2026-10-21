"""socratic_solver 解析与防泄题工具（迭代02 v2）

- extract_boxed：括号平衡扫描提取 \\boxed{}，支持任意嵌套
- parse_steps / validate_solution：[[STEP]] 分步解析与输出契约校验
- parse_solver_output：v2 契约解析（步骤 + 终答 + 难度行 + 代码块剥离 + 可选另解段）
- extract_code_blocks：TIR 代码块抽取
- normalize_for_compare / find_leak：发给学生文本的泄题检查（v2 增强：等式右值关键量）
- extract_math_expr：判答 sympy 快速通道的表达式抽取（启发式）
"""

from __future__ import annotations

import re

# ========== \boxed{} 提取（括号平衡扫描） ==========

_BOXED_MARKER = "\\boxed"


def _scan_braced(text: str, open_idx: int) -> tuple[str, int] | None:
    """从 text[open_idx]（必须是 '{'）开始平衡扫描，返回 (内容, 闭合后下标)。

    转义花括号 \\{ \\} 不参与计数。未闭合返回 None。
    """
    depth = 0
    i = open_idx
    start = open_idx + 1
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and text[i + 1] in "{}":
            i += 2  # 转义花括号，跳过
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i], i + 1
        i += 1
    return None


def extract_boxed(text: str) -> str | None:
    """提取最后一个 \\boxed{...} 的内容（任意嵌套），无则 None"""
    if not text:
        return None
    result: str | None = None
    pos = 0
    while True:
        idx = text.find(_BOXED_MARKER, pos)
        if idx < 0:
            break
        brace = idx + len(_BOXED_MARKER)
        # 允许 \boxed 与 { 之间有空白
        while brace < len(text) and text[brace] in " \t":
            brace += 1
        if brace < len(text) and text[brace] == "{":
            scanned = _scan_braced(text, brace)
            if scanned is not None:
                result = scanned[0]
                pos = scanned[1]
                continue
        pos = idx + len(_BOXED_MARKER)
    return result.strip() if result is not None else None


def remove_boxed(text: str) -> str:
    """移除文本中的 \\boxed{...} 片段（用于步骤文本清洗）"""
    out = text
    while True:
        idx = out.find(_BOXED_MARKER)
        if idx < 0:
            return out
        brace = idx + len(_BOXED_MARKER)
        while brace < len(out) and out[brace] in " \t":
            brace += 1
        if brace < len(out) and out[brace] == "{":
            scanned = _scan_braced(out, brace)
            if scanned is not None:
                out = out[:idx] + out[scanned[1] :]
                continue
        # 未闭合：去掉标记本身防死循环
        out = out[:idx] + out[idx + len(_BOXED_MARKER) :]


# ========== [[STEP]] 解析 ==========

_STEP_SPLIT = re.compile(r"\[\[STEP\]\]", re.IGNORECASE)
_ASSERT_RE = re.compile(r"\**\s*【?断言】?\s*\**\s*[:：]\s*")
_REASON_RE = re.compile(r"\**\s*【?原因】?\s*\**\s*[:：]\s*")
_LEADING_ASSERT_RE = re.compile(r"^\s*\**\s*【?断言】?\s*\**\s*[:：]?\s*")


def _clean_field(text: str) -> str:
    """清洗字段文本：去 markdown 强调、去 boxed、合并多余空白行"""
    text = remove_boxed(text)
    text = text.replace("**", "").strip()
    # 合并多行为单段（保留 LaTeX 内容），去掉空行
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return " ".join(lines).strip()


def parse_steps(solution: str) -> list[dict]:
    """解析 [[STEP]] 分隔的步骤 → [{assertion, reason}]

    健壮性：
    - 缺“断言：”前缀的步骤 → 断言兜底为清洗后的整段文本；
    - “断言：”变体（**断言**：/【断言】/半角冒号）均可识别；
    - 无 [[STEP]] 时整体按单步解析（单步塌缩）；
    - 纯终答段（清洗后为空）自动丢弃。
    """
    if not solution or not solution.strip():
        return []

    parts = _STEP_SPLIT.split(solution)
    steps: list[dict] = []
    for part in parts:
        if not part.strip():
            continue
        m_a = _ASSERT_RE.search(part)
        m_r = _REASON_RE.search(part)
        assertion = ""
        reason = ""
        if m_a:
            end = m_r.start() if m_r and m_r.start() > m_a.end() else len(part)
            assertion = _clean_field(part[m_a.end() : end])
        if m_r:
            reason = _clean_field(part[m_r.end() :])
        if not assertion:
            # 兜底：原因段之前的整段作为断言（剥离“断言：”残留前缀与 boxed）
            body = part if m_r is None else part[: m_r.start()]
            body = _LEADING_ASSERT_RE.sub("", body)
            assertion = _clean_field(body)
        if not assertion:
            continue  # 纯终答段/空段
        steps.append({"assertion": assertion, "reason": reason})
    return steps


def validate_solution(raw: str) -> tuple[list[dict] | None, str | None, str | None]:
    """校验 solver 输出契约。

    Returns:
        (steps, final_answer, error)；error 非 None 时前两者为 None，
        error 文本可直接作为重试反馈。
    """
    if not raw or not raw.strip():
        return None, None, "输出为空"
    if not _STEP_SPLIT.search(raw):
        return None, None, "缺少 [[STEP]] 分步标记，每一步之间必须用 [[STEP]] 单独一行分隔"
    steps = parse_steps(raw)
    if not steps:
        return None, None, "未能解析出任何有效步骤，每一步需要以“断言：”开头给出可校验结论"
    final_answer = extract_boxed(raw)
    if not final_answer:
        return (
            None,
            None,
            "缺少最终答案，必须在结尾用 \\boxed{} 包裹（证明题写 \\boxed{\\text{见上述推导}}）",
        )
    return steps, final_answer, None


# ========== v2：TIR 代码块与难度行 ==========

_CODE_BLOCK_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

_DIFFICULTY_RE = re.compile(
    r"(?:难度|difficulty)\s*[:：]\s*(easy|medium|hard|基础|中等|偏难|困难|压轴|竞赛)",
    re.IGNORECASE,
)
_DIFFICULTY_MAP = {
    "easy": "easy",
    "medium": "medium",
    "hard": "hard",
    "基础": "easy",
    "中等": "medium",
    "偏难": "hard",
    "困难": "hard",
    "压轴": "hard",
    "竞赛": "hard",
}


def extract_code_blocks(text: str) -> list[str]:
    """抽取 ```python 代码块（TIR：solver 中途请求机器计算）"""
    if not text:
        return []
    return [m.group(1).strip() for m in _CODE_BLOCK_RE.finditer(text) if m.group(1).strip()]


def strip_code_blocks(text: str) -> str:
    """移除全部代码块（最终回复契约不允许残留；解析前剥离防污染断言）"""
    if not text:
        return ""
    return _CODE_BLOCK_RE.sub("", text)


def extract_difficulty(text: str) -> str:
    """从 solver 输出提取难度行 → easy|medium|hard（缺省 medium）"""
    if not text:
        return "medium"
    m = _DIFFICULTY_RE.search(text)
    if not m:
        return "medium"
    return _DIFFICULTY_MAP.get(m.group(1).lower(), _DIFFICULTY_MAP.get(m.group(1), "medium"))


def parse_solver_output(raw: str) -> tuple[list[dict] | None, str | None, str, str, str | None]:
    """v2 契约解析：剥离代码块 → 步骤/终答校验 → 难度行 → 可选另解段。

    Returns:
        (steps, final_answer, difficulty, alt_solution, error)；error 非 None 时其余无效。
        alt_solution 为「另解：…」段的正文（无另解时为空字符串）——M2 重构新增，
        存在第二种自然解法时由 solver 软性附上，不强制。
    """
    cleaned = strip_code_blocks(raw or "")
    alt_solution = _extract_alt_solution(cleaned)
    steps, final_answer, error = validate_solution(cleaned)
    if error is not None:
        return None, None, "medium", "", error
    return steps, final_answer, extract_difficulty(raw), alt_solution, None


_ALT_SOLUTION_RE = re.compile(r"另解[:：]\s*(.+?)(?=\n\s*(?:最终答案|难度)[:：]|\Z)", re.DOTALL)


def _extract_alt_solution(text: str) -> str:
    """从 solver 输出中抽取可选的「另解：…」段正文（无则空串）"""
    m = _ALT_SOLUTION_RE.search(text or "")
    if not m:
        return ""
    return m.group(1).strip()[:600]


# parse_step_check 已随 M2 重构删除（步骤级验证脚本环节已移除）


# ========== 防泄题检查 =========

_LATEX_DECOR_RE = re.compile(r"\\(?:left|right|displaystyle|limits|!|,|;|:|quad|qquad|~| )")


def normalize_for_compare(text: str) -> str:
    """归一化：去空白、去 LaTeX 装饰（$、花括号、修饰命令、反斜杠）"""
    if not text:
        return ""
    s = _LATEX_DECOR_RE.sub("", text)
    s = s.replace("\\boxed", "")
    s = re.sub(r"\\text\{([^}]*)\}", r"\1", s)
    s = re.sub(r"[\s$~{}\\]+", "", s)
    return s


def _extract_answer_key_quantities(final_answer: str) -> list[str]:
    """从终答抽取"关键量"（等式右值/独立数值表达式），用于变体泄露检查。

    只保留归一化后长度 ≥4 的片段（过短的数字如 "3" 误报率太高，不查）。
    例：x=\\frac{1+\\sqrt{5}}{2} → 右值归一化 "frac{1+sqrt{5}}{2}" 形态可检出变体。
    """
    quantities: list[str] = []
    if not final_answer:
        return quantities
    # 先剥掉 \text{...} 包装（保留内容），避免按"或"拆分时残留花括号碎片
    cleaned = re.sub(r"\\text\{([^}]*)\}", r"\1", final_answer)
    # 按 "或/，/,/;" 切分多值终答
    parts = re.split(r"或|或者|[，,;；]", cleaned)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 等式：取右值
        rhs = part
        for sep in ("=", "\\approx", "\\approx", "\\leq", "\\geq", "<", ">", "\\in"):
            if sep in part:
                rhs = part.split(sep, 1)[1]
                break
        norm = normalize_for_compare(rhs)
        if len(norm) >= 4:
            quantities.append(norm)
    return quantities


def find_leak(text: str, plan: dict, current_step: int) -> str | None:
    """检查待发送文本是否泄题。

    命中返回泄露类型（"final_answer" / "future_step"），否则 None。
    current_step 为 1 基当前步；只检查当前步之后的断言（当前步允许按级别提示）。
    v2 增强：终答等式右值关键量的归一化包含检查（抓 LaTeX 变体泄露）。
    """
    if not text:
        return None

    norm_text = normalize_for_compare(text)
    final_answer = (plan.get("final_answer") or "").strip()
    if final_answer:
        if len(final_answer) >= 2 and final_answer in text:
            return "final_answer"
        norm_final = normalize_for_compare(final_answer)
        if len(norm_final) >= 3 and norm_final in norm_text:
            return "final_answer"
        # 短终答盲区补偿（迭代05 B-P1-17，ADR-033 泄露率=0）：
        # 归一化长度 1~2 的终答（如 "2"/"8"/"55"）原阈值完全放行，
        # 改用"句内独立 token + 答案揭示语境词"双条件严检，
        # 平衡误报（"第 2 步"这类步骤序号无揭示语境词不判泄露）。
        if 1 <= len(norm_final) <= 2 and re.fullmatch(r"[0-9A-Za-z]+", norm_final):
            has_answer_cue = (
                re.search(r"答案|结果为|结果是|得到|所以|因此|故\s|可知", text) is not None
            )
            has_bare_token = (
                re.search(r"(?<![0-9A-Za-z.])" + re.escape(norm_final) + r"(?![0-9A-Za-z])", text)
                is not None
            )
            if has_answer_cue and has_bare_token:
                return "final_answer"
        # 关键量检查（等式右值，如 \\frac{1+\\sqrt{5}}{2} 的变体）
        for quantity in _extract_answer_key_quantities(final_answer):
            if quantity in norm_text:
                return "final_answer"

    steps = plan.get("steps") or []
    for step in steps[current_step:]:  # current_step 1 基 → 后续步骤从下标 current_step 起
        assertion = (step.get("assertion") or "").strip()
        if len(assertion) >= 8 and assertion in text:
            return "future_step"
        norm_a = normalize_for_compare(assertion)
        if len(norm_a) >= 8 and norm_a in norm_text:
            return "future_step"
    return None


# ========== 判答 sympy 快速通道的表达式抽取 =========

_INLINE_MATH_RE = re.compile(r"\$([^$]+)\$")
_EXPR_CHARS_RE = re.compile(r"[0-9a-zA-Z+\-*/^=().√π_{}\[\]\\ ]{3,}")


def extract_math_expr(text: str) -> str | None:
    """从文本中启发式抽取可解析的数学表达式（判答快速通道用）。

    优先取最后一个 $...$ 行内公式；否则找含运算符的式子片段。
    抽不到（纯文字）返回 None —— 走 LLM judge。
    """
    if not text:
        return None
    inline = _INLINE_MATH_RE.findall(text)
    if inline:
        candidate = inline[-1].strip()
        if candidate:
            return candidate
    for m in _EXPR_CHARS_RE.finditer(text):
        frag = m.group(0).strip()
        # 至少含一个数字与一个运算符，避免抽中普通单词
        if any(c.isdigit() for c in frag) and any(op in frag for op in "=+*/^"):
            return frag
    return None
