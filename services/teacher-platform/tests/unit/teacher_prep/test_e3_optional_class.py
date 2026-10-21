"""E-3 回归：未绑定班级的教师可创建并运行备课会话（班级域能力显式降级/守卫）。"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.teacher_prep.conversation.intent import selection_complete
from app.teacher_prep.conversation.router import CreateConversationRequest
from app.teacher_prep.context.models import ContextSelection


def test_create_request_accepts_missing_class_id():
    req = CreateConversationRequest(idempotency_key="i" * 32, message="备一节圆锥曲线的课")
    assert req.class_id is None


def test_create_request_rejects_malformed_class_id():
    with pytest.raises(ValidationError):
        CreateConversationRequest(idempotency_key="i" * 32, message="x", class_id="not-a-uuid")


def test_selection_complete_without_class():
    assert selection_complete({"source_id": str(uuid.uuid4()), "section_id": str(uuid.uuid4())})
    assert not selection_complete({"source_id": str(uuid.uuid4())})


def test_context_selection_allows_none_class():
    sel = ContextSelection(source_id=str(uuid.uuid4()), section_id=str(uuid.uuid4()))
    assert sel.class_id is None
    with pytest.raises(ValidationError):
        ContextSelection(source_id="bad", section_id=str(uuid.uuid4()))


@pytest.mark.asyncio
async def test_verify_membership_skips_8000_when_class_absent():
    from app.teacher_prep.conversation.orchestrator import ConversationService

    calls: list[Any] = []

    class _Adapter:
        async def require_class_membership(self, *, token: str, class_id: str) -> Any:
            calls.append(class_id)
            return SimpleNamespace(member_role="owner")

    role = await ConversationService._verify_membership(_Adapter(), token="t", class_id=None)
    assert role == "teacher"
    assert calls == []  # 未绑定班级不得外呼 8000（也无从裁决）


def test_require_class_id_guard_for_class_scoped_actions():
    from app.teacher_prep.conversation.orchestrator import InvalidActionError, _require_class_id

    no_class = SimpleNamespace(class_id=None)
    with pytest.raises(InvalidActionError):
        _require_class_id("publish_diagnostic", no_class)
    bound = SimpleNamespace(class_id=uuid.uuid4())
    assert _require_class_id("publish_diagnostic", bound) == str(bound.class_id)


def test_class_ref_helper_never_fabricates_none_string():
    from app.teacher_prep.conversation.orchestrator import _class_ref

    assert _class_ref(SimpleNamespace(class_id=None)) is None
    cid = uuid.uuid4()
    assert _class_ref(SimpleNamespace(class_id=cid)) == str(cid)
