"""教师备课文本安全共享原语：真 HTML 标签白名单正则 + 模型/教材文本消毒。

历史缺陷（2026-09-14 真实链路 P1，handoff Work card E-1）：
旧正则 ``<[A-Za-z/!][^>]{0,N}>`` 把**数学不等式链**（如 ``0<a<1 时 f(a)>0``、
``x<b<b``）误判为 HTML 标签——授权教材切片的正文/教学目标充满这类文本，
导致 context/blueprint 卡片在 ``catalog.validate_component`` 落库闸门被 42200
拒绝，整条备课命令失败。

新正则只匹配**真实 HTML 标签**：标签名白名单 + ASCII 属性语法（属性段必须以
空白起始且仅含 ASCII 属性字符），因此含 CJK/空白的数学文本不再命中；
``<!-- -->`` 注释单独拦截。危险表达式（on*=、javascript: 等）仍由各闸门的
`_EXPR_RE` 独立拦截。

`strip_html_tags` 用于 UNTRUSTED 文本（教材 chunk、LLM 输出）进入展示/落库
链路前的确定性消毒：块级标签转换行、其余真标签剥除、数学文本原样保留。
实体（`&lt;` 等）不反转义——保持字面量，避免反解出新标签。
"""

from __future__ import annotations

import re

from app.teacher_prep.specs.math_text import latex_to_unicode, strip_boxed

# 真实 HTML 标签名白名单（含 SVG/iframe/script 等危险标签——命中即拒绝/剥除）
_HTML_TAGS = (
    "a|abbr|acronym|address|area|article|aside|b|base|bdi|bdo|blockquote|body|br|"
    "button|canvas|caption|center|cite|code|col|colgroup|datalist|dd|del|details|"
    "dfn|dialog|div|dl|dt|em|embed|fieldset|figcaption|figure|font|footer|form|"
    "frame|frameset|h[1-6]|head|header|hr|i|iframe|img|input|ins|kbd|label|legend|"
    "li|link|main|map|mark|menu|meta|meter|nav|noframes|noscript|object|ol|optgroup|"
    "option|output|p|param|picture|pre|progress|q|rp|rt|ruby|s|samp|script|section|"
    "select|slot|small|source|span|strike|strong|style|sub|summary|sup|svg|table|"
    "tbody|td|template|textarea|tfoot|th|thead|time|title|tr|track|tt|u|ul|var|"
    "video|wbr"
)

# 标签：`<tag>` / `</tag>` / `<tag attr="v" ... />`。属性段必须以空白起始且仅含
# ASCII 属性语法（name、="v"、='v'、=裸值），杜绝 `x<a 且 b>c`（CJK 属性段）命中。
# 注释 `<!-- -->` 单独拦截（含跨行）。
HTML_TAG_RE = re.compile(
    rf"</?(?:{_HTML_TAGS})(?:\s+[a-zA-Z_:][-a-zA-Z0-9_:.]*(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))?)*\s*/?>"
    r"|<!--.*?-->",
    re.IGNORECASE | re.DOTALL,
)

# 块级标签 → 换行（消毒时保留段落结构）
_BLOCK_TAG_RE = re.compile(r"<br\s*/?>|</(?:p|div|li|h[1-6]|tr|blockquote)\s*>", re.IGNORECASE)

# 表格结构保留（2026-10-11 教材数字流事故）：教师用书 chunk 以 HTML <table> 承载表格
# （必修二教师用书 28/421 chunk，9.3 节 3/23），原逻辑只把 </tr> 转行、</td>/<th> 被
# 无分隔剥除 → 单元格无缝拼成「序号12345678910 男生身高平均数171.3169.8…」数字流。
# 剥标签前先把单元格边界转 " | "、表格壳转行——产出 markdown 管道表，模型可读、
# 前端 markdown 渲染可复原行列。
_TABLE_CELL_RE = re.compile(r"</t[dh]\s*>", re.IGNORECASE)
_TABLE_EDGE_RE = re.compile(r"</?table[^>]*>|</?(?:thead|tbody|tfoot|caption)\s*>", re.IGNORECASE)
# 行尾悬挂管道（最后一个 </td> 紧跟 </tr> 时产生）清理
_TABLE_TRAILING_PIPE_RE = re.compile(r"\s*\|\s*\n")


