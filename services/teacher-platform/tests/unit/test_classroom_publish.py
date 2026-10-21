"""Phase B：备课 session → 8000 班级课堂课件发布编排（离线契约测试）。

覆盖任务书要求：
- 成功载荷：POST 路径正确、slides 数正确、speaker_note 读 `slide.speaker_note`
  （不是恒空的 `deck.speaker_notes`）、标题/教材行/deck revision/班级正确；
- 无 assets 时不崩；资产 ≤4MB 内 base64 + sha256 与实际字节同源；
- 资产超 4MB → 跳过 + warning，不阻断发布；
- 无 deck → 409；跨班非成员 → 403 fail-closed；跨教师 deck → 403（spec_access）；
- 8000 5xx → 503 契约错误；8000 信封 code!=0 → 对应契约错误；
- 全程 FakeApiTransport + fake storage：不真实调 LLM/MinIO。
"""

from __future__ import annotations

import base64
import hashlib
import uuid

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401 —— 注册全部 ORM 模型到 Base.metadata
import app.teacher_prep.router as prep_router
from app.core.deps import get_current_user, get_session
from app.core.security import UserToken
from app.db.base import Base
from app.ppt_v2.domain.spec import (
    AssetRef,
    DeckMetadata,
    MathObjectSpec,
    SlideElement,
    SlideSpec,
    TeachingDeckSpec,
    TextbookSourceRef,
)
from app.ppt_v2.persistence.models import PptV2DeckSpecRecord
from app.teacher_prep.models import TeachingBlueprintRow, TeachingSessionRow
from app.teacher_prep.specs.models import SpecProjectionRow
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter, FakeApiTransport
from app.teacher_prep_v2.adapters.mapping import InMemoryMappingRegistry
from app.teacher_prep_v2.contracts.errors import (
    ContractAuthorityUnreachable,
    ContractMembershipDenied,
)
from app.textbook_v2.models import TextbookSection, TextbookSource

TEACHER = "11111111-1111-1111-1111-111111111111"
OTHER_TEACHER = "44444444-4444-4444-4444-444444444444"
CLASS_A = "22222222-2222-2222-2222-222222222222"
CLASS_B = "33333333-3333-3333-3333-333333333333"
TOKEN = "jwt-teacher-token"
BASE = "/api/teacher-v3/prep"

SRC = uuid.UUID("50000000-0000-0000-0000-0000000000a1")
LESSON = uuid.UUID("50000000-0000-0000-0000-0000000000a3")

MAX_ASSET = 4 * 1024 * 1024


# ── 离线 DB / 8000 fake ─────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def db() -> AsyncSession:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


class Fake8000:
    """8000 事实模拟：班成员表 + 课堂课件创建端点（记录真实下发载荷）。"""

    def __init__(self) -> None:
        self.members: dict[str, dict] = {}
        self.courseware_calls: list[dict] = []
        self.courseware_response: tuple[int, dict | None] = (
            200,
            {"code": 0, "data": {"courseware_id": "cw-8000-1", "status": "preparing"}},
        )
        self.transport = FakeApiTransport()
        self.transport.handler = self.handle

    def set_member(
        self,
        class_id: str,
        *,
        user_id: str = TEACHER,
        role: str = "teacher",
        confirmed: bool = True,
    ) -> None:
        self.members[class_id] = {
            "class_id": class_id,
            "user_id": user_id,
            "member_role": role,
            "confirmed": confirmed,
        }

    def remove_member(self, class_id: str) -> None:
        self.members.pop(class_id, None)

    def handle(self, method: str, path: str, token: str, json_body: dict | None):
        if path == "/api/teacher/classroom-coursewares":
            self.courseware_calls.append(json_body or {})
            return self.courseware_response
        if path.startswith("/api/classes/"):
            parts = path.split("/")
            cid = parts[3] if len(parts) > 3 else ""
            member = self.members.get(cid)
            if member is None:
                return 404, {"detail": "member not found"}
            if member.get("confirmed") is False:
                return 403, {"detail": "unconfirmed"}
            return 200, member
        return 404, {"detail": "unknown path"}


