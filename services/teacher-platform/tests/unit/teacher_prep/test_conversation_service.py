"""R17 ConversationService 编排测试：新备/续备/自然语言修改/confirm/reject/retry/
cancel/basic mode/revision 冲突/幂等/跨日/双标签/重启/无重复写。

基建：teacher_prep conftest（SQLite）+ 合成教材种子 + 确定性 fake LLM。
"""

from __future__ import annotations

import json
import re

import pytest
from sqlalchemy import select

from app.reliability.jobs import JobEngine, fenced_run
from app.reliability.models import ReliabilityJob
from app.teacher_prep.conversation.handler import conversation_run_handler
from app.teacher_prep.conversation.models import (
    ContextSnapshotRow,
    ConversationMessageRow,
    MessageRunRow,
)
from app.teacher_prep.conversation.orchestrator import (
    ConversationService,
)
from app.teacher_prep.conversation.schema import WorkspaceSnapshot
from app.teacher_prep.models import TeachingBlueprintRow, TeachingSessionRow

from .conftest import CLASS_A, TEACHER, TOKEN, idem
from .tp_context_fixtures import LESSON1, LESSON2, SRC, seed_synthetic_textbook

SID = "22222222-2222-2222-2222-222222222222"
CLASS_B = "33333333-3333-3333-3333-333333333333"

INTENT_SRC = str(SRC)
INTENT_L1 = str(LESSON1)


