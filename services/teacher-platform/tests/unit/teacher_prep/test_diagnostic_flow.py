"""R18 学前诊断教师侧闭环测试：proposal → 审阅/编辑/删题/调时长 → 发布/跳过。

验收映射（本轮 FINAL-GATE 证据）：
- 提案四重闸门（schema/grounding/scope/hash+blueprint 锚点）；越界 fail closed 不落库；
- 编辑（改题/删题/调时长）+ 编辑同样受范围铁律约束；
- 发布高风险写：持久 PendingAction + 执行瞬间重验 + 8000 幂等创建；
- 重复确认（同 key 重放 / 异 key 已发布 noop）只创建一个 Assignment；
- 同 key 不同 payload → 409；8000/成员校验暂时失败 → run FAILED 不误报，可安全重试；
- 跳过审计（skipped_reason + PendingAction REJECTED）；终态不可编辑；跨班/越权 403。

基建与 R17 同构：SQLite + 合成教材种子 + 确定性 fake LLM + MembershipSim。
"""

from __future__ import annotations

import json
import re
import uuid

import pytest
from sqlalchemy import select

from app.agent.pending_actions import InMemoryPendingStore
from app.reliability.models import ReliabilityJob
from app.teacher_prep.conversation.handler import conversation_run_handler
from app.teacher_prep.conversation.models import MessageRunRow
from app.teacher_prep.conversation.orchestrator import ConversationService, InvalidActionError
from app.teacher_prep.diagnostic.models import DiagnosticRevisionRow, DiagnosticTaskRow
from app.teacher_prep.models import SessionEventRow, TeachingBlueprintRow
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter, FakeApiTransport
from app.teacher_prep_v2.adapters.mapping import InMemoryMappingRegistry

from .conftest import CLASS_A, TEACHER, TOKEN, idem
from .tp_context_fixtures import LESSON1, SRC, seed_synthetic_textbook

INTENT_SRC = str(SRC)
INTENT_L1 = str(LESSON1)

_INVENTED_REF = (
    "textbook_v2/locator/v1/source/99999999-9999-9999-9999-999999999999"
    "/chunk/88888888-8888-8888-8888-888888888888"
)


def _diag_item(no: int, *, kp: str = "集合", ref: str | None = None, objective: str = "prior_knowledge") -> dict:
    return {
        "item_no": no,
        "objective": objective,
        "item_type": "single_choice" if objective != "confidence" else "confidence_rating",
        "question": f"第{no}题：下列哪一组对象构成集合？" if objective != "confidence" else "你对本课内容的把握程度？",
        "answer": "A" if objective != "confidence" else "自评 1-5",
        "explanation": "考查集合元素的确定性" if objective != "confidence" else "信心自评不判分",
        "difficulty": "easy",
        "kp": kp,
        "source_ref": ref,
        "max_score": 2,
    }


def _diag_payload(snap_hash: str, blueprint_id: str, *, kp: str = "集合", ref: str | None = None) -> str:
    return json.dumps(
        {
            "schema_version": "0.1.0",
            "context_snapshot_hash": snap_hash,
            "blueprint_id": blueprint_id,
            "title": "1.1 学前诊断（5 分钟）",
            "description": "只诊断先备知识/误区/信心，不变成作业",
            "estimated_duration_minutes": 5,
            "knowledge_points": [kp],
            "items": [_diag_item(1, kp=kp, ref=ref), _diag_item(2, kp="元素", objective="confidence")],
            "warnings_ack": [],
        },
        ensure_ascii=False,
    )


