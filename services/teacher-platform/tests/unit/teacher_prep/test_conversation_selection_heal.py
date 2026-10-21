"""对话闭环修复（2026-10-04）：selection_ids 直传死局的确定性单测。

覆盖四个根因修复：
1. 脏 section_id=教材 UUID（前端历史版本误传）→ 自愈归正为 textbook_id，按缺失语义继续；
2. 非法 section 组合（章节不属于当前授权教材）→ deltas 语义丢弃，绝不写进 srow.intent；
3. 重复点选防抖：本轮选择与会话上一张选择卡同款 → 只回 prose，不重发卡；
4. 章节曾定过但当前缺教材（历史脏数据）→ 恢复路径改发教材选择卡（原 decision_card
   因无 source 必返回 None 的死局），prose 经 reply.compose_reply（宪法 §20 文案）。

基建：teacher_prep conftest（SQLite）+ 合成教材种子 + 额外"别册"种子 + 确定性 fake LLM。
"""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select

from app.teacher_prep.conversation.orchestrator import ConversationService
from app.teacher_prep.models import TeachingSessionRow
from app.textbook_v2.models import TextbookSection, TextbookSource

from .conftest import CLASS_A, TEACHER, TOKEN, idem
from .tp_context_fixtures import LESSON1, LESSON2, SRC, seed_synthetic_textbook

# 别册（第二个授权教材源 + 属于它的章节）：非法组合 / 归正命中判定用
SRC2 = uuid.UUID("50000000-0000-0000-0000-0000000000b1")
FOREIGN_SEC = uuid.UUID("50000000-0000-0000-0000-0000000000b2")


class _NoLlm:
    """意图解析必失败：本文件全部走 selection_ids 直传 / 预置脏数据，不依赖 LLM。"""

    def __call__(self, prompt: str) -> str:
        raise RuntimeError("provider connection refused")


class FakeLlmNoSelection:
    """意图只带知识点、不带教材/章节（复现"老师这句话不改变选择"）。"""

    def __init__(self) -> None:
        self.intent_calls = 0

    def __call__(self, prompt: str) -> str:
        if "教师消息" in prompt:
            self.intent_calls += 1
            return json.dumps(
                {
                    "intent_type": "modify",
                    "textbook_id": None,
                    "section_id": None,
                    "class_id": None,
                    "knowledge_points": ["函数"],
                    "lesson_scope": None,
                    "lesson_hours": None,
                    "objectives": None,
                    "note": None,
                    "confidence": 0.9,
                    "warnings": [],
                }
            )
        raise AssertionError("本测试不应触发蓝图生成（选择不完整不得进入投影）")


def make_service(fake) -> ConversationService:
    return ConversationService(llm_generate=fake)


def members_adapter(members):
    from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter, FakeApiTransport
    from app.teacher_prep_v2.adapters.mapping import InMemoryMappingRegistry

    transport = FakeApiTransport()
    transport.handler = members.handler
    return Api8000Adapter(transport, InMemoryMappingRegistry())


def _hash(*, path: str, body: dict | None) -> str:
    from app.reliability.idempotency import request_hash_of

    return request_hash_of("POST", path, body)


async def _new_session(service, db, members, *, key: str):
    """建一个不含任何选择的会话（意图解析失败 → basic mode，选择留空）。"""
    members.set(CLASS_A, user_id=TEACHER)
    return await service.create_conversation(
        db,
        adapter=members_adapter(members),
        token=TOKEN,
        actor_id=TEACHER,
        actor_role_hint="teacher",
        class_id=CLASS_A,
        message="备一节课",
        idempotency_key=key,
        request_hash=_hash(path="/prep/conversations", body={"message": "备一节课"}),
        title="对话闭环修复测试",
    )


async def _send(
    service,
    s,
    members,
    sid: str,
    revision: int,
    *,
    message: str,
    key: str,
    selection_ids: dict[str, str | None] | None = None,
):
    return await service.send_message(
        s,
        adapter=members_adapter(members),
        token=TOKEN,
        actor_id=TEACHER,
        actor_role_hint="teacher",
        session_id=sid,
        message=message,
        expected_revision=revision,
        idempotency_key=key,
        request_hash=_hash(path="/messages", body={"message": message}),
        selection_ids=selection_ids,
    )


def _selection_cards(ws) -> list:
    return [m for m in ws.timeline if m.component and m.component["type"] == "selection_card"]