def strip_html_tags(text: str) -> str:
    """确定性消毒：表格转管道表、块级标签转换行，其余真实标签剥除；数学文本原样保留。

    不做实体反转义（`&lt;` 保持字面量，避免反解出新标签）。空文本原样返回。
    """
    if not text:
        return text
    out = _TABLE_CELL_RE.sub(" | ", text)
    out = _BLOCK_TAG_RE.sub("\n", out)
    out = _TABLE_EDGE_RE.sub("\n", out)
    out = _TABLE_TRAILING_PIPE_RE.sub("\n", out)
    return HTML_TAG_RE.sub("", out)


# ── T2.5 教材证据引文清洗（markdown 图片/链接语法包装剥离）─────────────────────
# 教材 corpus chunk 原文含 `![图 3-1-2](images/xxx.jpg)`（或导入损坏态 `![] (images/...)`）；
# 原样进入证据卡会把图片引用串当正文渲染给教师（2026-09-21 课堂设计页截图实测）。
# 确定性规则（禁 LLM）：剥语法包装、保留 alt/链接文字与图题；$...$ 数学原样保留。

# markdown 图片：![alt](url)，含损坏态（`]` 与 `(` 间空白、url 内空白）。alt 空 → 整体删除
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\s*\(([^)]*)\)")
# markdown 链接：[text](url) → 保留 text（在图片之后处理，防嵌套误剥）
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\s*\(([^)]*)\)")
# 裸图片路径残片：(images/xxx.jpg)（含路径内空白/损坏段）→ 删除
_BARE_IMAGE_PATH_RE = re.compile(r"\(\s*images/[^)]*\)", re.IGNORECASE)
# 残留的多余空白（剥包装后的接缝）
_EXTRA_WS_RE = re.compile(r"[ \t]{2,}")


def strip_media_syntax(text: str) -> str:
    """确定性清洗：剥离 markdown 图片/链接语法包装与裸 images/ 路径残片。

    - `![图 3-1-2](images/x.jpg)` → `图 3-1-2`（alt/图题保留）；
    - `![] (images/x.jpg)` → ``（空 alt 的损坏图片串整体删除）；
    - `[文本](url)` → `文本`；
    - `(images/x.jpg)` 裸残片 → 删除；多余空白合并。
    数学 `$...$` 与普通文本原样保留。空文本原样返回。
    """
    if not text:
        return text
    out = _MD_IMAGE_RE.sub(lambda m: m.group(1).strip(), text)
    out = _MD_LINK_RE.sub(lambda m: m.group(1).strip(), out)
    out = _BARE_IMAGE_PATH_RE.sub("", out)
    out = _EXTRA_WS_RE.sub(" ", out)
    return out.strip()


# ── 编辑内部标记净化（LEAD-BAR §1.3 铁律：内部标记不得进入任何渲染文本）─────────
# 教师端 / AI 编辑流程会在标题、条目上写入「（教师修订）」「（教师新增）」这类
# **内部过程标注**；它们是流程产物，不是教学内容，一旦进入蓝图/PPT/教案/大纲即泄漏。
# 这里统一定义净化原语，由「提案入口」（blueprint/projector）与「投影入口」
# （specs/projector）共同调用，保证已落库的脏数据在投影时也被清洗。
#
# 注意：只清洗**内部标记**，不清洗教师自撰的正式后缀（如「（教师版）」「（第 2 稿）」）
# ——那些是教师主权内容，必须原样保留（KERNEL-RUBRIC §1.3 教师主权）。
_MARKER_WORDS = (
    "教师修订|教师新增|教师删除|教师补充|教师调整|教师改动|"
    # 自动化编辑验证标记（实测泄漏形态：「（教师A10编辑验证-1）」）
    "教师[A-Za-z]*\\d*编辑验证|"
    # 「（AI 生成变式）」是反幻觉打标，但它是工程黑话，不得进入教师可见正文
    # （R8-T 实测泄漏）；放在 `AI\\s*生成` 之前，避免被短模式先截断。
    "AI\\s*生成变式|AI\\s*生成|AI\\s*补全|自动生成|系统生成|机器生成|"
    "待确认|待补充|待完善|待定|TODO|TBD"
)
# 标记：`（教师修订）` / `（待确认：xxx）` / `(AI 生成)` / `（教师A10编辑验证-1）`；
# 可含一个 `[:：]`/`-`/`—` 说明段。
_EDIT_MARKER_RE = re.compile(
    rf"[（(]\s*(?:{_MARKER_WORDS})\s*(?:[:：\-—][^）)]*)?\s*[)）]"
)
# 删除标记后残留的空括号 `（）`/`()`（成对清理，确定性）
_EMPTY_PAIR_RE = re.compile(r"[（(]\s*[)）]")
# 标题/文本尾部的悬空标点（清理后接缝）
_TRAILING_JUNK_RE = re.compile(r"[\s，,、;；:：\-—]+$")


