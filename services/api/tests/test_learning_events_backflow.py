"""学习事件回流 + 学情可视化数据构建器（迭代21 · 学情数据轨）

覆盖（方案 testPlan · 回流测试 + D 数据服务）：
1. POST /student/error-records/{id}/review（remembered/forgotten/毕业档）→
   learning_events 新增 error_reviewed 行，payload 与 estimate_stability 现算一致，
   error_records 的 FSRS 缓存列随动作更新；
2. POST /student/practice/submit 逐题判分 → 每判一题 learning_events 新增
   quiz_judge 行（幂等键 submission_id:item_no），同键重放不重复落库；
   mastery_records / mastery_snapshots 行随判分同步更新；
3. learning_viz_data 构建器：曲线点/阶梯/趋势全部来自真实算法状态现算，
   稀疏数据返回可辨别空态（empty_reason/message），不合成假曲线。

说明：直接调用路由函数（user={"sub": ...} + 真实会话），沿用
test_butler_ledger.py 的 DB 级测试模式；不执行 BackgroundTasks（无外部 LLM 调用）。
测试数据用 TST 前缀命名空间，由 conftest._cleanup_pb_test_data 兜底清理。
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from math import ceil

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select

from app.gateway.student_router import (
    ErrorReviewRequest,
    PracticeSubmitRequest,
    complete_error_review,
    practice_submit,
)
from app.models.coursework import ErrorRecord, MasteryRecord
from app.models.database import async_session_factory
from app.models.knowledge_point import KnowledgePoint
from app.models.learning_event import LearningEvent
from app.models.mastery_snapshot import MasterySnapshot
from app.models.user import User
from app.services import bkt, fsrs
from app.services.learning_viz_data import (
    build_fsrs_retention,
    build_mastery_bkt,
    build_mastery_trend_series,
)


async def _make_user(s) -> uuid.UUID:
    u = User(phone=f"13{uuid.uuid4().int % 100000000:08d}", nickname="backflow-test")
    s.add(u)
    await s.flush()
    return u.id


def _user_dict(uid: uuid.UUID) -> dict:
    return {"sub": str(uid)}


# ==================== E-1：复习完成 → error_reviewed ====================


async def test_review_remembered_emits_error_reviewed():
    """复习成功推进 → learning_events.error_reviewed 落行，payload.stability=现算 S。"""
    async with async_session_factory() as s:
        uid = await _make_user(s)
        rec = ErrorRecord(
            user_id=uid,
            question_text="求 f(x)=x^2 的导数",
            answer_text="2x",
            source_channel="manual_photo",
            kp_code="TSTLEDRN-01",
            review_count=0,
            wrong_count=1,
            next_review_at=datetime.now(UTC) + timedelta(days=1),
        )
        s.add(rec)
        await s.commit()

        resp = await complete_error_review(
            rec.id, ErrorReviewRequest(result="remembered"), _user_dict(uid), s
        )
        assert resp["code"] == 0
        assert resp["data"]["review_count"] == 1
        assert resp["data"]["graduated"] is False

        ev = (
            await s.execute(
                select(LearningEvent).where(
                    LearningEvent.user_id == uid,
                    LearningEvent.event_type == "error_reviewed",
                )
            )
        ).scalar_one()
        assert ev.source_type == "review"
        assert ev.source_id == str(rec.id)
        assert ev.payload["record_id"] == str(rec.id)
        assert ev.payload["result"] == "remembered"
        assert ev.payload["kp_code"] == "TSTLEDRN-01"
        assert ev.payload["review_count"] == 1
        assert ev.payload["graduated"] is False
        assert ev.payload["stability"] == fsrs.estimate_stability(1, 1)

        # FSRS 状态随动作真实更新（error_records 缓存列回填）
        fresh = await s.get(ErrorRecord, rec.id)
        assert int(fresh.review_count) == 1
        assert float(fresh.fsrs_stability) == fsrs.estimate_stability(1, 1)


async def test_review_forgotten_resets_and_penalizes_stability():
    """result=forgotten → review_count 清零、wrong_count+1、S 按答错惩罚现算。"""
    async with async_session_factory() as s:
        uid = await _make_user(s)
        rec = ErrorRecord(
            user_id=uid,
            question_text="椭圆焦点距计算",
            answer_text="c=3",
            source_channel="auto_judge",
            kp_code="TSTLEELL-02",
            review_count=2,
            wrong_count=1,
        )
        s.add(rec)
        await s.commit()

        resp = await complete_error_review(
            rec.id, ErrorReviewRequest(result="forgotten"), _user_dict(uid), s
        )
        assert resp["code"] == 0

        ev = (
            await s.execute(
                select(LearningEvent).where(
                    LearningEvent.user_id == uid,
                    LearningEvent.event_type == "error_reviewed",
                )
            )
        ).scalar_one()
        assert ev.payload["result"] == "forgotten"
        assert ev.payload["review_count"] == 0
        # forgotten：wrong_count 1→2，estimate_stability(0, 2)=0.4*0.85=0.34
        assert ev.payload["stability"] == fsrs.estimate_stability(0, 2)
        fresh = await s.get(ErrorRecord, rec.id)
        assert int(fresh.wrong_count) == 2
        assert int(fresh.review_count) == 0


async def test_review_graduated_marks_payload():
    """走完 1/3/7/15 全部档位（review_count 3→4）→ graduated=True 入事件。"""
    async with async_session_factory() as s:
        uid = await _make_user(s)
        rec = ErrorRecord(
            user_id=uid,
            question_text="数列极限",
            answer_text="0",
            source_channel="auto_judge",
            kp_code="TSTLELIM-03",
            review_count=3,
            wrong_count=1,
        )
        s.add(rec)
        await s.commit()

        resp = await complete_error_review(
            rec.id, ErrorReviewRequest(result="remembered"), _user_dict(uid), s
        )
        assert resp["code"] == 0
        assert resp["data"]["graduated"] is True
        assert resp["data"]["next_review_at"] is None

        ev = (
            await s.execute(
                select(LearningEvent).where(
                    LearningEvent.user_id == uid,
                    LearningEvent.event_type == "error_reviewed",
                )
            )
        ).scalar_one()
        assert ev.payload["graduated"] is True
        assert ev.payload["review_count"] == 4


# ==================== E-2：刷题判分 → 逐题 quiz_judge ====================


async def test_practice_submit_emits_quiz_judge_per_item_with_idempotency():
    """3 题判分 → 3 行 quiz_judge（幂等键 submission_id:item_no）；同键重放不重复落库；
    mastery_records / mastery_snapshots 同步更新。"""
    async with async_session_factory() as s:
        uid = await _make_user(s)
        kp = KnowledgePoint(code=f"TSTLEBKT-{uuid.uuid4().hex[:6]}", name="回流测试知识点")
        s.add(kp)
        await s.flush()

        def _item(no: int, answer: str) -> dict:
            return {
                "item_no": no,
                "q_type": "choice",
                "answer_text": answer,
                "expected_answer": "A",
                "question_text": f"第{no}题：1+1=?",
                "kp_code": kp.code,
            }

        req = PracticeSubmitRequest(
            items=[_item(1, "A"), _item(2, "B"), _item(3, "A")],
            client_submit_id=f"c-{uuid.uuid4().hex[:12]}",
        )
        resp = await practice_submit(req, BackgroundTasks(), _user_dict(uid), s)
        assert resp["code"] == 0
        sid = resp["data"]["submission_id"]

        rows = (
            (
                await s.execute(
                    select(LearningEvent).where(
                        LearningEvent.user_id == uid,
                        LearningEvent.event_type == "quiz_judge",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 3
        assert {r.idempotency_key for r in rows} == {f"{sid}:{n}:quiz_judge" for n in (1, 2, 3)}
        assert all(r.source_type == "practice" and r.source_id == sid for r in rows)
        by_no = {r.payload["item_no"]: r for r in rows}
        assert by_no[1].payload["correct"] is True
        assert by_no[2].payload["correct"] is False
        assert by_no[3].payload["correct"] is True
        assert by_no[1].payload["score"] == resp["data"]["results"][0]["score"]
        assert by_no[2].payload["kp_code"] == kp.code

        # 同 submission 重放（同幂等键再 emit）→ 不重复落库（learning_events 幂等列天然防重放）
        from app.butler.event_bus import get_event_bus

        bus = get_event_bus()
        for n in (1, 2, 3):
            await bus.emit(
                s,
                user_id=uid,
                event_type="quiz_judge",
                source_type="practice",
                source_id=sid,
                payload=by_no[n].payload,
                idempotency_key=f"{sid}:{n}:quiz_judge",
            )
        await s.commit()
        rows_after = (
            (
                await s.execute(
                    select(LearningEvent).where(
                        LearningEvent.user_id == uid,
                        LearningEvent.event_type == "quiz_judge",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows_after) == 3

        # mastery_records / mastery_snapshots 随判分真实更新（2 对 1 错）
        mr = (
            await s.execute(select(MasteryRecord).where(MasteryRecord.user_id == uid))
        ).scalar_one()
        assert int(mr.practice_count) == 3
        assert int(mr.correct_count) == 2
        snap = (
            await s.execute(
                select(MasterySnapshot).where(
                    MasterySnapshot.user_id == uid,
                    MasterySnapshot.kp_code == kp.code,
                    MasterySnapshot.date == date.today(),
                )
            )
        ).scalar_one()
        assert abs(float(snap.mastery) - float(mr.mastery)) < 1e-6


# ==================== D：FSRS 保持率曲线构建器 ====================


async def _mk_error(s, uid, *, review_count, wrong_count, age_days, kp_code):
    rec = ErrorRecord(
        user_id=uid,
        question_text=f"测试错题-{kp_code}-{'x' * (review_count * 7 + 5)}",
        answer_text="ans",
        source_channel="auto_judge",
        kp_code=kp_code,
        review_count=review_count,
        wrong_count=wrong_count,
        updated_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    s.add(rec)
    return rec


async def test_fsrs_retention_curve_computed_from_real_stability():
    """曲线点全部由 fsrs.retrievability(t, S) 对真实 S 现算；阶梯=estimate_stability 逐档。"""
    async with async_session_factory() as s:
        uid = await _make_user(s)
        # 到期题：S=estimate_stability(0,1)=0.4，t≈2 天 → R≈0.64 < 0.85 → due
        rec_due = await _mk_error(
            s, uid, review_count=0, wrong_count=1, age_days=2, kp_code="TSTVIZA-01"
        )
        # 保持题：S=estimate_stability(4,1)=15，t≈0 → R≈1 → 不 due
        _rec_ok = await _mk_error(
            s, uid, review_count=4, wrong_count=1, age_days=0.04, kp_code="TSTVIZB-02"
        )
        await s.commit()

        data = await build_fsrs_retention(s, uid, limit=5, due_only=False)
        assert "empty_reason" not in data
        items = data["items"]
        assert len(items) == 2
        # 到期优先、R 最低在前
        assert items[0]["record_id"] == str(rec_due.id)
        assert items[0]["is_due"] is True
        assert items[1]["is_due"] is False

        s15 = fsrs.estimate_stability(4, 1)  # 15.0
        curve_ok = items[1]["curve"]
        assert curve_ok["thresholds"] == {"due": fsrs.DUE_THRESHOLD, "decay": fsrs.DECAY_THRESHOLD}
        # horizon=clamp(ceil(days_until(S,0.6))+7, 7, 30)；S=15 → days_until=90 → clamp 30
        horizon = min(30, max(7, ceil(fsrs.days_until(s15, fsrs.DECAY_THRESHOLD)) + 7))
        assert horizon == 30
        assert len(curve_ok["points"]) == horizon + 1
        assert curve_ok["points"][0] == {"t": 0, "r": 1.0}
        # 独立公式复核：R(1)=(1+1/(9*15))^-1=0.9926；R(30)=(1+30/135)^-1=0.8182
        assert curve_ok["points"][1]["r"] == round((1 + 1 / (9 * 15)) ** -1, 4)
        assert curve_ok["points"][-1]["r"] == round((1 + 30 / (9 * 15)) ** -1, 4)
        # 单调衰减（记忆曲线物理性质）
        rs = [p["r"] for p in curve_ok["points"]]
        assert all(a >= b for a, b in zip(rs, rs[1:], strict=False))

        assert items[1]["stability"] == s15
        assert [ladder["stability"] for ladder in items[1]["stability_ladder"]] == [
            0.4,
            1.0,
            3.0,
            7.0,
            15.0,
        ]
        assert [ladder["review_count"] for ladder in items[1]["stability_ladder"]] == [
            0,
            1,
            2,
            3,
            4,
        ]
        assert items[1]["review_count"] == 4 and items[1]["wrong_count"] == 1
        assert items[1]["level"] == "lv4"
        assert items[1]["next_review_at"] is None
        # current_t 是服务端在 T1 时刻算的「距 updated_at 的天数」，本断言在 T2 时刻重算；
        # 两次「now」相隔微秒级，精确相等本身就是缺陷（CI 上实测差 1.2e-9 天）。
        # 容差取 1e-6 天（≈86ms），远大于两次计算间隔，仍能抓住量级错误。
        assert items[0]["current_t"] == pytest.approx(fsrs.days_since(rec_due.updated_at), abs=1e-6)

        # question_preview ≤60 字契约
        assert len(items[0]["question_preview"]) <= 61  # 60 + 省略号


async def test_fsrs_retention_due_only_and_empty_states():
    """due_only=True 只出到期题；全无到期→message 空态（非 no_error_records）；无错题→硬空态。"""
    async with async_session_factory() as s:
        uid = await _make_user(s)
        await _mk_error(s, uid, review_count=4, wrong_count=1, age_days=0.01, kp_code="TSTVIZC-03")
        await s.commit()

        due_data = await build_fsrs_retention(s, uid, limit=3, due_only=True)
        assert due_data["items"] == []
        assert "empty_reason" not in due_data  # 有在册错题但无到期 ≠ 无数据，不得谎报
        assert "没有到期" in due_data["message"]

        empty = await build_fsrs_retention(s, uuid.uuid4())
        assert empty == {
            "items": [],
            "empty_reason": "no_error_records",
            "message": "完成一次练习或复习后，这里会出现你的记忆保持率曲线。",
        }


# ==================== D：BKT 掌握度构建器 ====================


async def _mk_mastery(s, uid, kp_code, kp_name, mastery, practice, correct, snaps):
    kp = KnowledgePoint(code=kp_code, name=kp_name)
    s.add(kp)
    await s.flush()
    s.add(
        MasteryRecord(
            user_id=uid,
            kp_id=kp.id,
            mastery=mastery,
            practice_count=practice,
            correct_count=correct,
            last_practiced_at=datetime.now(UTC),
        )
    )
    for d, m in snaps:
        s.add(MasterySnapshot(user_id=uid, kp_code=kp_code, date=d, mastery=m))
    return kp


async def test_mastery_bkt_from_real_records_and_snapshots():
    """P(known)=mastery_records 真值；趋势=mastery_snapshots 真行；模型标注如实 bkt_lite。"""
    async with async_session_factory() as s:
        uid = await _make_user(s)
        today = date.today()
        yesterday = today - timedelta(days=1)
        await _mk_mastery(
            s,
            uid,
            f"TSTVIZD-{uuid.uuid4().hex[:4]}A",
            "已掌握知识点",
            0.9,
            10,
            9,
            snaps=[(yesterday, 0.85), (today, 0.9)],
        )
        await _mk_mastery(
            s,
            uid,
            f"TSTVIZD-{uuid.uuid4().hex[:4]}B",
            "薄弱知识点",
            0.3,
            4,
            1,
            snaps=[(today, 0.3)],
        )
        await s.commit()

        data = await build_mastery_bkt(s, uid, days=14, limit=6)
        assert data["model"] == "bkt_lite"  # 标准四参数 BKT 仍是接口预留（bkt.py:8-9），如实标注
        assert data["min_answers_threshold"] == bkt.BKT_MIN_ANSWERS == 20
        assert data["summary"]["total_kps"] == 2
        assert abs(data["summary"]["avg_mastery"] - 0.6) < 1e-6
        assert data["summary"]["mastered_count"] == 1
        assert data["summary"]["weak_top1"]["mastery"] == 0.3

        items = data["items"]
        assert [i["state"] for i in items] == ["weak", "mastered"]  # 掌握度升序
        weak, mastered = items
        assert weak["delta"] is None  # 前一日无快照 → 不造 0 假变化
        assert mastered["delta"] == 0.05  # 今日 0.9 − 前日 0.85
        assert len(weak["trend"]) == 14
        assert weak["trend"][-1] == {"date": today.isoformat(), "mastery": 0.3}
        assert weak["trend"][0]["mastery"] is None  # 无快照日如实断线，不插值
        assert mastered["trend"][-2] == {"date": yesterday.isoformat(), "mastery": 0.85}
        assert data["history_days"] == 2


async def test_mastery_bkt_empty_state():
    async with async_session_factory() as s:
        uid = await _make_user(s)
        data = await build_mastery_bkt(s, uid)
        assert data["items"] == []
        assert data["empty_reason"] == "no_mastery_data"
        assert data["summary"]["total_kps"] == 0
        assert data["summary"]["weak_top1"] is None
        assert "练习" in data["message"]


async def test_mastery_trend_series_union_dedupe():
    """趋势序列：kp=最新掌握度升序 ∪ 练习量前 N，去重；空态 no_mastery_data。"""
    async with async_session_factory() as s:
        uid = await _make_user(s)
        today = date.today()
        await _mk_mastery(
            s,
            uid,
            f"TSTVIZE-{uuid.uuid4().hex[:4]}A",
            "高练习量低掌握",
            0.2,
            12,
            2,
            snaps=[(today, 0.2)],
        )
        await _mk_mastery(
            s,
            uid,
            f"TSTVIZE-{uuid.uuid4().hex[:4]}B",
            "低练习量高掌握",
            0.95,
            1,
            1,
            snaps=[(today, 0.95)],
        )
        await s.commit()

        data = await build_mastery_trend_series(s, uid, days=14, limit=6)
        # 掌握度升序（A 0.2, B 0.95）∪ 练习量前 N（A 12 题, B 1 题）→ 去重后 [A, B]
        assert [x["kp_name"] for x in data["series"]] == ["高练习量低掌握", "低练习量高掌握"]
        for entry in data["series"]:
            assert len(entry["points"]) == 14
            assert entry["points"][-1]["mastery"] is not None

        empty = await build_mastery_trend_series(s, uuid.uuid4())
        assert empty["series"] == []
        assert empty["empty_reason"] == "no_mastery_data"
