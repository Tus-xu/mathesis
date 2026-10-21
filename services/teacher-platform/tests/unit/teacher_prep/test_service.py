"""R14 领域服务验收测试：状态转换 / 409 / 幂等 / 跨日 / 跨班 / 成员变更 / 重启 / 事件 lineage。"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.core.errors import ErrorCode
from app.reliability.idempotency import IdempotencyConflict, request_hash_of
from app.teacher_prep.models import (
    BlueprintRevisionRow,
    SessionCommandRow,
    SessionEventRow,
    TeachingSessionRow,
)
from app.teacher_prep.service import (
    InvalidTransitionError,
    MembershipDeniedError,
    RevisionConflictError,
    SessionNotFoundError,
)

TEACHER = "11111111-1111-1111-1111-111111111111"
CLASS_A = "22222222-2222-2222-2222-222222222222"
CLASS_B = "33333333-3333-3333-3333-333333333333"
TOKEN = "jwt-teacher-token"


def idem(n: int) -> str:
    return f"r14-key-{n:04d}"


RH = lambda body: request_hash_of("POST", "/p", body)  # noqa: E731


# ── 创建 / 基础流程 ──────────────────────────────────────────────────────────

async def _create(db, service, adapter, *, class_id=CLASS_A, key=1, intent=None, title="备课"):
    return await service.create_session(
        db,
        adapter=adapter,
        token=TOKEN,
        actor_id=TEACHER,
        class_id=class_id,
        idempotency_key=idem(key),
        request_hash=RH({"class_id": class_id, "title": title, "intent": intent}),
        title=title,
        intent=intent,
    )


async def test_create_session_and_events_lineage(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    result = await _create(db, service, adapter)
    assert result["status"] == "draft" and result["revision"] == 1

    row = (
        await db.execute(
            select(TeachingSessionRow).where(TeachingSessionRow.id == uuid.UUID(result["session_id"]))
        )
    ).scalar_one()
    assert row.status == "draft" and row.last_event_seq == 2  # command.applied + session.created

    events = (
        (await db.execute(select(SessionEventRow).where(SessionEventRow.session_id == row.id).order_by(SessionEventRow.seq)))
        .scalars()
        .all()
    )
    assert [e.event_type for e in events] == ["command.applied", "session.created"]
    assert [e.seq for e in events] == [1, 2]
    # lineage 链：session.created 以 command.applied 为 parent
    assert events[1].parent_event_id == events[0].id
    assert events[0].parent_event_id is None

    cmds = (await db.execute(select(SessionCommandRow))).scalars().all()
    assert len(cmds) == 1 and cmds[0].result_status == "applied"
    assert cmds[0].idempotency_key == idem(1)


async def test_open_draft_activates(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    created = await _create(db, service, adapter)
    result = await service.open_session(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], expected_revision=1,
        idempotency_key=idem(2), request_hash=RH({"session_id": created["session_id"]}),
    )
    assert result["status"] == "active" and result["resumed"] is False


async def test_cross_day_resume_keeps_active(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    created = await _create(db, service, adapter)
    await service.open_session(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], expected_revision=1,
        idempotency_key=idem(2), request_hash=RH({"s": 1}),
    )
    # 次日/后续续接：状态保持 active，产生 resumed 事件
    detail = await service.get_session_detail(
        db, adapter=adapter, token=TOKEN, teacher_id=TEACHER, session_id=created["session_id"]
    )
    resume = await service.open_session(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], expected_revision=detail["revision"],
        idempotency_key=idem(3), request_hash=RH({"s": 2}),
    )
    assert resume["status"] == "active" and resume["resumed"] is True
    evs = (await db.execute(select(SessionEventRow.event_type))).scalars().all()
    assert "session.resumed" in evs


# ── 非法状态转换 ─────────────────────────────────────────────────────────────

async def test_archive_then_cancel_illegal(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    created = await _create(db, service, adapter)
    await service.archive_session(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], expected_revision=1,
        idempotency_key=idem(2), request_hash=RH({"a": 1}),
    )
    with pytest.raises(InvalidTransitionError) as ei:
        await service.cancel_session(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
            session_id=created["session_id"], expected_revision=2,
            idempotency_key=idem(3), request_hash=RH({"c": 1}),
        )
    assert ei.value.code == ErrorCode.CONFLICT  # 409


async def test_open_unknown_session_404(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    with pytest.raises(SessionNotFoundError):
        await service.open_session(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
            session_id="99999999-9999-9999-9999-999999999999", expected_revision=1,
            idempotency_key=idem(9), request_hash=RH({"x": 1}),
        )


# ── expected_revision 并发 ───────────────────────────────────────────────────

async def test_open_stale_revision_conflict(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    created = await _create(db, service, adapter)
    await service.open_session(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], expected_revision=1,
        idempotency_key=idem(2), request_hash=RH({"o": 1}),
    )
    with pytest.raises(RevisionConflictError) as ei:
        await service.open_session(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
            session_id=created["session_id"], expected_revision=1,  # 陈旧
            idempotency_key=idem(3), request_hash=RH({"o": 2}),
        )
    assert ei.value.code == ErrorCode.CONFLICT  # 409


async def test_confirm_stale_revision_conflict(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    created = await _create(db, service, adapter)
    prop = await service.propose_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], idempotency_key=idem(4),
        request_hash=RH({"bp": 1}), body={"topic": "函数单调性"},
    )
    await service.propose_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], idempotency_key=idem(5),
        request_hash=RH({"bp": 2}), body={"topic": "函数单调性 v2"},
        blueprint_id=prop["blueprint_id"], expected_revision=1,
    )
    with pytest.raises(RevisionConflictError):
        await service.confirm_blueprint(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            blueprint_id=prop["blueprint_id"], expected_revision=1,  # 陈旧（已是 2）
            idempotency_key=idem(6), request_hash=RH({"cf": 1}),
        )


# ── 幂等 ─────────────────────────────────────────────────────────────────────

async def test_idempotent_replay_no_duplicate(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    payload = {"class_id": CLASS_A, "title": "备课", "intent": None}
    first = await _create(db, service, adapter, key=1, intent=None)
    second = await service.create_session(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER, class_id=CLASS_A,
        idempotency_key=idem(1), request_hash=RH(payload), title="备课",
    )
    assert second["session_id"] == first["session_id"]  # 同 key 同载荷 → 重放首响应
    count = (await db.execute(select(func.count()).select_from(TeachingSessionRow))).scalar_one()
    assert count == 1  # 无重复副作用


async def test_same_key_different_payload_409(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    await _create(db, service, adapter, key=1, intent={"v": 1})
    with pytest.raises(IdempotencyConflict) as ei:
        await service.create_session(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER, class_id=CLASS_A,
            idempotency_key=idem(1), request_hash=RH({"class_id": CLASS_A, "title": "different"}),
            title="different",
        )
    assert ei.value.status_code == 409


# ── 蓝图 proposal → confirm / reject → 修订 ─────────────────────────────────

async def test_proposal_confirm_reject_repropose(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    created = await _create(db, service, adapter)
    prop = await service.propose_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], idempotency_key=idem(4),
        request_hash=RH({"bp": 1}), body={"topic": "A"}, title="v1",
    )
    assert prop["status"] == "proposal" and prop["revision"] == 1

    rejected = await service.reject_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        blueprint_id=prop["blueprint_id"], expected_revision=1,
        idempotency_key=idem(5), request_hash=RH({"rj": 1}), reason="超纲",
    )
    assert rejected["status"] == "rejected" and rejected["revision"] == 2

    # 驳回后可重新提案（revision 3）
    reproposed = await service.propose_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], idempotency_key=idem(6),
        request_hash=RH({"bp": 2}), body={"topic": "A2"}, title="v2",
        blueprint_id=prop["blueprint_id"], expected_revision=2,
    )
    assert reproposed["status"] == "proposal" and reproposed["revision"] == 3

    confirmed = await service.confirm_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        blueprint_id=prop["blueprint_id"], expected_revision=3,
        idempotency_key=idem(7), request_hash=RH({"cf": 2}),
    )
    assert confirmed["status"] == "confirmed" and confirmed["revision"] == 4

    revs = (
        (await db.execute(select(BlueprintRevisionRow).where(BlueprintRevisionRow.blueprint_id == uuid.UUID(prop["blueprint_id"])).order_by(BlueprintRevisionRow.revision_no)))
        .scalars()
        .all()
    )
    assert [r.revision_no for r in revs] == [1, 2, 3, 4]
    assert [r.verdict for r in revs] == ["proposal", "rejected", "proposal", "confirmed"]
    assert [r.parent_revision_no for r in revs] == [None, 1, 2, 3]  # lineage 连续
    assert [r.source for r in revs] == ["ai_suggestion", "teacher", "ai_suggestion", "teacher"]


async def test_confirm_directly_after_proposal_illegal(db, service, adapter, members) -> None:
    """已确认后不可直接再驳回；必须经 proposal 修订。"""
    members.set(CLASS_A, user_id=TEACHER)
    created = await _create(db, service, adapter)
    prop = await service.propose_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], idempotency_key=idem(4),
        request_hash=RH({"bp": 1}), body={"topic": "A"},
    )
    await service.confirm_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        blueprint_id=prop["blueprint_id"], expected_revision=1,
        idempotency_key=idem(5), request_hash=RH({"cf": 1}),
    )
    with pytest.raises(InvalidTransitionError):
        await service.reject_blueprint(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            blueprint_id=prop["blueprint_id"], expected_revision=2,
            idempotency_key=idem(6), request_hash=RH({"rj": 1}),
        )


async def test_llm_cannot_confirm(db, service, adapter, members) -> None:
    """LLM 只能产生 proposal；确认必须由教学角色（actor_role_hint=system → 403）。"""
    members.set(CLASS_A, user_id=TEACHER)
    created = await _create(db, service, adapter)
    prop = await service.propose_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], idempotency_key=idem(4),
        request_hash=RH({"bp": 1}), body={"topic": "A"}, source="ai_suggestion",
    )
    with pytest.raises(MembershipDeniedError) as ei:
        await service.confirm_blueprint(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="system",
            blueprint_id=prop["blueprint_id"], expected_revision=1,
            idempotency_key=idem(5), request_hash=RH({"cf": 1}),
        )
    assert ei.value.code == ErrorCode.ROLE_DENIED  # 403


async def test_membership_role_changed_to_student_blocks_confirm(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    created = await _create(db, service, adapter)
    prop = await service.propose_blueprint(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], idempotency_key=idem(4),
        request_hash=RH({"bp": 1}), body={"topic": "A"},
    )
    # 成员关系变化：同一教师从 teacher 变 student
    members.set(CLASS_A, user_id=TEACHER, role="student")
    with pytest.raises(MembershipDeniedError) as ei:
        await service.confirm_blueprint(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            blueprint_id=prop["blueprint_id"], expected_revision=1,
            idempotency_key=idem(5), request_hash=RH({"cf": 1}),
        )
    assert ei.value.code == ErrorCode.ROLE_DENIED


# ── 跨班 / 成员关系 ─────────────────────────────────────────────────────────

async def test_cross_class_403(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    created = await _create(db, service, adapter)
    # 会话属于 A；把 A 成员剔除（成员变更）→ 后续命令 403
    members.remove(CLASS_A)
    with pytest.raises(MembershipDeniedError) as ei:
        await service.open_session(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
            session_id=created["session_id"], expected_revision=1,
            idempotency_key=idem(2), request_hash=RH({"o": 1}),
        )
    assert ei.value.code == ErrorCode.ROLE_DENIED  # 40301


async def test_list_only_authorized_classes(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    members.set(CLASS_B, user_id=TEACHER)
    s1 = await _create(db, service, adapter, class_id=CLASS_A, key=1)
    s2 = await _create(db, service, adapter, class_id=CLASS_B, key=2)
    # 成员变更：B 班被移除 → 列表只返回 A
    members.remove(CLASS_B)
    items = await service.list_authorized_sessions(db, adapter=adapter, token=TOKEN, teacher_id=TEACHER)
    ids = {i["session_id"] for i in items}
    assert s1["session_id"] in ids and s2["session_id"] not in ids


async def test_authority_unreachable_503(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    members.set_5xx(CLASS_A)
    from app.teacher_prep.service import AuthorityUnreachableError

    with pytest.raises(AuthorityUnreachableError) as ei:
        await _create(db, service, adapter)
    assert ei.value.code == ErrorCode.DEPENDENCY_UNAVAILABLE  # 503 fail closed


# ── 服务重启恢复 ─────────────────────────────────────────────────────────────

async def test_restart_recovery(file_db, service, adapter, members) -> None:
    """服务重启：进程 1 创建并激活 → 进程 2 续接（resumed），事件与 revision 完整。"""
    members.set(CLASS_A, user_id=TEACHER)
    factory1 = file_db()
    async with factory1() as s1:
        created = await service.create_session(
            s1, adapter=adapter, token=TOKEN, actor_id=TEACHER, class_id=CLASS_A,
            idempotency_key=idem(1), request_hash=RH({"c": 1}), title="备课",
        )
        await service.open_session(
            s1, adapter=adapter, token=TOKEN, actor_id=TEACHER,
            session_id=created["session_id"], expected_revision=1,
            idempotency_key=idem(2), request_hash=RH({"o": 1}),
        )

    # 「重启」：全新 session_factory 连同一文件库
    factory2 = file_db()
    async with factory2() as s2:
        detail = await service.get_session_detail(
            s2, adapter=adapter, token=TOKEN, teacher_id=TEACHER,
            session_id=created["session_id"],
        )
        assert detail["status"] == "active" and detail["revision"] == 2
        resume = await service.open_session(
            s2, adapter=adapter, token=TOKEN, actor_id=TEACHER,
            session_id=created["session_id"], expected_revision=detail["revision"],
            idempotency_key=idem(3), request_hash=RH({"o": 2}),
        )
        assert resume["status"] == "active" and resume["resumed"] is True
        evs = await service.list_events(
            s2, adapter=adapter, token=TOKEN, teacher_id=TEACHER,
            session_id=created["session_id"],
        )
        assert [e["event_type"] for e in evs["items"]] == [
            "command.applied", "session.created",
            "command.applied", "session.opened",
            "command.applied", "session.resumed",
        ]


# ── 失败审计 ─────────────────────────────────────────────────────────────────

async def test_failed_command_audited(db, service, adapter, members) -> None:
    members.set(CLASS_A, user_id=TEACHER)
    created = await _create(db, service, adapter)
    await service.open_session(
        db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
        session_id=created["session_id"], expected_revision=1,
        idempotency_key=idem(2), request_hash=RH({"o": 1}),
    )
    # 陈旧 revision → 409；失败 attempt 必须留审计行
    with pytest.raises(RevisionConflictError):
        await service.open_session(
            db, adapter=adapter, token=TOKEN, actor_id=TEACHER,
            session_id=created["session_id"], expected_revision=1,
            idempotency_key=idem(3), request_hash=RH({"o": 2}),
        )
    cmds = (
        (await db.execute(select(SessionCommandRow).where(SessionCommandRow.idempotency_key == idem(3))))
        .scalars()
        .all()
    )
    assert len(cmds) == 1 and cmds[0].result_status in ("conflict", "invalid_transition")