@pytest.fixture
def api8000() -> Fake8000:
    return Fake8000()


@pytest.fixture
def adapter(api8000: Fake8000) -> Api8000Adapter:
    return Api8000Adapter(api8000.transport, InMemoryMappingRegistry())


@pytest.fixture
def api_app(db: AsyncSession, adapter: Api8000Adapter):
    """真实 FastAPI 栈 + 注入测试 DB/用户 + fake 8000（与 teacher_prep/test_api 同款）。"""
    from main import create_app

    application = create_app()
    original = prep_router._adapter
    prep_router._adapter = lambda: adapter  # type: ignore[assignment]

    async def _override_db():
        yield db

    def _override_user() -> UserToken:
        return UserToken(user_id=TEACHER, name="张老师", role="teacher", school_id=None)

    application.dependency_overrides[get_session] = _override_db
    application.dependency_overrides[get_current_user] = _override_user
    yield application
    prep_router._adapter = original


def _client(application) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    )


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


class FakeStorage:
    """MinIO 假读：只允许显式登记的对象；记录调用以证明未触达真实存储。"""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = objects or {}
        self.calls: list[tuple[str, str]] = []

    def get_bytes(self, bucket: str, object_name: str) -> bytes:
        self.calls.append((bucket, object_name))
        if object_name not in self.objects:
            raise KeyError(object_name)
        return self.objects[object_name]


# ── 种子 ────────────────────────────────────────────────────────────────────


async def _seed_textbook(session: AsyncSession) -> None:
    session.add(
        TextbookSource(
            id=SRC,
            school_id=None,
            publisher="合成出版社",
            edition="2019",
            grade="高一",
            volume="必修第一册",
            subject="数学",
            source_repo="https://example.invalid/synthetic.git",
            source_commit="0" * 40,
            source_path="synthetic/classroom.jsonl",
            file_sha256="f" * 64,
            rights_status="STAGED",
            rights_evidence_ref="队伍确认授权",
            parser_version="slice_md-202607",
            source_kind="direct-chunk",
            batch_id="synth-batch-01",
            manifest_sha256="m" * 64,
        )
    )
    session.add(
        TextbookSection(
            id=LESSON,
            textbook_id=SRC,
            parent_id=None,
            level=2,
            order=1,
            title="1.1 合成集合的概念",
            normalized_title="合成集合的概念",
            page_start=1,
            page_end=12,
            section_path="1.1",
            chunk_start=1,
            chunk_end=12,
        )
    )
    await session.flush()


async def _seed_session(
    session: AsyncSession,
    *,
    teacher: str = TEACHER,
    class_id: str = CLASS_A,
    title: str = "合成集合备课",
) -> TeachingSessionRow:
    row = TeachingSessionRow(
        id=uuid.uuid4(),
        teacher_id=uuid.UUID(teacher),
        class_id=uuid.UUID(class_id),
        status="active",
        revision=1,
        title=title,
    )
    session.add(row)
    await session.flush()
    return row


def _asset(
    asset_id: str,
    *,
    key: str,
    size: int,
    mime: str = "image/svg+xml",
    rights: str = "ACTIVE",
) -> AssetRef:
    return AssetRef(
        id=asset_id,
        kind="MATH_RENDER",
        object_key=key,
        sha256="a" * 64,
        mime_type=mime,
        size_bytes=size,
        rights_status=rights,
    )


def _slide(
    slide_id: str,
    *,
    type_: str = "concept",
    title: str,
    note: str | None = None,
    assets: list[str] | None = None,
    elements: list[SlideElement] | None = None,
    math_object: MathObjectSpec | None = None,
) -> SlideSpec:
    return SlideSpec(
        id=slide_id,
        type=type_,
        teaching_goal="掌握本页教学要点",
        title=title,
        content="",
        elements=elements or [],
        assets=assets or [],
        layout_hint=type_,
        speaker_note=note,
        math_object=math_object,
    )


