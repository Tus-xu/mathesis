r"""语音/口语公式 L1 规则引擎 v3（M3.1：词典 ≥60 条，种子自 mock voiceToLatex 22 条）。

三层管线（notes/B1-notes.md §4）：本模块 = L1 确定性层；L2 LLM 解析（双假设
few-shot）与 L3 KaTeX 校验在 M3 后续接入。

工程约定（防 re 模板坑）：
- 所有替换一律用**可调用**（返回字面量），不用 re 替换模板——模板的 `\\`→`\`
  语义与坏转义（如 `\\prime`→bad escape）在 Python 3.12 下静默/易错（v2 教训）；
- LaTeX 命令替换统一带尾随空格作分隔（`\\times r` 而非 `\\times r` 连写）；
  尾部空白由 voice_to_latex 统一收敛；
- 中文数字不做全局转换（「一些/一样」误伤），只做词组级定点规则；自然语言歧义
  交给 L2（M3 接入）。
"""
from __future__ import annotations

import re

_DIGIT_ONES = "一二三四五六七八九"
_O = "，。,、\\s"  # 口语边界（顿号/逗号/句号/空白；串尾由 |$ 兜底）


def _num_word(w: str) -> str:
    """十位级中文数字词 → 阿拉伯（十二→12、二十三→23、五→5、十→10）；不认识原样返回。"""
    if w == "十":
        return "10"
    m = re.fullmatch(rf"十(?P<ones>[{_DIGIT_ONES}])", w)  # 十二
    if m:
        return str(10 + _DIGIT_ONES.index(m.group("ones")) + 1)
    m = re.fullmatch(rf"(?:(?P<tens>[{_DIGIT_ONES}])十)?(?P<ones>[{_DIGIT_ONES}])?", w)
    if m and (m.group("tens") or m.group("ones")):
        tens = _DIGIT_ONES.index(m.group("tens")) + 1 if m.group("tens") else 0
        ones = _DIGIT_ONES.index(m.group("ones")) + 1 if m.group("ones") else 0
        return str(tens * 10 + ones)
    return w


_NUM_WORD = rf"(?:十[{_DIGIT_ONES}]?|[{_DIGIT_ONES}]十(?:[{_DIGIT_ONES}])?|[{_DIGIT_ONES}])"


def _lit(text: str):
    """字面量替换（无模板解析）。"""
    return lambda _m, _t=text: _t


_OPERAND = "[\\u4e00-\\u9fa5a-z0-9√^+*/(){}\\\\]"