def strip_edit_markers(text: str) -> str:
    """剥除文本中的编辑内部标记（`（教师修订）`/`（待确认）`/`（AI 生成）`…）。

    确定性、幂等：只删除标记与其残留空括号，不改写正文内容。空文本原样返回。
    """
    if not text:
        return text
    out = _EDIT_MARKER_RE.sub("", text)
    out = _EMPTY_PAIR_RE.sub("", out)
    return out


def sanitize_visible_text(text: str) -> str:
    """渲染可见文本的统一净化入口：剥编辑标记 + `\\boxed{}` 包装 + 去尾部悬空标点。

    由提案/投影入口共同调用（保持 source_ref 不被触碰——本函数只处理文本字段）。
    注意：**保留** `$…$` 内联数学源码——教案导出层（doc_render）负责排版为真数学式
    （PDF 叠式分式/DOCX OMML/MD Unicode），此处只剥 `\\boxed` 这类非法包装。
    清理后为空返回 ``""``（调用方据此丢弃整条，绝不编造补齐）。
    """
    out = strip_edit_markers(str(text or ""))
    out = strip_boxed(out)
    return _TRAILING_JUNK_RE.sub("", out).strip()


# ── R3e 幽灵图引用清洗（产物审查 G：05「作图结果」/07「对应图形」页内 0 图）────
# 课件正文引用了不存在的图形/作图结果 = 幽灵引用；确定性剥除引用短语（保留语义主干）。
# 教案「教师活动/设计意图」列不走本函数（那里描述教学手段是合法的）。
_GHOST_FIG_PATTERNS = (
    re.compile(r"(根据|依据|由)(学生)?(的)?(作图|画图|绘图)(结果|结论)[，,、]?\s*"),
    re.compile(r"(借助|通过)(多媒体|课件)?(展示|呈现)(的)?(作图|画图|图形)?(结果)?[，,、]?\s*"),
    re.compile(r"[，,、；;]?\s*(与)?(其|该|对应|相应|上述)(的)?(图形|图象|图像|图表)"),
    re.compile(r"(如图|如上图|如下图)(所示)?"),
    re.compile(r"(见|参见)(上|下)?图"),
    re.compile(r"图\s*\d+[-−]\d+(?:[-−]\d+)?(?:\s*[(（]\d+[)）])?"),
    re.compile(r"结合图形(分析|观察)?[，,、]?\s*"),
)


def strip_ghost_figure_refs(text: str) -> str:
    """剥除指向不存在图形的引用短语（幽灵图引用）。确定性、幂等。"""
    if not text:
        return text
    out = str(text)
    for pat in _GHOST_FIG_PATTERNS:
        out = pat.sub("", out)
    out = re.sub(r"[（(]\s*[)）]", "", out)
    return _TRAILING_JUNK_RE.sub("", re.sub(r"\s{2,}", " ", out)).strip()


