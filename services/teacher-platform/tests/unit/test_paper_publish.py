# coding: utf-8
"""组卷 → 8000 班级作业桥（paper_publish）契约单测。"""
from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.core.errors import ApiError, ErrorCode
from app.domains.grading import paper_publish as pp
from app.teacher_prep_v2.adapters.dto import ApiMembershipRow
from app.teacher_prep_v2.contracts.errors import ContractMembershipDenied


def _question(**over):
    base = dict(
        id=uuid.uuid4(),
        q_type="choice",
        stem_latex="(5 分) 已知 $a_n=2n+1$，则 $a_3=$",
        options={"A": "5", "B": "7", "C": "9", "D": "11"},
        answer="B",
        analysis="代入 n=3。",
        kp_code="SEQ-01",
        difficulty="easy",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_map_choice_is_real_8000_quiz_item():
    item = pp._map_item(_question(), 1)
    assert item["q_type"] == "choice"
    assert item["options"] == {"A": "5", "B": "7", "C": "9", "D": "11"}
    assert item["question_text"].startswith("已知")
    assert item["max_score"] == 5.0
    assert item["answer"] == "B"


def test_map_fill_and_solve():
    fill = pp._map_item(_question(q_type="fill", options=None, answer="7"), 2)
    solve = pp._map_item(_question(q_type="solve", options=None, answer="证明略"), 3)
    assert fill["q_type"] == "blank"
    assert solve["q_type"] == "solution"
    assert solve["max_score"] == 12.0


def test_image_question_is_blocked_not_faked():
    with pytest.raises(ApiError) as exc:
        pp._map_item(_question(q_type="image"), 4)
    assert exc.value.code == ErrorCode.VALIDATION
    assert "图片题" in exc.value.message


def test_missing_answer_blocks_publish():
    with pytest.raises(ApiError) as exc:
        pp._map_item(_question(answer=""), 5)
    assert exc.value.code == ErrorCode.VALIDATION
    assert "缺少标准答案" in exc.value.message


class _FakeAdapter:
    def __init__(self, *, role="teacher", fail: Exception | None = None):
        self.role = role
        self.fail = fail
        self.created = None
        self.published = None

    async def require_class_membership(self, *, token, class_id):
        if self.fail is not None:
            raise self.fail
        return ApiMembershipRow(
            class_id=class_id, user_id=str(uuid.uuid4()), member_role=self.role, confirmed=True
        )

    async def create_assignment_inline(self, **kwargs):
        self.created = kwargs
        return "asg-8000-1"

    async def publish_assignment(self, **kwargs):
        self.published = kwargs
        return kwargs["assignment_id"]


def _body(question_ids):
    return pp.PaperPublishRequest(
        class_id=uuid.uuid4(),
        title="数列 · 课后作业",
        question_ids=question_ids,
        deadline=None,
        type="homework",
        client_request_id="req-12345678",
    )


def test_publish_creates_draft_then_publishes(monkeypatch):
    questions = [_question(), _question(q_type="fill", options=None, answer="7")]
    monkeypatch.setattr(pp, "_load_ordered", lambda session, ids: _async(questions))
    adapter = _FakeAdapter()

    result = asyncio.run(
        pp.publish_paper(None, token="t", body=_body([q.id for q in questions]), adapter=adapter)
    )

    assert result["assignment_id"] == "asg-8000-1"
    assert result["status"] == "published"
    assert result["question_count"] == 2
    assert adapter.created["quiz_items"][0]["q_type"] == "choice"
    assert adapter.created["quiz_items"][1]["q_type"] == "blank"
    assert adapter.created["assignment_type"] == "homework"
    # 只有显式 publish 才会让学生可见
    assert adapter.published["assignment_id"] == "asg-8000-1"
    assert adapter.published["idempotency_key"].startswith("publish:")


def test_student_role_cannot_publish(monkeypatch):
    questions = [_question()]
    monkeypatch.setattr(pp, "_load_ordered", lambda session, ids: _async(questions))
    adapter = _FakeAdapter(role="student")

    with pytest.raises(ApiError) as exc:
        asyncio.run(
            pp.publish_paper(None, token="t", body=_body([questions[0].id]), adapter=adapter)
        )
    assert exc.value.code == ErrorCode.FORBIDDEN
    assert adapter.created is None


def test_membership_denied_maps_to_403(monkeypatch):
    questions = [_question()]
    monkeypatch.setattr(pp, "_load_ordered", lambda session, ids: _async(questions))
    adapter = _FakeAdapter(fail=ContractMembershipDenied("not a member"))

    with pytest.raises(ApiError) as exc:
        asyncio.run(
            pp.publish_paper(None, token="t", body=_body([questions[0].id]), adapter=adapter)
        )
    assert exc.value.code in (ErrorCode.FORBIDDEN, ErrorCode.ROLE_DENIED)
    assert exc.value.status_code == 403


async def _async(value):
    return value