def _rules() -> list[tuple[re.Pattern, object]]:
    R: list[tuple[re.Pattern, object]] = []
    # ── 礼貌语剥离（mock :99-100）与定点中文数字 ──
    R.append((re.compile(r"^\s*(帮我|请)?(把)?(转换成|转成|输入|说出|说)?(可编辑的公式|的公式|公式)?"), _lit("")))
    R.append((re.compile(r"(转换|转)成(可编辑)?的?公式$"), _lit("")))
    R.append((re.compile(r"零点([一二三四五六七八九])"), lambda m: f"0.{_DIGIT_ONES.index(m.group(1)) + 1}"))
    # ── 分式（中文数字词组优先于通配，mock :102 扩展）──
    R.append(
        (
            re.compile(rf"({_NUM_WORD})分之({_NUM_WORD})(?=[{_O}]|$)"),
            lambda m: "\\frac{%s}{%s}" % (_num_word(m.group(2)), _num_word(m.group(1))),
        )
    )
    R.append(
        (
            re.compile(rf"({_OPERAND}{{1,12}})分之({_OPERAND}{{1,12}})(?=[{_O}]|$)"),
            lambda m: "\\frac{%s}{%s}" % (m.group(2), m.group(1)),
        )
    )
    # ── 根式（mock :104 + 无「下」变体 + n 次根）──
    R.append(
        (
            re.compile(rf"(二|三|四)次根号(?:下)?({_OPERAND}{{1,20}})(?=[{_O}]|$)"),
            lambda m: "\\sqrt[%s]{%s}" % (_num_word(m.group(1)), m.group(2)),
        )
    )
    R.append((re.compile(rf"根号下?({_OPERAND}{{1,20}})(?=[{_O}]|$)"), lambda m: "\\sqrt{%s}" % m.group(1)))
    # ── 幂（mock :106-109 + 负次方）──
    R.append((re.compile(r"e的([a-z0-9]+)次方"), lambda m: "e^{%s}" % m.group(1)))
    R.append((re.compile(r"的负([a-z0-9]+)次方"), lambda m: "^{-%s}" % m.group(1)))
    R.append((re.compile(r"的平方|平方"), _lit("^{2}")))
    R.append((re.compile(r"的立方|立方"), _lit("^{3}")))
    R.append((re.compile(r"的四次方"), _lit("^{4}")))
    R.append((re.compile(r"的(\d+)次方"), lambda m: "^{%s}" % m.group(1)))
    R.append((re.compile(r"的([a-z])次方"), lambda m: "^{%s}" % m.group(1)))
    R.append((re.compile(r"e的"), _lit("e^")))
    # ── 下标/角标/第 n 项 ──
    R.append((re.compile(r"([a-z])下标\(?([a-z0-9]+)\)?"), lambda m: "%s_{%s}" % (m.group(1), m.group(2))))
    R.append((re.compile(r"([a-z])角标\(?([a-z0-9]+)\)?"), lambda m: "%s_{%s}" % (m.group(1), m.group(2))))
    R.append((re.compile(r"第([a-z0-9]+)项"), lambda m: "a_{%s}" % m.group(1)))
    # ── 希腊字母（常用 12）与圆周率 ──
    for zh, g in [
        ("阿尔法", "alpha"), ("贝塔", "beta"), ("伽马", "gamma"), ("德尔塔", "delta"),
        ("艾普西龙", "epsilon"), ("西塔", "theta"), ("兰姆达", "lambda"), ("缪", "mu"),
        ("派", "pi"), ("柔", "rho"), ("西格玛", "sigma"), ("欧米伽", "omega"),
    ]:
        R.append((re.compile(zh), _lit(f"\\{g} ")))
    R.append((re.compile(r"圆周率"), _lit("\\pi ")))
    # ── 运算符（mock :111-116）──
    R.append((re.compile(r"正负|加减"), _lit("\\pm ")))
    # 绝对值须在四则替换前执行，否则操作数里的加减号会破坏边界
    R.append((re.compile(rf"绝对值({_OPERAND}{{1,12}})(?=[{_O}]|$)"), lambda m: "\\left|%s\\right|" % m.group(1)))
    R.append((re.compile(r"加上|加"), _lit("+")))
    R.append((re.compile(r"减去|减"), _lit("-")))
    R.append((re.compile(r"乘以|乘上|乘"), _lit("\\times ")))
    R.append((re.compile(r"除以|除"), _lit("\\div ")))
    # ── 比较（mock :114-115；「比较零」先于比较词，比较词先于「等于」）──
    R.append(
        (
            re.compile(r"(大于等于|小于等于|不等于|约等于|等于)零"),
            lambda m: {"大于等于": "\\geq 0", "小于等于": "\\leq 0", "不等于": "\\neq 0", "约等于": "\\approx 0", "等于": "=0"}[m.group(1)],
        )
    )
    R.append((re.compile(r"大于等于"), _lit("\\geq ")))
    R.append((re.compile(r"小于等于"), _lit("\\leq ")))
    R.append((re.compile(r"不等于"), _lit("\\neq ")))
    R.append((re.compile(r"恒等于"), _lit("\\equiv ")))
    R.append((re.compile(r"约等于"), _lit("\\approx ")))
    R.append((re.compile(r"等于"), _lit("=")))
    R.append((re.compile(r"大于"), _lit(">")))
    R.append((re.compile(r"小于"), _lit("<")))
    R.append((re.compile(r"无穷"), _lit("\\infty ")))
    # ── 集合与关系（并/交 只在字母上下文，防「并且/交叉」误伤）──
    R.append((re.compile(r"属于"), _lit("\\in ")))
    R.append((re.compile(r"包含于|子集(于)?"), _lit("\\subseteq ")))
    R.append((re.compile(r"并集"), _lit("\\cup ")))
    R.append((re.compile(r"(?<=[a-z0-9\\)])并(?=[a-z0-9\\(])"), _lit("\\cup ")))
    R.append((re.compile(r"交集"), _lit("\\cap ")))
    R.append((re.compile(r"(?<=[a-z0-9\\)])交(?=[a-z0-9\\(])"), _lit("\\cap ")))
    R.append((re.compile(r"全等于"), _lit("\\cong ")))
    R.append((re.compile(r"相似于"), _lit("\\sim ")))
    # ── 三角/对数/度（mock :118-120 扩展）──
    R.append((re.compile(r"反正弦"), _lit("\\arcsin ")))
    R.append((re.compile(r"反余弦"), _lit("\\arccos ")))
    R.append((re.compile(r"反正切"), _lit("\\arctan ")))
    # (?<!\\) 防止字母替代词咬进已生成的命令（\arcsin 里的 sin、\log 里的 log）
    R.append((re.compile(r"正弦|(?<!\\)sin"), _lit("\\sin ")))
    R.append((re.compile(r"余弦|(?<!\\)cos"), _lit("\\cos ")))
    R.append((re.compile(r"正切|(?<!\\)tan"), _lit("\\tan ")))
    R.append((re.compile(r"以([a-z0-9]+)为底"), lambda m: "\\log_{%s}" % m.group(1)))
    R.append((re.compile(r"(?<=[a-z0-9}])的对数"), _lit("")))
    R.append((re.compile(r"自然对数|(?<!\\)ln"), _lit("\\ln ")))
    R.append((re.compile(r"对数|(?<!\\)log"), _lit("\\log ")))
    R.append((re.compile(r"(\d+)度"), lambda m: "%s^{\\circ}" % m.group(1)))
    # ── 级数/积分/极限/导数 ──
    R.append(
        (
            re.compile(rf"从负?([a-z0-9一-九十]+)到(正?无穷|[a-z0-9一-九十]+)的?求和"),
            lambda m: "\\sum_{%s}^{%s}" % (_num_word(m.group(1)) if m.group(1).isalpha() else m.group(1), "\\infty " if m.group(2).endswith("无穷") else (_num_word(m.group(2)) if m.group(2).isalpha() else m.group(2))),
        )
    )
    R.append((re.compile(r"求和(符号)?"), _lit("\\sum ")))
    R.append(
        (
            re.compile(rf"从负?([a-z0-9一-九十]+)到(正?无穷|[a-z0-9一-九十]+)的?积分"),
            lambda m: "\\int_{%s}^{%s}" % (_num_word(m.group(1)) if m.group(1).isalpha() else m.group(1), "\\infty " if m.group(2).endswith("无穷") else (_num_word(m.group(2)) if m.group(2).isalpha() else m.group(2))),
        )
    )
    R.append((re.compile(r"积分(符号)?"), _lit("\\int ")))
    R.append((re.compile(r"([a-z])趋于(正?无穷|[a-z0-9]+)"), lambda m: "\\lim_{%s \\to %s}" % (m.group(1), "\\infty " if m.group(2).endswith("无穷") else m.group(2))))
    R.append((re.compile(r"的极限|极限(符号)?"), _lit("")))
    R.append((re.compile(r"的一阶导(数)?|的二阶导(数)?|的导数|一阶导(数)?|导数"), _lit("^\\prime")))
    R.append((re.compile(r"撇"), _lit("'")))
    # ── 括号口令 ──
    R.append((re.compile(r"左括号|左圆括号"), _lit("(")))
    R.append((re.compile(r"右括号|右圆括号"), _lit(")")))
    R.append((re.compile(r"左方括号"), _lit("[")))
    R.append((re.compile(r"右方括号"), _lit("]")))
    return R


_RULES = _rules()


def voice_to_latex(text: str) -> str:
    """L1 规则解析：口语 → LaTeX。有规则命中但残留中文时包 \\text{}（mock 兜底同款）。"""
    out = text.strip()
    for pattern, repl in _RULES:
        out = pattern.sub(repl, out)
    out = re.sub(r"\s+", " ", out).strip()
    if "\\text" not in out and re.search(r"[\u4e00-\u9fa5]", out):
        out = f"\\text{{{out}}}"
    return out


def rule_confidence(text: str, latex: str) -> float:
    """L1 直出置信度：规则命中且无残留中文 = 0.95；有残留（待 L2/L3）= 0.4。"""
    if latex != text.strip() and not re.search(r"[\u4e00-\u9fa5]", latex):
        return 0.95
    return 0.4
