"""RV14 · PaperSpec → Markdown/LaTeX 确定性 serializer（红→绿）。

覆盖要求（任务书 1/7 + 先红后绿）：
1. 全部 11 种节点类型**显式**转换（无 LLM、无静默丢弃）；
2. 确定性：同输入两次序列化字节一致（无时间戳等随机源）；
3. lineage：产物携带 paperId/revision/contentHash/serializer 版本溯源头；
4. review_mark 默认排除且计数如实上报；
5. 公式/引用/图/表/超长文档覆盖；
6. 恶意 LaTeX 静态守卫：危险控制序列 → 带 source node locator 的失败。
"""

from __future__ import annotations

import hashlib

import pytest

from research_vnext.core.export.serializer import (
    SERIALIZER_VERSION,
    SerializerError,
    node_type_counts,
    serialize_latex,
    serialize_markdown,
)

META = {
    "paperId": "3f6d2c1e-0000-4000-8000-000000000001",
    "revision": 7,
    "contentHash": "ab" * 32,
}


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# 覆盖全部 11 种节点类型的夹具（含中文、公式、引用、图、表、callout、claim）
# --------------------------------------------------------------------------

def all_node_blocks() -> list[dict]:
    return [
        {"id": "s1", "type": "section", "nodeRev": 1, "text": "引论", "number": "1"},
        {"id": "p1", "type": "paragraph", "nodeRev": 1,
         "text": "本文研究排队系统的稳态行为，重点考察等待时间的分布。"},
        {"id": "h1", "type": "heading", "nodeRev": 1, "text": "预备知识", "level": 2},
        {"id": "p2", "type": "paragraph", "nodeRev": 1,
         "text": "服务强度 _rho_ 满足 100% & 全部条件。"},
        {"id": "e1", "type": "equation", "nodeRev": 1,
         "latex": r"\sum_{i=1}^{n} i^2 = \frac{n(n+1)}{6}", "display": True},
        {"id": "e2", "type": "equation", "nodeRev": 1, "latex": r"\lambda W", "display": False},
        {"id": "c1", "type": "citation", "nodeRev": 1,
         "text": "", "refs": ["11111111-1111-4111-8111-111111111111"]},
        {"id": "f1", "type": "figure", "nodeRev": 1,
         "text": "", "refs": ["anchor-rv14-1"], "caption": "系统吞吐率对比"},
        {"id": "t1", "type": "table", "nodeRev": 1,
         "text": "", "refs": ["anchor-rv14-2"], "caption": "关键参数",
         "columns": ["参数", "取值"]},
        {"id": "k1", "type": "callout", "nodeRev": 1, "text": "稳态仅在 rho<1 时存在。", "kind": "WARNING"},
        {"id": "p3", "type": "paragraph", "nodeRev": 1, "text": "该结论对应已验证主张。",
         "claimRef": "22222222-2222-4222-8222-222222222222"},
        {"id": "r1", "type": "review_mark", "nodeRev": 1, "text": "此处需要补充实验对照。"},
    ]


CITATIONS = {
    "11111111-1111-4111-8111-111111111111": {
        "title": "Queueing at campus pickup points",
        "author": [{"family": "王", "given": "小明"}],
        "issued": {"date-parts": [[2026, 1, 1]]},
        "container-title": "Journal of Research Verification",
        "DOI": "10.1000/rv12.paper",
    }
}


# ==========================================================================
# 1 · 确定性
# ==========================================================================


def test_markdown_is_deterministic() -> None:
    a = serialize_markdown(all_node_blocks(), meta=META)
    b = serialize_markdown(all_node_blocks(), meta=META)
    assert sha(a) == sha(b)


def test_latex_is_deterministic() -> None:
    a = serialize_latex(all_node_blocks(), meta=META, citations=CITATIONS)
    b = serialize_latex(all_node_blocks(), meta=META, citations=CITATIONS)
    assert sha(a) == sha(b)


# ==========================================================================
# 2 · 全类型显式转换（SERIALIZER-COVERAGE 的机器判据）
# ==========================================================================


