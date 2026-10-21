"""RV13 · PaperSpec typed-node 契约测试（先红后绿）。

钉住任务书第 1/2 条：
- PaperSpec 是 canonical structured document，节点是**版本化 typed nodes**；
- 输入 HTML 严格 sanitize、URL scheme allowlist，禁止 script/style/iframe/event handler；
- 存储时不以 editor DOM / raw HTML / LaTeX 为事实源。
"""

from __future__ import annotations

import json

import pytest

from research_vnext.core.paper.errors import PaperSchemaError
from research_vnext.core.paper.nodes import (
    PAPER_NODE_TYPES,
    SERVER_OWNED_NODE_FIELDS,
    normalize_node,
    strip_server_owned,
    validate_blocks,
)
from research_vnext.core.paper.sanitize import (
    URL_ALLOWED_SCHEMES,
    sanitize_rich_html,
    sanitize_text,
    sanitize_url,
)


# --------------------------------------------------------------------------
# 1 · 封闭类型集：论文九类节点 + claim/evidence/review 引用位
# --------------------------------------------------------------------------


def test_node_type_taxonomy_is_closed_and_covers_task_types() -> None:
    for t in (
        "section", "heading", "paragraph", "equation", "citation",
        "figure", "table", "callout",
    ):
        assert t in PAPER_NODE_TYPES, t
    # RV12 论文引用门依赖的类型不得移除
    assert {"claim_ref", "evidence_ref", "review_mark"} <= set(PAPER_NODE_TYPES)


def test_unknown_node_type_is_rejected() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        normalize_node({"id": "x", "type": "raw_html", "text": "<b>hi</b>"})
    assert ei.value.code == "PAPER_NODE_TYPE_UNKNOWN"


# --------------------------------------------------------------------------
# 2 · 版本化：nodeRev 必须存在且单调
# --------------------------------------------------------------------------


def test_node_carries_node_revision_and_normalization_fills_default() -> None:
    node = normalize_node({"id": "p1", "type": "paragraph", "text": "正文"})
    assert node["nodeRev"] == 1
    node2 = normalize_node({"id": "p1", "type": "paragraph", "text": "正文", "nodeRev": 3})
    assert node2["nodeRev"] == 3


def test_non_positive_node_rev_is_rejected() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        normalize_node({"id": "p1", "type": "paragraph", "text": "x", "nodeRev": 0})
    assert ei.value.code == "PAPER_NODE_REV_INVALID"


# --------------------------------------------------------------------------
# 3 · 每类节点的必填字段与规范化
# --------------------------------------------------------------------------


def test_heading_requires_level_in_range() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        normalize_node({"id": "h1", "type": "heading", "text": "方法", "level": 9})
    assert ei.value.code == "PAPER_HEADING_LEVEL_INVALID"
    node = normalize_node({"id": "h1", "type": "heading", "text": "方法", "level": 2})
    assert node["level"] == 2


def test_equation_carries_latex_not_an_image() -> None:
    node = normalize_node(
        {"id": "e1", "type": "equation", "latex": "y = Wx + b", "display": True}
    )
    assert node["latex"] == "y = Wx + b"
    assert node["display"] is True
    assert "image" not in json.dumps(node).lower()
    # 只有 text 的公式（AI 常见输出）规范化为 latex
    node2 = normalize_node({"id": "e2", "type": "equation", "text": "y = x^2"})
    assert node2["latex"] == "y = x^2"


def test_equation_without_any_formula_is_rejected() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        normalize_node({"id": "e1", "type": "equation"})
    assert ei.value.code == "PAPER_EQUATION_EMPTY"


def test_citation_node_requires_refs() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        normalize_node({"id": "c1", "type": "citation"})
    assert ei.value.code == "PAPER_REFS_REQUIRED"


def test_figure_node_requires_refs_and_allows_locator() -> None:
    node = normalize_node(
        {
            "id": "f1", "type": "figure", "refs": ["anchor-1"],
            "locator": {"page": 4, "bbox": [72, 120, 480, 300]},
            "caption": "图 1：损失曲线",
        }
    )
    assert node["locator"]["page"] == 4


def test_table_node_requires_refs() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        normalize_node({"id": "t1", "type": "table", "caption": "表 1"})
    assert ei.value.code == "PAPER_REFS_REQUIRED"


def test_callout_requires_known_kind() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        normalize_node({"id": "co1", "type": "callout", "text": "缺口", "kind": "JOKE"})
    assert ei.value.code == "PAPER_CALLOUT_KIND_UNKNOWN"
    node = normalize_node({"id": "co1", "type": "callout", "text": "缺口", "kind": "GAP"})
    assert node["kind"] == "GAP"


# --------------------------------------------------------------------------
# 4 · HTML / script / URL 越权（存储端 sanitize）
# --------------------------------------------------------------------------