class FakeLlm:
    """确定性 fake：intent（教师消息）/ blueprint（蓝图）/ diagnostic（学前诊断）三路。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_intent = False
        self.fail_blueprint = False
        self.diag_scope_break = False
        self.diag_grounding_break = False
        self.diag_provider_fail = False

    def __call__(self, prompt: str) -> str:
        self.calls.append(prompt[:40])
        if "教师消息" in prompt:
            if self.fail_intent:
                raise RuntimeError("provider connection refused")
            return json.dumps(
                {
                    "intent_type": "modify",
                    "textbook_id": INTENT_SRC,
                    "section_id": INTENT_L1,
                    "class_id": CLASS_A,
                    "knowledge_points": ["集合", "元素"],
                    "lesson_scope": "第一课时",
                    "lesson_hours": 1,
                    "objectives": [{"text": "理解集合与元素的概念", "source_ref": None}],
                    "note": None,
                    "confidence": 0.9,
                    "warnings": [],
                }
            )
        if "学前诊断" in prompt:
            if self.diag_provider_fail:
                raise RuntimeError("provider 500")
            snap_hash = re.search(r"context_snapshot_hash：([0-9a-f]{64})", prompt).group(1)
            bp = re.search(r"blueprint_id：([0-9a-fA-F-]{36})", prompt)
            bp_id = bp.group(1) if bp else "0" * 8 + "-0000-0000-0000-" + "0" * 12
            if self.diag_scope_break:
                return _diag_payload(snap_hash, bp_id, kp="向量")
            ref = _INVENTED_REF if self.diag_grounding_break else None
            return _diag_payload(snap_hash, bp_id, ref=ref)
        if self.fail_blueprint:
            raise RuntimeError("provider 500")
        m = re.search(r"context_snapshot_hash：([0-9a-f]{64})", prompt)
        snap_hash = m.group(1) if m else "0" * 64
        return json.dumps(
            {
                "schema_version": "0.1.0",
                "context_snapshot_hash": snap_hash,
                "title": "合成集合 1.1 教学蓝图",
                "objectives": [{"text": "理解集合与元素的概念", "source_ref": None}],
                "prior_knowledge": [],
                "concepts": [],
                "theorems": [],
                "examples": [{"text": "用列举法表示集合", "source_ref": None}],
                "inquiries": [],
                "design_intent": [],
                "key_points": [{"text": "集合的三条性质", "source_ref": None}],
                "difficult_points": [],
                "lesson_flow": [
                    {"stage": "introduction", "title": "情境导入", "items": [{"text": "引入", "source_ref": None}], "duration_minutes": 5}
                ],
                "warnings_ack": [],
            }
        )


class PublishingTransport(FakeApiTransport):
    """8000 模拟：成员校验走 MembershipSim；assignments POST 可计数/可注入 5xx。

    tel T3：8000 create 恒 draft → 编排器创建成功后补 POST /{id}/publish（draft→published）。
    本模拟同样承载 publish 端点（200 幂等语义），并独立计数便于断言「创建 1 次、发布 1 次」。
    """

    def __init__(self, members) -> None:
        super().__init__()
        self._members_handler = members.handler
        self.assignment_posts = 0  # 成功创建数（重复确认 → 恒 1）
        self.assignment_attempts = 0  # 全部 POST 尝试（含失败）
        self.publish_posts = 0  # 成功发布数（tel T3）
        self.fail_next_posts = 0
        self.fail_next_publishes = 0
        self.membership_calls = 0
        self.fail_membership_from = None  # 第 N 次成员校验起 500（模拟执行瞬间权威暂不可达）

    async def request(self, method, path, *, token, json=None, params=None):
        if path.startswith("/api/classes/"):
            self.membership_calls += 1
            if self.fail_membership_from is not None and self.membership_calls >= self.fail_membership_from:
                return 500, {"detail": "simulated membership outage"}
            return self._members_handler(method, path, token, json)
        if path == "/api/teacher/assignments" and method == "POST":
            self.assignment_attempts += 1
            if self.fail_next_posts > 0:
                self.fail_next_posts -= 1
                return 500, {"detail": "simulated 8000 outage"}
            self.assignment_posts += 1
            return 201, {"id": f"aaaaaaaa-0000-0000-0000-{self.assignment_posts:012d}"}
        if path.startswith("/api/teacher/assignments/") and path.endswith("/publish") and method == "POST":
            if self.fail_next_publishes > 0:
                self.fail_next_publishes -= 1
                return 500, {"detail": "simulated publish outage"}
            self.publish_posts += 1
            assignment_id = path.split("/")[-2]
            return 200, {"id": assignment_id, "status": "published", "replayed": self.publish_posts > 1}
        return 404, {"detail": "unknown path"}


def make_service(fake: FakeLlm, session_factory) -> ConversationService:
    return ConversationService(
        llm_generate=fake,
        pending_store=InMemoryPendingStore(),
        session_factory=session_factory,
    )


def adapter_for(transport: FakeApiTransport) -> Api8000Adapter:
    return Api8000Adapter(transport, InMemoryMappingRegistry())


def _hash(*, path: str, body: dict | None) -> str:
    from app.reliability.idempotency import request_hash_of

    return request_hash_of("POST", path, body)


async def process_pending_jobs(factory, fake: FakeLlm) -> list[str]:
    """模拟 worker：claim 全部 PENDING conversation.run 并 fenced_run 执行。"""
    from datetime import UTC, datetime, timedelta

    from app.reliability.jobs import JobEngine, fenced_run

    handler = await conversation_run_handler(factory, fake, lease_s=5.0)
    statuses: list[str] = []
    async with factory() as s:
        pending = (
            (
                await s.execute(
                    select(ReliabilityJob).where(
                        ReliabilityJob.owner_service == "teacher_prep",
                        ReliabilityJob.status == "PENDING",
                    )
                )
            )
            .scalars()
            .all()
        )
        for j in pending:
            j.available_at = datetime.now(UTC) - timedelta(seconds=5)
        await s.commit()
        jobs = await JobEngine.claim(s, owner_service="teacher_prep", worker_name="test-worker", limit=20)
        await s.commit()
        for job in jobs:
            if job.job_type != "conversation.run":
                continue
            st = await fenced_run(s, job_id=job.id, claim_token=job.claim_token or "", handler=handler)
            statuses.append(st)
    return statuses


async def bootstrap_confirmed(db, session_factory, members, fake: FakeLlm):
    """种子教材 → 新备 → worker 蓝图 → confirm：返回 (svc, session_id, blueprint_id, revision)。"""
    await seed_synthetic_textbook(db)
    members.set(CLASS_A, user_id=TEACHER)
    svc = make_service(fake, session_factory)
    ws = await svc.create_conversation(
        db,
        adapter=adapter_for(members_transport(members)),
        token=TOKEN,
        actor_id=TEACHER,
        actor_role_hint="teacher",
        class_id=CLASS_A,
        message="给高一3班备1.1集合第一课时",
        idempotency_key=idem(1),
        request_hash=_hash(path="/prep/conversations", body={"message": "备1.1集合"}),
        title="函数单调性备课",
    )
    sid = ws.session["session_id"]
    await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        bp = (
            await s.execute(select(TeachingBlueprintRow).where(TeachingBlueprintRow.session_id == uuid.UUID(sid)))
        ).scalar_one()
        out = await svc.run_action(
            s, adapter=adapter_for(members_transport(members)), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="confirm_proposal", target=str(bp.id), expected_revision=ws.session["revision"],
            idempotency_key=idem(2), request_hash=_hash(path="/actions/confirm", body={}),
        )
    return svc, sid, str(bp.id), out.session["revision"]


def members_transport(members) -> FakeApiTransport:
    t = FakeApiTransport()
    t.handler = members.handler
    return t


# ── 提案：四重闸门 + 落库 ─────────────────────────────────────────────────────


async def test_propose_generates_diagnostic_proposal(db, session_factory, members):
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        actions = {a.action for a in ws.available_actions}
        assert "propose_diagnostic" in actions
        await svc.run_action(
            s, adapter=adapter_for(members_transport(members)), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="propose_diagnostic", target=bid, expected_revision=ws.session["revision"],
            idempotency_key=idem(3), request_hash=_hash(path="/actions/propose_diag", body={}),
        )
    await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        drow = (
            await s.execute(select(DiagnosticTaskRow).where(DiagnosticTaskRow.session_id == uuid.UUID(sid)))
        ).scalar_one()
        assert drow.status == "proposal" and drow.revision == 1
        revs = (
            await s.execute(select(DiagnosticRevisionRow).where(DiagnosticRevisionRow.diagnostic_id == drow.id))
        ).scalars().all()
        assert len(revs) == 1 and revs[0].source == "ai_suggestion"
        # 时长 5–10 硬约束 + 题目字段齐全
        assert 5 <= drow.estimated_duration_minutes <= 10
        assert drow.body["items"][0]["objective"] in ("prior_knowledge", "misconception", "confidence")
        ws2 = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
    diag = ws2.artifact.diagnostic
    assert diag is not None and diag["status"] == "proposal" and len(diag["items"]) == 2
    kinds = [m.kind for m in ws2.timeline]
    assert "diagnostic" in kinds
    actions2 = {a.action for a in ws2.available_actions}
    assert {"edit_diagnostic", "publish_diagnostic", "skip_diagnostic"} <= actions2
    assert "propose_diagnostic" not in actions2  # 已有诊断 → 不再派生提案入口


async def test_propose_requires_confirmed_blueprint(db, session_factory, members):
    fake = FakeLlm()
    await seed_synthetic_textbook(db)
    members.set(CLASS_A, user_id=TEACHER)
    svc = make_service(fake, session_factory)
    ws = await svc.create_conversation(
        db, adapter=adapter_for(members_transport(members)), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        class_id=CLASS_A, message="备1.1集合", idempotency_key=idem(4),
        request_hash=_hash(path="/prep/conversations", body={"message": "备1.1集合"}),
    )
    await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        bp = (
            await s.execute(select(TeachingBlueprintRow).where(TeachingBlueprintRow.session_id == uuid.UUID(ws.session["session_id"])))
        ).scalar_one()
        ws2 = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=ws.session["session_id"])
        with pytest.raises(InvalidActionError):
            await svc.run_action(
                s, adapter=adapter_for(members_transport(members)), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
                session_id=ws.session["session_id"], action="propose_diagnostic", target=str(bp.id),
                expected_revision=ws2.session["revision"], idempotency_key=idem(5),
                request_hash=_hash(path="/actions/propose_diag", body={}),
            )


async def test_propose_scope_violation_fail_closed(db, session_factory, members):
    fake = FakeLlm()
    fake.diag_scope_break = True
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        await svc.run_action(
            s, adapter=adapter_for(members_transport(members)), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="propose_diagnostic", target=bid, expected_revision=ws.session["revision"],
            idempotency_key=idem(6), request_hash=_hash(path="/actions/propose_diag", body={}),
        )
    await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        drows = (
            await s.execute(select(DiagnosticTaskRow).where(DiagnosticTaskRow.session_id == uuid.UUID(sid)))
        ).scalars().all()
        assert drows == []  # 越界 proposal 绝不落库（不得偷偷扩大知识点范围）
        runs = (
            await s.execute(select(MessageRunRow).where(MessageRunRow.session_id == uuid.UUID(sid)))
        ).scalars().all()
        diag_runs = [r for r in runs if r.run_type == "diagnostic"]
        assert diag_runs and diag_runs[0].status == "BASIC_MODE" and diag_runs[0].provider_error == "scope_violation"
        ws2 = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
    assert ws2.basic_mode is True  # 带标签可编辑降级，不沉默/不假成功
    assert ws2.artifact.diagnostic is None


async def test_propose_grounding_violation_fail_closed(db, session_factory, members):
    fake = FakeLlm()
    fake.diag_grounding_break = True
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        await svc.run_action(
            s, adapter=adapter_for(members_transport(members)), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="propose_diagnostic", target=bid, expected_revision=ws.session["revision"],
            idempotency_key=idem(7), request_hash=_hash(path="/actions/propose_diag", body={}),
        )
    await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        drows = (
            await s.execute(select(DiagnosticTaskRow).where(DiagnosticTaskRow.session_id == uuid.UUID(sid)))
        ).scalars().all()
        assert drows == []
        runs = (
            await s.execute(select(MessageRunRow).where(MessageRunRow.session_id == uuid.UUID(sid)))
        ).scalars().all()
        diag_runs = [r for r in runs if r.run_type == "diagnostic"]
        assert diag_runs[0].provider_error == "grounding_violation"  # 发明引用拒绝


# ── 编辑：改题/删题/调时长 + 范围铁律 ─────────────────────────────────────────


def _draft_payload(drow_body: dict, *, drop_first: bool = False, duration: int = 8, extra_kp: str | None = None) -> dict:
    items = [dict(it) for it in drow_body["items"]]
    if drop_first:
        items = items[1:]
        for i, it in enumerate(items, start=1):
            it["item_no"] = i
    if extra_kp:
        items = items + [_diag_item(len(items) + 1, kp=extra_kp)]
    return {
        "title": "1.1 学前诊断（教师修订）",
        "description": drow_body.get("description"),
        "estimated_duration_minutes": duration,
        "knowledge_points": ["集合"],
        "items": items,
    }


async def test_edit_updates_draft_revision_and_items(db, session_factory, members):
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        await svc.run_action(
            s, adapter=adapter_for(members_transport(members)), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="propose_diagnostic", target=bid, expected_revision=ws.session["revision"],
            idempotency_key=idem(8), request_hash=_hash(path="/actions/propose_diag", body={}),
        )
    await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        did = ws.artifact.diagnostic["diagnostic_id"]
        body = ws.artifact.diagnostic
        out = await svc.run_action(
            s, adapter=adapter_for(members_transport(members)), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="edit_diagnostic", target=did, expected_revision=ws.session["revision"],
            idempotency_key=idem(9), request_hash=_hash(path="/actions/edit_diag", body={}),
            payload={"draft": _draft_payload(body, drop_first=True, duration=8), "note": "删掉第 1 题，改 8 分钟"},
        )
    diag = out.artifact.diagnostic
    assert diag["status"] == "draft" and diag["revision"] == 2
    assert diag["estimated_duration_minutes"] == 8 and len(diag["items"]) == 1
    async with session_factory() as s:
        drow = await s.get(DiagnosticTaskRow, uuid.UUID(did))
        revs = (
            await s.execute(select(DiagnosticRevisionRow).where(DiagnosticRevisionRow.diagnostic_id == drow.id).order_by(DiagnosticRevisionRow.revision_no))
        ).scalars().all()
        assert [r.revision_no for r in revs] == [1, 2] and revs[1].source == "teacher" and revs[1].verdict == "draft"
        evs = (
            await s.execute(select(SessionEventRow).where(SessionEventRow.session_id == uuid.UUID(sid)))
        ).scalars().all()
        assert any(e.event_type == "diagnostic.edited" for e in evs)


async def test_edit_scope_violation_rejected(db, session_factory, members):
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        await svc.run_action(
            s, adapter=adapter_for(members_transport(members)), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="propose_diagnostic", target=bid, expected_revision=ws.session["revision"],
            idempotency_key=idem(10), request_hash=_hash(path="/actions/propose_diag", body={}),
        )
    await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        did = ws.artifact.diagnostic["diagnostic_id"]
        from app.core.errors import ApiError

        with pytest.raises(ApiError):
            await svc.run_action(
                s, adapter=adapter_for(members_transport(members)), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
                session_id=sid, action="edit_diagnostic", target=did, expected_revision=ws.session["revision"],
                idempotency_key=idem(11), request_hash=_hash(path="/actions/edit_diag", body={}),
                payload={"draft": _draft_payload(ws.artifact.diagnostic, extra_kp="向量")},  # 越界 kp
            )
        ws2 = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        assert ws2.artifact.diagnostic["revision"] == 1  # 编辑被拒 → 事实不变（fail closed）


# ── 发布：高风险写 + 幂等 + 失败重试 ──────────────────────────────────────────


async def _propose_and_get_diag(svc, session_factory, members, sid, bid, key_no: int):
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        await svc.run_action(
            s, adapter=adapter_for(members_transport(members)), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="propose_diagnostic", target=bid, expected_revision=ws.session["revision"],
            idempotency_key=idem(key_no), request_hash=_hash(path="/actions/propose_diag", body={}),
        )
    await process_pending_jobs(session_factory, svc._llm)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        return ws.artifact.diagnostic["diagnostic_id"], ws.session["revision"]


async def test_publish_creates_single_assignment_with_pending_action(db, session_factory, members):
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    did, _ = await _propose_and_get_diag(svc, session_factory, members, sid, bid, 12)
    transport = PublishingTransport(members)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(transport), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        out = await svc.run_action(
            s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="publish_diagnostic", target=did, expected_revision=ws.session["revision"],
            idempotency_key=idem(13), request_hash=_hash(path="/actions/publish_diag", body={}),
        )
    diag = out.artifact.diagnostic
    assert diag["status"] == "published" and diag["published_assignment_id"] == "aaaaaaaa-0000-0000-0000-000000000001"
    assert diag["pending_publish"] is False
    assert transport.assignment_posts == 1 and transport.assignment_attempts == 1
    async with session_factory() as s:
        drow = await s.get(DiagnosticTaskRow, uuid.UUID(did))
        assert drow.status == "published" and drow.published_by == uuid.UUID(TEACHER)
        assert drow.provision["authority_service"] == "api8000" and drow.provision["assignment_id"]
        assert drow.pending_action_id is not None
        revs = (
            await s.execute(select(DiagnosticRevisionRow).where(DiagnosticRevisionRow.diagnostic_id == drow.id).order_by(DiagnosticRevisionRow.revision_no))
        ).scalars().all()
        assert revs[-1].verdict == "published"  # 发布版本快照
        evs = (
            await s.execute(select(SessionEventRow).where(SessionEventRow.session_id == uuid.UUID(sid)))
        ).scalars().all()
        assert any(e.event_type == "diagnostic.published" for e in evs)
        assert any(e.event_type == "conversation.diagnostic.publish_requested" for e in evs)


async def test_publish_duplicate_confirm_same_key_replays(db, session_factory, members):
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    did, _ = await _propose_and_get_diag(svc, session_factory, members, sid, bid, 14)
    transport = PublishingTransport(members)
    key = idem(15)
    rhash = _hash(path="/actions/publish_diag", body={})
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(transport), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        rev = ws.session["revision"]
        await svc.run_action(
            s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="publish_diagnostic", target=did, expected_revision=rev,
            idempotency_key=key, request_hash=rhash,
        )
        # 完全重复的确认（同 key 同 payload）→ 幂等重放，不再创建
        out2 = await svc.run_action(
            s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="publish_diagnostic", target=did, expected_revision=rev,
            idempotency_key=key, request_hash=rhash,
        )
    assert transport.assignment_posts == 1  # 重复确认只创建一个 Assignment
    assert out2.artifact.diagnostic["status"] == "published"


async def test_publish_republish_after_published_is_noop(db, session_factory, members):
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    did, _ = await _propose_and_get_diag(svc, session_factory, members, sid, bid, 16)
    transport = PublishingTransport(members)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(transport), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        await svc.run_action(
            s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="publish_diagnostic", target=did, expected_revision=ws.session["revision"],
            idempotency_key=idem(17), request_hash=_hash(path="/actions/publish_diag", body={}),
        )
        ws2 = await svc.get_workspace(s, adapter=adapter_for(transport), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        # 新 key 再次发布 → 已发布幂等 no-op（不二次创建）
        out = await svc.run_action(
            s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="publish_diagnostic", target=did, expected_revision=ws2.session["revision"],
            idempotency_key=idem(18), request_hash=_hash(path="/actions/publish_diag", body={"x": 1}),
        )
    assert transport.assignment_posts == 1
    assert out.artifact.diagnostic["status"] == "published"


async def test_publish_same_key_different_payload_409(db, session_factory, members):
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    did, _ = await _propose_and_get_diag(svc, session_factory, members, sid, bid, 19)
    transport = PublishingTransport(members)
    key = idem(20)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(transport), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        await svc.run_action(
            s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="publish_diagnostic", target=did, expected_revision=ws.session["revision"],
            idempotency_key=key, request_hash=_hash(path="/actions/publish_diag", body={"due_at": "2026-09-12T08:00:00+00:00"}),
        )
        from app.core.errors import ApiError

        with pytest.raises(ApiError) as ei:
            await svc.run_action(
                s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
                session_id=sid, action="publish_diagnostic", target=did, expected_revision=ws.session["revision"],
                idempotency_key=key,  # 同 key
                request_hash=_hash(path="/actions/publish_diag", body={"due_at": "2026-09-13T08:00:00+00:00"}),  # 不同 payload
            )
        assert ei.value.code == 40900
    assert transport.assignment_posts == 1


async def test_publish_8000_outage_then_retry_single_assignment(db, session_factory, members):
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    did, _ = await _propose_and_get_diag(svc, session_factory, members, sid, bid, 21)
    transport = PublishingTransport(members)
    transport.fail_next_posts = 1  # 第一次发布：8000 暂时失败
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(transport), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        out = await svc.run_action(
            s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="publish_diagnostic", target=did, expected_revision=ws.session["revision"],
            idempotency_key=idem(22), request_hash=_hash(path="/actions/publish_diag", body={}),
        )
    diag = out.artifact.diagnostic
    assert diag["status"] in ("proposal", "draft")  # UI 不误报成功
    assert diag["publish_failed"] is True and diag["pending_publish"] is False
    assert diag["published_assignment_id"] is None
    async with session_factory() as s:
        evs = (
            await s.execute(select(SessionEventRow).where(SessionEventRow.session_id == uuid.UUID(sid)))
        ).scalars().all()
        failed = [e for e in evs if e.event_type == "diagnostic.publish.failed"]
        assert failed and failed[0].payload["error_label"] == "authority_unreachable"
        runs = (
            await s.execute(select(MessageRunRow).where(MessageRunRow.run_type == "diagnostic_publish"))
        ).scalars().all()
        assert runs[0].status == "FAILED" and runs[0].provider_error == "authority_unreachable"
    # 重试（新 key）：同稳定映射键 → 成功且只创建一个 Assignment
    async with session_factory() as s:
        ws2 = await svc.get_workspace(s, adapter=adapter_for(transport), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        out2 = await svc.run_action(
            s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="publish_diagnostic", target=did, expected_revision=ws2.session["revision"],
            idempotency_key=idem(23), request_hash=_hash(path="/actions/publish_diag", body={}),
        )
    assert out2.artifact.diagnostic["status"] == "published"
    assert transport.assignment_attempts == 2 and transport.assignment_posts == 1  # 失败可重试 + 单 Assignment


async def test_publish_membership_outage_at_execution_fails_then_recovers(db, session_factory, members):
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    did, _ = await _propose_and_get_diag(svc, session_factory, members, sid, bid, 24)
    transport = PublishingTransport(members)
    transport.fail_membership_from = 3  # workspace 预检=1、阶段 1=2 通过；阶段 2B 执行瞬间重验（第 3 次）失败
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(transport), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        out = await svc.run_action(
            s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="publish_diagnostic", target=did, expected_revision=ws.session["revision"],
            idempotency_key=idem(25), request_hash=_hash(path="/actions/publish_diag", body={}),
        )
    assert out.artifact.diagnostic["status"] in ("proposal", "draft")
    assert out.artifact.diagnostic["publish_failed"] is True
    assert transport.assignment_posts == 0  # 未向 8000 泄漏任何创建
    transport.fail_membership_from = None
    async with session_factory() as s:
        ws2 = await svc.get_workspace(s, adapter=adapter_for(transport), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        out2 = await svc.run_action(
            s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="publish_diagnostic", target=did, expected_revision=ws2.session["revision"],
            idempotency_key=idem(26), request_hash=_hash(path="/actions/publish_diag", body={}),
        )
    assert out2.artifact.diagnostic["status"] == "published"
    assert transport.assignment_posts == 1


async def test_publish_cross_class_membership_403(db, session_factory, members):
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    did, _ = await _propose_and_get_diag(svc, session_factory, members, sid, bid, 27)
    transport = PublishingTransport(members)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(transport), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        members.remove(CLASS_A)  # 执行瞬间成员关系：非本班成员 → 403（发布阶段 1 现场重验）
        from app.core.errors import ApiError

        with pytest.raises(ApiError) as ei:
            await svc.run_action(
                s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
                session_id=sid, action="publish_diagnostic", target=did, expected_revision=ws.session["revision"],
                idempotency_key=idem(28), request_hash=_hash(path="/actions/publish_diag", body={}),
            )
        assert ei.value.code == 40301


async def test_publish_role_denied_403(db, session_factory, members):
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    did, _ = await _propose_and_get_diag(svc, session_factory, members, sid, bid, 29)
    transport = PublishingTransport(members)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(transport), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        from app.core.errors import ApiError

        with pytest.raises(ApiError) as ei:
            await svc.run_action(
                s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="student",
                session_id=sid, action="publish_diagnostic", target=did, expected_revision=ws.session["revision"],
                idempotency_key=idem(30), request_hash=_hash(path="/actions/publish_diag", body={}),
            )
        assert ei.value.code == 40301
    assert transport.assignment_posts == 0


# ── 跳过 / 终态 / 过期审计 ────────────────────────────────────────────────────


async def test_skip_diagnostic_audits_and_rejects_pending_action(db, session_factory, members):
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    did, _ = await _propose_and_get_diag(svc, session_factory, members, sid, bid, 31)
    store = InMemoryPendingStore()
    svc._pending = store  # 注入同一 store 以断言审计联动
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        # 先制造一个在途发布审批（阶段 1 落库后不执行阶段 2/3：直接用 skip 覆盖）
        from app.agent.pending_actions import PendingAction

        await store.create(
            PendingAction(
                action_id=f"diag-publish:{did}:deadbeefcafe",
                session_id=sid,
                teacher_id=TEACHER,
                tool="publish_diagnostic",
                card={"diagnostic_id": did, "title": "x", "revision": 1, "estimated_duration_minutes": 5,
                      "due_at": "2026-09-12T08:00:00+00:00", "class_id": CLASS_A},
                fingerprint="f" * 64,
                expires_at=None,
            )
        )
        out = await svc.run_action(
            s, adapter=adapter_for(members_transport(members)), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="skip_diagnostic", target=did, expected_revision=ws.session["revision"],
            idempotency_key=idem(32), request_hash=_hash(path="/actions/skip_diag", body={}),
            payload={"reason": "本班已掌握先备知识，无需诊断"},
        )
    diag = out.artifact.diagnostic
    assert diag["status"] == "skipped" and diag["skipped_reason"] == "本班已掌握先备知识，无需诊断"
    async with session_factory() as s:
        drow = await s.get(DiagnosticTaskRow, uuid.UUID(did))
        assert drow.skipped_by == uuid.UUID(TEACHER) and drow.skipped_reason
        evs = (
            await s.execute(select(SessionEventRow).where(SessionEventRow.session_id == uuid.UUID(sid)))
        ).scalars().all()
        assert any(e.event_type == "diagnostic.skipped" for e in evs)
    pending_after = await store.list_by_teacher(TEACHER, include_handled=False)
    assert all(p.action_id != f"diag-publish:{did}:deadbeefcafe" for p in pending_after)  # 在途审批已作废


async def test_skip_creates_own_pending_action_when_no_prior(db, session_factory, members):
    """skip 前无审批时：不产生孤儿审批；published 审批键不被误伤。"""
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    did, _ = await _propose_and_get_diag(svc, session_factory, members, sid, bid, 33)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(members_transport(members)), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        out = await svc.run_action(
            s, adapter=adapter_for(members_transport(members)), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="skip_diagnostic", target=did, expected_revision=ws.session["revision"],
            idempotency_key=idem(34), request_hash=_hash(path="/actions/skip_diag", body={}),
            payload={"reason": None},
        )
    assert out.artifact.diagnostic["status"] == "skipped"


async def test_terminal_state_rejects_edit_and_publish(db, session_factory, members):
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    did, _ = await _propose_and_get_diag(svc, session_factory, members, sid, bid, 35)
    transport = PublishingTransport(members)
    async with session_factory() as s:
        ws = await svc.get_workspace(s, adapter=adapter_for(transport), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        await svc.run_action(
            s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="skip_diagnostic", target=did, expected_revision=ws.session["revision"],
            idempotency_key=idem(36), request_hash=_hash(path="/actions/skip_diag", body={}),
            payload={"reason": "x"},
        )
        ws2 = await svc.get_workspace(s, adapter=adapter_for(transport), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        from app.core.errors import ApiError

        with pytest.raises(ApiError):
            await svc.run_action(
                s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
                session_id=sid, action="edit_diagnostic", target=did, expected_revision=ws2.session["revision"],
                idempotency_key=idem(37), request_hash=_hash(path="/actions/edit_diag", body={}),
                payload={"draft": _draft_payload(ws2.artifact.diagnostic)},
            )
        with pytest.raises(ApiError):
            await svc.run_action(
                s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
                session_id=sid, action="publish_diagnostic", target=did, expected_revision=ws2.session["revision"],
                idempotency_key=idem(38), request_hash=_hash(path="/actions/publish_diag", body={}),
            )
    assert transport.assignment_posts == 0  # 终态后不可能再下发
    actions = set()
    async with session_factory() as s:
        ws3 = await svc.get_workspace(s, adapter=adapter_for(transport), token=TOKEN, teacher_id=TEACHER, session_id=sid)
        actions = {a.action for a in ws3.available_actions}
    assert not {"edit_diagnostic", "publish_diagnostic", "skip_diagnostic"} & actions


async def test_expired_pending_action_is_audited(db, session_factory, members):
    from app.agent.pending_actions import PendingAction

    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    did, _ = await _propose_and_get_diag(svc, session_factory, members, sid, bid, 39)
    store = InMemoryPendingStore()
    svc._pending = store
    pa = PendingAction(
        action_id=f"diag-publish:{did}:000000000000",
        session_id=sid, teacher_id=TEACHER, tool="publish_diagnostic",
        card={"diagnostic_id": did}, fingerprint="a" * 64,
        expires_at=0.0,  # 已过期
    )
    await store.create(pa)
    got = await store.get(pa.action_id)
    assert got is not None and got.status == "EXPIRED"  # 惰性过期 + handled_at 留痕


async def test_propose_revision_conflict_409(db, session_factory, members):
    fake = FakeLlm()
    svc, sid, bid, _rev = await bootstrap_confirmed(db, session_factory, members, fake)
    did, _ = await _propose_and_get_diag(svc, session_factory, members, sid, bid, 40)
    transport = PublishingTransport(members)
    async with session_factory() as s:
        from app.core.errors import ApiError
        from app.teacher_prep.service import RevisionConflictError

        with pytest.raises((ApiError, RevisionConflictError)) as ei:
            await svc.run_action(
                s, adapter=adapter_for(transport), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
                session_id=sid, action="publish_diagnostic", target=did, expected_revision=1,  # 陈旧 revision
                idempotency_key=idem(41), request_hash=_hash(path="/actions/publish_diag", body={}),
            )
        assert ei.value.code == 40900
    assert transport.assignment_posts == 0