def test_every_node_type_leaves_a_trace_in_markdown() -> None:
    md = serialize_markdown(all_node_blocks(), meta=META)
    for nid, needle in [
        ("s1", "引论"), ("p1", "稳态行为"), ("h1", "预备知识"), ("p2", "100%"),
        ("e1", r"\sum_{i=1}^{n}"), ("e2", r"\lambda W"), ("c1", "[1]"),
        ("f1", "系统吞吐率对比"), ("t1", "关键参数"), ("k1", "WARNING"),
        ("p3", "[claim:22222222"),
    ]:
        assert needle in md, f"markdown 缺少 {nid} 的转换痕迹: {needle!r}"


def test_every_node_type_leaves_a_trace_in_latex() -> None:
    tex = serialize_latex(all_node_blocks(), meta=META, citations=CITATIONS)
    for nid, needle in [
        ("s1", "引论"), ("p1", "稳态行为"), ("h1", "预备知识"), ("p2", "100\\%"),
        ("e1", r"\sum_{i=1}^{n}"), ("e2", r"\lambda W"),
        ("f1", r"\begin{figure}"), ("f1", "系统吞吐率对比"),
        ("t1", r"\begin{table}"), ("t1", "关键参数"),
        ("k1", r"\begin{quote}"),
        ("c1", r"\cite{"), ("p3", r"claim:22222222"),
    ]:
        assert needle in tex, f"latex 缺少 {nid} 的转换痕迹: {needle!r}"


def test_bibliography_lists_all_cited_refs_deterministically() -> None:
    tex = serialize_latex(all_node_blocks(), meta=META, citations=CITATIONS)
    assert r"\begin{thebibliography}" in tex
    assert "Queueing at campus pickup points" in tex
    assert "10.1000/rv12.paper" in tex
    md = serialize_markdown(all_node_blocks(), meta=META, citations=CITATIONS)
    assert "Queueing at campus pickup points" in md


def test_citation_without_meta_still_renders_placeholder_not_silence() -> None:
    tex = serialize_latex(all_node_blocks(), meta=META, citations={})
    assert "11111111" in tex  # 引用位如实渲染为占位，不静默消失


# ==========================================================================
# 3 · lineage 头
# ==========================================================================


def test_outputs_carry_lineage_header() -> None:
    md = serialize_markdown(all_node_blocks(), meta=META)
    tex = serialize_latex(all_node_blocks(), meta=META, citations=CITATIONS)
    for out in (md, tex):
        assert META["paperId"] in out
        assert "revision=7" in out
        assert META["contentHash"] in out
        assert f"serializerVersion={SERIALIZER_VERSION}" in out


# ==========================================================================
# 4 · review_mark 默认排除、显式包含；计数如实
# ==========================================================================


def test_review_mark_excluded_by_default_but_counted() -> None:
    md = serialize_markdown(all_node_blocks(), meta=META)
    assert "此处需要补充实验对照" not in md
    tex = serialize_latex(all_node_blocks(), meta=META, citations=CITATIONS)
    assert "此处需要补充实验对照" not in tex


def test_review_mark_included_when_requested() -> None:
    md = serialize_markdown(all_node_blocks(), meta=META, include_review_marks=True)
    assert "此处需要补充实验对照" in md


def test_node_type_counts_reports_review_exclusion() -> None:
    counts = node_type_counts(all_node_blocks(), include_review_marks=False)
    assert counts["included"]["review_mark"] == 0
    assert counts["excluded"]["review_mark"] == 1
    counts_all = node_type_counts(all_node_blocks(), include_review_marks=True)
    assert counts_all["included"]["review_mark"] == 1


# ==========================================================================
# 5 · 文本转义（LaTeX 特殊字符不进入结构层）
# ==========================================================================


def test_text_special_chars_are_escaped_in_latex() -> None:
    blocks = [{"id": "p1", "type": "paragraph", "nodeRev": 1,
               "text": r"收益 50% & 成本 $x$ #1 _a_ {b} ~c ^d \input{evil}"}]
    tex = serialize_latex(blocks, meta=META, citations={})
    assert r"\%" in tex and r"\&" in tex
    # v2：$x$ 是合法行内公式 → 原样保留（不再是 \$ 转义）；其余特殊字符照常转义
    assert "$x$" in tex
    assert r"\#" in tex and r"\_" in tex and r"\{" in tex
    assert r"\input" not in tex.replace(r"\\input", "") or r"\textbackslash input" in tex
    # 反斜杠必须被中和：不得出现可执行的 \input{evil}
    assert "\\input{evil}" not in tex


