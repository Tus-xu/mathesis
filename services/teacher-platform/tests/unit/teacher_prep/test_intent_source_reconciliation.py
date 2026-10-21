"""意图选定册次的确定性一致性校正测试（内容正确性兜底）。

背景：LLM 只能凭「版本·册次」标签猜册次，实测「高二椭圆」被解析到选择性必修第二册
（数列/导数），而椭圆在选择性必修第一册。覆盖：
- A：教材选项章节能给 LLM「这本书讲什么」（topics），且不破坏既有 id/label；
- B：LLM 选定后的一致性校正——错册→命中册并清空 section_id；正确/0 命中/多人候选/无知识点
  一律保持原选择；关键词匹配归一化有效。
"""

from __future__ import annotations

import uuid

from app.teacher_prep.conversation.intent import build_intent_prompt
from app.teacher_prep.conversation.options import (
    apply_source_correction,
    choose_corrected_source,
    list_textbook_options,
    normalize_text,
    reconcile_textbook_selection,
)
from app.textbook_v2.models import TextbookSection, TextbookSource

from .tp_context_fixtures import SRC, seed_synthetic_textbook

BOOK1 = "a1807bec-7996-4628-9780-9896dc2edb0b"  # 选择性必修第一册（含椭圆）
BOOK2 = "e8c85e43-d575-4587-8046-c4a8b6fe79ec"  # 选择性必修第二册（数列/导数）

# 真实册次的章节话题（title 形态）
TEXTS = {
    BOOK1: ["第3章 圆锥曲线的方程", "3.1 椭圆", "3.1.1 椭圆及其标准方程", "3.1.2 椭圆的简单几何性质"],
    BOOK2: ["第4章 数列", "4.1 数列的概念", "第5章 一元函数的导数及其应用"],
}


# ── B：纯函数校正规则 ────────────────────────────────────────────────────────


def test_normalize_text_handles_fullwidth_case_and_space():
    assert normalize_text("  ＳＩＮ 　sin ") == "sinsin"  # 全角→半角 + 去空白 + 大小写折叠
    assert normalize_text("３ . １ 椭圆") == "3.1椭圆"
    assert normalize_text("") == ""


def test_wrong_volume_corrected_to_matching_and_section_cleared():
    target = choose_corrected_source(TEXTS, source_id=BOOK2, knowledge_points=["椭圆"])
    assert target == BOOK1
    fixed = apply_source_correction(
        {"source_id": BOOK2, "section_id": "sec-from-book2", "knowledge_points": ["椭圆"]},
        {"from": BOOK2, "to": target, "matched": ["椭圆"]},
    )
    assert fixed["source_id"] == BOOK1
    assert fixed["section_id"] is None  # 旧册章节不可跨书沿用
    assert fixed["knowledge_points"] == ["椭圆"]  # 其它字段不动


def test_correct_selection_unchanged():
    assert choose_corrected_source(TEXTS, source_id=BOOK1, knowledge_points=["椭圆"]) is None


def test_zero_hit_keeps_llm_choice():
    assert choose_corrected_source(TEXTS, source_id=BOOK2, knowledge_points=["立体几何"]) is None


def test_two_candidate_hits_keep_llm_choice():
    texts = {BOOK2: TEXTS[BOOK2], "b1": ["3.1.1 椭圆及其标准方程"], "b2": ["2.2 椭圆"]}
    assert choose_corrected_source(texts, source_id=BOOK2, knowledge_points=["椭圆"]) is None


def test_empty_or_missing_inputs_keep_llm_choice():
    assert choose_corrected_source(TEXTS, source_id=BOOK2, knowledge_points=[]) is None
    assert choose_corrected_source(TEXTS, source_id=BOOK2, knowledge_points=None) is None
    assert choose_corrected_source(TEXTS, source_id=None, knowledge_points=["椭圆"]) is None


def test_keyword_normalization_matches_fullwidth_and_spaces():
    # 教师输入含全角空格/内部空白/全角数字，仍应命中「3.1.1 椭圆及其标准方程」
    assert choose_corrected_source(TEXTS, source_id=BOOK2, knowledge_points=["　椭 圆 "]) == BOOK1
    assert choose_corrected_source(TEXTS, source_id=BOOK2, knowledge_points=["３．１．１"]) == BOOK1


# ── A：prompt 给 LLM 真实话题信号 ────────────────────────────────────────────


def test_build_intent_prompt_grounds_textbook_on_topics():
    prompt = build_intent_prompt(
        user_message="准备高二椭圆第一课时",
        current=None,
        available_sources=[
            {"id": BOOK1, "label": "人教A版 · 选择性必修第一册 · 高二 · 必修", "topics": "3.1.1 椭圆及其标准方程"}
        ],
    )
    assert "topics" in prompt
    assert "3.1.1 椭圆及其标准方程" in prompt
    assert "宁可选 null" in prompt


# ── A/B：DB 路径 ─────────────────────────────────────────────────────────────


async def test_list_textbook_options_topics_without_breaking_fields(db):
    await seed_synthetic_textbook(db)
    options = await list_textbook_options(db, include_topics=True)
    assert len(options) == 1
    opt = options[0]
    assert set(opt) == {"id", "label", "topics"}  # 既有字段原样保留
    assert opt["id"] == str(SRC)
    assert opt["label"] == "合成出版社 · 2019 · 高一 · 必修第一册"
    # 详情层 = level>1 中行数最多者（此处 level=2）：节标题而非「未分节」碎片
    assert "1.1 合成集合的概念" in opt["topics"]
    assert "1.2 合成集合间的关系" in opt["topics"]
    # 默认（选择卡契约）仍只有 id/label
    plain = await list_textbook_options(db)
    assert all(set(o) == {"id", "label"} for o in plain)


async def test_reconcile_db_corrects_wrong_source(db):
    await seed_synthetic_textbook(db)  # SRC：合成集合
    alt = uuid.UUID("70000000-0000-0000-0000-0000000000b1")
    db.add(
        TextbookSource(
            id=alt, school_id=None, publisher="人教A版", edition="2019", grade="高二",
            volume="选择性必修第二册", subject="数学",
            source_repo="https://example.invalid/synth.git", source_commit="0" * 40,
            source_path="synth/alt.jsonl", file_sha256="e" * 64, rights_status="STAGED",
            rights_evidence_ref="队伍确认授权", parser_version="slice_md-202607",
            source_kind="direct-chunk", batch_id="synth-batch-01", manifest_sha256="m" * 64,
        )
    )
    db.add(
        TextbookSection(
            id=uuid.uuid4(), textbook_id=alt, parent_id=None, level=2, order=1,
            title="4.1 数列的概念", normalized_title="数列的概念",
            page_start=1, page_end=1, section_path="4.1",
        )
    )
    await db.flush()

    correction = await reconcile_textbook_selection(
        db, source_id=str(alt), knowledge_points=["集合"]
    )
    assert correction == {"from": str(alt), "to": str(SRC), "matched": ["集合"]}
    fixed = apply_source_correction({"source_id": str(alt), "section_id": "sec-old"}, correction)
    assert fixed["source_id"] == str(SRC)
    assert fixed["section_id"] is None
    # 无知识点 / 已命中 → 不校正
    assert await reconcile_textbook_selection(db, source_id=str(alt), knowledge_points=[]) is None
    assert await reconcile_textbook_selection(db, source_id=str(SRC), knowledge_points=["集合"]) is None