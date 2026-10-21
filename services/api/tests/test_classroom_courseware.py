"""教师课堂课件（8000）：发布准备 → 学生进入 → 进度/练习回流 → 教师监控。

覆盖：
- 创建草稿 + 后台准备 + confirm/publish（真实状态机 + TeacherAction 审计）；
- 讲稿优先级（speaker_note > AI > 确定性降级）与 LaTeX 源码清洗；
- blocks / deck_slide / asset_urls / 分层练习；
- 学生列表 / 打开（复用会话）/ 进度 / 练习 / 教师监控真实聚合；
- 软下架、权限拒绝、LLM 失败降级、载荷约束。

测试不真实调用 LLM：monkeypatch `app.providers.router.get_model_router` 与
`stage_router._gen_practice`。
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.domains.classroom import courseware as cw
from app.domains.classroom import stage_router
from app.main import app
from app.models.classroom import ClassroomSession
from app.models.database import async_session_factory
from app.models.teacher import TeacherAction, TeachingArtifact
from tests._m3_helpers import add_member, make_class, make_user, token


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


def _slide(index: int, **overrides) -> dict:
    slide = {
        "id": f"slide-{index:02d}",
        "type": "content",
        "title": f"第 {index} 页标题",
        "content": "",
        "layout_hint": "concept",
        "elements": [
            {"role": "definition", "text": "给定两个非空数集，按对应关系建立函数。"},
            {"role": "formula", "latex": "f(x)=x^2+1", "text": "函数解析式"},
            {"role": "bullets", "items": ["要点一", "要点二"]},
        ],
        "math_object": None,
        "assets": [],
        "speaker_note": None,
    }
    slide.update(overrides)
    return slide


def _body(class_id: uuid.UUID, *, slides: list[dict] | None = None, **overrides) -> dict:
    body = {
        "class_id": str(class_id),
        "deck_id": "deck-0001",
        "deck_revision": 3,
        "title": "1.1 集合的概念",
        "textbook_line": "人教A版2019 必修第一册 · 第一章",
        "subject": "math",
        "slides": slides or [_slide(1), _slide(2), _slide(3)],
        "source_refs": [{"textbook_id": "tb-1", "section_id": "sec-1", "locator": "p.12"}],
        "warnings": ["教师端预警：单页素材偏少"],
    }
    body.update(overrides)
    return body


class _FakeRouter:
    def __init__(self, content: str):
        self.content = content
        self.calls: list[dict] = []

    async def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        return {"content": self.content}


class _RaisingRouter:
    async def chat(self, messages, **kwargs):
        raise RuntimeError("model unavailable")


@pytest.fixture
def fake_practice(monkeypatch):
    async def _gen(topic, outlines, slides, per_tier=3):
        return {
            "basic": [
                {
                    "question": "集合中元素的确定性指什么？",
                    "options": ["对象可确定", "对象可数", "对象无限", "对象是数"],
                    "answer": 0,
                    "analysis": "给定集合，任一对象是否属于它是确定的。",
                }
            ],
            "advanced": [],
            "challenge": [],
        }

    monkeypatch.setattr(stage_router, "_gen_practice", _gen)
    return _gen


@pytest.fixture
def fake_narration(monkeypatch):
    router = _FakeRouter(
        '{"narrations":[{"order":2,"narration":"第二页我们来看集合的表示。集合可以用列举法或描述法表示，先观察元素是否满足确定性和互异性，再选择合适的方法。"}]}'
    )
    monkeypatch.setattr("app.providers.router.get_model_router", lambda: router)
    return router


async def _setup_class(*, student_confirmed: bool = True):
    async with async_session_factory() as db:
        teacher_id = await make_user(db, nickname="张老师")
        class_id = await make_class(db, teacher_id, name="高二2班")
        student_id = await make_user(db, nickname="小明", teacher_verified=None)
        await add_member(
            db, class_id, student_id, member_role="student", confirmed=student_confirmed
        )
        await db.commit()
    return teacher_id, class_id, student_id


async def _create(client, tok, body) -> dict:
    resp = await client.post(
        "/api/teacher/classroom-coursewares", json=body, headers=_auth(tok)
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["code"] == 0, payload
    return payload["data"]


async def _wait_published(courseware_id: str, timeout: float = 10.0) -> TeachingArtifact:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        async with async_session_factory() as db:
            artifact = await db.get(TeachingArtifact, uuid.UUID(courseware_id))
            if artifact is not None and artifact.status in ("published", "archived"):
                db.expunge(artifact)
                # 让后台任务跑完 finally 并关闭自己的 NullPool 连接，
                # 避免连接在下一个测试的事件循环里被 GC 关闭
                await asyncio.sleep(0.1)
                return artifact
        await asyncio.sleep(0.1)
    raise AssertionError(f"courseware {courseware_id} was not published in time")


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------


def test_narration_sanitizer_removes_latex_source():
    text = cw._sanitize_narration(r"我们看 $\frac{1}{2}$ 与 \boxed{3} 的关系，$x^{2}$ 很重要")
    assert "$" not in text
    assert "\\" not in text
    assert "boxed" not in text
    assert len(text) <= cw.MAX_NARRATION_CHARS


def test_blocks_mapping_covers_all_roles():
    slide = {
        "title": "函数的概念",
        "content": "",
        "math_object": {"kind": "plot2d"},
        "elements": [
            {"role": "definition", "text": "函数的定义"},
            {"role": "formula", "latex": "y=x+1"},
            {"role": "knowledge_card", "title": "要点", "latex": "y=kx+b", "items": ["斜截式"]},
            {"role": "steps", "items": ["第一步", "第二步"]},
            {"role": "bullets", "items": ["甲", "乙"]},
            {"role": "table", "items": ["x|y", "1|2", "3|4"]},
            {"role": "note", "text": "注意定义域"},
            {"role": "figure", "figure_ref": "math_object"},
        ],
    }
    blocks = cw._blocks_from_elements(slide, ["asset-1"])
    kinds = [block["kind"] for block in blocks]
    assert kinds[0] == "theorem"
    assert "latex" in kinds
    assert "note" in kinds
    assert "table" in kinds
    assert kinds[-1] == "figure"
    assert blocks[-1]["asset_id"] == "asset-1"
    table = next(block for block in blocks if block["kind"] == "table")
    assert table["headers"] == ["x", "y"]
    assert table["rows"] == [["1", "2"], ["3", "4"]]
    steps = next(block for block in blocks if block["kind"] == "text" and "第一步" in block["text"])
    assert steps["text"].startswith("①")


def test_derive_narration_never_leaks_latex():
    narration = cw._derive_narration(
        "点斜式 $y-y_0=k(x-x_0)$",
        [{"role": "formula", "latex": "\\boxed{y=kx+b}"}, {"role": "note", "text": "先确定斜率"}],
    )
    assert "$" not in narration
    assert "boxed" not in narration
    assert "先确定斜率" in narration


# ---------------------------------------------------------------------------
# 创建 → 准备 → 发布
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_prepare_publishes_real_courseware(client, fake_practice, fake_narration):
    teacher_id, class_id, _ = await _setup_class()
    tok = token(teacher_id, "teacher")

    png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    slides = [
        _slide(
            1,
            type="title",
            layout_hint="title",
            elements=[{"role": "bullets", "items": ["学习目标", "重点难点"]}],
            speaker_note=r"同学们好，今天我们学习集合，先看 $A\cap B$ 与 \boxed{1} 的关系。",
            assets=[{"id": "asset-1", "mime_type": "image/png", "data_base64": png}],
        ),
        _slide(2, elements=[{"role": "steps", "items": ["先读定义", "再举例子"]}]),
        _slide(3, elements=[{"role": "table", "items": ["元素|性质", "1|确定", "2|互异"]}]),
    ]
    data = await _create(client, tok, _body(class_id, slides=slides))
    assert data["status"] == "preparing"
    courseware_id = data["courseware_id"]

    async with async_session_factory() as db:
        draft = await db.get(TeachingArtifact, uuid.UUID(courseware_id))
        assert draft.status == "draft"
        assert draft.artifact_type == "slide_deck"
        assert draft.scene == "classroom_courseware"
        assert str(draft.class_id) == str(class_id)
        assert draft.owner_id == teacher_id

    artifact = await _wait_published(courseware_id)
    assert artifact.artifact_type == "slide_deck"
    assert artifact.scene == "classroom_courseware"
    assert artifact.published_at is not None

    async with async_session_factory() as db:
        actions = (
            (
                await db.execute(
                    select(TeacherAction.action_type).where(
                        TeacherAction.artifact_id == uuid.UUID(courseware_id)
                    )
                )
            )
            .scalars()
            .all()
        )
    assert "artifact.confirm" in actions
    assert "artifact.publish" in actions

    payload = artifact.payload
    assert payload["narration_status"] == "mixed"  # cover + ai(第2页) + derived(第3页)
    assert payload["practice_status"] == "ready"
    assert payload["practice"]["basic"][0]["answer"] == 0
    assert payload["page_count"] == 3

    first = payload["slides"][0]
    assert first["order"] == 1
    assert first["minutes"] == 3
    # 封面用确定性开场；教师备注只作意图上下文，不直接读给学生
    assert first["narration_source"] == "cover", (first["narration_source"], first["narration"][:120])
    assert "同学们好" in first["narration"] and "第 1 页标题" in first["narration"]
    assert "先看 $A" not in first["narration"]
    assert "学习目标" not in first["narration"] and "重点难点" not in first["narration"]
    assert "$" not in first["narration"] and "\\" not in first["narration"]
    assert first["asset_urls"]["asset-1"].startswith("data:image/png;base64,")
    assert first["deck_slide"]["id"] == "slide-01"
    assert first["deck_slide"]["layout_hint"] == "title"
    assert first["verification_result"] == {"status": "verified"}
    assert first["deck_meta"]["theme"] == "golden-b"

    second = payload["slides"][1]
    assert second["narration_source"] == "ai"
    assert second["narration"].startswith("第二页我们来看集合的表示。")
    steps = next(b for b in second["blocks"] if "先读定义" in b.get("text", ""))
    assert steps["kind"] == "text" and steps["text"].startswith("①")

    third = payload["slides"][2]
    assert third["narration_source"] == "derived"
    table = next(b for b in third["blocks"] if b["kind"] == "table")
    assert table["headers"] == ["元素", "性质"]
    assert any("确定性讲稿" in w for w in artifact.warnings)
    assert artifact.degraded is True


@pytest.mark.asyncio
async def test_llm_and_practice_failure_publishes_degraded(client, monkeypatch):
    teacher_id, class_id, _ = await _setup_class()
    tok = token(teacher_id, "teacher")
    monkeypatch.setattr(
        "app.providers.router.get_model_router", lambda: _RaisingRouter()
    )

    async def _no_practice(topic, outlines, slides, per_tier=3):
        return None

    monkeypatch.setattr(stage_router, "_gen_practice", _no_practice)

    data = await _create(client, tok, _body(class_id))
    artifact = await _wait_published(data["courseware_id"])

    payload = artifact.payload
    assert artifact.degraded is True
    assert payload["practice_status"] == "fallback"
    assert payload["practice"]["basic"]  # 保底练习真实可作答
    assert payload["narration_status"] == "fallback"
    assert all(s["narration_source"] == "derived" for s in payload["slides"])
    assert any("讲稿生成" in w for w in artifact.warnings)
    assert any("保底复习题" in w for w in artifact.warnings)
    for slide in payload["slides"]:
        assert "$" not in slide["narration"]
        assert "\\" not in slide["narration"]


@pytest.mark.asyncio
async def test_payload_constraints_truncate_elements_and_skip_bad_assets(client, fake_practice, fake_narration):
    teacher_id, class_id, _ = await _setup_class()
    tok = token(teacher_id, "teacher")
    many = [{"role": "bullets", "items": [f"要点{i}"]} for i in range(15)]
    slides = [
        _slide(1, elements=many),
        _slide(
            2,
            assets=[
                {"id": "bad", "mime_type": "text/html", "data_base64": "PGh0bWw+"},
                {"id": "ok", "mime_type": "image/png", "data_base64": "iVBORw0KGgo="},
            ],
        ),
        _slide(3),
    ]
    data = await _create(client, tok, _body(class_id, slides=slides))
    artifact = await _wait_published(data["courseware_id"])

    payload = artifact.payload
    assert len(payload["slides"][0]["deck_slide"]["elements"]) == cw.MAX_ELEMENTS_PER_SLIDE
    assert set(payload["slides"][1]["asset_urls"].keys()) == {"ok"}
    assert any("元素超过" in w for w in artifact.warnings)
    assert any("MIME" in w for w in artifact.warnings)


@pytest.mark.asyncio
async def test_rejects_deck_outside_page_bounds(client, fake_practice, fake_narration):
    teacher_id, class_id, _ = await _setup_class()
    tok = token(teacher_id, "teacher")
    resp = await client.post(
        "/api/teacher/classroom-coursewares",
        json=_body(class_id, slides=[_slide(1), _slide(2)]),
        headers=_auth(tok),
    )
    assert resp.status_code == 422
    assert resp.json()["code"] == 40001


# ---------------------------------------------------------------------------
# 教师列表 / 详情
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_teacher_list_and_detail_show_preparation_state(client, fake_practice, fake_narration):
    teacher_id, class_id, _ = await _setup_class()
    tok = token(teacher_id, "teacher")
    data = await _create(client, tok, _body(class_id))
    await _wait_published(data["courseware_id"])

    listing = await client.get(
        "/api/teacher/classroom-coursewares",
        params={"class_id": str(class_id), "deck_id": "deck-0001"},
        headers=_auth(tok),
    )
    assert listing.status_code == 200
    body = listing.json()["data"]
    assert body["total"] == 1
    item = body["items"][0]
    assert item["courseware_id"] == data["courseware_id"]
    assert item["status"] == "published"
    assert item["page_count"] == 3
    assert item["total"] == 1  # 班级已确认学生数
    assert item["students_total"] == 1
    assert item["opened"] == 0
    assert item["avg_progress"] == 0.0
    assert item["avg_progress_pct"] == 0.0
    assert item["practice_accuracy"] is None
    assert item["class_name"] == "高二2班"
    assert item["teacher_name"] == "张老师"
    assert item["updated_at"]

    detail = await client.get(
        f"/api/teacher/classroom-coursewares/{data['courseware_id']}",
        headers=_auth(tok),
    )
    assert detail.status_code == 200
    detail_data = detail.json()["data"]
    assert detail_data["slides_ready"] == 3
    assert detail_data["slides_total"] == 3
    assert detail_data["practice_status"] == "ready"
    assert detail_data["preparation"]["done"] == 3
    assert detail_data["preparation"]["total"] == 3
    assert detail_data["preparation"]["practice_status"] == "ready"
    assert detail_data["warnings"]
    assert detail_data["published_at"]


# ---------------------------------------------------------------------------
# 学生列表 / 打开 / 进度 / 练习 / 监控
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_student_flow_and_teacher_monitor_use_real_data(client, fake_practice, fake_narration):
    teacher_id, class_id, student_id = await _setup_class()
    teacher_tok = token(teacher_id, "teacher")
    student_tok = token(student_id, "student")
    data = await _create(client, teacher_tok, _body(class_id))
    courseware_id = data["courseware_id"]
    await _wait_published(courseware_id)

    # 学生列表：未打开
    listing = await client.get("/api/student/classroom-coursewares", headers=_auth(student_tok))
    assert listing.status_code == 200
    items = listing.json()["data"]["items"]
    assert len(items) == 1
    item = items[0]
    assert item["courseware_id"] == courseware_id
    assert item["teacher_name"] == "张老师"
    assert item["class_name"] == "高二2班"
    assert item["opened"] is False
    assert item["progress_pct"] == 0.0
    assert item["last_session_id"] is None

    # 打开 → 新建 teacher_deck 会话，内容深拷贝自 artifact
    opened = await client.post(
        f"/api/student/classroom-coursewares/{courseware_id}/open", headers=_auth(student_tok)
    )
    assert opened.status_code == 200, opened.text
    session_id = opened.json()["data"]["session_id"]
    assert opened.json()["data"]["title"] == "1.1 集合的概念"

    session_resp = await client.get(
        f"/api/classroom/sessions/{session_id}", headers=_auth(student_tok)
    )
    session = session_resp.json()["data"]
    assert session["source_type"] == "teacher_deck"
    assert session["source_ref"]["courseware_artifact_id"] == courseware_id
    assert session["source_ref"]["class_id"] == str(class_id)
    assert session["status"] == "ready"
    assert len(session["slides"]) == 3
    assert session["slides"][0]["deck_slide"]["title"] == "第 1 页标题"
    assert session["practice"]["basic"]

    # 进度与练习回流（复用既有端点）
    progress = await client.patch(
        f"/api/classroom/sessions/{session_id}/progress",
        json={"slide_index": 1, "page_check": {"0": "ok"}},
        headers=_auth(student_tok),
    )
    assert progress.status_code == 200
    for correct in (True, True, False):
        answer = await client.post(
            f"/api/classroom/sessions/{session_id}/practice-answer",
            json={"tier": "basic", "question_index": 0, "correct": correct},
            headers=_auth(student_tok),
        )
        assert answer.status_code == 200

    # 学生列表反映真实进度
    listing = await client.get("/api/student/classroom-coursewares", headers=_auth(student_tok))
    item = listing.json()["data"]["items"][0]
    assert item["opened"] is True
    assert item["progress_pct"] == 66.7  # 3 页中到过 2 页
    assert item["practice_accuracy"] == 66.7  # 3 答 2 对
    assert item["last_session_id"] == session_id

    # 教师监控：真实逐生 + 页级留存
    monitor = await client.get(
        f"/api/teacher/classroom-coursewares/{courseware_id}/monitor",
        headers=_auth(teacher_tok),
    )
    assert monitor.status_code == 200, monitor.text
    m = monitor.json()["data"]
    assert m["students_total"] == 1
    assert m["opened"] == 1
    assert m["completed"] == 0
    assert m["avg_progress_pct"] == 66.7
    assert m["practice_accuracy"] == 66.7
    assert m["page_count"] == 3
    assert [p["page"] for p in m["page_dropoff"]] == [0, 1, 2]
    assert [p["reached"] for p in m["page_dropoff"]] == [1, 1, 0]
    assert [p["count"] for p in m["page_dropoff"]] == [1, 1, 0]
    student = m["students"][0]
    assert student["user_id"] == str(student_id)
    assert student["name"] == "小明"
    assert student["opened"] is True
    assert student["current_page"] == 1  # 0 基（slide_index=1 → 展示第 2 页）
    assert student["current_page_no"] == 2
    assert student["pages_seen"] == 2
    assert student["self_check_ok"] == 1
    assert student["self_check_again"] == 0
    assert student["practice_answered"] == 3
    assert student["practice_correct"] == 2
    assert student["last_active"]

    # 再打开 → 复用同一会话，不重置进度
    reopened = await client.post(
        f"/api/student/classroom-coursewares/{courseware_id}/open", headers=_auth(student_tok)
    )
    assert reopened.json()["data"]["session_id"] == session_id
    async with async_session_factory() as db:
        sessions = (
            await db.execute(
                select(ClassroomSession).where(
                    ClassroomSession.user_id == student_id,
                    ClassroomSession.source_type == "teacher_deck",
                )
            )
        ).scalars().all()
        assert len(sessions) == 1
        assert sessions[0].progress["slide_index"] == 1


@pytest.mark.asyncio
async def test_monitor_counts_unopened_students_honestly(client, fake_practice, fake_narration):
    teacher_id, class_id, student_id = await _setup_class()
    async with async_session_factory() as db:
        quiet_id = await make_user(db, nickname="小红", teacher_verified=None)
        await add_member(db, class_id, quiet_id, member_role="student", confirmed=True)
        pending_id = await make_user(db, nickname="待确认", teacher_verified=None)
        await add_member(db, class_id, pending_id, member_role="student", confirmed=False)
        await db.commit()
    teacher_tok = token(teacher_id, "teacher")
    student_tok = token(student_id, "student")

    data = await _create(client, teacher_tok, _body(class_id))
    courseware_id = data["courseware_id"]
    await _wait_published(courseware_id)
    await client.post(
        f"/api/student/classroom-coursewares/{courseware_id}/open", headers=_auth(student_tok)
    )

    monitor = await client.get(
        f"/api/teacher/classroom-coursewares/{courseware_id}/monitor",
        headers=_auth(teacher_tok),
    )
    m = monitor.json()["data"]
    assert m["students_total"] == 2  # 待确认学生不计入
    assert m["opened"] == 1
    names = {row["name"] for row in m["students"]}
    assert names == {"小明", "小红"}
    quiet = next(row for row in m["students"] if row["name"] == "小红")
    assert quiet["opened"] is False
    assert quiet["current_page"] is None
    assert quiet["last_active"] is None


# ---------------------------------------------------------------------------
# 软下架 / 权限
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_hides_courseware_from_students(client, fake_practice, fake_narration):
    teacher_id, class_id, student_id = await _setup_class()
    teacher_tok = token(teacher_id, "teacher")
    student_tok = token(student_id, "student")
    data = await _create(client, teacher_tok, _body(class_id))
    await _wait_published(data["courseware_id"])

    archived = await client.post(
        f"/api/teacher/classroom-coursewares/{data['courseware_id']}/archive",
        headers=_auth(teacher_tok),
    )
    assert archived.status_code == 200
    assert archived.json()["data"]["status"] == "archived"

    async with async_session_factory() as db:
        artifact = await db.get(TeachingArtifact, uuid.UUID(data["courseware_id"]))
        assert artifact.status == "archived"
        actions = (
            (
                await db.execute(
                    select(TeacherAction.action_type).where(
                        TeacherAction.artifact_id == uuid.UUID(data["courseware_id"])
                    )
                )
            )
            .scalars()
            .all()
        )
    assert "artifact.archive" in actions

    listing = await client.get("/api/student/classroom-coursewares", headers=_auth(student_tok))
    assert listing.json()["data"]["items"] == []
    opened = await client.post(
        f"/api/student/classroom-coursewares/{data['courseware_id']}/open",
        headers=_auth(student_tok),
    )
    assert opened.status_code == 404

    teacher_list = await client.get(
        "/api/teacher/classroom-coursewares", headers=_auth(teacher_tok)
    )
    assert teacher_list.json()["data"]["items"] == []


@pytest.mark.asyncio
async def test_permission_boundaries(client, fake_practice, fake_narration):
    teacher_id, class_id, student_id = await _setup_class()
    teacher_tok = token(teacher_id, "teacher")
    other_teacher_id, other_class_id, _ = await _setup_class(student_confirmed=False)
    other_tok = token(other_teacher_id, "teacher")

    # 非本班教师发布 → 40302
    resp = await client.post(
        "/api/teacher/classroom-coursewares",
        json=_body(class_id),
        headers=_auth(other_tok),
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == 40302

    data = await _create(client, teacher_tok, _body(class_id))
    await _wait_published(data["courseware_id"])

    # 其他教师读详情 / 监控 → 40400（不泄露存在性）
    for suffix in ("", "/monitor"):
        detail = await client.get(
            f"/api/teacher/classroom-coursewares/{data['courseware_id']}{suffix}",
            headers=_auth(other_tok),
        )
        assert detail.status_code == 404
        assert detail.json()["code"] == 40400

    # 非本班学生：列表为空；打开 → 40302
    outsider_id = await make_user_async(nickname="路人", teacher_verified=None)
    outsider_tok = token(outsider_id, "student")
    listing = await client.get(
        "/api/student/classroom-coursewares", headers=_auth(outsider_tok)
    )
    assert listing.json()["data"]["items"] == []
    denied = await client.post(
        f"/api/student/classroom-coursewares/{data['courseware_id']}/open",
        headers=_auth(outsider_tok),
    )
    assert denied.status_code == 403

    # 未确认学生：不能打开
    async with async_session_factory() as db:
        pending_id = await make_user(db, nickname="待确认", teacher_verified=None)
        await add_member(db, class_id, pending_id, member_role="student", confirmed=False)
        await db.commit()
    pending_tok = token(pending_id, "student")
    denied = await client.post(
        f"/api/student/classroom-coursewares/{data['courseware_id']}/open",
        headers=_auth(pending_tok),
    )
    assert denied.status_code == 403
    assert denied.json()["code"] == 40302


async def make_user_async(**kwargs):
    async with async_session_factory() as db:
        uid = await make_user(db, **kwargs)
        await db.commit()
    return uid


# ---------------------------------------------------------------------------
# 课堂讲稿：教师备注不是学生讲稿
# ---------------------------------------------------------------------------

VALID_AI_NARRATION = (
    "这一页我们观察函数图象的变化。曲线在区间上持续上升时，函数值随自变量增大而增大，"
    "我们把这种性质叫做单调递增。请结合图上的关键点，说一说两侧的增减趋势有什么不同。"
)


def _note_slide() -> dict:
    return {
        "id": "s2",
        "type": "concept",
        "title": "先看图像：增减趋势",
        "content": "",
        "elements": [
            {"role": "definition", "title": "单调递增", "text": "若 x1<x2，则 f(x1)<f(x2)，称函数在区间上单调递增。"},
            {"role": "bullets", "items": ["图象从左到右上升", "函数值随 x 增大而增大"]},
        ],
        "speaker_note": "先不引入导数，让学生用自己的语言描述上升和下降，请学生同桌互查。",
    }


def test_teacher_note_is_not_student_narration():
    slides, _, warnings, _ = cw._build_slides_and_outlines(
        {"title": "函数的单调性", "slides": [_note_slide()]}, {}
    )
    narration = slides[0]["narration"]
    assert slides[0]["narration_source"] == "derived"
    assert "让学生" not in narration and "请学生" not in narration
    assert "单调递增" in narration
    assert any("确定性讲稿" in w for w in warnings)


def test_valid_ai_narration_wins_over_teacher_note():
    slides, _, warnings, degraded = cw._build_slides_and_outlines(
        {"title": "函数的单调性", "slides": [_note_slide()]},
        {1: VALID_AI_NARRATION},
    )
    assert slides[0]["narration_source"] == "ai"
    assert slides[0]["narration"] == VALID_AI_NARRATION
    assert "让学生" not in slides[0]["narration"]
    assert warnings == [] and degraded is False


def test_short_or_teacher_facing_ai_narration_rejected():
    for bad in ("这一页看图象。", "让学生先观察图象，再请学生回答上升和下降。"):
        slides, _, _, _ = cw._build_slides_and_outlines(
            {"title": "函数的单调性", "slides": [_note_slide()]}, {1: bad}
        )
        assert slides[0]["narration_source"] == "derived"
        assert slides[0]["narration"] != bad


@pytest.mark.asyncio
async def test_narration_prompt_contains_formula_figure_and_note_guard(monkeypatch):
    router = _FakeRouter(
        '{"narrations":[{"order":1,"narration":"' + VALID_AI_NARRATION + '"}]}'
    )
    monkeypatch.setattr("app.providers.router.get_model_router", lambda: router)
    pages = [
        {
            "order": 1,
            "title": "导数的符号",
            "content": "",
            "units": ["观察切线斜率"],
            "formulas": ["f'(x)>0"],
            "figure": "f(x)=x^3-3x 的图象",
            "teacher_note": "让学生先观察图象",
        }
    ]
    cleaned, warning = await cw._generate_narrations("函数的单调性", pages)
    assert cleaned[1] == VALID_AI_NARRATION
    assert warning is None
    prompt = router.calls[0]["messages"][0]["content"]
    assert "f'(x)>0" in prompt
    assert "f(x)=x^3-3x 的图象" in prompt
    assert "禁止原样朗读" in prompt


@pytest.mark.asyncio
async def test_long_deck_narrations_are_batched(monkeypatch):
    router = _FakeRouter(
        '{"narrations":[{"order":1,"narration":"' + VALID_AI_NARRATION + '"}]}'
    )
    monkeypatch.setattr("app.providers.router.get_model_router", lambda: router)
    pages = [
        {"order": i, "title": f"第{i}页", "content": "", "units": [], "formulas": [], "figure": "", "teacher_note": ""}
        for i in range(1, 18)
    ]
    await cw._generate_narrations("长课件", pages)
    assert len(router.calls) == 4  # 8 + 8 + 1 + 缺失页重试


def test_cover_narration_ignores_hidden_elements():
    page = cw._narration_page(
        {
            "type": "title",
            "layout_hint": "title",
            "title": "空间向量的数量积运算",
            "content": "人教A版 选择性必修一",
            "elements": [{"role": "steps", "items": ["整节课的全部内容", "不应出现在封面讲稿"]}],
            "speaker_note": "教师备注",
        },
        1,
    )
    assert page["units"] == []
    assert page["formulas"] == []
    assert page["figure"] == ""
    assert page["content"] == "人教A版 选择性必修一"


def test_math_source_fragments_are_not_spoken():
    units = cw._plain_units(
        [
            {"role": "note", "text": "a coslangle a,brangle(b) 不能朗读"},
            {"role": "bullets", "items": ["先观察图象"]},
        ]
    )
    assert units == ["先观察图象"]


def test_cover_long_body_content_not_used_as_cover_narration():
    page = cw._narration_page(
        {
            "type": "title",
            "layout_hint": "title",
            "title": "空间向量的数量积运算",
            "content": "1. 定义：a·b=...\n2. 运算律...\n3. 作业...",
            "elements": [],
        },
        1,
    )
    assert page["content"] == ""
    assert page["units"] == []


def test_cover_page_uses_deterministic_intro_not_hidden_body():
    slides, _, warnings, degraded = cw._build_slides_and_outlines(
        {
            "title": "空间向量的数量积运算",
            "slides": [
                {
                    "type": "title",
                    "layout_hint": "title",
                    "title": "空间向量的数量积运算",
                    "content": "整节课的全部正文都在封面 content 里，但封面不渲染它。这里继续写入足够多的正文，确保长度超过六十个字符，从而不能被当作封面副标题使用，也不能进入学生讲稿。",
                    "elements": [{"role": "steps", "items": ["定义", "运算律", "作业"]}],
                    "speaker_note": "教师备注",
                }
            ],
        },
        {},
    )
    assert slides[0]["narration_source"] == "cover"
    assert "同学们好" in slides[0]["narration"]
    assert "空间向量的数量积运算" in slides[0]["narration"]
    assert "全部正文" not in slides[0]["narration"]
    assert "作业" not in slides[0]["narration"]
    assert warnings == [] and degraded is False