def _deck(
    *,
    slides: list[SlideSpec],
    assets: list[AssetRef] | None = None,
    title: str = "1.1 合成集合",
    revision: int = 7,
) -> TeachingDeckSpec:
    notes = {slides[0].id: "（deck.speaker_notes 汇总值，不应被课堂采用）"} if slides else {}
    return TeachingDeckSpec(
        id=str(uuid.uuid4()),
        revision=revision,
        metadata=DeckMetadata(title=title, grade="高一"),
        textbook_sources=[TextbookSourceRef(textbook_id=str(SRC), section_id=str(LESSON))],
        slides=slides,
        assets=assets or [],
        speaker_notes=notes,
    )


async def _attach_deck(
    session: AsyncSession,
    session_row: TeachingSessionRow,
    spec: TeachingDeckSpec,
) -> PptV2DeckSpecRecord:
    """落 deck 唯一事实 + 本 session 的 deck 投影（发布走「最新投影」路径）。"""
    deck_row = PptV2DeckSpecRecord(
        id=uuid.uuid4(),
        school_id=session_row.class_id,
        schema_version="2.0.0",
        revision=spec.revision,
        spec=spec.model_dump(mode="json"),
    )
    session.add(deck_row)
    blueprint = TeachingBlueprintRow(
        id=uuid.uuid4(),
        session_id=session_row.id,
        status="confirmed",
        revision=1,
        title="合成蓝图",
        body={"title": spec.metadata.title},
    )
    session.add(blueprint)
    await session.flush()
    session.add(
        SpecProjectionRow(
            session_id=session_row.id,
            blueprint_id=blueprint.id,
            kind="deck",
            spec_target_id=deck_row.id,
            projection_hash="h" * 64,
            approved_blueprint_revision=1,
            context_snapshot_hash="c" * 64,
            lineage={},
            idempotency_key=f"seed-{uuid.uuid4().hex[:16]}",
            request_hash="r" * 64,
        )
    )
    await session.flush()
    return deck_row


async def _three_slides(**overrides) -> list[SlideSpec]:
    first = _slide(
        "slide-1",
        type_="title",
        title="1.1 合成集合",
        note="同学们好，今天我们学习集合的概念。",
        **overrides,
    )
    return [
        first,
        _slide("slide-2", title="元素与集合", note=None),
        _slide(
            "slide-3",
            type_="summary",
            title="课堂小结",
            note="回顾三条性质。",
            math_object=MathObjectSpec(kind="line", params={"k": 2, "x0": 0, "y0": 1}),
        ),
    ]


def _publish_url(session_id: str) -> str:
    return f"{BASE}/sessions/{session_id}/publish-classroom"


# ── 成功链路 ────────────────────────────────────────────────────────────────