# ── R3e 数学术语守卫（产物审查 G-07：「两点式」误用，应为「点斜式」）──────────
_FORM_TERMS = ("点斜式", "斜截式", "两点式", "一般式", "截距式", "法线式")
FORM_TERMS = _FORM_TERMS  # 公开别名（投影器据此提取「本课所教形式名」）


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def normalize_form_terms(text: str, taught: set[str] | frozenset[str] | None) -> str:
    """方程形式术语守卫：文本中的「非本课所教形式名」→ 最接近的本课形式名。

    就近判定 = 编辑距离，其次**字面重合度**（实测：「两点式」距「点斜式」「斜截式」都是 2，
    但本课教点斜式——字面重合「点/式」2 字胜出），再次字典序（确定性）。
    只在 `taught`（本课真实教授的形式名集合，由蓝图概念/公式确定性提取）非空时生效；
    教错术语（点斜式/斜截式/两点式混用）是数学错误，必须在生成源头纠正。
    """
    if not text or not taught:
        return text
    out = str(text)
    for term in _FORM_TERMS:
        if term in taught or term not in out:
            continue
        nearest = min(
            sorted(taught),
            key=lambda t: (_edit_distance(term, t), -len(set(term) & set(t)), t),
        )
        out = out.replace(term, nearest)
    return out


# ── Deck 可见正文净化（R8-T：教研批注 / 模板前缀 / 工程黑话）──────────────────
# 背景（2026-09-23 真实导出 P0 实证）：deck 正文里混入三类非教学内容——
#   ① 教研批注当正文：「引导学生回忆椭圆的定义…」（教师动作，不是学生可见板书）；
#   ② 模板前缀：「本节结论：掌握…的方法」（投影内部标签泄漏）；
#   ③ 工程黑话：「（AI 生成变式）」「教材直连 · SourceLocator 溯源见备注」。
# 这里给出**确定性、幂等**的判定/净化原语，由 deck 投影（specs/projector）与增稠
# （ppt_v2/service/enrich）在**落库边界**共同调用，保证 DB 里的 spec 本身就是干净的。
#
# 关键边界：教研批注剔除**只作用于 deck 页面的正文/要点元素**——教案的「教师活动」列
# 本来就该写教师行为，绝不能误伤（故本模块只提供判定原语，应用位置由调用方决定）。

# 教师行为动词/短语：以这些开头或主导的整句是教研批注，不是学生可见板书内容。
_TEACHER_ACTION_OPENERS = (
    "引导学生", "帮助学生", "让学生", "请学生", "请同学", "要求学生", "启发学生",
    "组织学生", "组织同学", "教师引导", "教师通过", "教师先", "教师提出", "教师提问",
    "教师巡视", "教师点评", "教师小结", "教师板书", "教师组织", "布置教材", "布置本节课",
    "布置课后", "布置作业", "导入新课", "巡视", "追问",
    # R3e（产物审查 G-10）：小结/作业环节的**课堂活动描述**不是板书小结——
    # 「师生共同梳理本节课知识点」「说明作业要求，布置课后作业」不得进收尾页版面。
    "师生共同", "师生一起", "说明作业要求",
)
_TEACHER_ACTION_PHRASES = (
    "引导学生", "让学生", "请学生", "请同学", "要求学生", "启发学生", "组织学生",
    "教师通过", "教师引导", "教师提出", "教师提问", "布置教材", "布置作业", "布置课后",
)
# 句首条目编号（「1. 」「（1）」「- 」）——判定批注前先剥离，避免漏判。
_LEADING_NUMBER_RE = re.compile(r"^\s*(?:\d+\s*[.、)）]|[（(]\s*\d+\s*[）)]|[-•*])\s*")

# 模板前缀：「本节结论：」「本节小结：」「结论：」等投影内部标签，落版前剥掉（保留其后正文）。
_TEMPLATE_PREFIX_RE = re.compile(
    r"^\s*(?:本节结论|本节小结|本课小结|课堂小结|课时小结|结论|小结)\s*[：:]\s*"
)

# 工程黑话（裸词形态；括号形态如「（AI 生成变式）」由 strip_edit_markers 处理）。
_INTERNAL_TERM_RE = re.compile(r"SourceLocator|教材直连|溯源见备注|来源见备注")
# 剥词后残留的分隔符/空白（「教材直连 · SourceLocator 溯源见备注」→「·」）
_SEP_CLEAN_RE = re.compile(r"\s*[·•|]\s*")
_SEP_ONLY_RE = re.compile(r"^[\s·•\-—|/、,，。.]+$")


