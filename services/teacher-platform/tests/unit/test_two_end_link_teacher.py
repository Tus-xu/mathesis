"""tel 两端贯通 · B2（8100 教师平台接线）契约测试（PLAN.md §9.1-B2）。

全部 stub 8000 响应（FakeApiTransport），离线 aiosqlite，不依赖真实 8000 进程，
零真实 LLM / 零外网（AGENTS.md 测试铁律）。覆盖六项：

1. ``ApiAssignmentCreate`` DTO 字段集 == 8000 ``CreateAssignmentRequest`` 契约
   （services/api/app/domains/teacher/schemas.py，``_Strict`` extra="forbid"）——T3 契约锁定；
2. ``adapter.create_assignment`` 载荷含 artifact_id、type 由 task_ref.kind 映射、
   422 → 幂等读回路径（tel T3）；
3. orchestrator ``_publish_diagnostic`` 全链（含创建后补 publish 调用）在 Fake 传输下
   落定 ``provision["authority_service"]="api8000"``（orchestrator.py `_publish_execute` 语义）；
4. ``adapter.publish_assignment`` 转发 POST /api/teacher/assignments/{id}/publish（tel T3）；
5. 8000 ``/api/classes/mine`` 携带 students 计数 → ``normalize_authorized_class_items``
   归一保留（A1 消费面；catalog/service.py 只读 import，不改动该域）；
6. ``recalculate_profile`` 成员守卫 fail-closed（复用既有 ContractAuthorityUnreachable /
   MembershipDeniedError 语义；service.py 只读调用，不改动）。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import get_args

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401 —— 注册全部 ORM 模型（tp_* 与 reliability_*）到 Base.metadata
from app.agent.pending_actions import InMemoryPendingStore
from app.db.base import Base
from app.domains.catalog.service import normalize_authorized_class_items
from app.teacher_prep.conversation.orchestrator import ConversationService
from app.teacher_prep.diagnostic.models import DiagnosticTaskRow
from app.teacher_prep.models import TeachingBlueprintRow, TeachingSessionRow
from app.teacher_prep.service import (
    AuthorityUnreachableError,
    MembershipDeniedError,
    TeacherPrepService,
)
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter, FakeApiTransport
from app.teacher_prep_v2.adapters.dto import ApiAssignmentCreate
from app.teacher_prep_v2.adapters.mapping import (
    InMemoryMappingRegistry,
    assignment_mapping_key,
)
from app.teacher_prep_v2.contracts.errors import (
    ContractAuthorityUnreachable,
    ContractIdempotencyConflict,
    ContractMembershipDenied,
)
from app.teacher_prep_v2.contracts.models import TaskAssignment

TEACHER = "11111111-1111-1111-1111-111111111111"
CLASS_A = "22222222-2222-2222-2222-222222222222"
TOKEN = "jwt-teacher-token"
ASSIGNMENT_ID = "c0c0c0c0-c0c0-4c0c-8c0c-c0c0c0c00001"
ARTIFACT_ID = "dededede-dede-4ded-8ded-dedededede01"

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "teacher_prep_v2" / "contracts" / "fixtures"


def _load(kind: str, name: str) -> dict:
    return json.loads((FIXTURES / kind / "valid" / name).read_text(encoding="utf-8"))


def _task_assignment() -> TaskAssignment:
    """fixture TaskAssignment：task_kind=PRE_CLASS_DIAGNOSTIC（经模型校验，dict→TaskRef）。"""
    return TaskAssignment.model_validate(_load("TaskAssignment", "assignment_provisioned.json"))


def _idem(n: int) -> str:
    return f"tel-key-{n:04d}"


def _teacher_membership(class_id: str) -> tuple[int, dict]:
    return (
        200,
        {
            "membership": {
                "class_id": class_id,
                "user_id": TEACHER,
                "member_role": "teacher",
                "confirmed": True,
                "deleted": False,
            }
        },
    )


# ── 1. DTO 契约锁定（tel T3）──────────────────────────────────────────────────


def test_api_assignment_create_dto_matches_8000_contract() -> None:
    """DTO 字段集 == {class_id,title,artifact_id,quiz_items,client_assignment_id,deadline,type}，
    8000 extra=forbid 下多发的 status/creator_id/quiz_id/lesson_id/source_artifact_id 必被删除。
    （tel F1：8000 CreateAssignmentRequest 起接受 artifact_id ∥ quiz_items 二选一。）"""
    fields = set(ApiAssignmentCreate.model_fields)
    assert fields == {
        "class_id",
        "title",
        "artifact_id",
        "quiz_items",
        "client_assignment_id",
        "deadline",
        "type",
    }
    forbidden = {"status", "creator_id", "quiz_id", "lesson_id", "source_artifact_id"}
    assert not (fields & forbidden), f"禁止载荷残留 8000 不认识字段: {fields & forbidden}"
    # type 语义 = D2 扩展：quiz / pre_class / homework（原 quiz|watch pattern 移除）
    assert get_args(ApiAssignmentCreate.model_fields["type"].annotation) == ("quiz", "pre_class", "homework")
    # 旧 type 值 watch 不再被接受（fail-closed，避免语义回落）
    with pytest.raises(ValidationError):
        ApiAssignmentCreate.model_validate(
            {
                "class_id": CLASS_A,
                "title": "学前诊断",
                "client_assignment_id": "tel00001",
                "type": "watch",
            }
        )


# ── 2. adapter.create_assignment：载荷含 artifact_id + 422 幂等读回（tel T3）──


async def test_adapter_create_assignment_sends_artifact_id() -> None:
    assignment = _task_assignment()
    stable_key = assignment_mapping_key(assignment)[:64]
    seen: dict = {}
    registry = InMemoryMappingRegistry()

    def handler(method, path, token, json_body):
        if method == "POST" and path == "/api/teacher/assignments":
            seen["payload"] = json_body
            return (422, {"detail": "extra fields not permitted"})  # 首次：模拟契约拒绝
        if method == "GET" and path == "/api/teacher/assignments":
            # 幂等读回：按 client_assignment_id 检索既有行（8000 list 端点语义）
            return (
                200,
                {
                    "items": [
                        {
                            "id": ASSIGNMENT_ID,
                            "class_id": CLASS_A,
                            "creator_id": TEACHER,
                            "title": "对账读回",
                            "type": "pre_class",
                            "client_assignment_id": stable_key,
                        }
                    ],
                    "total": 1,
                },
            )
        return (404, {"detail": "unknown path"})

    transport = FakeApiTransport()
    transport.handler = handler
    adapter = Api8000Adapter(transport, registry)

    ref = await adapter.create_assignment(
        token=TOKEN, assignment=assignment, creator_id=TEACHER, artifact_id=ARTIFACT_ID
    )
    assert ref.external_id == ASSIGNMENT_ID
    assert (await registry.resolve(ref.scope, ref.key)).external_id == ASSIGNMENT_ID

    payload = seen["payload"]
    assert payload["artifact_id"] == ARTIFACT_ID  # 载荷必含 artifact_id（8000 必填）
    assert payload["type"] == "pre_class"  # task_ref.kind=PRE_CLASS_DIAGNOSTIC → pre_class（tel D2）
    assert payload["class_id"] == str(assignment.class_id) and payload["client_assignment_id"] == stable_key
    assert "creator_id" not in payload and "status" not in payload  # extra=forbid 红线
    assert "quiz_id" not in payload and "lesson_id" not in payload and "source_artifact_id" not in payload
    # model_dump(mode="json")：含 datetime deadline 的载荷必须可 JSON 序列化（httpx json= 前提）
    json.dumps(payload, ensure_ascii=False)
    assert isinstance(payload["deadline"], str)


async def test_adapter_create_assignment_without_artifact_sends_quiz_items() -> None:
    """tel F1：备课诊断直发路径——quiz_items 内联载荷逐字透传（8000 据此物化 Quiz）。"""
    seen: dict = {}
    items = [
        {
            "item_no": 1,
            "q_type": "choice",
            "question_text": "下列哪一组对象构成集合？",
            "options": {"A": "全体女生", "B": "全体高个子"},
            "answer": "A",
            "analysis": "考查集合元素的确定性",
            "kp_code": "集合",
            "difficulty": "easy",
            "max_score": 2,
        }
    ]

    def handler(method, path, token, json_body):
        seen["payload"] = json_body
        return (200, {"id": ASSIGNMENT_ID})

    transport = FakeApiTransport()
    transport.handler = handler
    adapter = Api8000Adapter(transport, InMemoryMappingRegistry())
    ref = await adapter.create_assignment(
        token=TOKEN, assignment=_task_assignment(), creator_id=TEACHER, quiz_items=items
    )
    assert ref.external_id == ASSIGNMENT_ID
    payload = seen["payload"]
    assert payload["artifact_id"] is None
    assert payload["quiz_items"] == items  # 内联题组原样下发（8000 _materialize_quiz_items 消费口径）


async def test_adapter_create_assignment_reconciles_via_real_envelope() -> None:
    """tel F1 对账读回真实 8000 契约：_ok 信封 {code,message,data:{assignments:[…]}}、
    行键 assignment_id（无 id/creator_id）→ 归一后按 client_assignment_id 命中既有行。"""
    assignment = _task_assignment()
    stable_key = assignment_mapping_key(assignment)[:64]
    registry = InMemoryMappingRegistry()

    def handler(method, path, token, json_body):
        if method == "POST":
            return (422, {"code": 40001, "message": "artifact_or_quiz_items_required", "data": {}})
        if method == "GET" and path == "/api/teacher/assignments":
            return (
                200,
                {
                    "code": 0,
                    "message": "ok",
                    "request_id": "req-1",
                    "data": {
                        "assignments": [
                            {
                                "assignment_id": ASSIGNMENT_ID,  # 8000 行键（非 id）
                                "class_id": CLASS_A,
                                "title": "对账读回",
                                "type": "pre_class",
                                "client_assignment_id": stable_key,
                            }
                        ]
                    },
                },
            )
        return (404, {"detail": "unknown path"})

    transport = FakeApiTransport()
    transport.handler = handler
    adapter = Api8000Adapter(transport, registry)

    ref = await adapter.create_assignment(
        token=TOKEN, assignment=assignment, creator_id=TEACHER, quiz_items=[{"item_no": 1}]
    )
    assert ref.external_id == ASSIGNMENT_ID
    assert (await registry.resolve(ref.scope, ref.key)).external_id == ASSIGNMENT_ID


# ── 3. orchestrator 发布全链（含补 publish）落定 api8000 provision ───────────


class TelPublishTransport(FakeApiTransport):
    """8000 模拟（tel F1：镜像真实契约，不再掩盖载荷不匹配）。

    - 一切成功响应走 8000 ``_ok`` 信封 ``{code,message,data,request_id}``；
    - create：校验 artifact_id ∥ quiz_items 二选一（皆缺 → 422 错误信封，同 8000）；
    - list：``data.assignments`` 行键 ``assignment_id``（对账读回真实形态）。
    """

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str]] = []
        self.create_payload: dict | None = None
        self.publish_payload: dict | None = None
        self.assignment_id = ASSIGNMENT_ID

    @staticmethod
    def _ok(data: dict) -> tuple[int, dict]:
        return 200, {"code": 0, "message": "ok", "data": data, "request_id": "req-tel"}

    async def request(self, method, path, *, token, json=None, params=None):
        self.calls.append((method, path))
        if path.startswith("/api/classes/"):
            return _teacher_membership(path.split("/")[-1])
        if path == "/api/teacher/assignments" and method == "POST":
            if (json or {}).get("artifact_id") is None and not (json or {}).get("quiz_items"):
                # 8000 CreateAssignmentRequest model_validator：artifact_id 与 quiz_items 二选一
                return 422, {"code": 40001, "message": "artifact_id 与 quiz_items 必须二选一", "data": {}}
            self.create_payload = json
            return self._ok(
                {
                    "assignment_id": self.assignment_id,
                    "class_id": (json or {}).get("class_id"),
                    "title": (json or {}).get("title"),
                    "type": (json or {}).get("type"),
                    "status": "draft",
                    "replayed": False,
                }
            )
        if path == "/api/teacher/assignments" and method == "GET":
            return self._ok(
                {
                    "assignments": [
                        {
                            "assignment_id": self.assignment_id,
                            "class_id": CLASS_A,
                            "title": "对账读回",
                            "type": "pre_class",
                            "client_assignment_id": params.get("client_assignment_id") if params else None,
                        }
                    ]
                }
            )
        if path.startswith("/api/teacher/assignments/") and path.endswith("/publish") and method == "POST":
            self.publish_payload = json
            return self._ok(
                {"assignment_id": self.assignment_id, "status": "published", "replayed": False}
            )
        return 404, {"detail": "unknown path"}


_DIAG_BODY: dict = {
    "title": "1.1 学前诊断（tel）",
    "description": None,
    "estimated_duration_minutes": 5,
    "knowledge_points": ["集合"],
    "items": [
        {
            "item_no": 1,
            "objective": "prior_knowledge",
            "item_type": "single_choice",
            "question": "下列哪一组对象构成集合？\nA. 高一(3)班全体高个子同学\nB. 高一(3)班全体女生\nC. 很小的数\nD. 接近零的数",
            "answer": "B",
            "explanation": "考查集合元素的确定性",
            "difficulty": "easy",
            "kp": "集合",
            "source_ref": None,
            "max_score": 2,
        },
        {
            "item_no": 2,
            "objective": "confidence",
            "item_type": "confidence_rating",
            "question": "你对「集合中元素的确定性」的把握程度（1–5 分，5 最有把握）",
            "answer": "4",
            "explanation": "信心自评，无客观标答",
            "difficulty": "easy",
            "kp": "集合",
            "source_ref": None,
            "max_score": 2,
        },
    ],
}


async def _seed_publish_world(session_factory) -> tuple[str, str, str]:
    """最小事实行：会话（绑定班级）+ 已确认蓝图 + proposal 诊断任务。"""
    sid, bid, did = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with session_factory() as s:
        s.add(
            TeachingSessionRow(
                id=sid, teacher_id=uuid.UUID(TEACHER), class_id=uuid.UUID(CLASS_A),
                status="active", revision=1, title="tel 发布会话",
            )
        )
        s.add(
            TeachingBlueprintRow(
                id=bid, session_id=sid, status="confirmed", revision=1,
                body={"title": "集合蓝图", "objectives": []},
            )
        )
        s.add(
            DiagnosticTaskRow(
                id=did, session_id=sid, blueprint_id=bid, status="proposal", revision=1,
                title=_DIAG_BODY["title"], estimated_duration_minutes=5, body=dict(_DIAG_BODY),
            )
        )
        await s.commit()
    return str(sid), str(bid), str(did)


async def test_orchestrator_publish_diagnostic_happy_path_with_fixed_adapter(session_factory):
    sid, _bid, did = await _seed_publish_world(session_factory)
    transport = TelPublishTransport()
    svc = ConversationService(
        llm_generate=lambda _p: "",  # publish 链路不触 LLM
        pending_store=InMemoryPendingStore(),
        session_factory=session_factory,
    )
    adapter = Api8000Adapter(transport, InMemoryMappingRegistry())
    async with session_factory() as s:
        out = await svc.run_action(
            s, adapter=adapter, token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="publish_diagnostic", target=did, expected_revision=1,
            idempotency_key=_idem(31), request_hash="0" * 64,
        )
    diag = out.artifact.diagnostic
    assert diag is not None and diag["status"] == "published"
    assert diag["published_assignment_id"] == ASSIGNMENT_ID

    # 8000 侧交互：恰一次 create + 恰一次 publish（draft→published 显式补发）
    assert ("POST", "/api/teacher/assignments") in transport.calls
    assert ("POST", f"/api/teacher/assignments/{ASSIGNMENT_ID}/publish") in transport.calls
    assert transport.create_payload is not None and transport.publish_payload is not None
    assert transport.create_payload["type"] == "pre_class"  # 学前诊断语义落 8000 不丢失
    assert transport.publish_payload["client_request_id"]

    # tel F1：诊断内容以内联题组真实下发（学生端可作答），不再 artifact_id=None 空 content；
    # 题干内嵌 A./B. 选项行 → choice + options；信心自评 → solution（教师终审，不伪自动判分）。
    items = transport.create_payload["quiz_items"]
    assert transport.create_payload["artifact_id"] is None
    assert len(items) == 2
    assert items[0]["q_type"] == "choice"
    assert items[0]["options"] == {
        "A": "高一(3)班全体高个子同学",
        "B": "高一(3)班全体女生",
        "C": "很小的数",
        "D": "接近零的数",
    }
    assert items[1]["q_type"] == "solution"  # confidence_rating 无客观标答 → 主观题
    assert transport.create_payload["title"] == "1.1 学前诊断（tel）"  # 真实标题（非 UUID）

    async with session_factory() as s:
        drow = await s.get(DiagnosticTaskRow, uuid.UUID(did))
        assert drow is not None and drow.status == "published"
        assert drow.provision["authority_service"] == "api8000"  # 权威锚点语义不变
        assert drow.provision["assignment_id"] == ASSIGNMENT_ID


# ── 4. adapter.publish_assignment 转发（tel T3 新增方法）──────────────────────


async def test_adapter_publish_assignment_forwards_post() -> None:
    transport = FakeApiTransport()
    seen: dict = {}

    def ok_handler(method, path, token, json_body):
        seen.update({"method": method, "path": path, "payload": json_body})
        return (200, {"id": ASSIGNMENT_ID, "status": "published"})

    transport.handler = ok_handler
    adapter = Api8000Adapter(transport, InMemoryMappingRegistry())
    out = await adapter.publish_assignment(
        token=TOKEN, assignment_id=ASSIGNMENT_ID,
        client_request_id="diag-publish:abc", idempotency_key="tel-publish-key-0001",
    )
    assert out == ASSIGNMENT_ID
    assert seen["method"] == "POST"
    assert seen["path"] == f"/api/teacher/assignments/{ASSIGNMENT_ID}/publish"
    assert seen["payload"]["client_request_id"] == "diag-publish:abc"
    assert seen["payload"]["idempotency_key"] == "tel-publish-key-0001"

    def status_handler(status: int):
        def handler(method, path, token, json_body):
            return (status, {"detail": "rejected"})

        return handler

    transport.handler = status_handler(422)  # invalid_state → 对账冲突语义
    with pytest.raises(ContractIdempotencyConflict):
        await adapter.publish_assignment(
            token=TOKEN, assignment_id=ASSIGNMENT_ID, client_request_id="diag-publish:abc"
        )
    transport.handler = status_handler(404)  # 资源不可见 → 越权语义（不泄露存在性）
    with pytest.raises(ContractMembershipDenied):
        await adapter.publish_assignment(
            token=TOKEN, assignment_id=ASSIGNMENT_ID, client_request_id="diag-publish:abc"
        )


# ── 5. 8000 /classes/mine students 计数 → 归一保留（A1 消费面）────────────────


def test_normalize_authorized_class_items_consumes_students_count() -> None:
    payload = {
        "items": [
            # snake_case + students 计数：归一保留（教师班级目录/课堂 pickClass 数据面）
            {"class_id": CLASS_A, "name": "高一(3)班", "my_role": "teacher", "confirmed": True, "students": 46},
            # camelCase 变体 + studentCount：同样保留
            {
                "classId": "33333333-3333-3333-3333-333333333333",
                "name": "高一(4)班",
                "memberRole": "owner",
                "confirmed": True,
                "studentCount": 12,
            },
            # 非教学角色 / 未确认 / 缺 students：按既有口径过滤（students 落 0）
            {"class_id": "44444444-4444-4444-4444-444444444444", "name": "旁听班", "my_role": "student", "confirmed": True},
            {"class_id": "55555555-5555-5555-5555-555555555555", "name": "待确认班", "my_role": "teacher", "confirmed": False},
        ]
    }
    items = normalize_authorized_class_items(payload)
    assert [i["class_id"] for i in items] == [CLASS_A, "33333333-3333-3333-3333-333333333333"]
    assert items[0]["students"] == 46
    assert items[1]["students"] == 12
    # data 信封变体（8000 _ok() 惯例）同样消费
    assert normalize_authorized_class_items({"data": payload})[0]["students"] == 46


# ── 6. recalculate_profile 成员守卫 fail-closed（T1 数据前置）─────────────────


async def test_recalculate_profile_requires_membership_fail_closed(db):
    svc = TeacherPrepService()
    kwargs = dict(
        token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        session_id=str(uuid.uuid4()), class_id=CLASS_A,
        idempotency_key=_idem(61), request_hash="0" * 64,
        assignment_id="a1b2c3d4-0001-4000-8000-000000000001",
    )

    # 8000 不可达（5xx）→ 权威不可达 fail-closed，不产出任何降级画像
    transport_5xx = FakeApiTransport()
    transport_5xx.handler = lambda m, p, t, j: (500, {"detail": "simulated outage"})
    with pytest.raises((AuthorityUnreachableError, ContractAuthorityUnreachable)):
        await svc.recalculate_profile(
            db, adapter=Api8000Adapter(transport_5xx, InMemoryMappingRegistry()), **kwargs
        )

    # 非本班成员（404）→ 成员拒绝，绝不放行重算
    transport_404 = FakeApiTransport()
    transport_404.handler = lambda m, p, t, j: (404, {"detail": "member not found"})
    with pytest.raises((MembershipDeniedError, ContractMembershipDenied)):
        await svc.recalculate_profile(
            db, adapter=Api8000Adapter(transport_404, InMemoryMappingRegistry()), **kwargs
        )


# ── 本文件自持 fixtures（离线 aiosqlite，对齐 tests/unit/teacher_prep/conftest 路线）──


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncSession:
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)
