"""RV13 · 节点级 diff 测试（先红后绿）。

钉住任务书第 7 条：expected_revision 冲突必须返回 **node-level diff**，
前端才能显示"哪个节点被谁改了什么"，而不是一行 409 码。
"""

from __future__ import annotations

from research_vnext.core.paper.diff import diff_nodes
from research_vnext.core.paper.nodes import validate_blocks


def _spec_a() -> list[dict]:
    return validate_blocks(
        [
            {"id": "s1", "type": "section", "text": "结果"},
            {"id": "p1", "type": "paragraph", "text": "原句", "nodeRev": 1},
            {"id": "e1", "type": "equation", "latex": "y = x", "nodeRev": 1},
        ]
    )


def test_changed_node_reports_field_level_before_after() -> None:
    b = [dict(n) for n in _spec_a()]
    b[1]["text"] = "改后的句子"
    b[1]["nodeRev"] = 2
    d = diff_nodes(_spec_a(), b)
    assert d["count"] == 1
    ch = d["changed"][0]
    assert ch["id"] == "p1" and ch["type"] == "paragraph"
    fields = {f["field"]: f for f in ch["fields"]}
    assert fields["text"]["before"] == "原句"
    assert fields["text"]["after"] == "改后的句子"
    assert ch["nodeRev"] == {"before": 1, "after": 2}


def test_added_and_removed_are_keyed_by_node_id() -> None:
    b = [n for n in _spec_a() if n["id"] != "e1"]
    b.append({"id": "p-new", "type": "paragraph", "text": "新段", "nodeRev": 1})
    d = diff_nodes(_spec_a(), validate_blocks(b))
    assert [x["id"] for x in d["removed"]] == ["e1"]
    assert [x["id"] for x in d["added"]] == ["p-new"]


def test_move_is_reported_separately_from_change() -> None:
    a = _spec_a()
    b = a[2:] + a[:2]  # 旋转：[e1, s1, p1]，三个节点全部换位
    d = diff_nodes(a, b)
    assert d["moved"], "纯位置变化不得报成内容 changed"
    moved_ids = {m["id"] for m in d["moved"]}
    assert moved_ids == {"s1", "p1", "e1"}
    assert d["count"] == len(d["moved"]) + len(d["added"]) + len(d["removed"]) + len(d["changed"])


def test_identical_specs_yield_empty_diff() -> None:
    d = diff_nodes(_spec_a(), _spec_a())
    assert d["count"] == 0 and d["empty"] is True


def test_diff_is_deterministic() -> None:
    b = [dict(n) for n in _spec_a()]
    b[1]["text"] = "v2"
    b[1]["nodeRev"] = 2
    b[2]["latex"] = "y = z"
    b[2]["nodeRev"] = 2
    d1 = diff_nodes(_spec_a(), b)
    d2 = diff_nodes(_spec_a(), b)
    assert d1 == d2


def test_long_text_diff_is_previewed_not_truncated_silently() -> None:
    b = [dict(n) for n in _spec_a()]
    b[1]["text"] = "长" * 500
    d = diff_nodes(_spec_a(), b)
    before = d["changed"][0]["fields"][0]["before"]
    assert len(before) <= 220, "长文本必须截断预览并显式标注"