def is_teacher_annotation(text: str) -> bool:
    """整句是否为「教研批注 / 教师行为描述」（deck 正文不得出现）。

    高置信判定（宁保守不误伤，只作用于 deck 页面正文）：
    ① 剥句首条目编号后，以教师行为动词/短语开头；
    ② 不含数学记号（``$`` / ``\\``）且句中出现明确的教师行为短语
       （「引导学生」「布置作业」…）——实测形态「结合教材…，先引导学生…」走此路。
    """
    s = _LEADING_NUMBER_RE.sub("", str(text or "")).strip()
    if not s:
        return False
    if s.startswith(_TEACHER_ACTION_OPENERS):
        return True
    if "$" not in s and "\\" not in s and any(p in s for p in _TEACHER_ACTION_PHRASES):
        return True
    return False


def strip_template_prefix(text: str) -> str:
    """剥离「本节结论：」这类模板前缀（保留其后正文）。确定性、幂等。"""
    if not text:
        return text
    return _TEMPLATE_PREFIX_RE.sub("", text)


def strip_internal_terms(text: str) -> str:
    """剥除可见文本里的工程黑话（SourceLocator / 教材直连 / 溯源见备注）。

    剥词后若只剩分隔符/空白则返回 ``""``（整行都是黑话 → 丢弃）。确定性、幂等。
    """
    if not text:
        return text
    out = _INTERNAL_TERM_RE.sub("", text)
    out = _SEP_CLEAN_RE.sub(" ", out).strip()
    if _SEP_ONLY_RE.match(out):
        return ""
    return out


def has_internal_term(text: str) -> bool:
    """文本是否含内部术语/工程黑话/模板前缀（QA 判定用，不修改文本）。"""
    s = str(text or "")
    if not s:
        return False
    return bool(
        _INTERNAL_TERM_RE.search(s)
        or _TEMPLATE_PREFIX_RE.match(s)
        or _EDIT_MARKER_RE.search(s)
    )


# 孤立标点行（R8-F P1）：整行/整段仅由标点与空白组成 → 句子被剔除后残留的句末标点。
# 实测形态（R8-U slide4-rendered.png）：定义带正文下方悬着一个孤立的「。」——
# 「本节结论：。」这类模板前缀剥掉后只剩标点，或教研批注句被剔除后句末标点残留。
_ORPHAN_PUNCT_LINE_RE = re.compile(r"^[\s。；;，,、！!？?：:…·•\-—|/\.]*$")


def strip_orphan_punctuation_lines(text: str) -> str:
    """剔除仅由标点/空白组成的孤立行（句子被剔除后残留的句末标点）。

    确定性、幂等：逐行判定，整行只含标点与空白（含空行）→ 丢弃；否则原样保留。
    多行输入返回清理后的多行文本；空文本原样返回。
    """
    if not text:
        return text
    kept = [ln for ln in str(text).split("\n") if not _ORPHAN_PUNCT_LINE_RE.match(ln)]
    return "\n".join(kept)


# ── 裸命令词干补救（R8c）──────────────────────────────────────────────────
# 历史成因：`latex_to_unicode` 末尾的 `_BARE_CMD_RE` 兜底会剥掉未登记命令的反斜杠
# （"绝不把 \\cmd 裸源码留给产物"）。`\mathbb{R}` 此前未登记 → 被剥成裸字面量
# `mathbb{R}` 直接上屏（实测：deck 5b109adb 幂函数性质对比表）。该垃圾已落库到存量
# spec，而 `latex_to_unicode` 的数学证据门（`\`/`$`/`^{`）此时已不成立 → 导出期净化
# 不会再修。故在此做**幂等补救**：裸词干 → 正确数集符号（未知字母保留原字母）。
_MATHBB_SYMBOLS = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "P": "ℙ", "E": "𝔼"}
_BARE_MATHBB_RE = re.compile(r"\bmathbb\s*\{\s*([A-Za-z])\s*\}|\bmathbb\s+([A-Za-z])\b")


def restore_bare_mathbb(text: str) -> str:
    """裸字面量 `mathbb{R}` / `mathbb R` → `ℝ`（存量 spec 的幂等补救，不清洗真实内容）。"""
    if not text or "mathbb" not in text:
        return text

    def _sub(m: re.Match[str]) -> str:
        letter = (m.group(1) or m.group(2) or "").upper()
        return _MATHBB_SYMBOLS.get(letter, letter)

    return _BARE_MATHBB_RE.sub(_sub, text)


