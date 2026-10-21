"""M3 教师端：teacher role + class_scope 越权（§8 / §19.1）。"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.gateway.jwt import create_token_with_role
from app.main import app
from app.models.database import async_session_factory
from app.models.role_binding import RoleBinding
from tests._m3_helpers import add_member, make_class, make_user, token


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


@pytest.mark.asyncio
async def test_teacher_owner_accesses_today(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        await db.commit()
    resp = await client.get("/api/teacher/today", headers=_auth(token(tid, "teacher")))
    assert resp.status_code == 200
    assert resp.json()["code"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("teacher_verified", [None, False])
async def test_teacher_router_rejects_missing_or_unverified_live_binding(
    client, teacher_verified
):
    """移除/取消审核有效绑定后，仍持 teacher JWT 也不得读取业务数据。

    统一认证后由网关 get_current_user 实时校验 approved 绑定（40300），
    先于教师 scope 的 40301 二次防线；两层均失败关闭。
    """
    async with async_session_factory() as db:
        tid = await make_user(db, teacher_verified=teacher_verified)
        await db.commit()

    response = await client.get("/api/teacher/today", headers=_auth(token(tid, "teacher")))

    assert response.status_code == 403
    assert response.json()["code"] == 40300


@pytest.mark.asyncio
async def test_teacher_router_rejects_soft_deleted_live_binding(client):
    """已软删除的审核绑定不构成教师准入。"""
    async with async_session_factory() as db:
        tid = await make_user(db)
        binding = (
            await db.execute(
                select(RoleBinding).where(
                    RoleBinding.user_id == tid, RoleBinding.role == "teacher"
                )
            )
        ).scalar_one()
        binding.deleted_at = datetime.now(UTC)
        await db.commit()

    response = await client.get("/api/teacher/today", headers=_auth(token(tid, "teacher")))

    assert response.status_code == 403
    assert response.json()["code"] == 40300


@pytest.mark.asyncio
async def test_teacher_router_rechecks_binding_after_token_issuance(client):
    """签发 token 后撤销教师绑定，下一次请求必须在路由边界被拒绝。"""
    async with async_session_factory() as db:
        tid = await make_user(db)
        await db.commit()
    issued_token = token(tid, "teacher")

    async with async_session_factory() as db:
        binding = (
            await db.execute(
                select(RoleBinding).where(
                    RoleBinding.user_id == tid, RoleBinding.role == "teacher"
                )
            )
        ).scalar_one()
        binding.deleted_at = datetime.now(UTC)
        await db.commit()

    response = await client.get("/api/teacher/today", headers=_auth(issued_token))

    assert response.status_code == 403
    assert response.json()["code"] == 40300


@pytest.mark.asyncio
async def test_student_cannot_access_teacher_endpoint(client):
    async with async_session_factory() as db:
        sid = await make_user(db)
        await db.commit()
    resp = await client.get("/api/teacher/today", headers=_auth(token(sid, "student")))
    assert resp.status_code == 403
    assert resp.json()["code"] == 40301  # role_denied


@pytest.mark.asyncio
async def test_roles_claim_cannot_bypass_active_role(client):
    """审计 C-01：active_role=student 且 roles 含 teacher 的令牌必须拒绝。"""
    async with async_session_factory() as db:
        uid = await make_user(db)
        await db.commit()
    # 手工铸造 active_role=student、roles=[student, teacher] 的令牌
    tok = create_token_with_role(str(uid), "student", roles=["student", "teacher"])
    resp = await client.get("/api/teacher/today", headers=_auth(tok))
    assert resp.status_code == 403
    assert resp.json()["code"] == 40301


@pytest.mark.asyncio
async def test_cross_teacher_grading_detail_denied(client):
    """审计 C-03：教师 B 不能读取教师 A 班级的提交项详情。"""
    from app.models.coursework import Assignment, Submission, SubmissionItem

    async with async_session_factory() as db:
        owner = await make_user(db)
        other = await make_user(db)
        cid = await make_class(db, owner)
        a = Assignment(class_id=cid, creator_id=owner, title="作业", type="quiz", status="published")
        db.add(a)
        await db.flush()
        s = Submission(user_id=owner, assignment_id=a.id, client_submit_id="sx")
        db.add(s)
        await db.flush()
        it = SubmissionItem(submission_id=s.id, item_no=1, q_type="text", verdict="pending_review")
        db.add(it)
        await db.commit()
        item_id = it.id
    resp = await client.get(
        f"/api/teacher/grading/{item_id}", headers=_auth(token(other, "teacher"))
    )
    assert resp.status_code in (403, 404)
    assert resp.json()["code"] in (40302, 40400)


@pytest.mark.asyncio
async def test_other_teacher_cannot_read_class(client):
    async with async_session_factory() as db:
        owner = await make_user(db)
        other = await make_user(db)
        cid = await make_class(db, owner)
        await db.commit()
    resp = await client.get(
        f"/api/teacher/classes/{cid}/insights", headers=_auth(token(other, "teacher"))
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == 40302  # class_scope_denied


@pytest.mark.asyncio
async def test_confirmed_member_teacher_can_access(client):
    async with async_session_factory() as db:
        owner = await make_user(db)
        other = await make_user(db)
        cid = await make_class(db, owner)
        await add_member(db, cid, other, member_role="teacher", confirmed=True)
        await db.commit()
    resp = await client.get(
        f"/api/teacher/classes/{cid}/insights", headers=_auth(token(other, "teacher"))
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_unconfirmed_member_denied(client):
    async with async_session_factory() as db:
        owner = await make_user(db)
        other = await make_user(db)
        cid = await make_class(db, owner)
        await add_member(db, cid, other, member_role="teacher", confirmed=False)
        await db.commit()
    resp = await client.get(
        f"/api/teacher/classes/{cid}/insights", headers=_auth(token(other, "teacher"))
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == 40302


@pytest.mark.asyncio
async def test_teacher_cannot_list_another_class_assignments(client):
    """显式外班 class_id 不能绕过教师的作业列表范围。"""
    from app.models.coursework import Assignment

    async with async_session_factory() as db:
        owner = await make_user(db)
        other = await make_user(db)
        foreign_class = await make_class(db, owner)
        db.add(
            Assignment(
                class_id=foreign_class,
                creator_id=owner,
                title="外班作业",
                type="quiz",
                status="published",
            )
        )
        await db.commit()

    response = await client.get(
        f"/api/teacher/assignments?class_id={foreign_class}",
        headers=_auth(token(other, "teacher")),
    )

    assert response.status_code == 403
    assert response.json()["code"] == 40302


@pytest.mark.asyncio
async def test_teacher_cannot_list_another_class_grading_queue(client):
    """显式外班 class_id 不能泄露已进入队列的提交项。"""
    from app.models.coursework import Assignment, Submission, SubmissionItem

    async with async_session_factory() as db:
        owner = await make_user(db)
        other = await make_user(db)
        foreign_class = await make_class(db, owner)
        assignment = Assignment(
            class_id=foreign_class,
            creator_id=owner,
            title="外班待批改作业",
            type="quiz",
            status="published",
        )
        db.add(assignment)
        await db.flush()
        submission = Submission(
            user_id=owner,
            assignment_id=assignment.id,
            client_submit_id="foreign-scope-submission",
        )
        db.add(submission)
        await db.flush()
        db.add(
            SubmissionItem(
                submission_id=submission.id,
                item_no=1,
                q_type="text",
                verdict="pending_review",
            )
        )
        await db.commit()

    response = await client.get(
        f"/api/teacher/grading/queue?class_id={foreign_class}",
        headers=_auth(token(other, "teacher")),
    )

    assert response.status_code == 403
    assert response.json()["code"] == 40302


@pytest.mark.asyncio
async def test_omitted_assignments_exclude_soft_deleted_teacher_membership(client):
    """被移除教师的省略 class_id 作业列表不得聚合旧班级。"""
    from app.models.coursework import Assignment

    async with async_session_factory() as db:
        owner = await make_user(db)
        removed_teacher = await make_user(db)
        cid = await make_class(db, owner)
        membership = await add_member(db, cid, removed_teacher, member_role="teacher")
        membership.deleted_at = datetime.now(UTC)
        db.add(
            Assignment(
                class_id=cid,
                creator_id=owner,
                title="已移除教师不可见的作业",
                type="quiz",
                status="published",
            )
        )
        await db.commit()

    response = await client.get(
        "/api/teacher/assignments", headers=_auth(token(removed_teacher, "teacher"))
    )

    assert response.status_code == 200
    assert response.json()["data"]["assignments"] == []


@pytest.mark.asyncio
async def test_omitted_grading_queue_excludes_soft_deleted_teacher_membership(client):
    """被移除教师的省略 class_id 批改队列不得聚合旧班级。"""
    from app.models.coursework import Assignment, Submission, SubmissionItem

    async with async_session_factory() as db:
        owner = await make_user(db)
        removed_teacher = await make_user(db)
        cid = await make_class(db, owner)
        membership = await add_member(db, cid, removed_teacher, member_role="teacher")
        membership.deleted_at = datetime.now(UTC)
        assignment = Assignment(
            class_id=cid,
            creator_id=owner,
            title="已移除教师不可见的待批改作业",
            type="quiz",
            status="published",
        )
        db.add(assignment)
        await db.flush()
        submission = Submission(
            user_id=owner,
            assignment_id=assignment.id,
            client_submit_id="removed-member-submission",
        )
        db.add(submission)
        await db.flush()
        db.add(
            SubmissionItem(
                submission_id=submission.id,
                item_no=1,
                q_type="text",
                verdict="pending_review",
            )
        )
        await db.commit()

    response = await client.get(
        "/api/teacher/grading/queue", headers=_auth(token(removed_teacher, "teacher"))
    )

    assert response.status_code == 200
    assert response.json()["data"]["queue"] == []


@pytest.mark.asyncio
async def test_omitted_assignments_exclude_soft_deleted_owned_class(client):
    """班级软删除后，原 owner 的省略 class_id 查询也不得返回其作业。"""
    from app.models.coursework import Assignment
    from tests._m3_helpers import class_of

    async with async_session_factory() as db:
        owner = await make_user(db)
        cid = await make_class(db, owner)
        clazz = await class_of(db, cid)
        assert clazz is not None
        clazz.deleted_at = datetime.now(UTC)
        db.add(
            Assignment(
                class_id=cid,
                creator_id=owner,
                title="已删除班级的作业",
                type="quiz",
                status="published",
            )
        )
        await db.commit()

    response = await client.get("/api/teacher/assignments", headers=_auth(token(owner, "teacher")))

    assert response.status_code == 200
    assert response.json()["data"]["assignments"] == []


@pytest.mark.asyncio
async def test_cross_teacher_artifact_uuid_not_found(client):

    from app.domains.teacher.artifacts import create_artifact

    async with async_session_factory() as db:
        owner = await make_user(db)
        other = await make_user(db)
        cid = await make_class(db, owner)
        art = await create_artifact(
            db, owner_id=owner, artifact_type="lesson_plan", scene="teacher.prep", class_id=cid,
            payload={"topic": "t"},
        )
        db.add(art)
        await db.commit()
        aid = art.id
    # 其他教师猜测 artifact UUID → not_found（不泄露存在性）
    resp = await client.get(
        f"/api/teacher/artifacts/{aid}", headers=_auth(token(other, "teacher"))
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == 40400