async def test_publish_success_payload(
    api_app, api8000: Fake8000, db: AsyncSession, monkeypatch
) -> None:
    storage = FakeStorage({"math/fig1.svg": b"<svg/>"})
    monkeypatch.setattr("app.storage.minio.get_storage", lambda: storage)

    await _seed_textbook(db)
    session_row = await _seed_session(db)
    slides = await _three_slides(
        assets=["asset-1"],
        elements=[SlideElement(role="bullets", items=["元素", "集合", "确定性"])],
    )
    deck_row = await _attach_deck(
        db,
        session_row,
        _deck(
            slides=slides,
            assets=[_asset("asset-1", key="math/fig1.svg", size=6)],
            revision=7,
        ),
    )
    await db.commit()
    api8000.set_member(CLASS_A)

    async with _client(api_app) as client:
        resp = await client.post(
            _publish_url(str(session_row.id)),
            json={"class_id": CLASS_A},
            headers=_headers(),
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["code"] == 0
    data = body["data"]
    assert data["courseware_id"] == "cw-8000-1"
    assert data["status"] == "preparing"
    assert data["title"] == "1.1 合成集合"
    assert data["class_id"] == CLASS_A
    assert data["warnings"] == []

    assert len(api8000.courseware_calls) == 1
    payload = api8000.courseware_calls[0]
    assert payload["class_id"] == CLASS_A
    assert payload["deck_id"] == str(deck_row.id)
    assert payload["deck_revision"] == 7
    assert payload["subject"] == "math"
    assert payload["title"] == "1.1 合成集合"
    assert payload["textbook_line"] == "合成出版社 必修第一册 · 1.1 合成集合的概念"
    assert payload["source_refs"] == [
        {"textbook_id": str(SRC), "section_id": str(LESSON), "locator": None}
    ]

    assert len(payload["slides"]) == 3
    first = payload["slides"][0]
    assert first["id"] == "slide-1"
    assert first["type"] == "title"
    assert first["layout_hint"] == "title"
    # 讲稿来源必须是 slide.speaker_note（deck.speaker_notes 恒空/汇总，不是事实源）
    assert first["speaker_note"] == "同学们好，今天我们学习集合的概念。"
    assert first["elements"] == [
        {"role": "bullets", "text": None, "latex": None, "title": None,
         "items": ["元素", "集合", "确定性"], "figure_ref": None}
    ]
    assert payload["slides"][2]["math_object"]["kind"] == "line"

    assert len(first["assets"]) == 1
    asset = first["assets"][0]
    assert asset["id"] == "asset-1"
    assert asset["mime_type"] == "image/svg+xml"
    assert base64.b64decode(asset["data_base64"]) == b"<svg/>"
    assert asset["sha256"] == hashlib.sha256(b"<svg/>").hexdigest()

    # 仅读取被引用的 teacher-assets 对象（无真实 MinIO 调用）
    assert storage.calls == [("teacher-assets", "math/fig1.svg")]
    # 无资产引用的页不崩
    assert payload["slides"][1]["assets"] == []
    assert payload["slides"][2]["assets"] == []


async def test_publish_deck_without_assets_ok(
    api_app, api8000: Fake8000, db: AsyncSession, monkeypatch
) -> None:
    class _ExplodingStorage:
        def get_bytes(self, bucket: str, object_name: str) -> bytes:  # pragma: no cover
            raise AssertionError("无资产 deck 不应读取对象存储")

    monkeypatch.setattr("app.storage.minio.get_storage", lambda: _ExplodingStorage())

    await _seed_textbook(db)
    session_row = await _seed_session(db)
    await _attach_deck(db, session_row, _deck(slides=await _three_slides()))
    await db.commit()
    api8000.set_member(CLASS_A)

    async with _client(api_app) as client:
        resp = await client.post(
            _publish_url(str(session_row.id)), json={"class_id": CLASS_A}, headers=_headers()
        )

    assert resp.status_code == 200, resp.text
    payload = api8000.courseware_calls[0]
    assert [len(s["assets"]) for s in payload["slides"]] == [0, 0, 0]
    assert payload["warnings"] == []


async def test_publish_asset_over_budget_skips_with_warning(
    api_app, api8000: Fake8000, db: AsyncSession, monkeypatch
) -> None:
    storage = FakeStorage({"math/small.svg": b"<svg>small</svg>"})
    monkeypatch.setattr("app.storage.minio.get_storage", lambda: storage)

    await _seed_textbook(db)
    session_row = await _seed_session(db)
    slides = await _three_slides(assets=["asset-small", "asset-huge"])
    await _attach_deck(
        db,
        session_row,
        _deck(
            slides=slides,
            assets=[
                _asset("asset-small", key="math/small.svg", size=15),
                _asset("asset-huge", key="math/huge.png", size=MAX_ASSET + 1, mime="image/png"),
            ],
        ),
    )
    await db.commit()
    api8000.set_member(CLASS_A)

    async with _client(api_app) as client:
        resp = await client.post(
            _publish_url(str(session_row.id)), json={"class_id": CLASS_A}, headers=_headers()
        )

    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert any("4MB" in w for w in data["warnings"])
    payload = api8000.courseware_calls[0]
    assert [a["id"] for a in payload["slides"][0]["assets"]] == ["asset-small"]
    # 超限资产在读取前即被跳过（不浪费 MinIO 往返）
    assert storage.calls == [("teacher-assets", "math/small.svg")]


# ── 失败语义（不返回假成功）────────────────────────────────────────────────


async def test_publish_without_deck_409(
    api_app, api8000: Fake8000, db: AsyncSession
) -> None:
    session_row = await _seed_session(db)
    await db.commit()
    api8000.set_member(CLASS_A)

    async with _client(api_app) as client:
        resp = await client.post(
            _publish_url(str(session_row.id)), json={"class_id": CLASS_A}, headers=_headers()
        )

    assert resp.status_code == 409, resp.text
    assert resp.json()["code"] == 40900
    assert api8000.courseware_calls == []


async def test_publish_cross_class_403_fail_closed(
    api_app, api8000: Fake8000, db: AsyncSession
) -> None:
    await _seed_textbook(db)
    session_row = await _seed_session(db)
    await _attach_deck(db, session_row, _deck(slides=await _three_slides()))
    await db.commit()
    api8000.set_member(CLASS_A)  # 目标班 CLASS_B 无成员关系

    async with _client(api_app) as client:
        resp = await client.post(
            _publish_url(str(session_row.id)), json={"class_id": CLASS_B}, headers=_headers()
        )

    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == 40301
    assert api8000.courseware_calls == []


async def test_publish_foreign_deck_403(
    api_app, api8000: Fake8000, db: AsyncSession
) -> None:
    await _seed_textbook(db)
    other_session = await _seed_session(db, teacher=OTHER_TEACHER, class_id=CLASS_B)
    foreign_deck = await _attach_deck(db, other_session, _deck(slides=await _three_slides()))
    my_session = await _seed_session(db)
    await db.commit()
    api8000.set_member(CLASS_A)

    async with _client(api_app) as client:
        resp = await client.post(
            _publish_url(str(my_session.id)),
            json={"class_id": CLASS_A, "deck_id": str(foreign_deck.id)},
            headers=_headers(),
        )

    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == 40300
    assert api8000.courseware_calls == []


async def test_publish_8000_5xx_is_503(
    api_app, api8000: Fake8000, db: AsyncSession, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.storage.minio.get_storage", lambda: FakeStorage({})
    )
    await _seed_textbook(db)
    session_row = await _seed_session(db)
    await _attach_deck(db, session_row, _deck(slides=await _three_slides()))
    await db.commit()
    api8000.set_member(CLASS_A)
    api8000.courseware_response = (500, {"detail": "boom"})

    async with _client(api_app) as client:
        resp = await client.post(
            _publish_url(str(session_row.id)), json={"class_id": CLASS_A}, headers=_headers()
        )

    assert resp.status_code == 503, resp.text
    assert resp.json()["code"] == 50300


# ── Adapter 契约解析 ────────────────────────────────────────────────────────


async def test_adapter_maps_envelope_code_and_success() -> None:
    transport = FakeApiTransport()
    adapter = Api8000Adapter(transport, InMemoryMappingRegistry())

    transport.handler = lambda m, p, t, j: (
        200,
        {"code": 40301, "message": "非班级成员", "data": None},
    )
    with pytest.raises(ContractMembershipDenied):
        await adapter.create_classroom_courseware(token="t", payload={"class_id": CLASS_A})

    transport.handler = lambda m, p, t, j: (
        500,
        {"code": 50000, "message": "db down", "data": None},
    )
    with pytest.raises(ContractAuthorityUnreachable):
        await adapter.create_classroom_courseware(token="t", payload={"class_id": CLASS_A})

    transport.handler = lambda m, p, t, j: (
        201,
        {"code": 0, "data": {"courseware_id": "cw-x", "status": "preparing"}},
    )
    created = await adapter.create_classroom_courseware(
        token="t", payload={"class_id": CLASS_A}
    )
    assert created == {"courseware_id": "cw-x", "status": "preparing"}
    assert transport.requests[-1][0:2] == ("POST", "/api/teacher/classroom-coursewares")

    transport.handler = lambda m, p, t, j: (200, {"code": 0, "data": {}})
    with pytest.raises(ContractAuthorityUnreachable):
        await adapter.create_classroom_courseware(token="t", payload={"class_id": CLASS_A})