def sanitize_deck_text(text: str) -> str:
    """deck 可见正文统一净化：媒体语法 + 编辑标记 + 工程黑话 + 模板前缀 + 幽灵图引用
    + 数学源码规范化（`$…$`/`\\boxed`/`\\frac` → 干净 Unicode 数学式）+ 孤立标点 + 尾部悬空标点。

    R3e 产物红线：裸 LaTeX 源码不得进入任何产物文本——**生成源头**就产出干净文本
    （8201 的 LaTeX→Unicode 过渡排版只兜底部分命令，`\\boxed` 实测裸奔进 PPTX）。
    确定性、幂等；不清洗真实教学内容（只剥标记/前缀/残留标点）。清理后为空返回 ``""``。
    """
    out = strip_media_syntax(str(text or ""))
    out = strip_edit_markers(out)
    out = strip_internal_terms(out)
    out = strip_template_prefix(out)
    out = strip_ghost_figure_refs(out)
    out = latex_to_unicode(out)
    out = restore_bare_mathbb(out)
    out = strip_orphan_punctuation_lines(out)
    return _TRAILING_JUNK_RE.sub("", out).strip()


# ── 数学感知截断（`$…$` 定界符边界收口）────────────────────────────────────
# 朴素字符截断（text[:n]）会把截断点切进 `$…$` 内联数学内部，截断后留下**未闭合
# 定界符**：后半段文本被渲染器当作数学源码（或渲染失败）。消费方：证据投影
# （conversation/views._evidence_refs、conversation/handler._evidence_refs）与
# prose 回应（conversation/reply._clean_reply）。
# 确定性规则：
# 1. 结果长度 ≤ limit（只减不增——可安全通过 catalog max_length schema 闸门）；
# 2. 截断点落在 `$…$` 内部 → 回退到该未闭合 `$` 之前收口（优先句边界，否则紧贴 `$`）；
# 3. 截断后残留的落单 `$` 直接剥掉。

_SENT_BOUNDARY_RE = re.compile(r"[。！？；\n\r]")


def _strip_unbalanced_dollars(text: str) -> str:
    """剥掉未配对的 `$`（自左向右两两贪心配对，落单的最后一个剥除）。确定性、幂等。"""
    positions = [i for i, ch in enumerate(text) if ch == "$"]
    if len(positions) % 2 == 0:
        return text
    drop = positions[-1]
    return text[:drop] + text[drop + 1 :]


def truncate_math_aware(text: str, limit: int) -> str:
    """数学感知截断：截断点避开 `$…$` 定界符内部，在 `$` 成对 / 句边界收口。

    - 长度 ≤ limit 只减不增（不突破任何 max_length schema 闸门）；
    - 截断点切进 `$…$` 内部时回退收口（句边界优先，退化为未闭合 `$` 之前）；
    - 截断后落单的 `$` 直接剥掉（含原文本身未闭合的 `$`）；
    - 不超限原样返回；确定性、幂等。
    """
    s = str(text or "")
    if limit <= 0:
        return ""
    if len(s) <= limit:
        return s
    cut = s[:limit]
    dollars = [i for i, ch in enumerate(cut) if ch == "$"]
    end = limit
    if len(dollars) % 2 == 1:
        # 截断点切进未闭合的 `$…$`：在该 `$` 之前收口（句边界优先，保住语义完整性）。
        open_pos = dollars[-1]
        boundaries = [m.end() for m in _SENT_BOUNDARY_RE.finditer(s, 0, open_pos)]
        end = boundaries[-1] if boundaries else open_pos
    return _strip_unbalanced_dollars(s[:end])


__all__ = [
    "HTML_TAG_RE",
    "strip_html_tags",
    "strip_media_syntax",
    "strip_edit_markers",
    "strip_boxed",
    "sanitize_visible_text",
    "is_teacher_annotation",
    "strip_template_prefix",
    "strip_internal_terms",
    "strip_orphan_punctuation_lines",
    "strip_ghost_figure_refs",
    "normalize_form_terms",
    "FORM_TERMS",
    "has_internal_term",
    "sanitize_deck_text",
    "truncate_math_aware",
]
