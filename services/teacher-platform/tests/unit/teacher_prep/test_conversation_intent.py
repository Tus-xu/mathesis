"""R17 意图解析测试：deltas 语义、schema 校验、apply、失败标签（basic mode 触发）。"""

from __future__ import annotations

import json

import pytest

from app.teacher_prep.conversation.intent import (
    IntentParseFailure,
    IntentValidationError,
    apply_intent,
    build_intent_prompt,
    parse_and_validate_intent,
    parse_intent,
    selection_complete,
)
from app.teacher_prep.conversation.schema import PrepIntent, intent_snapshot_hash

SRC = "50000000-0000-0000-0000-0000000000a1"
LESSON1 = "50000000-0000-0000-0000-0000000000a3"
CLASS = "22222222-2222-2222-2222-222222222222"


def _intent_payload(**over):
    base = {
        "intent_type": "modify",
        "textbook_id": SRC,
        "section_id": LESSON1,
        "class_id": CLASS,
        "knowledge_points": ["集合", "元素"],
        "lesson_scope": "第一课时",
        "lesson_hours": 1,
        "objectives": [{"text": "理解集合概念", "source_ref": None}],
        "note": None,
        "confidence": 0.9,
        "warnings": [],
    }
    base.update(over)
    return base


def test_parse_valid():
    p = parse_and_validate_intent(json.dumps(_intent_payload()))
    assert p.intent.section_id == LESSON1
    assert p.snapshot_hash == intent_snapshot_hash(p.intent)


def test_parse_fence_tolerant():
    p = parse_and_validate_intent("```json\n" + json.dumps(_intent_payload()) + "\n```")
    assert p.intent.class_id == CLASS


def test_parse_rejects_html():
    payload = _intent_payload()
    payload["knowledge_points"] = ["<script>alert(1)</script>集合"]
    with pytest.raises(IntentValidationError):
        parse_and_validate_intent(json.dumps(payload))


def test_parse_drops_bad_uuid_instead_of_rejecting():
    """2026-09-23 契约放宽（救援通道实测）：id 非 UUID 且无法在服务端选项里唯一匹配时，
    **丢弃该字段**（deltas 语义 = 保持现状 → 编排层走选择卡），不再整单拒绝。

    安全面不变：绝不猜/编造 id；能匹配到唯一真实选项时才改写为 id（见下一个用例）。
    """
    p = parse_and_validate_intent(json.dumps(_intent_payload(section_id="not-a-uuid")))
    assert p.intent.section_id is None  # 丢弃 → 保持现状


def test_parse_coerces_title_to_option_id():
    """模型回填章节**标题**时，用服务端已下发的真实选项做确定性映射（唯一命中才接受）。"""
    options = [{"id": LESSON1, "label": "3.1.2 椭圆的简单几何性质"}]
    p = parse_and_validate_intent(
        json.dumps(_intent_payload(section_id="3.1.2 椭圆的简单几何性质")),
        available_sections=options,
    )
    assert p.intent.section_id == LESSON1


def test_parse_drops_extra_fields_instead_of_rejecting():
    """2026-09-23 契约放宽：模型多吐的解释字段（reason/confidence/…）**丢弃**而非整单拒绝；
    与 node_ops.parse_node_revision 同法（只保留 schema 已知键）。真危险内容（HTML）照旧拒绝。"""
    payload = _intent_payload()
    payload["evil_component"] = "x"
    p = parse_and_validate_intent(json.dumps(payload))
    assert p.intent.class_id == CLASS  # 已知字段照常生效


def test_parse_normalizes_minutes_to_lesson_hours():
    """2026-09-23 实测：模型把「45 分钟」填成 lesson_hours=45 → 按 45 分钟/课时折算。"""
    payload = _intent_payload()
    payload["lesson_hours"] = 45
    p = parse_and_validate_intent(json.dumps(payload))
    assert p.intent.lesson_hours == 1
    payload["lesson_hours"] = 90
    assert parse_and_validate_intent(json.dumps(payload)).intent.lesson_hours == 2
    payload["lesson_hours"] = 3  # 正常值原样通过
    assert parse_and_validate_intent(json.dumps(payload)).intent.lesson_hours == 3


def test_parse_non_json():
    with pytest.raises(IntentValidationError):
        parse_and_validate_intent("抱歉，我无法解析")


async def test_parse_intent_provider_failure_labeled():
    def boom(prompt: str) -> str:
        raise RuntimeError("connection refused")

    out = await parse_intent(boom, user_message="给3班备函数单调性")
    assert isinstance(out, IntentParseFailure)
    assert out.error_label == "provider_unavailable"


async def test_parse_intent_timeout_labeled():
    def boom(prompt: str) -> str:
        raise TimeoutError()

    out = await parse_intent(boom, user_message="给3班备函数单调性")
    assert out.error_label == "provider_timeout"


async def test_parse_intent_invalid_labeled():
    def bad(prompt: str) -> str:
        return "not json"

    out = await parse_intent(bad, user_message="给3班备函数单调性")
    assert isinstance(out, IntentParseFailure)
    assert out.error_label == "intent_invalid"


def test_apply_intent_delta_semantics():
    current = {
        "source_id": SRC,
        "section_id": LESSON1,
        "class_id": CLASS,
        "knowledge_points": ["旧知识点"],
        "lesson_scope": "第一课时",
        "lesson_hours": 1,
        "objectives": [{"text": "旧目标"}],
    }
    intent = PrepIntent(intent_type="modify", knowledge_points=["新知识点"], class_id=CLASS)
    out = apply_intent(current, intent)
    assert out["knowledge_points"] == ["新知识点"]  # list 提供 → 整体替换
    assert out["section_id"] == LESSON1  # 未提供 → 保持
    assert out["lesson_scope"] == "第一课时"  # 未提供 → 保持
    assert out["class_id"] == CLASS  # 相同 class 保持


def test_apply_intent_on_empty():
    intent = PrepIntent(
        intent_type="new_prep",
        textbook_id=SRC,
        section_id=LESSON1,
        class_id=CLASS,
        knowledge_points=["集合"],
        lesson_scope="第一课时",
        objectives=[{"text": "理解概念", "source_ref": None}],
    )
    out = apply_intent(None, intent)
    assert out["source_id"] == SRC and out["section_id"] == LESSON1
    assert out["objectives"] == [{"text": "理解概念", "source_ref": None}]
    assert selection_complete(out) is True


def test_selection_incomplete():
    assert selection_complete({"source_id": SRC, "section_id": None, "class_id": CLASS}) is False
    assert selection_complete({}) is False


def test_build_intent_prompt_bounded_and_no_secrets():
    prompt = build_intent_prompt(user_message="给高一3班备集合", current={"source_id": SRC})
    assert "教师消息" in prompt
    assert "api" not in prompt.lower()
    assert "key" not in prompt.lower()
    assert "token" not in prompt.lower()