async def _session_intent(session_factory, sid: str) -> dict:
    async with session_factory() as s:
        row = (
            await s.execute(
                select(TeachingSessionRow).where(TeachingSessionRow.id == uuid.UUID(sid))
            )
        ).scalar_one()
        return dict(row.intent or {})


async def _seed_foreign_source(db) -> None:
    """别册授权源 + 属于它的章节（构造"章节不属于当前教材"的非法组合）。"""
    db.add(
        TextbookSource(
            id=SRC2,
            school_id=None,
            publisher="别册出版社",
            edition="2019",
            grade="高一",
            volume="必修第二册",
            subject="数学",
            source_repo="https://example.invalid/synthetic-r16-b.git",
            source_commit="1" * 40,
            source_path="synthetic/r16-b.jsonl",
            file_sha256="e" * 64,
            rights_status="STAGED",
            rights_evidence_ref="队伍确认授权",
            parser_version="slice_md-202607",
            source_kind="direct-chunk",
            batch_id="synth-batch-02",
            manifest_sha256="n" * 64,
            provenance={"import_id": "synth-import-02", "manifest_sha256": "n" * 64},
        )
    )
    db.add(
        TextbookSection(
            id=FOREIGN_SEC,
            textbook_id=SRC2,
            parent_id=None,
            level=2,
            order=1,
            title="2.1 别册章节",
            normalized_title="别册章节",
            page_start=1,
            page_end=4,
            section_path="2.1",
            chunk_start=1,
            chunk_end=4,
        )
    )
    await db.commit()


# ── 1. 脏 section_id=教材 UUID → 自愈归正为 textbook_id ─────────────────────────


async def test_dirty_section_id_healed_to_textbook(db, session_factory, members):
    await seed_synthetic_textbook(db)
    svc = make_service(_NoLlm())
    ws0 = await _new_session(svc, db, members, key=idem(901))
    sid = ws0.session["session_id"]
    # 前端历史缺陷：把教材卡选项 id（TextbookSource 主键）放进了 section_id
    out = await _send(
        svc, db, members, sid, ws0.session["revision"],
        message="就选这本教材",
        key=idem(902),
        selection_ids={"section_id": str(SRC)},
    )
    cards = _selection_cards(out)
    assert len(cards) == 1
    props = cards[0].component["props"]
    # 归正后缺章节 → 按缺失语义继续：章节选择卡（带当前已选教材，前端契约字段）
    assert props["kind"] == "section"
    assert props["source_id"] == str(SRC)
    option_ids = {o["id"] for o in props["options"]}
    assert {str(LESSON1), str(LESSON2)} <= option_ids
    # 脏组合不得落库：srow.intent 教材归正、章节视为未提供
    intent = await _session_intent(session_factory, sid)
    assert intent["source_id"] == str(SRC)
    assert intent["section_id"] is None


# ── 2. 非法 section 组合 → deltas 语义丢弃，不写 intent ──────────────────────────


async def test_illegal_section_combo_dropped_and_not_persisted(db, session_factory, members):
    await seed_synthetic_textbook(db)
    await _seed_foreign_source(db)
    svc = make_service(_NoLlm())
    ws0 = await _new_session(svc, db, members, key=idem(911))
    sid = ws0.session["session_id"]
    # 第 1 轮：脏点击归正出教材（见测试 1）
    out1 = await _send(
        svc, db, members, sid, ws0.session["revision"],
        message="就选这本教材", key=idem(912), selection_ids={"section_id": str(SRC)},
    )
    # 第 2 轮：点选本册真实章节 → 选择完整、上下文构建
    out2 = await _send(
        svc, db, members, sid, out1.session["revision"],
        message="就讲这一节", key=idem(913),
        selection_ids={"textbook_id": str(SRC), "section_id": str(LESSON1)},
    )
    assert out2.artifact.context is not None
    # 第 3 轮：非法组合——章节属于别册 SRC2，与当前教材 SRC 不匹配
    out3 = await _send(
        svc, db, members, sid, out2.session["revision"],
        message="换到别册那一章", key=idem(914),
        selection_ids={"textbook_id": str(SRC), "section_id": str(FOREIGN_SEC)},
    )
    # 非法组合不得写进 srow.intent（历史脏数据死局的根源）
    intent = await _session_intent(session_factory, sid)
    assert intent["source_id"] == str(SRC)
    assert intent["section_id"] is None
    # 丢弃后按缺失语义继续：章节曾定过 → prose + 改选确认按钮（decision_card 仍可用）
    kinds = [m.component["type"] for m in out3.timeline if m.component]
    assert "decision_card" in kinds
    # 不重建上下文：非法章节没有生效，备课定位仍是原章节
    assert out3.artifact.context["section"]["id"] == str(LESSON1)
    # 未发新的章节选择卡（G6 抑制：走 decision_card 恢复路径）
    assert len(_selection_cards(out3)) == 1  # 仅第 1 轮那张


