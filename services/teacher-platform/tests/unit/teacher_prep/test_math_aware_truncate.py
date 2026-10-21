"""数学感知截断：字符截断不得产生未闭合 `$…$` 定界符。

缺陷回归（2026-10-04）：朴素截断 `text[:n]` 会把截断点切进内联数学内部，截断后留下
未闭合 `$`（后半段文本被渲染器当作数学源码）。三处消费点一起钉死：证据投影
（views._evidence_refs / handler._evidence_refs）与 prose 回应（reply._clean_reply）。
"""

from __future__ import annotations

import uuid

from app.teacher_prep.conversation import views
from app.teacher_prep.conversation.handler import _evidence_refs as handler_evidence_refs
from app.teacher_prep.conversation.models import ContextSnapshotRow
from app.teacher_prep.conversation.reply import _clean_reply
from app.teacher_prep.models import TeachingSessionRow
from app.teacher_prep.text_safety import truncate_math_aware

from .conftest import TEACHER


# ── helper 单元行为 ─────────────────────────────────────────────────────────────


def test_truncate_math_aware_within_limit_returns_unchanged():
    s = "a" * 100
    assert truncate_math_aware(s, 200) == s


def test_truncate_math_aware_plain_text_same_as_naive():
    # 无 $ 干扰时与朴素截断一致（长度 ≤ limit）
    s = "a" * 300
    out = truncate_math_aware(s, 160)
    assert out == "a" * 160


def test_truncate_math_aware_closed_pair_inside_cut_preserved():
    # 闭合对完整落在截断窗口内 → 原样保留（数学不被剥除）
    s = "a" * 100 + "$x=1$" + "b" * 200
    out = truncate_math_aware(s, 160)
    assert out == "a" * 100 + "$x=1$" + "b" * 55
    assert out.count("$") == 2


def test_truncate_math_aware_open_dollar_stripped():
    # 未闭合 $ + 截断 → 截断后不残留落单 $
    s = "a" * 100 + "$" + "c" * 200
    out = truncate_math_aware(s, 160)
    assert "$" not in out
    assert len(out) <= 160


def test_truncate_math_aware_pair_spanning_cut_stripped():
    # 原文闭合对跨截断点（闭合 $ 被切掉）→ 重新收口剥掉 open $
    s = "a" * 100 + "$x" + "c" * 80 + "$" + "b" * 50
    out = truncate_math_aware(s, 160)
    assert out.count("$") % 2 == 0
    assert len(out) <= 160


def test_truncate_math_aware_sentence_boundary_preferred():
    # 未闭合 $ 之前有句边界 → 在句边界收口（完整句子保留，$ 剥除）
    s = "先讲定义。" + "b" * 290 + "$公式" + "c" * 50
    out = truncate_math_aware(s, 300)
    assert "$" not in out
    assert out.startswith("先讲定义。")
    assert len(out) <= 300


def test_truncate_math_aware_idempotent():
    s = "a" * 100 + "$" + "c" * 200
    once = truncate_math_aware(s, 160)
    assert truncate_math_aware(once, 160) == once


def test_truncate_math_aware_edges():
    assert truncate_math_aware("", 10) == ""
    assert truncate_math_aware("abc", 0) == ""
    assert truncate_math_aware("abc", -1) == ""
    assert truncate_math_aware(None, 10) == ""  # type: ignore[arg-type]


# ── 投影接线：证据 rail（views / handler 同口径）──────────────────────────────


async def _snapshot_with_fact(db, text: str) -> ContextSnapshotRow:
    srow = TeachingSessionRow(teacher_id=uuid.UUID(TEACHER), status="active")
    db.add(srow)
    await db.flush()
    snap = ContextSnapshotRow(
        session_id=srow.id,
        snapshot={"facts": [{"source_ref": "loc://t1#p1", "kind": "fact", "text": text}]},
        snapshot_hash="0" * 64,
    )
    db.add(snap)
    await db.commit()
    return snap


async def test_views_evidence_refs_no_unbalanced_dollar(db):
    # 截断点（160）落在 $…$ 内部 → 截断后不再产生未闭合 $
    text = "a" * 100 + "$x=1" + "，" + "b" * 200
    snap = await _snapshot_with_fact(db, text)
    refs = views._evidence_refs(snap, None)
    assert len(refs) == 1
    r = refs[0]
    assert len(text) > 160  # 确认确实触发了截断
    assert r["snippet"] != text
    assert len(r["snippet"]) <= 160
    assert len(r["title"]) <= 60
    assert r["snippet"].count("$") % 2 == 0
    assert r["title"].count("$") % 2 == 0


async def test_views_evidence_refs_closed_pair_preserved(db):
    # 闭合对完整在截断窗口内 → 数学定界符原样保留
    text = "a" * 50 + "$x=1$" + "b" * 300
    snap = await _snapshot_with_fact(db, text)
    refs = views._evidence_refs(snap, None)
    assert refs[0]["snippet"].count("$") == 2
    assert len(refs[0]["snippet"]) <= 160


async def test_handler_evidence_refs_no_unbalanced_dollar(db):
    # handler 口径（title 200 / snippet 400 = catalog max_length 上限）：同样不产生未闭合 $
    text = "a" * 300 + "$x=1" + "，" + "b" * 200
    snap = await _snapshot_with_fact(db, text)
    refs = handler_evidence_refs(snap, {})
    assert len(refs) == 1
    r = refs[0]
    assert len(text) > 400
    assert len(r["snippet"]) <= 400
    assert len(r["title"]) <= 200
    assert r["snippet"].count("$") % 2 == 0
    assert r["title"].count("$") % 2 == 0


# ── 投影接线：prose 回应（reply._clean_reply）─────────────────────────────────


def test_clean_reply_no_unbalanced_dollar():
    # 300 字截断点落在未闭合 $ 之后 → 截断后无落单 $
    text = "先看这个式子$" + "接着分区间讨论单调性，" * 30
    out = _clean_reply(text)
    assert len(text) > 300
    assert len(out) <= 300
    assert out.count("$") % 2 == 0


def test_clean_reply_plain_text_unchanged():
    assert _clean_reply("好的，我们来准备1.1集合的备课。") == "好的，我们来准备1.1集合的备课。"