class FakeLlm:
    """确定性 fake：intent 返回固定意图；blueprint 返回固定 proposal（hash 取自 prompt）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_intent: bool = False
        self.fail_blueprint: bool = False
        self.blueprint_timeout: bool = False
        # 蓝图阶段返回非 JSON（复现 proposal_invalid：LLM 输出未过 schema 校验）
        self.invalid_blueprint: bool = False

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
        if self.fail_blueprint:
            raise RuntimeError("provider 500")
        if self.blueprint_timeout:
            raise TimeoutError()
        if self.invalid_blueprint:
            return "这不是 JSON（模型偶发输出未过 schema 校验）"
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


def make_service(fake: FakeLlm | None = None) -> ConversationService:
    return ConversationService(llm_generate=fake or FakeLlm())


async def process_pending_jobs(factory, fake: FakeLlm) -> list[str]:
    """模拟 worker：claim 全部 PENDING conversation.run 并 fenced_run 执行。"""
    from datetime import UTC, datetime, timedelta

    handler = await conversation_run_handler(factory, fake, lease_s=5.0)
    statuses: list[str] = []
    async with factory() as s:
        # SQLite 字符串比较：available_at 微秒尾缀 > 同秒 func.now() → 拨回 5 秒确保到期（R11.5-C 同法）
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
        jobs = await JobEngine.claim(
            s, owner_service="teacher_prep", worker_name="test-worker", limit=20
        )
        await s.commit()
        for job in jobs:
            if job.job_type != "conversation.run":
                continue
            st = await fenced_run(s, job_id=job.id, claim_token=job.claim_token or "", handler=handler)
            statuses.append(st)
    return statuses


def _hash(*, path: str, body: dict | None) -> str:
    from app.reliability.idempotency import request_hash_of

    return request_hash_of("POST", path, body)


async def _new_session(service, db, members, *, message: str, key: str, class_id: str = CLASS_A):
    members.set(class_id, user_id=TEACHER)
    ws = await service.create_conversation(
        db,
        adapter=members_adapter(members),
        token=TOKEN,
        actor_id=TEACHER,
        actor_role_hint="teacher",
        class_id=class_id,
        message=message,
        idempotency_key=key,
        request_hash=_hash(path="/prep/conversations", body={"message": message}),
        title="函数单调性备课",
    )
    return ws


def members_adapter(members):
    from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter, FakeApiTransport
    from app.teacher_prep_v2.adapters.mapping import InMemoryMappingRegistry

    transport = FakeApiTransport()
    transport.handler = members.handler
    return Api8000Adapter(transport, InMemoryMappingRegistry())


# ── 新备：意图 → 上下文卡 → 作业入队 ──────────────────────────────────────────


async def test_new_prep_creates_session_context_and_job(db, session_factory, members):
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="给高一3班备1.1集合第一课时", key=idem(1))
    assert isinstance(ws, WorkspaceSnapshot)
    assert ws.session["status"] == "active"
    kinds = [m.kind for m in ws.timeline]
    assert "text" in kinds and "context" in kinds
    ctx_card = next(m for m in ws.timeline if m.kind == "context")
    assert ctx_card.component["type"] == "context_card"
    assert ctx_card.component["props"]["section_title"] == "1.1 合成集合的概念"
    assert set(ctx_card.component["props"]["knowledge_points"]) == {"集合", "元素"}
    assert len(ws.job_summary) == 1 and ws.job_summary[0].status == "PENDING"
    assert ws.artifact.context is not None
    # 无步骤条
    assert "steps" not in ws.model_dump(mode="json")
    # 服务端权威：消息/run/上下文快照全持久
    async with session_factory() as s:
        n_msg = (await s.execute(__import__("sqlalchemy").select(ConversationMessageRow))).scalars().all()
        assert len(n_msg) >= 2
        n_runs = (await s.execute(__import__("sqlalchemy").select(MessageRunRow))).scalars().all()
        assert len(n_runs) == 1 and n_runs[0].status == "PENDING"
        n_snap = (await s.execute(__import__("sqlalchemy").select(ContextSnapshotRow))).scalars().all()
        assert len(n_snap) == 1


async def test_worker_completes_blueprint_proposal(db, session_factory, members):
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="备1.1集合", key=idem(2))
    sid = ws.session["session_id"]
    statuses = await process_pending_jobs(session_factory, fake)
    assert "completed" in statuses
    async with session_factory() as s:
        bp = (await s.execute(__import__("sqlalchemy").select(TeachingBlueprintRow))).scalars().all()
        assert len(bp) == 1 and bp[0].status == "proposal"
        runs = (await s.execute(__import__("sqlalchemy").select(MessageRunRow))).scalars().all()
        assert runs[0].status == "COMPLETED" and runs[0].result_ref == str(bp[0].id)
        ws2 = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=sid
        )
    kinds = [m.kind for m in ws2.timeline]
    assert "proposal" in kinds and "evidence" in kinds
    bp_card = next(m for m in ws2.timeline if m.kind == "proposal")
    assert bp_card.component["type"] == "blueprint_card"
    assert bp_card.component["props"]["status"] == "proposal"
    ev = next(m for m in ws2.timeline if m.kind == "evidence")
    assert ev.component["type"] == "evidence_rail"
    actions = {a.action for a in ws2.available_actions}
    assert {"confirm_proposal", "reject_proposal"} <= actions


async def test_confirm_proposal(db, session_factory, members):
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="备1.1集合", key=idem(3))
    await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=ws.session["session_id"]
        )
        rev = ws.session["revision"]
        bp_card = next(m for m in ws.timeline if m.kind == "proposal")
        out = await svc.run_action(
            s,
            adapter=members_adapter(members),
            token=TOKEN,
            actor_id=TEACHER,
            actor_role_hint="teacher",
            session_id=ws.session["session_id"],
            action="confirm_proposal",
            target=bp_card.component["props"]["blueprint_id"],
            expected_revision=rev,
            idempotency_key=idem(4),
            request_hash=_hash(path="/actions/confirm", body={}),
        )
    assert out.artifact.blueprint["status"] == "confirmed"
    # R6-n1fix（N6 治本）：确认即章节落定 → 会话标题/章节主题与绑定章节同源同步
    # （s4 画布页眉 DeckPane 优先读 session.title，首页/导航读 intent.knowledge_points；
    #  NL 原始知识点易过期，确认时点收敛到绑定章节）。断言对象=确认后的工作区 out。
    assert out.session["title"] == "1.1 合成集合的概念"
    assert out.session["intent"]["knowledge_points"] == ["合成集合的概念"]
    actions = {a.action for a in out.available_actions}
    assert "propose_revision" in actions


async def test_project_specs_action_composes_with_conversation_dispatch(db, session_factory, members):
    """对话编排层调用双 Spec 投影时不得嵌套提交并关闭外层事务。"""
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="备1.1集合", key=idem(31))
    await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=ws.session["session_id"]
        )
        bp_card = next(m for m in ws.timeline if m.kind == "proposal")
        bid = bp_card.component["props"]["blueprint_id"]
        confirmed = await svc.run_action(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=ws.session["session_id"], action="confirm_proposal", target=bid,
            expected_revision=ws.session["revision"], idempotency_key=idem(32),
            request_hash=_hash(path="/actions/confirm", body={}),
        )
        projected = await svc.run_action(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=ws.session["session_id"], action="project_specs", target=bid,
            expected_revision=confirmed.session["revision"], idempotency_key=idem(33),
            request_hash=_hash(path="/actions/project_specs", body={}),
            payload={"kinds": ["lesson_plan", "deck"]},
        )
    assert projected.artifact.lesson_plan is not None
    assert projected.artifact.teaching_deck is not None
    assert projected.artifact.teaching_deck["deck_id"]
    assert projected.artifact.teaching_deck["spec_id"]


async def test_reject_proposal_and_revise(db, session_factory, members):
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="备1.1集合", key=idem(5))
    await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=ws.session["session_id"]
        )
        sid = ws.session["session_id"]
        bp_card = next(m for m in ws.timeline if m.kind == "proposal")
        bid = bp_card.component["props"]["blueprint_id"]
        out = await svc.run_action(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="reject_proposal", target=bid, expected_revision=ws.session["revision"],
            idempotency_key=idem(6), request_hash=_hash(path="/actions/reject", body={}),
            payload={"reason": "目标不清晰"},
        )
        assert out.artifact.blueprint["status"] == "rejected"
        # 教师修订提案（propose_revision，无 LLM）
        out2 = await svc.run_action(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="propose_revision", target=bid, expected_revision=out.session["revision"],
            idempotency_key=idem(7), request_hash=_hash(path="/actions/revise", body={}),
            payload={"fields": {"objectives": [{"text": "修订后的目标", "source_ref": None}]}},
        )
        assert out2.artifact.blueprint["status"] == "proposal"
        assert out2.artifact.blueprint["body"]["objectives"][0]["text"] == "修订后的目标"


# ── 自然语言修改（续备）───────────────────────────────────────────────────────


async def test_continue_modify_knowledge_points(db, session_factory, members):
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="备1.1集合", key=idem(8))
    await process_pending_jobs(session_factory, fake)
    sid = ws.session["session_id"]
    async with session_factory() as s:
        ws = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=sid
        )
        # 修改知识点（不同 intent 载荷）
        fake.calls.clear()
        out = await svc.send_message(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, message="把知识点改成集合的表示法", expected_revision=ws.session["revision"],
            idempotency_key=idem(9), request_hash=_hash(path="/messages", body={"message": "改知识点"}),
        )
    assert out.session["revision"] > ws.session["revision"]
    ctx_card = next(m for m in out.timeline if m.kind == "context")
    assert set(ctx_card.component["props"]["knowledge_points"]) == {"集合", "元素"}  # fake 固定意图
    assert out.artifact.context is not None


# ── basic mode（provider 失败 → 带标签可编辑；不编造）─────────────────────────


async def test_intent_failure_basic_mode(db, members):
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    fake.fail_intent = True
    svc = make_service(fake)
    members.set(CLASS_A, user_id=TEACHER)
    ws = await svc.create_conversation(
        db, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        class_id=CLASS_A, message="给3班备集合", idempotency_key=idem(10),
        request_hash=_hash(path="/prep/conversations", body={"message": "给3班备集合"}),
    )
    assert ws.basic_mode is True
    bm = next(m for m in ws.timeline if m.kind == "basic_mode")
    assert bm.component["type"] == "basic_mode_card"
    assert bm.component["props"]["error_label"] == "provider_unavailable"
    assert bm.editable is True
    # 无 job、无上下文、无蓝图（不编造成功）
    assert ws.job_summary == []
    assert ws.artifact.context is None and ws.artifact.blueprint is None
    assert not any(m.kind == "proposal" for m in ws.timeline)
    # retry 动作可用
    assert any(a.action == "retry_run" for a in ws.available_actions)


async def test_blueprint_failure_basic_mode_then_retry(db, session_factory, members):
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    fake.fail_blueprint = True
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="备1.1集合", key=idem(11))
    sid = ws.session["session_id"]
    await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=sid
        )
        assert ws.basic_mode is True
        bm = next(m for m in ws.timeline if m.kind == "basic_mode")
        assert bm.component["props"]["error_label"] in ("provider_unavailable", "provider_timeout")
        assert ws.artifact.blueprint is None  # 未假成功
        # 局部重试：provider 恢复 → 仅重跑 blueprint 阶段（retry target = run.message_id 的用户消息）
        retry_target = next(a for a in ws.available_actions if a.action == "retry_run").target
        fake.fail_blueprint = False
        retry = await svc.run_action(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="retry_run", target=retry_target, expected_revision=ws.session["revision"],
            idempotency_key=idem(12), request_hash=_hash(path="/actions/retry", body={}),
        )
        assert any(a.action == "cancel_run" for a in retry.available_actions)
    await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=sid
        )
        assert ws.artifact.blueprint is not None
        runs = (await s.execute(__import__("sqlalchemy").select(MessageRunRow).order_by(MessageRunRow.attempt))).scalars().all()
        assert [r.status for r in runs] == ["BASIC_MODE", "COMPLETED"]
        assert [r.attempt for r in runs] == [1, 2]
        # 重试已成功 → 旧的失败行不得再派生「重试」假入口（否则界面永久挂重试按钮）
        assert not any(a.action == "retry_run" for a in ws.available_actions)


async def test_proposal_invalid_degrades_to_deterministic_fallback(db, session_factory, members):
    """W2.1 四段式（RULINGS 裁定 5）：LLM 输出未过校验 → 严格指令重试 1 次 →
    仍失败 → **确定性兜底 proposal**（消除用户可见的「生成内容未通过校验，请重试」）。

    口径：兜底 proposal 结构合法、事实来自服务端上下文预填（零编造）；失败留痕进
    `warnings_ack`（blueprint_strict_retry_used / blueprint_fallback_after_parse_failure，
    网关侧另有 ai_runs 双记录）；界面只见提案，不出现 basic mode 卡与 error 事件。
    """
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    fake.invalid_blueprint = True
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="备1.1集合", key=idem(18))
    sid = ws.session["session_id"]
    await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=sid
        )
        bp = ws.artifact.blueprint
        assert bp is not None  # 兜底成功（不再是 basic mode 假失败）
        ack = (bp["body"] or {}).get("warnings_ack") or []
        assert "blueprint_fallback_after_parse_failure" in ack
        assert "blueprint_strict_retry_used" in ack
        # 教师界面零失败文案：无降级卡、无「生成内容未通过校验」error 事件
        assert not any(m.kind == "basic_mode" for m in ws.timeline)
        from app.teacher_prep.models import SessionEventRow

        evs = (
            (
                await s.execute(
                    select(SessionEventRow).where(
                        SessionEventRow.session_id == __import__("uuid").UUID(sid),
                        SessionEventRow.event_type == "error",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert evs == []


async def test_apply_edits_teacher_proposal_without_llm(db, members):
    """basic mode 可编辑路径：教师手工编辑 → 确定性上下文 + teacher 提案（无 LLM）。"""
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    fake.fail_intent = True  # AI 不可用
    svc = make_service(fake)
    members.set(CLASS_A, user_id=TEACHER)
    ws = await svc.create_conversation(
        db, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        class_id=CLASS_A, message="备集合", idempotency_key=idem(13),
        request_hash=_hash(path="/prep/conversations", body={"message": "备集合"}),
    )
    sid = ws.session["session_id"]
    bm = next(m for m in ws.timeline if m.kind == "basic_mode")
    # 教师手工编辑提交（无 LLM 调用）
    fake.calls.clear()
    out = await svc.run_action(
        db, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        session_id=sid, action="apply_edits", target=str(bm.message_id), expected_revision=ws.session["revision"],
        idempotency_key=idem(14), request_hash=_hash(path="/actions/apply", body={}),
        payload={
            "summary": "教师手工编辑提交",
            "fields": {
                "source_id": str(SRC),
                "section_id": str(LESSON1),
                "class_id": CLASS_A,
                "knowledge_points": ["集合"],
                "lesson_scope": "第一课时",
                "objectives": [{"text": "教师目标：理解集合", "source_ref": None}],
            },
        },
    )
    assert fake.calls == []  # 全程无 LLM
    assert out.artifact.blueprint is not None
    assert out.artifact.blueprint["status"] == "proposal"
    assert out.artifact.blueprint["body"]["objectives"][0]["text"] == "教师目标：理解集合"
    assert out.artifact.context is not None


# ── 取消 / revision 冲突 / 幂等 ───────────────────────────────────────────────


async def test_cancel_run_marks_cancelled(db, session_factory, members):
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    fake.fail_blueprint = True  # 让 run 停在 BASIC_MODE 后，再开一轮 run 供取消
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="备1.1集合", key=idem(15))
    sid = ws.session["session_id"]
    await process_pending_jobs(session_factory, fake)
    # 新 run（retry 后仍失败）→ 再 retry 一次产生 RUNNING/PENDING run
    async with session_factory() as s:
        ws = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=sid
        )
        _bm = next(m for m in ws.timeline if m.kind == "basic_mode")
        retry_target = next(a for a in ws.available_actions if a.action == "retry_run").target
        retry = await svc.run_action(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="retry_run", target=retry_target, expected_revision=ws.session["revision"],
            idempotency_key=idem(16), request_hash=_hash(path="/actions/retry2", body={}),
        )
        cancel_actions = [a for a in retry.available_actions if a.action == "cancel_run"]
        assert cancel_actions
        _out = await svc.run_action(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, action="cancel_run", target=str(cancel_actions[0].target), expected_revision=retry.session["revision"],
            idempotency_key=idem(17), request_hash=_hash(path="/actions/cancel", body={}),
        )
        runs = (await s.execute(__import__("sqlalchemy").select(MessageRunRow))).scalars().all()
        assert runs[-1].status == "CANCELLED" and runs[-1].cancel_requested is True
    # 取消后不再产生蓝图（worker 不处理已取消 run）
    _statuses = await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        from sqlalchemy import select

        bp = (await s.execute(select(TeachingBlueprintRow))).scalars().all()
        assert len(bp) == 0  # 取消的 run 未写 proposal


async def test_revision_conflict_409(db, members):
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="备1.1集合", key=idem(18))
    sid = ws.session["session_id"]
    from app.teacher_prep.service import RevisionConflictError

    with pytest.raises(RevisionConflictError):
        await svc.send_message(
            db, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, message="再发一条", expected_revision=ws.session["revision"] - 100,
            idempotency_key=idem(19), request_hash=_hash(path="/messages", body={"message": "再发一条"}),
        )


async def test_idempotent_replay_no_duplicate(db, members):
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    svc = make_service(fake)
    members.set(CLASS_A, user_id=TEACHER)
    body = {"message": "备1.1集合"}
    req = {"class_id": CLASS_A, "message": "备1.1集合"}
    first = await svc.create_conversation(
        db, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        class_id=CLASS_A, message=body["message"], idempotency_key=idem(20),
        request_hash=_hash(path="/prep/conversations", body=req), title="t",
    )
    from sqlalchemy import func, select

    n1 = (await db.execute(select(func.count()).select_from(ConversationMessageRow))).scalar()
    # 同 key 同载荷重放 → 返回同一工作区（幂等缓存响应），不重复写
    second = await svc.create_conversation(
        db, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        class_id=CLASS_A, message=body["message"], idempotency_key=idem(20),
        request_hash=_hash(path="/prep/conversations", body=req), title="t",
    )
    second_ws = (
        second
        if isinstance(second, WorkspaceSnapshot)
        else WorkspaceSnapshot.model_validate(second)
    )
    assert second_ws.session["session_id"] == first.session["session_id"]
    n2 = (await db.execute(select(func.count()).select_from(ConversationMessageRow))).scalar()
    assert n1 == n2  # 无重复写
    sessions = (await db.execute(select(func.count()).select_from(TeachingSessionRow))).scalar()
    assert sessions == 1


async def test_idempotent_same_key_diff_payload_409(db, members):
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    svc = make_service(fake)
    members.set(CLASS_A, user_id=TEACHER)
    await svc.create_conversation(
        db, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
        class_id=CLASS_A, message="备1.1集合", idempotency_key=idem(21),
        request_hash=_hash(path="/prep/conversations", body={"class_id": CLASS_A, "message": "备1.1集合"}),
    )
    from app.reliability.idempotency import IdempotencyConflict

    with pytest.raises(IdempotencyConflict):
        await svc.create_conversation(
            db, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            class_id=CLASS_A, message="完全不同的消息", idempotency_key=idem(21),
            request_hash=_hash(path="/prep/conversations", body={"class_id": CLASS_A, "message": "完全不同的消息"}),
        )


# ── 跨日续备 / API 重启 / 无重复写 ────────────────────────────────────────────


async def test_cross_day_continue_and_restart_no_duplicate(file_db, members):
    """跨日续备 + API 重启：新 service/新连接消化 PENDING 作业，同一 Session 状态一致。"""
    maker = file_db()
    await seed_synthetic_textbook_into(maker)
    fake = FakeLlm()
    svc1 = make_service(fake)
    # 第 1 天：发消息 → job PENDING（模拟 API 在 worker 执行前崩溃）
    async with maker() as s:
        members.set(CLASS_A, user_id=TEACHER)
        ws = await svc1.create_conversation(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            class_id=CLASS_A, message="备1.1集合", idempotency_key=idem(30),
            request_hash=_hash(path="/prep/conversations", body={"message": "备1.1集合"}),
        )
        sid = ws.session["session_id"]
        rev_day1 = ws.session["revision"]
    # 模拟重启：全新 service 实例 + 新连接共享同一文件库；跨日续备
    fake2 = FakeLlm()
    svc2 = make_service(fake2)
    async with maker() as s:
        # 重启后先消化积压 PENDING 作业
        statuses = await process_pending_jobs(maker, fake2)
        assert "completed" in statuses
        # 跨日续备：读回 workspace 一致 + 继续发消息
        ws = await svc2.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=sid
        )
        assert ws.session["session_id"] == sid
        assert ws.artifact.blueprint is not None  # 重启后蓝图已落库（无重复写）
        # 续备：再发一条（跨日 continue）
        out = await svc2.send_message(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, message="接着昨天的继续，重点讲集合的表示法", expected_revision=ws.session["revision"],
            idempotency_key=idem(31), request_hash=_hash(path="/messages", body={"message": "接着昨天的"}),
        )
        assert out.session["session_id"] == sid
        assert out.session["revision"] > rev_day1
    # 全库断言：仅 1 个会话、1 个蓝图（无重复写）
    async with maker() as s:
        from sqlalchemy import func, select

        assert (await s.execute(select(func.count()).select_from(TeachingSessionRow))).scalar() == 1
        assert (await s.execute(select(func.count()).select_from(TeachingBlueprintRow))).scalar() == 1


async def seed_synthetic_textbook_into(maker) -> None:
    """把合成教材种子写入文件库（跨连接持久）。"""
    async with maker() as s:
        await seed_synthetic_textbook(s)


# ── 双标签并发（同一 Session 状态一致、无重复写）──────────────────────────────


async def test_dual_tab_serialized_and_conflict(file_db, members):
    maker = file_db()
    await seed_synthetic_textbook_into(maker)
    fake = FakeLlm()
    svc = make_service(fake)
    async with maker() as s:
        members.set(CLASS_A, user_id=TEACHER)
        ws = await svc.create_conversation(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            class_id=CLASS_A, message="备1.1集合", idempotency_key=idem(40),
            request_hash=_hash(path="/prep/conversations", body={"message": "备1.1集合"}),
        )
        sid = ws.session["session_id"]
    # 标签 1：继续发消息
    async with maker() as s:
        _ws1 = await svc.send_message(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, message="tab1 补充知识点", expected_revision=ws.session["revision"],
            idempotency_key=idem(41), request_hash=_hash(path="/messages", body={"message": "tab1"}),
        )
    # 标签 2：携带标签 1 的陈旧 revision → 409（revision conflict）
    from app.teacher_prep.service import RevisionConflictError

    with pytest.raises(RevisionConflictError):
        async with maker() as s:
            await svc.send_message(
                s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
                session_id=sid, message="tab2 同时改", expected_revision=ws.session["revision"],
                idempotency_key=idem(42), request_hash=_hash(path="/messages", body={"message": "tab2"}),
            )
    # 标签 2 刷新后以新 revision 成功 → 状态一致
    async with maker() as s:
        cur = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=sid
        )
        _ws2 = await svc.send_message(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, message="tab2 刷新后发送", expected_revision=cur.session["revision"],
            idempotency_key=idem(43), request_hash=_hash(path="/messages", body={"message": "tab2 刷新"}),
        )
    # 全库断言：消息 seq 连续无重复
    async with maker() as s:
        from sqlalchemy import select

        msgs = (
            (await s.execute(select(ConversationMessageRow).order_by(ConversationMessageRow.seq))).scalars().all()
        )
        seqs = [m.seq for m in msgs]
        assert seqs == sorted(seqs) and len(seqs) == len(set(seqs))  # 无重复、全序


# ── R2 对话去表单机（G5/G6/G7/G9/G10 + 裁决5 标题同步）─────────────────────────


class FakeLlmSection2(FakeLlm):
    """首句定位 LESSON1，第二句起改讲 LESSON2（复现"老师改讲另一节"）。"""

    def __init__(self) -> None:
        super().__init__()
        self.intent_calls = 0

    def __call__(self, prompt: str) -> str:
        if "教师消息" in prompt:
            self.calls.append(prompt[:40])
            self.intent_calls += 1
            section = LESSON1 if self.intent_calls == 1 else LESSON2
            return json.dumps(
                {
                    "intent_type": "modify",
                    "textbook_id": INTENT_SRC,
                    "section_id": str(section),
                    "class_id": CLASS_A,
                    "knowledge_points": ["函数"],
                    "lesson_scope": None,
                    "lesson_hours": None,
                    "objectives": None,
                    "note": None,
                    "confidence": 0.9,
                    "warnings": [],
                }
            )
        return super().__call__(prompt)


async def test_r2_blueprint_card_carries_targeted_prose_and_confirm_shape(db, session_factory, members):
    """G5 + G10：蓝图卡先有针对老师该句的 prose，卡内含备课信息/意图/下一步。"""
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="备1.1集合", key=idem(51))
    await process_pending_jobs(session_factory, fake)
    async with session_factory() as s:
        ws = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=ws.session["session_id"]
        )
    bp_card = next(m for m in ws.timeline if m.kind == "proposal")
    assert bp_card.text and "备1.1集合" in bp_card.text  # prose 针对老师该句（确定性兜底引用原话）
    props = bp_card.component["props"]
    labels = [i["label"] for i in props["info"]]
    assert labels[:4] == ["课题", "教材", "年级班级", "课时"]  # G10 备课信息 2×2
    assert isinstance(props["intent_bullets"], list) and props["next_step"]
    ev = next(m for m in ws.timeline if m.kind == "evidence")
    assert ev.text  # G5：卡消息必须带 prose


async def test_r2_question_gets_prose_only_no_card(db, session_factory, members):
    """问知识点 → 只回 prose（不重投影、不发卡）——去表单机核心。"""
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="备1.1集合", key=idem(52))
    await process_pending_jobs(session_factory, fake)
    sid = ws.session["session_id"]
    async with session_factory() as s:
        before = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=sid
        )
        runs_before = (await s.execute(select(MessageRunRow))).scalars().all()
        out = await svc.send_message(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, message="单调性在高考里常怎么考？",
            expected_revision=before.session["revision"], idempotency_key=idem(53),
            request_hash=_hash(path="/messages", body={"message": "q"}),
        )
        runs_after = (await s.execute(select(MessageRunRow))).scalars().all()
    tail = [m for m in out.timeline if m.role == "assistant"][-1]
    assert tail.component["type"] == "assistant_text"
    assert tail.text and "高考" in tail.text  # G5：回应针对这一句
    assert len(runs_after) == len(runs_before)  # 不触发蓝图重投影（去表单机）


async def test_r2_feedback_gets_prose_only(db, session_factory, members):
    """质疑（"课时安排太满了"）→ prose 回应，不重发大卡。"""
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="备1.1集合", key=idem(54))
    await process_pending_jobs(session_factory, fake)
    sid = ws.session["session_id"]
    async with session_factory() as s:
        before = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=sid
        )
        out = await svc.send_message(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, message="等等，我觉得课时安排太满了",
            expected_revision=before.session["revision"], idempotency_key=idem(55),
            request_hash=_hash(path="/messages", body={"message": "f"}),
        )
    tail = [m for m in out.timeline if m.role == "assistant"][-1]
    assert tail.component["type"] == "assistant_text"
    assert tail.text and "太满" in tail.text  # G5：针对这一句


async def test_r2_chat_confirm_confirms_blueprint_without_reprojection(db, session_factory, members):
    """G9："按这个方向继续" → blueprint 真实 confirmed（状态迁移），不再重投影。"""
    await seed_synthetic_textbook(db)
    fake = FakeLlm()
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="备1.1集合", key=idem(56))
    await process_pending_jobs(session_factory, fake)
    sid = ws.session["session_id"]
    async with session_factory() as s:
        before = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=sid
        )
        runs_before = (await s.execute(select(MessageRunRow))).scalars().all()
        out = await svc.send_message(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, message="按这个方向继续",
            expected_revision=before.session["revision"], idempotency_key=idem(57),
            request_hash=_hash(path="/messages", body={"message": "c"}),
        )
        runs_after = (await s.execute(select(MessageRunRow))).scalars().all()
    assert out.artifact.blueprint["status"] == "confirmed"  # G9 备课状态真实改变
    assert len(runs_after) == len(runs_before)  # 不重投影
    tail = [m for m in out.timeline if m.role == "assistant"][-1]
    assert tail.component["type"] == "assistant_text" and tail.text


async def test_r2_section_change_asks_confirm_then_applies_and_syncs_title(db, session_factory, members):
    """G6/G7 + 裁决5：改章节 → prose+确认动作（非胶囊云）；确认后落定并同步标题。"""
    await seed_synthetic_textbook(db)
    fake = FakeLlmSection2()
    svc = make_service(fake)
    ws = await _new_session(svc, db, members, message="备1.1集合", key=idem(58))
    await process_pending_jobs(session_factory, fake)
    sid = ws.session["session_id"]
    async with session_factory() as s:
        before = await svc.get_workspace(
            s, adapter=members_adapter(members), token=TOKEN, teacher_id=TEACHER, session_id=sid
        )
        out = await svc.send_message(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, message="改讲1.2合成关系",
            expected_revision=before.session["revision"], idempotency_key=idem(59),
            request_hash=_hash(path="/messages", body={"message": "m"}),
        )
        # 改选确认：prose + decision_card，且**不静默换章节**、不发 selection_card
        kinds = [m.component["type"] for m in out.timeline if m.component]
        assert "decision_card" in kinds
        assert "selection_card" not in kinds  # G6：章节已确定 → 不再发选择卡
        assert out.artifact.context["section"]["id"] == str(LESSON1)
        tail = [m for m in out.timeline if m.role == "assistant"][-2:]
        assert any(m.text and "确认" in m.text for m in tail)  # prose 明说要确认
        # 点「确认改选」= selection id 直传（selection_confirmed）→ 落定 + 标题同步
        out2 = await svc.send_message(
            s, adapter=members_adapter(members), token=TOKEN, actor_id=TEACHER, actor_role_hint="teacher",
            session_id=sid, message="确认改选为 1.2 合成关系",
            expected_revision=out.session["revision"], idempotency_key=idem(60),
            request_hash=_hash(path="/messages", body={"message": "m2"}),
            selection_ids={"textbook_id": INTENT_SRC, "section_id": str(LESSON2)},
        )
    assert out2.artifact.context["section"]["id"] == str(LESSON2)
    # 裁决5：会话标题与工作台定位同源（列表/工作台不再漂移）
    assert out2.session["title"] == out2.artifact.context["section"]["title"]


def test_r2_dialog_detectors_and_book_change_clears_section():
    from app.teacher_prep.conversation.intent import (
        apply_intent,
        detect_confirm,
        detect_feedback,
        detect_question,
    )
    from app.teacher_prep.conversation.schema import PrepIntent

    assert detect_confirm("按这个方向继续")
    assert detect_confirm("好的，继续")
    assert not detect_confirm("继续备1.1集合")
    assert detect_question("单调性在高考里常怎么考？")
    assert not detect_question("把例题换成椭圆的")
    assert detect_feedback("等等，我觉得课时安排太满了")
    assert not detect_feedback("课时太多，删掉一个环节")
    # 换册清空旧册章节（唯一允许清空已确认信息的场景）；同册不动
    cur = {
        "source_id": "11111111-1111-1111-1111-111111111111",
        "section_id": "22222222-2222-2222-2222-222222222222",
        "class_id": None,
        "knowledge_points": [], "lesson_scope": None, "lesson_hours": 1, "objectives": [],
    }
    changed = apply_intent(
        cur,
        PrepIntent(intent_type="modify", textbook_id="33333333-3333-3333-3333-333333333333"),
    )
    assert changed["section_id"] is None
    assert changed["source_id"] == "33333333-3333-3333-3333-333333333333"
    kept = apply_intent(
        cur,
        PrepIntent(intent_type="modify", textbook_id="11111111-1111-1111-1111-111111111111"),
    )
    assert kept["section_id"] == "22222222-2222-2222-2222-222222222222"
