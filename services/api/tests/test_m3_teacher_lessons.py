"""M3 教师端：教案/课件/讲解（§5.3）。"""

import io
import zipfile
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.domains.teacher.artifacts import create_artifact
from app.main import app
from app.models.database import async_session_factory
from app.models.teacher import TeachingArtifact
from tests._m3_helpers import make_class, make_user, token


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.mark.asyncio
async def test_adapt_lesson_creates_draft(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    resp = await client.post(
        "/api/teacher/lessons/adapt",
        json={"class_id": str(cid), "topic": "函数的单调性", "requirements": "精简讲授",
              "duration_minutes": 45},
        headers=_auth(token(tid, "teacher")),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "draft"
    assert "__art" and data["artifact_id"]


@pytest.mark.asyncio
async def test_local_lesson_template_is_explicitly_degraded_not_a_publishable_ai_result(client):
    """没有可用模型时，教案只能作为标明来源的基础草稿，不能伪装成 AI 成品。"""
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    response = await client.post(
        "/api/teacher/lessons/adapt",
        json={"class_id": str(cid), "topic": "函数的单调性", "requirements": "从真实材料出发"},
        headers=_auth(token(tid, "teacher")),
    )
    data = response.json()["data"]
    assert data["engine"] == "local"
    assert data["degraded"] is True
    assert "lesson_basic_template" in data["warnings"]


@pytest.mark.asyncio
async def test_adapt_lesson_records_only_teacher_owned_extracted_resource_as_source(client):
    """上传材料必须经归属与可提取文本校验，才可成为教案的可追溯输入。"""
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    headers = _auth(token(tid, "teacher"))
    uploaded = await client.post(
        "/api/teacher/resources/upload",
        files={"file": ("monotonicity.txt", "函数单调性的定义与例题来源", "text/plain")},
        headers=headers,
    )
    resource_id = uploaded.json()["data"]["resource_id"]
    response = await client.post(
        "/api/teacher/lessons/adapt",
        json={
            "class_id": str(cid), "topic": "函数的单调性", "source_resource_ids": [resource_id],
            "requirements": "使用来源材料中的定义与例题",
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert f"resource:{resource_id}" in data["source_refs"]
    assert data["validation"]["source_resources"] == [resource_id]


@pytest.mark.asyncio
async def test_lesson_list_and_get(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    tok = token(tid, "teacher")
    r1 = await client.post("/api/teacher/lessons/adapt",
                           json={"class_id": str(cid), "topic": "导数"}, headers=_auth(tok))
    aid = r1.json()["data"]["artifact_id"]
    lst = await client.get(f"/api/teacher/lessons?class_id={cid}", headers=_auth(tok))
    assert any(li["artifact_id"] == aid for li in lst.json()["data"]["lessons"])
    got = await client.get(f"/api/teacher/lessons/{aid}", headers=_auth(tok))
    assert got.json()["data"]["lesson_id"] == aid


@pytest.mark.asyncio
async def test_create_slides_requires_confirmed_lesson(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    tok = token(tid, "teacher")
    r1 = await client.post("/api/teacher/lessons/adapt",
                           json={"class_id": str(cid), "topic": "导数"}, headers=_auth(tok))
    aid = r1.json()["data"]["artifact_id"]
    # draft 未确认 → 42210
    resp = await client.post(f"/api/teacher/lessons/{aid}/slides",
                             json={"version": 1}, headers=_auth(tok))
    assert resp.json()["code"] == 42210


@pytest.mark.asyncio
async def test_succeeded_remote_lesson_with_invalid_content_falls_back_to_authoritative_template(client):
    """星辰的“成功”状态不应使不完整教案绕过落库前校验。"""
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    auth = _auth(token(tid, "teacher"))
    remote = AsyncMock(
        return_value={
            "status": "succeeded",
            "content": {
                "topic": "被远程内容覆盖",
                "objectives": [],
                "timeline": [{"phase": "导入", "minutes": 60}],
            },
        }
    )
    with patch("app.domains.teacher.capability_gateway.adapter.run", new=remote):
        response = await client.post(
            "/api/teacher/lessons/adapt",
            json={
                "class_id": str(cid),
                "topic": "  导数的概念  ",
                "requirements": "保留板演",
                "duration_minutes": 60,
            },
            headers=auth,
        )
    assert response.status_code == 200, response.text
    artifact = response.json()["data"]
    payload = artifact["content"]
    assert artifact["engine"] == "local"
    assert artifact["degraded"] is True
    assert "xingchen_lesson_payload_invalid" in artifact["warnings"]
    assert artifact["validation"]["lesson_payload"] == "local_fallback"
    assert payload["topic"] == "导数的概念"
    assert payload["duration_minutes"] == 60
    assert payload["requirements"] == "保留板演"
    assert payload["objectives"]
    assert sum(item["minutes"] for item in payload["timeline"]) == 60
    assert all(item["activities"] for item in payload["timeline"])


@pytest.mark.asyncio
async def test_valid_remote_lesson_is_normalized_to_request_and_keeps_teacher_requirement(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    remote = AsyncMock(
        return_value={
            "status": "succeeded",
            "content": {
                "topic": "远程题目",
                "duration_minutes": 5,
                "requirements": "远程要求",
                "objectives": ["理解导数的概念并联系图像"],
                "timeline": [
                    {
                        "phase": "探究",
                        "minutes": 60,
                        "activities": ["学生观察函数图像并讨论变化率。"],
                    }
                ],
            },
        }
    )
    with patch("app.domains.teacher.capability_gateway.adapter.run", new=remote):
        response = await client.post(
            "/api/teacher/lessons/adapt",
            json={
                "class_id": str(cid),
                "topic": "  导数的概念  ",
                "requirements": "保留学生板演",
                "duration_minutes": 60,
            },
            headers=_auth(token(tid, "teacher")),
        )
    assert response.status_code == 200, response.text
    artifact = response.json()["data"]
    payload = artifact["content"]
    assert artifact["engine"] == "xingchen"
    assert artifact["validation"]["lesson_payload"] == "normalized"
    assert payload["topic"] == "导数的概念"
    assert payload["duration_minutes"] == 60
    assert payload["requirements"] == "保留学生板演"
    assert any(
        "保留学生板演" in activity
        for item in payload["timeline"]
        for activity in item["activities"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("duration", [1, 5, 6, 240])
async def test_adapt_lesson_short_and_long_durations_are_exact_and_editable(client, duration):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    response = await client.post(
        "/api/teacher/lessons/adapt",
        json={"class_id": str(cid), "topic": "极限", "duration_minutes": duration},
        headers=_auth(token(tid, "teacher")),
    )
    assert response.status_code == 200, response.text
    timeline = response.json()["data"]["content"]["timeline"]
    assert sum(item["minutes"] for item in timeline) == duration
    assert all(item["minutes"] > 0 and item["activities"] for item in timeline)


@pytest.mark.asyncio
async def test_adapt_lesson_rejects_whitespace_only_topic_without_creating_artifact(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    response = await client.post(
        "/api/teacher/lessons/adapt",
        json={"class_id": str(cid), "topic": "   ", "duration_minutes": 45},
        headers=_auth(token(tid, "teacher")),
    )
    assert response.status_code == 422
    async with async_session_factory() as db:
        artifacts = list(
            (await db.execute(select(TeachingArtifact.id).where(TeachingArtifact.owner_id == tid))).scalars()
        )
    assert artifacts == []


@pytest.mark.asyncio
async def test_create_slides_requires_confirmed_lesson_plan_and_matching_version(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        quiz = await create_artifact(
            db,
            owner_id=tid,
            artifact_type="quiz_set",
            scene="teacher.prep",
            class_id=cid,
            payload={},
        )
        db.add(quiz)
        await db.commit()
        quiz_id = quiz.id
    auth = _auth(token(tid, "teacher"))
    wrong_type = await client.post(
        f"/api/teacher/lessons/{quiz_id}/slides", json={"version": 1}, headers=auth
    )
    assert wrong_type.status_code == 404

    adapted = await client.post(
        "/api/teacher/lessons/adapt",
        json={"class_id": str(cid), "topic": "导数"},
        headers=auth,
    )
    lesson_id = adapted.json()["data"]["artifact_id"]
    await client.post(
        f"/api/teacher/artifacts/{lesson_id}/confirm",
        json={"client_request_id": "confirm-version", "idempotency_key": "confirm-version"},
        headers=auth,
    )
    stale_version = await client.post(
        f"/api/teacher/lessons/{lesson_id}/slides", json={"version": 99}, headers=auth
    )
    assert stale_version.status_code == 409
    assert stale_version.json()["code"] == 40901

    archived = await client.post(
        f"/api/teacher/artifacts/{lesson_id}/archive",
        json={"client_request_id": "archive-version", "idempotency_key": "archive-version"},
        headers=auth,
    )
    assert archived.json()["code"] == 0
    archived_slides = await client.post(
        f"/api/teacher/lessons/{lesson_id}/slides", json={"version": 1}, headers=auth
    )
    assert archived_slides.status_code == 422
    assert archived_slides.json()["code"] == 42210


@pytest.mark.asyncio
async def test_adapt_lesson_builds_editable_topic_plan_and_requires_explicit_slide_confirmation(client):
    """教案必须是可编辑的、按主题和课时确定性生成的草稿。"""
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    auth = _auth(token(tid, "teacher"))
    topic = "导数的概念"
    requirements = "强调图像与导数含义的对应，保留学生板演时间"

    adapted = await client.post(
        "/api/teacher/lessons/adapt",
        json={
            "class_id": str(cid),
            "topic": topic,
            "requirements": requirements,
            "duration_minutes": 60,
        },
        headers=auth,
    )
    assert adapted.status_code == 200, adapted.text
    lesson = adapted.json()["data"]
    assert lesson["status"] == "draft"
    payload = lesson["content"]
    assert payload["topic"] == topic
    assert payload["duration_minutes"] == 60
    assert topic in "\n".join(payload["objectives"])
    assert sum(item["minutes"] for item in payload["timeline"]) == 60
    assert all(
        isinstance(item.get("activities"), list)
        and all(isinstance(activity, str) and activity.strip() for activity in item["activities"])
        for item in payload["timeline"]
    )
    rendered = str(payload)
    assert "不知道" not in rendered
    assert "Exit Ticket" not in rendered
    assert any(requirements in activity for item in payload["timeline"] for activity in item["activities"])

    # 适配只创建 lesson_plan 草稿，绝不隐式生成课件。
    async with async_session_factory() as db:
        artifacts = list(
            (await db.execute(
                select(TeachingArtifact.artifact_type).where(
                    TeachingArtifact.owner_id == tid,
                    TeachingArtifact.class_id == cid,
                )
            )).scalars()
        )
    assert artifacts == ["lesson_plan"]

    lesson_id = lesson["artifact_id"]
    premature = await client.post(
        f"/api/teacher/lessons/{lesson_id}/slides",
        json={"version": 1},
        headers=auth,
    )
    assert premature.status_code == 422
    assert premature.json()["code"] == 42210
    assert premature.json()["message"] == "confirmation_required"

    confirmed = await client.post(
        f"/api/teacher/artifacts/{lesson_id}/confirm",
        json={"client_request_id": "confirm-calculus", "idempotency_key": "confirm-calculus"},
        headers=auth,
    )
    assert confirmed.json()["code"] == 0
    slides = await client.post(
        f"/api/teacher/lessons/{lesson_id}/slides",
        json={"version": 1},
        headers=auth,
    )
    assert slides.json()["code"] == 0, slides.text
    downloaded = await client.get(slides.json()["data"]["content"]["download_url"], headers=auth)
    assert downloaded.status_code == 200
    assert len(downloaded.content) > 1_000
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        slides_xml = [
            archive.read(name).decode("utf-8")
            for name in archive.namelist()
            if name.startswith("ppt/slides/slide") and name.endswith(".xml")
        ]
    assert len(slides_xml) >= 2
    assert any(topic in slide_xml for slide_xml in slides_xml)


@pytest.mark.asyncio
async def test_confirmed_lesson_creates_downloadable_pptx(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    auth = _auth(token(tid, "teacher"))
    lesson = await client.post(
        "/api/teacher/lessons/adapt",
        json={"class_id": str(cid), "topic": "函数的单调性", "duration_minutes": 45},
        headers=auth,
    )
    lesson_id = lesson.json()["data"]["artifact_id"]
    confirmed = await client.post(
        f"/api/teacher/artifacts/{lesson_id}/confirm",
        json={"client_request_id": f"confirm-{lesson_id}", "idempotency_key": f"confirm-{lesson_id}"},
        headers=auth,
    )
    assert confirmed.json()["code"] == 0
    slides = await client.post(
        f"/api/teacher/lessons/{lesson_id}/slides",
        json={"version": 1, "style": "简洁课堂"},
        headers=auth,
    )
    assert slides.json()["code"] == 0, slides.text
    data = slides.json()["data"]
    assert data["content"]["download_url"].endswith("/download")
    downloaded = await client.get(data["content"]["download_url"], headers=auth)
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    )
    assert downloaded.content[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        slide_parts = [name for name in archive.namelist() if name.startswith("ppt/slides/slide")]
        assert len(slide_parts) >= 2


@pytest.mark.asyncio
async def test_lesson_plan_downloads_as_real_docx(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    auth = _auth(token(tid, "teacher"))
    lesson = await client.post(
        "/api/teacher/lessons/adapt",
        json={"class_id": str(cid), "topic": "函数的单调性", "duration_minutes": 45},
        headers=auth,
    )
    lesson_id = lesson.json()["data"]["artifact_id"]

    downloaded = await client.get(
        f"/api/teacher/lessons/{lesson_id}/download", headers=auth
    )
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert downloaded.content[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
        assert "函数的单调性" in document_xml


@pytest.mark.asyncio
async def test_apply_insight_creates_new_version(client):
    async with async_session_factory() as db:
        tid = await make_user(db)
        cid = await make_class(db, tid)
        await db.commit()
    tok = token(tid, "teacher")
    r1 = await client.post("/api/teacher/lessons/adapt",
                           json={"class_id": str(cid), "topic": "导数"}, headers=_auth(tok))
    # 先确认以触发新版本
    aid = r1.json()["data"]["artifact_id"]
    await client.post(f"/api/teacher/artifacts/{aid}/confirm",
                      json={"client_request_id": "x", "idempotency_key": "conf"}, headers=_auth(tok))
    # 造一条真实 active 洞察（按 insight_id 加载，不信任前端摘要）
    async with async_session_factory() as db:
        from app.models.teacher import ActionableInsight

        ins = ActionableInsight(
            class_id=cid, kind="review_backlog", summary="待复核 3 份",
            evidence={"count": 3}, recommended_actions=[{"action": "open_grading", "label": "开始批改"}],
            confidence=0.9,
        )
        db.add(ins)
        await db.commit()
        insight_id = ins.id
    resp = await client.post(
        f"/api/teacher/lessons/{aid}/apply-insight",
        json={"insight_id": str(insight_id), "version": 1},
        headers=_auth(tok),
    )
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["version"] == 2
    # 洞察被标记 applied
    async with async_session_factory() as db:
        ins2 = await db.get(ActionableInsight, insight_id)
        assert ins2.status == "applied"
