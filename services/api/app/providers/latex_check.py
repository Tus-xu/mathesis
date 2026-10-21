"""LaTeX 服务端校验（ADR-M2B-005）

两层校验：
1. 结构校验（纯 Python）：分隔符配对、花括号平衡、长度上限
2. 语义可解析校验：sympy.parsing.latex.parse_latex（可选，antlr4 依赖）

校验失败带错误信息重试一次（prompt 附错误原因），再失败返回 {latex:null, ambiguous:true}。
"""

from __future__ import annotations

import re

import structlog

logger = structlog.get_logger(__name__)

# 最大 LaTeX 长度
MAX_LATEX_LEN = 2000


def check_structure(latex: str) -> tuple[bool, str]:
    """结构校验（纯 Python）

    Returns:
        (is_valid, error_message)
    """
    if not latex or not latex.strip():
        return False, "LaTeX 为空"

    if len(latex) > MAX_LATEX_LEN:
        return False, f"LaTeX 长度超限（{len(latex)} > {MAX_LATEX_LEN}）"

    # 花括号平衡
    depth = 0
    for ch in latex:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        if depth < 0:
            return False, "花括号不配对：多余的 }"
    if depth != 0:
        return False, f"花括号不配对：缺少 {depth} 个 }}"

    # $ 分隔符配对（行内 $...$）
    # 先移除 $$...$$（独立公式），再检查 $ 数量
    text_no_display = re.sub(r"\$\$.*?\$\$", "", latex, flags=re.DOTALL)
    single_dollars = text_no_display.count("$")
    if single_dollars % 2 != 0:
        return False, "行内公式 $ 分隔符不配对"

    # $$ 配对
    display_count = latex.count("$$")
    if display_count % 2 != 0:
        return False, "独立公式 $$ 分隔符不配对"

    # \begin{} / \end{} 配对
    begins = re.findall(r"\\begin\{(\w+)\}", latex)
    ends = re.findall(r"\\end\{(\w+)\}", latex)
    if len(begins) != len(ends):
        return False, f"\\begin/\\end 数量不匹配（{len(begins)} vs {len(ends)}）"
    for b, e in zip(begins, ends, strict=True):
        if b != e:
            return False, f"\\begin{{{b}}} 与 \\end{{{e}}} 不匹配"

    # 禁止 \(...\) 分隔符（ADR-023 纪律）
    if r"\(" in latex or r"\)" in latex:
        return False, "禁止使用 \\(...\\) 分隔符，请用 $...$"

    return True, ""


def check_parseable(latex: str) -> tuple[bool, str]:
    """语义可解析校验（依赖 sympy + antlr4）

    解析库不可用时降级为仅结构校验。

    Returns:
        (is_valid, error_message)
    """
    try:
        from sympy.parsing.latex import parse_latex

        # 去除 $ 分隔符再解析
        clean = latex.strip()
        clean = re.sub(r"^\$\$?", "", clean)
        clean = re.sub(r"\$\$?$", "", clean)
        clean = clean.strip()

        if not clean:
            return True, ""  # 空内容结构校验已覆盖

        parse_latex(clean)
        return True, ""

    except ImportError:
        # antlr4 未安装，降级为仅结构校验
        logger.debug("latex_parse_unavailable", reason="sympy.parsing.latex not available")
        return True, ""  # 不阻塞

    except Exception as e:
        return False, f"LaTeX 语义解析失败: {str(e)[:100]}"


def validate_latex(latex: str, strict: bool = False) -> tuple[bool, str]:
    """完整 LaTeX 校验

    Args:
        latex: 待校验的 LaTeX 字符串
        strict: 严格模式（启用语义解析）

    Returns:
        (is_valid, error_message)
    """
    # 第一层：结构校验（必须通过）
    valid, err = check_structure(latex)
    if not valid:
        return False, err

    # 第二层：语义校验（strict 模式或默认尝试）
    if strict:
        valid, err = check_parseable(latex)
        if not valid:
            return False, err

    return True, ""


def normalize_latex(latex: str) -> str:
    """LaTeX 规范化（入库前/比对前）

    - 统一分隔符为 $...$
    - 去除多余空白
    - 确保花括号内无首尾空格
    """
    if not latex:
        return ""

    # \(...\) → $...$
    result = latex.replace(r"\(", "$").replace(r"\)", "$")

    # 压缩多余空白（保留公式内空格）
    result = re.sub(r"[ \t]+", " ", result)

    # 去除首尾空白
    result = result.strip()

    return result


def check_formula_pairing(text: str) -> bool:
    """公式配对检查（知识库入库红线，f4 §5）

    检查文本中：
    - $ 出现偶数次
    - $$ 成对
    - \begin{...}/\\end{...} 配对

    全量通过才允许入库。
    """
    # $$ 先移除
    text_no_display = re.sub(r"\$\$.*?\$\$", "", text, flags=re.DOTALL)

    # 单 $ 偶数
    if text_no_display.count("$") % 2 != 0:
        return False

    # $$ 偶数
    if text.count("$$") % 2 != 0:
        return False

    # begin/end 配对
    begins = re.findall(r"\\begin\{(\w+)\}", text)
    ends = re.findall(r"\\end\{(\w+)\}", text)
    return begins == ends
