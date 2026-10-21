"""T22.3-C · session 投影必须附 class_name（classes.name），禁止 UUID 冒充班级名。"""
from __future__ import annotations

import uuid

from app.db.models.platform import Classes
from app.teacher_prep.service import load_class_names, session_dto

from .conftest import CLASS_A, TEACHER, TOKEN


def test_session_dto_includes_class_name_key() -> None:
    class _Row:
        id = uuid.uuid4()
        teacher_id = uuid.UUID(TEACHER)
        class_id = uuid.UUID(CLASS_A)
        course_id = None
        schedule_id = None
        status = "draft"
        revision = 1
        title = "备课"
        intent = None
        context_snapshot_id = None
        prompt_release_id = None
        ai_run_id = None
        pending_action_id = None
        opened_at = None
        created_at = None
        updated_at = None
        last_event_seq = 0

    dto = session_dto(_Row())
    assert "class_name" in dto
    assert dto["class_name"] is None  # 未附名时诚实 null，不伪造
    named = session_dto(_Row(), class_name="高二(3)班")
    assert named["class_name"] == "高二(3)班"
    empty = session_dto(_Row(), class_name="")
    assert empty["class_name"] is None


async def test_load_class_names_batch(db) -> None:
    a = uuid.UUID(CLASS_A)
    b = uuid.UUID("33333333-3333-3333-3333-333333333333")
    db.add(Classes(id=a, name="高二(3)班"))
    db.add(Classes(id=b, name="高二(5)班"))
    await db.commit()

    names = await load_class_names(db, {a, b})
    assert names == {CLASS_A: "高二(3)班", str(b): "高二(5)班"}

    # 未入库的 class_id 不进结果（诚实缺失）
    missing = uuid.UUID("44444444-4444-4444-4444-444444444444")
    assert await load_class_names(db, {missing}) == {}
    assert await load_class_names(db, set()) == {}


async def test_list_sessions_attaches_class_name(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    db.add(Classes(id=uuid.UUID(CLASS_A), name="高二(3)班"))
    await db.commit()

    created = await service.create_session(
        db,
        adapter=adapter,
        token=TOKEN,
        actor_id=TEACHER,
        class_id=CLASS_A,
        idempotency_key="cname-key-1",
        request_hash="h1",
        title="双曲线",
    )
    items = await service.list_authorized_sessions(
        db, adapter=adapter, token=TOKEN, teacher_id=TEACHER
    )
    hit = next(i for i in items if i["session_id"] == created["session_id"])
    assert hit["class_name"] == "高二(3)班"
    assert hit["class_id"] == CLASS_A