def test_text_fields_strip_script_and_event_handlers() -> None:
    dirty = '<script>alert(1)</script>稳态队长<script src="http://evil/x.js"></script>'
    node = normalize_node({"id": "p1", "type": "paragraph", "text": dirty})
    assert "<script" not in node["text"]
    assert "alert(1)" not in node["text"]
    assert "稳态队长" in node["text"]
    assert "onerror" not in node["text"]


def test_caption_with_img_onerror_is_neutralized() -> None:
    dirty = '<img src=x onerror="alert(1)">图 1'
    node = normalize_node({"id": "f1", "type": "figure", "refs": ["a1"], "caption": dirty})
    assert "<img" not in node["caption"]
    assert "onerror" not in node["caption"]
    assert "图 1" in node["caption"]


def test_rich_html_allowlist_strips_iframe_style_and_handlers() -> None:
    dirty = (
        '<p>正文</p><iframe src="http://evil"></iframe>'
        '<style>body{}</style><a href="https://ok.example/a" onclick="x()">链</a>'
        '<div onclick="y()">块</div>'
    )
    clean, removed = sanitize_rich_html(dirty)
    assert "<iframe" not in clean
    assert "<style" not in clean
    assert "onclick" not in clean
    assert '<a href="https://ok.example/a"' in clean
    assert any("iframe" in r for r in removed)


def test_url_scheme_allowlist_rejects_javascript_and_data() -> None:
    assert URL_ALLOWED_SCHEMES == ("http", "https", "mailto")
    for bad in ("javascript:alert(1)", "data:text/html;base64,x", "vbscript:x", "file:///etc/passwd"):
        with pytest.raises(PaperSchemaError) as ei:
            sanitize_url(bad)
        assert ei.value.code == "PAPER_URL_SCHEME_FORBIDDEN"
    assert sanitize_url("https://doi.org/10.1000/x") == "https://doi.org/10.1000/x"


def test_figure_src_with_bad_scheme_is_rejected() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        normalize_node(
            {"id": "f1", "type": "figure", "refs": ["a1"], "src": "javascript:alert(1)"}
        )
    assert ei.value.code == "PAPER_URL_SCHEME_FORBIDDEN"


def test_sanitize_text_keeps_chinese_and_symbols() -> None:
    out = sanitize_text("稳态队长期望 W_q ≤ 3，置信度 ≥ 95%")
    assert out == "稳态队长期望 W_q ≤ 3，置信度 ≥ 95%"


# --------------------------------------------------------------------------
# 5 · server-owned 字段：AI 不得自带 claimSnapshot
# --------------------------------------------------------------------------


def test_claim_snapshot_is_server_owned() -> None:
    assert "claimSnapshot" in SERVER_OWNED_NODE_FIELDS
    with pytest.raises(PaperSchemaError) as ei:
        strip_server_owned({"id": "p1", "type": "paragraph", "text": "x", "claimSnapshot": {}})
    assert ei.value.code == "PAPER_CLAIM_SNAPSHOT_SERVER_OWNED"


# --------------------------------------------------------------------------
# 6 · canonical round-trip：normalize→serialize→normalize 恒等
# --------------------------------------------------------------------------


def _full_spec() -> list[dict]:
    return [
        {"id": "s1", "type": "section", "text": "方法"},
        {"id": "h1", "type": "heading", "text": "模型设定", "level": 3, "nodeRev": 2},
        {"id": "p1", "type": "paragraph", "text": "考虑稳态队列 W_q ≤ 3 的场景。"},
        {"id": "e1", "type": "equation", "latex": r"W_q = \frac{\lambda}{\mu - \lambda}", "display": True},
        {"id": "p2", "type": "paragraph", "text": "行内公式 $x^2$ 与中文混排。"},
        {"id": "e2", "type": "equation", "latex": r"x_i = \alpha_i + \beta", "display": False},
        {"id": "c1", "type": "citation", "refs": ["0b5e6a9c-0000-4000-8000-000000000001"]},
        {"id": "f1", "type": "figure", "refs": ["anchor-1"], "locator": {"page": 4}, "caption": "图 1"},
        {"id": "t1", "type": "table", "refs": ["anchor-2"], "caption": "表 1：参数"},
        {"id": "co1", "type": "callout", "text": "缺独立性验证", "kind": "GAP"},
    ]


def test_paper_spec_round_trip_is_identity() -> None:
    spec = validate_blocks(_full_spec())
    again = validate_blocks(json.loads(json.dumps(spec)))
    assert again == spec, "canonical 序列化往返必须恒等"


def test_duplicate_node_ids_are_rejected() -> None:
    blocks = _full_spec() + [{"id": "p1", "type": "paragraph", "text": "dup"}]
    with pytest.raises(PaperSchemaError) as ei:
        validate_blocks(blocks)
    assert ei.value.code == "PAPER_NODE_ID_DUPLICATE"


def test_empty_paper_is_rejected() -> None:
    with pytest.raises(PaperSchemaError) as ei:
        validate_blocks([])
    assert ei.value.code == "PAPER_NO_BLOCKS"