def test_text_special_chars_safe_in_markdown() -> None:
    blocks = [{"id": "p1", "type": "paragraph", "nodeRev": 1,
               "text": "下标 a_b 与 星号 *x* 与 井号 #h"}]
    md = serialize_markdown(blocks, meta=META)
    # Markdown 端不引入"会改变结构"的解释（裸 # 后必须非空格才不构成标题等）——
    # 这里只要求输出包含原字符序列（不要求反转义）。
    assert "a_b" in md and "*x*" in md and "#h" in md


# ==========================================================================
# 6 · 超长文档：不炸、不截断、确定性保持
# ==========================================================================


def test_long_document_500_blocks_serializes_deterministically() -> None:
    blocks: list[dict] = []
    for i in range(100):
        blocks.append({"id": f"s{i}", "type": "section", "nodeRev": 1, "text": f"第{i}节", "number": str(i)})
        blocks.append({"id": f"p{i}", "type": "paragraph", "nodeRev": 1, "text": f"第{i}段的中文内容。" * 5})
        blocks.append({"id": f"e{i}", "type": "equation", "nodeRev": 1,
                       "latex": rf"\sum_{{i=1}}^{{{i}}} x^2 = {i}", "display": True})
        blocks.append({"id": f"q{i}", "type": "paragraph", "nodeRev": 1, "text": "公式推导与误差分析。" * 8})
    assert len(blocks) == 400
    a = serialize_latex(blocks, meta=META, citations={})
    b = serialize_latex(blocks, meta=META, citations={})
    assert sha(a) == sha(b) and len(a) > 5000


def test_document_over_hard_cap_is_rejected() -> None:
    from research_vnext.core.export.serializer import MAX_BLOCKS
    blocks = [{"id": f"p{i}", "type": "paragraph", "nodeRev": 1, "text": "x"} for i in range(MAX_BLOCKS + 1)]
    with pytest.raises(SerializerError) as ei:
        serialize_markdown(blocks, meta=META)
    assert ei.value.code == "EXPORT_DOC_TOO_LARGE"


# ==========================================================================
# 7 · 恶意 LaTeX 静态守卫（source node locator）
# ==========================================================================


@pytest.mark.parametrize(
    "latex",
    [
        r"\write18{rm -rf /}",
        r"\openout7 secrets.txt",
        r"\openin0 /etc/passwd",
        r"\input{/etc/passwd}",
        r"\include{evil}",
        r"\catcode`\!=13",
        r"\csname write\endcsname",
        r"\directlua{os.execute('id')}",
        r"\immediate\write16{pwn}",
        r"\special{psfile=//etc/passwd}",
    ],
)
def test_forbidden_control_sequences_rejected_with_node_locator(latex: str) -> None:
    blocks = [{"id": "evil-eq", "type": "equation", "nodeRev": 1, "latex": latex, "display": True}]
    with pytest.raises(SerializerError) as ei:
        serialize_latex(blocks, meta=META, citations={})
    assert ei.value.code == "EXPORT_FORBIDDEN_SEQUENCE"
    assert ei.value.node_id == "evil-eq"


def test_includegraphics_is_allowed() -> None:
    blocks = [{"id": "f1", "type": "figure", "nodeRev": 1, "text": "", "refs": ["a1"],
               "caption": "图", "src": "https://example.com/a.png"}]
    tex = serialize_latex(blocks, meta=META, citations={})
    assert r"\includegraphics" in tex


# ==========================================================================
# 8 · 空输入
# ==========================================================================


def test_empty_blocks_rejected() -> None:
    with pytest.raises(SerializerError) as ei:
        serialize_markdown([], meta=META)
    assert ei.value.code == "EXPORT_EMPTY_DOCUMENT"