# ── 3. 重复点选防抖 → 只回 prose，不重发卡 ───────────────────────────────────────


async def test_duplicate_selection_click_replies_prose_only(db, session_factory, members):
    await seed_synthetic_textbook(db)
    svc = make_service(_NoLlm())
    ws0 = await _new_session(svc, db, members, key=idem(921))
    sid = ws0.session["session_id"]
    out1 = await _send(
        svc, db, members, sid, ws0.session["revision"],
        message="就选这本教材", key=idem(922), selection_ids={"section_id": str(SRC)},
    )
    assert len(_selection_cards(out1)) == 1
    # 同一张卡再点一次（脏值同前）→ 合并后的选择完全相同 → 防抖
    out2 = await _send(
        svc, db, members, sid, out1.session["revision"],
        message="再点一次这本教材", key=idem(923), selection_ids={"section_id": str(SRC)},
    )
    cards = _selection_cards(out2)
    assert len(cards) == 1  # 未新增卡（仍是第 1 轮那张）
    tail = [m for m in out2.timeline if m.role == "assistant"][-1]
    assert tail.component["type"] == "assistant_text"
    assert tail.text  # prose 确认（测试环境走确定性兜底，文案是老师的话）
    # 会话状态不变：选择仍是"已选教材、缺章节"
    intent = await _session_intent(session_factory, sid)
    assert intent["source_id"] == str(SRC) and intent["section_id"] is None


# ── 4. 章节曾定过但当前缺教材 → 恢复路径发教材选择卡（不再死局）──────────────────


async def test_recovery_without_source_sends_textbook_card(db, session_factory, members):
    await seed_synthetic_textbook(db)
    svc = make_service(_NoLlm())
    ws0 = await _new_session(svc, db, members, key=idem(931))
    sid = ws0.session["session_id"]
    # 预置历史脏数据（修复前由根因 a 持久化）：source 缺失、section 是教材 UUID
    async with session_factory() as s:
        row = (
            await s.execute(
                select(TeachingSessionRow).where(TeachingSessionRow.id == uuid.UUID(sid))
            )
        ).scalar_one()
        row.intent = {
            "source_id": None,
            "section_id": str(SRC),
            "class_id": CLASS_A,
            "knowledge_points": [],
            "lesson_scope": None,
            "lesson_hours": None,
            "objectives": [],
        }
        await s.commit()
    # 老师正常说一句话（意图不带教材/章节）→ 脏组合被丢弃 + 恢复路径发教材选择卡
    fake2 = FakeLlmNoSelection()
    svc2 = make_service(fake2)
    out = await _send(
        svc2, db, members, sid, ws0.session["revision"],
        message="调整一下教学安排，重点讲函数", key=idem(932),
    )
    assert fake2.intent_calls == 1
    cards = _selection_cards(out)
    assert len(cards) == 1
    props = cards[0].component["props"]
    assert props["kind"] == "textbook"
    assert props["source_id"] is None
    option_ids = {o["id"] for o in props["options"]}
    assert str(SRC) in option_ids  # 选项 100% 来自已授权目录
    # 卡消息带 prose（宪法 §20：老师的话，不含「还差X才能继续备课」系统机制话术）
    assert cards[0].text
    assert "才能继续备课" not in cards[0].text
    # 脏组合已从 srow.intent 清除（不再每轮复现死局）
    intent = await _session_intent(session_factory, sid)
    assert intent["source_id"] is None
    assert intent["section_id"] is None
    # 再说一句仍然不变的话 → 教材选择卡同样防抖：不重发卡，只回 prose
    out2 = await _send(
        svc2, db, members, sid, out.session["revision"],
        message="再想想怎么安排", key=idem(933),
    )
    assert len(_selection_cards(out2)) == 1  # 仍是恢复路径那一张
    tail2 = [m for m in out2.timeline if m.role == "assistant"][-1]
    assert tail2.component["type"] == "assistant_text"
