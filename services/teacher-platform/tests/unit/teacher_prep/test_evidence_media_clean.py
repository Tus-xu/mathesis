"""T2.5 教材证据引文清洗：markdown 图片/链接语法包装剥离（确定性，禁 LLM）。

场景（主控截图取证）：教材 chunk 原文 `![图 3-1-2](images/xxx.jpg)`（含导入损坏态
`![] (images/... ...jpg)`）原样进入证据卡 snippet —— 教师可见垃圾串。
规则：剥语法包装、保留 alt/链接文字与图题、剥离裸 images/ 残片、合并多余空白；
数学 $...$ 与普通文本原样保留。
"""

from __future__ import annotations

import pytest

from app.teacher_prep.conversation import catalog
from app.teacher_prep.conversation.views import _evidence_refs as workspace_evidence_refs
from app.teacher_prep.text_safety import strip_media_syntax


# ── 清洗函数本身 ─────────────────────────────────────────────────────────────


def test_strip_markdown_image_keeps_alt_caption() -> None:
    """标准图片语法：保留 alt/图题文字。"""
    assert strip_media_syntax("![图 3-1-2](images/d13fb6b8.jpg)") == "图 3-1-2"


def test_strip_damaged_image_with_spaces() -> None:
    """损坏态（`] (url)` 带空格、url 内空白）：空 alt 整体删除，图题保留。"""
    dirty = "![] (images/d13fb6b8f48bef164c6913c08474596da5fef d35021387ee265854ebdea0189.jpg) 图3-1-2"
    assert strip_media_syntax(dirty) == "图3-1-2"


def test_strip_named_damaged_image() -> None:
    """损坏态但有 alt：保留 alt。"""
    assert strip_media_syntax("![图 1-2] (images/a b.jpg)") == "图 1-2"


def test_strip_empty_alt_image() -> None:
    """空 alt 图片整体删除，接缝空白合并。"""
    out = strip_media_syntax("前文 ![](images/x.jpg) 后文")
    assert out == "前文 后文"
    assert "images/" not in out and "![" not in out


def test_strip_markdown_link_keeps_text() -> None:
    assert strip_media_syntax("参见[椭圆的定义](https://tb/x)") == "参见椭圆的定义"


def test_strip_bare_image_path_fragment() -> None:
    assert strip_media_syntax("如图 (images/d13fb6b8.jpg) 所示") == "如图 所示"


def test_preserve_latex_and_plain_text() -> None:
    """数学与普通文本逐字节保留（反幻觉边界：只剥语法包装，不动内容）。"""
    text = "椭圆标准方程 $\\\\frac{x^2}{a^2}+\\\\frac{y^2}{b^2}=1$，焦点在 x 轴上"
    assert strip_media_syntax(text) == text
    assert strip_media_syntax("已知集合 A={1,2,3}") == "已知集合 A={1,2,3}"


def test_multi_image_mixed_language() -> None:
    dirty = "如图 ![fig-3](images/a.jpg) 与 ![图 3-2](images/b c.jpg) 所示，[结论](images/c.jpg)"
    out = strip_media_syntax(dirty)
    assert out == "如图 fig-3 与 图 3-2 所示，结论"


def test_empty_and_none_safe() -> None:
    assert strip_media_syntax("") == ""
    assert strip_media_syntax("   ") == ""


# ── 接入点 ①：views._evidence_refs（工作区 evidence rail 读时投影）────────────


class _SnapStub:
    """最小快照桩（_evidence_refs 只读 .snapshot dict）。"""

    def __init__(self, snapshot: dict) -> None:
        self.snapshot = snapshot


def test_workspace_evidence_refs_clean_dirty_fact() -> None:
    snap = _SnapStub(
        {
            "facts": [
                {
                    "kind": "definition",
                    "text": "![] (images/d13fb6b8f48bef164c6913c08474596da5fef d35021387ee265854ebdea0189.jpg) 图3-1-2 椭圆的定义",
                    "source_ref": "tb://v1/x/sections/s1#chunk-01",
                }
            ]
        }
    )
    refs = workspace_evidence_refs(snap, None)
    assert len(refs) == 1
    assert "images/" not in refs[0]["snippet"]
    assert "![" not in refs[0]["snippet"]
    assert "图3-1-2" in refs[0]["snippet"]
    assert "images/" not in refs[0]["title"]


# ── 接入点 ②：catalog.validate_component（evidence_rail 读写共用闸门）─────────


def test_catalog_evidence_rail_cleans_legacy_dirty_snippet() -> None:
    """存量脏消息读时（与写时同闸门）确定性清洗。"""
    props = {
        "refs": [
            {
                "ref": "tb://v1/x/sections/s1#chunk-02",
                "kind": "concepts",
                "title": "![图 3-1](images/a.jpg) 椭圆",
                "snippet": "![] (images/d13f d350.jpg) 图3-1-2 椭圆的定义：平面内到两定点距离之和为定值",
            }
        ]
    }
    out = catalog.validate_component("evidence_rail", props)
    assert out is not None
    ref = out["refs"][0]
    assert "images/" not in ref["snippet"] and "![" not in ref["snippet"]
    assert "图3-1-2" in ref["snippet"]
    assert ref["title"] == "图 3-1 椭圆"


def test_catalog_other_components_untouched() -> None:
    """清洗只作用于 evidence_rail，其余组件 props 原样规范化。"""
    props = {"text": "已知 $x^2=a^2$（普通数学文本）"}
    out = catalog.validate_component("assistant_text", props)
    assert out is not None and out["text"] == props["text"]


def test_catalog_evidence_rail_schema_still_enforced() -> None:
    """清洗不得放松契约：extra=forbid / 上限照常拒绝（统一包装为 ComponentSpecInvalid）。"""
    with pytest.raises(catalog.ComponentSpecInvalid):
        catalog.validate_component(
            "evidence_rail",
            {"refs": [{"ref": "r://x", "kind": "k", "title": "t", "snippet": "s", "junk": 1}]},
        )
    with pytest.raises(catalog.ComponentSpecInvalid, match="120"):
        catalog.validate_component(
            "evidence_rail",
            {"refs": [{"ref": f"r://{i}", "kind": "k", "title": "t"} for i in range(121)]},
        )
