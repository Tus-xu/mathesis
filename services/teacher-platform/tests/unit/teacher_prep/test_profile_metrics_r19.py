"""R19 教师侧 Evidence + 版本化 ClassLearningProfile 单测。

覆盖验收（R19 人工指令）：
- 确定性统计 oracle（golden JSON 逐数字比对；同输入同 now → 同输出）；
- 版本/重算幂等（同指纹 → replayed no-op；变数据 → revision++ 旧版本不可变）；
- 证据可追溯（typed refs → 8000 submission id）+ 证据失效（新版本后旧指纹行 invalid）；
- PII 最小化（body/证据/API 响应零答案内容/零学生标识）；
- 跨班 403、成员移除后权限保持正确；
- AI 失败不影响基础统计（basic_mode；成功时 proposal 为 ai_suggestion 证据行）；
- 12 类边界：无数据/低覆盖/未提交/迟交/重交/重复事件/异常值/成员变更/答案缺失/
  置信度缺失/证据失效/attempt policy 缺失 + 低样本不下肯定结论。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import ValidationError
from sqlalchemy import select

from app.reliability.idempotency import request_hash_of
from app.teacher_prep.profile.builder import build_profile_body
from app.teacher_prep.profile.evidence import TeachingEvidenceRow
from app.teacher_prep.profile.facts import bundle_from_payload
from app.teacher_prep.profile.metrics import (
    CALC_VERSION,
    MIN_SAMPLE,
    MetricsInput,
    compute_profile_metrics,
)
from app.teacher_prep.profile.models import (
    ClassLearningProfileRevisionRow,
    ClassLearningProfileRow,
)
from app.teacher_prep.profile.schema import AttemptPolicyMetrics
from app.teacher_prep.service import (
    MembershipDeniedError,
    TeacherPrepService,
)
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter
from app.teacher_prep_v2.adapters.mapping import InMemoryMappingRegistry

FIXTURES = Path(__file__).parent / "fixtures" / "profile_r19"
TEACHER = "11111111-1111-1111-1111-111111111111"
CLASS_A = "22222222-2222-2222-2222-222222222222"
CLASS_B = "33333333-3333-3333-3333-333333333333"
TOKEN = "jwt-teacher-token"
NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
KP_BY_ITEM = {"K1": [1, 2], "K2": [2, 3]}
ATTEMPT_POLICY = AttemptPolicyMetrics(
    known=True, attempts_allowed=1, resubmit_allowed=False, allow_late=True
)


def idem(n: int) -> str:
    return f"r19-key-{n:04d}"


def RH(body: dict) -> str:
    return request_hash_of("POST", "/p", body)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _metrics_input(bundle, **kw) -> MetricsInput:
    return MetricsInput(
        bundle=bundle,
        kp_by_item=kw.pop("kp_by_item", dict(KP_BY_ITEM)),
        attempt_policy=kw.pop("attempt_policy", ATTEMPT_POLICY),
        prior_eligible_count=kw.pop("prior_eligible_count", None),
        now=kw.pop("now", NOW),
        run_id=kw.pop("run_id", "run-oracle-001"),
        expires_at=kw.pop("expires_at", datetime(2026, 9, 30, tzinfo=UTC)),
        **kw,
    )


# ── fake 传输：成员裁决 + submission-facts 载荷 ───────────────────────────────


class R19Transport:
    """按 path 提供成员裁决与统计素材载荷（可变异，模拟 5xx/成员移除）。"""

    base_url = "http://api8000.test"

    def __init__(self) -> None:
        self.members: dict[str, dict] = {}
        self.facts_payload: dict | None = None
        self.facts_calls = 0
        self.fail_5xx = False

    async def request(self, method, path, *, token, json=None, params=None):
        if path.startswith("/api/classes/"):
            cid = path.split("/")[-1]
            m = self.members.get(cid)
            if m is None:
                return 404, {"detail": "not member"}
            if m.get("confirmed") is False:
                return 403, {"detail": "unconfirmed"}
            return 200, m
        if path.endswith("/submission-facts"):
            self.facts_calls += 1
            if self.fail_5xx:
                return 500, {"detail": "boom"}
            assert self.facts_payload is not None, "facts_payload 未设置"
            return 200, self.facts_payload
        return 404, {"detail": "unknown path"}


@pytest_asyncio.fixture
async def r19_env(db):
    transport = R19Transport()
    transport.members[CLASS_A] = {
        "class_id": CLASS_A,
        "user_id": TEACHER,
        "member_role": "teacher",
        "confirmed": True,
    }
    adapter = Api8000Adapter(transport, InMemoryMappingRegistry())
    return {"db": db, "transport": transport, "adapter": adapter, "service": TeacherPrepService()}


async def _create_session(env, *, class_id: str = CLASS_A, key: int = 1) -> str:
    env["transport"].members[class_id] = {
        "class_id": class_id,
        "user_id": TEACHER,
        "member_role": "teacher",
        "confirmed": True,
    }
    res = await env["service"].create_session(
        env["db"],
        adapter=env["adapter"],
        token=TOKEN,
        actor_id=TEACHER,
        class_id=class_id,
        idempotency_key=idem(key),
        request_hash=RH({"class_id": class_id}),
    )
    return res["session_id"]


async def _recalc(env, session_id: str, *, class_id: str = CLASS_A, key: int, **kw):
    return await env["service"].recalculate_profile(
        env["db"],
        adapter=env["adapter"],
        token=TOKEN,
        actor_id=TEACHER,
        actor_role_hint="teacher",
        session_id=session_id,
        class_id=class_id,
        idempotency_key=idem(key),
        request_hash=RH({"k": key}),
        assignment_id=kw.pop("assignment_id", "a1b2c3d4-0001-4000-8000-000000000001"),
        kp_by_item=kw.pop("kp_by_item", dict(KP_BY_ITEM)),
        attempt_policy=kw.pop("attempt_policy", ATTEMPT_POLICY),
        **kw,
    )


# ── 1. 确定性统计 oracle ─────────────────────────────────────────────────────


def test_oracle_golden_metrics() -> None:
    """合成 fixture → 逐数字比对 golden（公式/阈值/去重/分档全锚定）。"""
    payload = _load("valid/workspace_full.json")
    bundle = bundle_from_payload(payload)
    result = compute_profile_metrics(_metrics_input(bundle))

    golden = _load("oracle_golden.json")
    m = result.metrics.model_dump(mode="json")
    for key in (
        "coverage", "sample", "attempt_policy", "observed",
        "confidence", "difficulty_by_item", "class_mean_correct_rate", "kp_map_provided",
    ):
        assert m[key] == golden[key], f"metrics.{key} 与 oracle 不一致"

    mastery = [km.model_dump(mode="json") for km in result.mastery_by_kp]
    assert mastery == golden["mastery_by_kp"]
    misconceptions = [mc.model_dump(mode="json") for mc in result.misconceptions]
    assert misconceptions == golden["misconceptions"]

    # warnings 顺序确定性（schema 校验器推导）+ 教师动作一一对应
    profile = build_profile_body(
        profile_id=str(uuid.uuid4()),
        class_id=CLASS_A,
        session_id=str(uuid.uuid4()),
        owner_id=TEACHER,
        result=result,
        assignment_id="a1b2c3d4-0001-4000-8000-000000000001",
        assignment_title=None,
        due_at=bundle.deadline,
        task_scope=None,
        expires_at=datetime(2026, 9, 30, tzinfo=UTC),
        now=NOW,
    )
    assert list(profile.warnings) == golden["warnings"]
    assert [a.code for a in profile.teacher_actions] == golden["teacher_action_codes"]
    assert all(a.action for a in profile.teacher_actions)


def test_metrics_fingerprint_stable_and_sensitive() -> None:
    """同输入不同 now → 同指纹；单点作答变化 → 指纹变化（重算幂等锚）。"""
    payload = _load("valid/workspace_full.json")
    r1 = compute_profile_metrics(_metrics_input(bundle_from_payload(payload), now=NOW))
    r2 = compute_profile_metrics(
        _metrics_input(bundle_from_payload(payload), now=datetime(2026, 9, 3, tzinfo=UTC))
    )
    assert r1.metrics_fingerprint == r2.metrics_fingerprint

    changed = json.loads(json.dumps(payload))
    # 注意：submissions[0] 是被去重的重复事件；必须改「有效」提交才能改变统计
    changed["submissions"][1]["items"][0]["verdict"] = "wrong"
    r3 = compute_profile_metrics(_metrics_input(bundle_from_payload(changed)))
    assert r1.metrics_fingerprint != r3.metrics_fingerprint
    assert r1.input_digest != r3.input_digest


def test_bundle_invalid_payloads_fail_closed() -> None:
    """畸形素材 fail closed：缺 item_no / 置信度越界 → ValidationError，不静默丢题。"""
    with pytest.raises(ValidationError):
        bundle_from_payload(_load("invalid/missing_item_no.json"))
    with pytest.raises(ValidationError):
        bundle_from_payload(_load("invalid/confidence_out_of_range.json"))


# ── 2. 边界（纯指标层，参数化断言）────────────────────────────────────────────


def _sub(sid, key, csid, items, *, status="graded", at="2026-09-01T08:00:00+00:00", late=None, attempt=None):
    from app.teacher_prep.profile.facts import ItemFacts, SubmissionFacts

    return SubmissionFacts(
        submission_id=sid,
        student_key=key,
        client_submit_id=csid,
        status=status,
        submitted_at=datetime.fromisoformat(at),
        late_seconds=late,
        attempt_number=attempt,
        items=[ItemFacts(**it) for it in items],
    )


def _it(no, verdict, *, conf=0.9, has=True, err=None, pending=False):
    return {
        "item_no": no,
        "q_type": "choice",
        "verdict": "pending_review" if pending else verdict,
        "suggestion_confidence": conf,
        "has_answer": has,
        "needs_review": pending,
        "error_type": err,
    }


def _bundle(subs, eligible):
    from app.teacher_prep.profile.facts import SubmissionFactsBundle

    return SubmissionFactsBundle(
        assignment_id="assn-1",
        class_id=CLASS_A,
        deadline=datetime(2026, 9, 1, 10, 0, tzinfo=UTC),
        eligible_student_keys=eligible,
        submissions=subs,
    )


K = "k0000000000000000000000000000000"  # 伪名化键样例（32hex）


def test_edge_no_data_unknown_and_actions() -> None:
    """无数据 → no_data + 全 unknown + 教师动作；不得给任何肯定结论。"""
    res = compute_profile_metrics(_metrics_input(_bundle([], [K, K + "2"])))
    m = res.metrics
    assert m.sample.sample_size == 0 and m.sample.sufficient is False
    # eligible=2、answered=0 → 覆盖率是确定的 0.0（不是 unknown）；unknown 只出现在
    # eligible 本身无法确定（0）的场景
    assert m.coverage.submission_rate == 0.0 and m.coverage.answered == 0
    assert m.coverage.low_coverage is True
    assert all(km.mastery is None for km in res.mastery_by_kp)
    assert m.class_mean_correct_rate is None
    assert m.difficulty_by_item == []
    assert "no_data" in _warn_codes(m)


def test_edge_zero_eligible_rate_unknown() -> None:
    """eligible=0（无当前学生成员）→ 覆盖率显式 unknown（None），不伪造 0/0。"""
    subs = [_sub("s1", K, "c1", [_it(1, "correct")])]
    res = compute_profile_metrics(_metrics_input(_bundle(subs, [])))
    assert res.metrics.coverage.eligible == 0
    assert res.metrics.coverage.submission_rate is None
    assert res.metrics.observed.non_member_submissions == 1


def test_edge_low_coverage_and_not_submitted() -> None:
    subs = [_sub("s1", K, "c1", [_it(1, "correct")])]
    res = compute_profile_metrics(
        _metrics_input(_bundle(subs, [K] + [K + str(i) for i in range(2, 11)]))
    )
    assert res.metrics.coverage.eligible == 10
    assert res.metrics.coverage.answered == 1
    assert res.metrics.coverage.not_submitted == 9
    assert res.metrics.coverage.submission_rate == 0.1
    assert res.metrics.coverage.low_coverage is True
    assert "low_profile_coverage" in _warn_codes(res.metrics)


def test_edge_late_and_resubmit_counts_only() -> None:
    subs = [
        _sub("s1", K, "c1", [_it(1, "correct")], late=5400),
        _sub("s2", K + "2", "c2", [_it(1, "wrong")], status="resubmitted", attempt=2),
    ]
    res = compute_profile_metrics(_metrics_input(_bundle(subs, [K, K + "2"])))
    assert res.metrics.attempt_policy.late_count == 1
    assert res.metrics.attempt_policy.resubmit_count == 1
    codes = _warn_codes(res.metrics)
    assert "late_submissions" in codes and "resubmissions" in codes
    # 不具名：统计块无学生标识
    assert K not in res.metrics.model_dump_json()


def test_edge_duplicate_events_keep_latest() -> None:
    """同 (student, client_submit_id) ×3 → 保留 submitted_at 最新，丢弃计数 2。"""
    subs = [
        _sub("s-old", K, "c1", [_it(1, "wrong")], at="2026-09-01T08:00:00+00:00"),
        _sub("s-new", K, "c1", [_it(1, "correct")], at="2026-09-01T08:30:00+00:00"),
        _sub("s-mid", K, "c1", [_it(1, "wrong")], at="2026-09-01T08:10:00+00:00"),
    ]
    res = compute_profile_metrics(_metrics_input(_bundle(subs, [K])))
    assert res.metrics.observed.duplicates_dropped == 2
    assert res.metrics.sample.sample_size == 1
    assert res.metrics.difficulty_by_item[0].correct == 1 and res.metrics.difficulty_by_item[0].wrong == 0
    assert "duplicates_dropped" in _warn_codes(res.metrics)


def test_edge_outlier_excluded_from_mean() -> None:
    """9 题：8 题 rate=0.8、1 题 rate=0.0 → IQR 围栏（[0.8,0.8]）剔极端题出均值。"""
    subs = []
    for i in range(5):  # 5 名学生
        items = []
        for no in range(1, 9):  # 8 题：轮转 1 人错 → rate 0.8
            wrong_student = (no - 1) % 5
            items.append(_it(no, "wrong" if i == wrong_student else "correct"))
        items.append(_it(9, "wrong"))  # 第 9 题：全错 → rate 0.0（离群）
        subs.append(_sub(f"s{i}", K + str(i), f"c{i}", items))
    res = compute_profile_metrics(_metrics_input(_bundle(subs, [K + str(i) for i in range(5)])))
    codes = _warn_codes(res.metrics)
    assert "outliers_excluded" in codes
    assert res.metrics.observed.outliers_excluded == 1
    by_no = {d.item_no: d for d in res.metrics.difficulty_by_item}
    assert by_no[9].outlier is True and by_no[9].difficulty is None
    # rate 0.8 ≥ 0.8 → easy（分档阈值见 PROFILE-METRICS.md）
    assert by_no[1].outlier is False and by_no[1].difficulty == "easy"
    assert res.metrics.class_mean_correct_rate == 0.8  # 离群题不参与均值


def test_edge_missing_answers_and_confidence() -> None:
    subs = [
        _sub("s1", K, "c1", [_it(1, "correct", conf=None, has=False)]),
        _sub("s2", K + "2", "c2", [_it(1, "correct", conf=0.5)]),
    ]
    res = compute_profile_metrics(_metrics_input(_bundle(subs, [K, K + "2"])))
    assert res.metrics.observed.missing_answers_count == 1
    assert res.metrics.observed.pending_review_count == 0
    assert res.metrics.confidence.missing_count == 1
    codes = _warn_codes(res.metrics)
    assert "missing_answers" in codes and "missing_confidence" in codes


def test_edge_membership_changed() -> None:
    subs = [_sub("s1", K, "c1", [_it(1, "correct")]) for _ in range(1)]
    res = compute_profile_metrics(
        _metrics_input(_bundle(subs, [K, K + "2", K + "3", K + "4", K + "5"]), prior_eligible_count=10)
    )
    assert res.metrics.coverage.eligible_changed is True
    assert "membership_changed" in _warn_codes(res.metrics)


def test_edge_non_member_submissions_excluded() -> None:
    """非当前成员的提交不计入统计总体（成员变更口径），只留计数。"""
    subs = [
        _sub("s1", K, "c1", [_it(1, "correct")]),
        _sub("s2", "gone0000000000000000000000000001", "c2", [_it(1, "wrong")]),
    ]
    res = compute_profile_metrics(_metrics_input(_bundle(subs, [K])))
    assert res.metrics.observed.non_member_submissions == 1
    assert res.metrics.coverage.answered == 1
    assert res.metrics.difficulty_by_item[0].judged == 1


def test_edge_attempt_policy_unknown() -> None:
    subs = [_sub("s1", K, "c1", [_it(1, "correct")])]
    res = compute_profile_metrics(_metrics_input(_bundle(subs, [K]), attempt_policy=None))
    assert res.metrics.attempt_policy.known is False
    assert "attempt_policy_unknown" in _warn_codes(res.metrics)


def test_edge_kp_map_missing_no_mastery() -> None:
    subs = [_sub("s1", K, "c1", [_it(1, "correct")])]
    res = compute_profile_metrics(_metrics_input(_bundle(subs, [K]), kp_by_item={}))
    assert res.mastery_by_kp == [] and res.metrics.kp_map_provided is False
    assert "missing_item_kp_map" in _warn_codes(res.metrics)


def test_edge_low_sample_no_affirmative_conclusion() -> None:
    """样本 <5：mastery/difficulty = None（显式 unknown），sufficient=False。"""
    subs = [
        _sub("s1", K, "c1", [_it(1, "correct"), _it(2, "wrong")]),
        _sub("s2", K + "2", "c2", [_it(1, "correct"), _it(2, "correct")]),
    ]
    res = compute_profile_metrics(_metrics_input(_bundle(subs, [K, K + "2"])))
    assert res.metrics.sample.sample_size == 2
    assert res.metrics.sample.sufficient is False
    assert all(km.mastery is None for km in res.mastery_by_kp)
    assert all(d.difficulty is None for d in res.metrics.difficulty_by_item)
    assert "low_sample" in _warn_codes(res.metrics)
    assert MIN_SAMPLE == 5


def _warn_codes(metrics) -> list[str]:
    from app.teacher_prep.profile.schema import _derive_metric_warnings

    return _derive_metric_warnings(metrics)


# ── 3. 服务层：版本/重算幂等 + 证据 + 权限 + AI ───────────────────────────────


async def test_recalculate_publishes_versioned_profile(r19_env) -> None:
    env = r19_env
    env["transport"].facts_payload = _load("valid/workspace_full.json")
    sid = await _create_session(env)
    res = await _recalc(env, sid, key=2)
    assert res["result"] == "updated" and res["revision"] == 1
    assert res["status"] == "published"

    prow = (
        await env["db"].execute(
            select(ClassLearningProfileRow).where(ClassLearningProfileRow.id == uuid.UUID(res["profile_id"]))
        )
    ).scalar_one()
    body = prow.body
    # R19 必需字段齐备
    for key in (
        "task_scope", "time_window", "metrics", "evidence_refs", "teacher_actions",
        "aggregation", "warnings", "computed_from",
    ):
        assert key in body, f"body 缺 {key}"
    assert body["metrics"]["calc_version"] == CALC_VERSION
    # body.profile_id 与行 id 一致
    assert body["profile_id"] == res["profile_id"]
    # coverage 冗余列（R20 evidence 快照口径）与 body.metrics.coverage 一致
    assert prow.coverage["coverage_ratio"] == 0.8333
    assert prow.coverage["low_coverage"] is False
    assert body["metrics"]["coverage"]["answered"] == 5
    assert prow.warnings == body["warnings"]


async def test_recalculate_idempotent_replay(r19_env) -> None:
    """同数据再次重算（新幂等键）→ replayed：不新增版本、不重复落证据。"""
    env = r19_env
    env["transport"].facts_payload = _load("valid/workspace_full.json")
    sid = await _create_session(env)
    first = await _recalc(env, sid, key=2)
    assert first["result"] == "updated"

    ev_before = (
        (
            await env["db"].execute(
                select(TeachingEvidenceRow).where(
                    TeachingEvidenceRow.profile_id == uuid.UUID(first["profile_id"]),
                    TeachingEvidenceRow.status == "valid",
                )
            )
        )
        .scalars()
        .all()
    )
    second = await _recalc(env, sid, key=3)
    assert second["result"] == "replayed"
    assert second["revision"] == first["revision"] == 1
    assert second["metrics_fingerprint"] == first["metrics_fingerprint"]

    ev_after = (
        (
            await env["db"].execute(
                select(TeachingEvidenceRow).where(
                    TeachingEvidenceRow.profile_id == uuid.UUID(first["profile_id"]),
                    TeachingEvidenceRow.status == "valid",
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(ev_after) == len(ev_before)  # 幂等：证据行不重复


async def test_recalculate_version_bump_keeps_old_and_invalidates_evidence(r19_env) -> None:
    """数据变化 → revision++；旧 revision body 不可变；旧指纹证据行失效（保留审计）。"""
    env = r19_env
    payload = _load("valid/workspace_full.json")
    env["transport"].facts_payload = payload
    sid = await _create_session(env)
    first = await _recalc(env, sid, key=2)

    changed = json.loads(json.dumps(payload))
    # submissions[1]（学生 b 的有效提交）改判 → 统计变化 → 指纹变化 → 新版本
    changed["submissions"][1]["items"][0]["verdict"] = "wrong"
    env["transport"].facts_payload = changed
    second = await _recalc(env, sid, key=3, profile_id=first["profile_id"], expected_revision=1)

    assert second["result"] == "updated" and second["revision"] == 2
    assert second["metrics_fingerprint"] != first["metrics_fingerprint"]

    revs = (
        (
            await env["db"].execute(
                select(ClassLearningProfileRevisionRow)
                .where(ClassLearningProfileRevisionRow.profile_id == uuid.UUID(first["profile_id"]))
                .order_by(ClassLearningProfileRevisionRow.revision_no)
            )
        )
        .scalars()
        .all()
    )
    assert [r.revision_no for r in revs] == [1, 2]
    assert revs[0].body["metrics"]["metrics_fingerprint"] == first["metrics_fingerprint"]  # 旧快照不可变
    assert revs[1].body["metrics"]["metrics_fingerprint"] == second["metrics_fingerprint"]

    rows = (
        (
            await env["db"].execute(
                select(TeachingEvidenceRow).where(TeachingEvidenceRow.profile_id == uuid.UUID(first["profile_id"]))
            )
        )
        .scalars()
        .all()
    )
    valid_rows = [r for r in rows if r.status == "valid"]
    invalid_rows = [r for r in rows if r.status == "invalid"]
    assert invalid_rows, "旧指纹证据行应被标记失效"
    assert all(r.metrics_fingerprint == second["metrics_fingerprint"] for r in valid_rows)
    # 失效行内容保留（审计），只是退出引用集
    assert all(r.value for r in invalid_rows)


async def test_evidence_traceability_typed_refs(r19_env) -> None:
    """证据行 typed refs 可追溯到 8000 submission id；画像 evidence_refs 引用证据行。"""
    env = r19_env
    payload = _load("valid/workspace_full.json")
    env["transport"].facts_payload = payload
    sid = await _create_session(env)
    res = await _recalc(env, sid, key=2)

    detail = await env["service"].get_profile(
        env["db"], adapter=env["adapter"], token=TOKEN, session_id=sid, profile_id=res["profile_id"]
    )
    body = detail["body"]
    assert body["evidence_refs"], "画像必须引用证据行"
    ref_ids = {r["evidence_id"] for r in body["evidence_refs"]}
    ev_ids = {e["evidence_id"] for e in detail["evidence"]}
    assert ref_ids <= ev_ids

    # typed refs → 8000 submission 外部 id（fixture 中的提交行）
    external_ids = {
        ref["external_id"]
        for e in detail["evidence"]
        for ref in e["refs"]
        if ref.get("kind") == "submission"
    }
    fixture_submission_ids = {s["submission_id"] for s in payload["submissions"]}
    assert external_ids and external_ids <= fixture_submission_ids
    # 每条证据带计算血缘
    assert all(e["calc_version"] == CALC_VERSION for e in detail["evidence"])


# PII 标记扫描用词边界正则：避免 "profile_id" 天然包含 "file_id" 的误报
FORBIDDEN_PII_RE = re.compile(
    r"(?<![a-z_])(file_id|answer_text|attachments|nickname|suggestion_rationale"
    r"|suggestion_feedback|user_id|student_key)(?![a-z_])"
)


async def test_pii_minimization(r19_env) -> None:
    """画像 body / 证据行 / API 响应零 PII：无答案内容、无学生标识、无文件引用。"""
    env = r19_env
    payload = _load("valid/workspace_full.json")
    env["transport"].facts_payload = payload
    sid = await _create_session(env)
    res = await _recalc(env, sid, key=2)
    detail = await env["service"].get_profile(
        env["db"], adapter=env["adapter"], token=TOKEN, session_id=sid, profile_id=res["profile_id"]
    )
    blob = json.dumps(detail, ensure_ascii=False, default=str)
    leak = FORBIDDEN_PII_RE.search(blob)
    assert leak is None, f"响应泄漏 PII 标记: {leak.group(0) if leak else ''}"
    # fixture 中的伪名键不落任何持久化结构（仅内存统计用）；
    # 提交行 id 允许出现（typed ref 锚点，见 traceability 测试）。
    for s in payload["submissions"]:
        assert s["student_key"] not in blob


async def test_cross_class_access_denied(r19_env) -> None:
    """跨班 403：B 班会话/班级口径访问 A 班素材与画像被当前成员关系拒绝。"""
    env = r19_env
    env["transport"].facts_payload = _load("valid/workspace_full.json")
    sid_a = await _create_session(env, class_id=CLASS_A, key=1)
    # 教师不是 CLASS_B 成员
    with pytest.raises(MembershipDeniedError):
        await _recalc(env, sid_a, class_id=CLASS_B, key=2)
    # 画像创建后，从另一会话（同教师、B 班口径）读取 → 403
    first = await _recalc(env, sid_a, key=3)
    sid_b = await _create_session(env, class_id=CLASS_B, key=4)
    env["transport"].members[CLASS_B] = {
        "class_id": CLASS_B, "user_id": TEACHER, "member_role": "teacher", "confirmed": True,
    }
    with pytest.raises(MembershipDeniedError):
        await env["service"].get_profile(
            env["db"], adapter=env["adapter"], token=TOKEN,
            session_id=sid_b, profile_id=first["profile_id"],
        )


async def test_member_removal_permission_correct(r19_env) -> None:
    """成员移除后：读/写全部 403；恢复成员后权限恢复；画像数据零变化。"""
    env = r19_env
    env["transport"].facts_payload = _load("valid/workspace_full.json")
    sid = await _create_session(env)
    first = await _recalc(env, sid, key=2)

    env["transport"].members.pop(CLASS_A)  # 教师被移出班级
    with pytest.raises(MembershipDeniedError):
        await env["service"].get_profile(
            env["db"], adapter=env["adapter"], token=TOKEN,
            session_id=sid, profile_id=first["profile_id"],
        )
    with pytest.raises(MembershipDeniedError):
        await _recalc(env, sid, key=3, profile_id=first["profile_id"], expected_revision=1)

    env["transport"].members[CLASS_A] = {
        "class_id": CLASS_A, "user_id": TEACHER, "member_role": "teacher", "confirmed": True,
    }
    detail = await env["service"].get_profile(
        env["db"], adapter=env["adapter"], token=TOKEN, session_id=sid, profile_id=first["profile_id"]
    )
    assert detail["revision"] == first["revision"]  # 数据零变化


class BoomInterpreter:
    async def interpret(self, payload: dict) -> dict:
        raise RuntimeError("llm down")


class FakeInterpreter:
    async def interpret(self, payload: dict) -> dict:
        assert "metrics" in payload and "aggregation" in payload
        assert "student_key" not in json.dumps(payload)  # 解释输入零 PII
        return {"summary": "K1 掌握偏低，建议复习符号运算", "proposal_items": ["K1 复习"]}


async def test_ai_failure_does_not_touch_stats(r19_env) -> None:
    """AI 失败/未配置 → basic_mode；画像与统计零改动。"""
    env = r19_env
    env["transport"].facts_payload = _load("valid/workspace_full.json")
    sid = await _create_session(env)
    first = await _recalc(env, sid, key=2)

    res1 = await env["service"].interpret_profile(
        env["db"], adapter=env["adapter"], token=TOKEN,
        actor_id=TEACHER, actor_role_hint="teacher",
        session_id=sid, profile_id=first["profile_id"],
        idempotency_key=idem(3), request_hash=RH({"i": 3}),
        interpreter=None,
    )
    assert res1["basic_mode"] is True and res1["reason"] == "interpreter_not_configured"

    res2 = await env["service"].interpret_profile(
        env["db"], adapter=env["adapter"], token=TOKEN,
        actor_id=TEACHER, actor_role_hint="teacher",
        session_id=sid, profile_id=first["profile_id"],
        idempotency_key=idem(4), request_hash=RH({"i": 4}),
        interpreter=BoomInterpreter(),
    )
    assert res2["basic_mode"] is True and res2["reason"].startswith("interpreter_error:")

    # 基础统计零改动
    detail = await env["service"].get_profile(
        env["db"], adapter=env["adapter"], token=TOKEN, session_id=sid, profile_id=first["profile_id"]
    )
    assert detail["revision"] == first["revision"]
    assert detail["body"]["metrics"]["metrics_fingerprint"] == first["metrics_fingerprint"]


async def test_ai_success_stores_proposal_as_evidence(r19_env) -> None:
    """AI 成功 → proposal 存为 ai_suggestion 证据行；统计仍零改动（proposal 不升格）。"""
    env = r19_env
    env["transport"].facts_payload = _load("valid/workspace_full.json")
    sid = await _create_session(env)
    first = await _recalc(env, sid, key=2)

    res = await env["service"].interpret_profile(
        env["db"], adapter=env["adapter"], token=TOKEN,
        actor_id=TEACHER, actor_role_hint="teacher",
        session_id=sid, profile_id=first["profile_id"],
        idempotency_key=idem(3), request_hash=RH({"i": 3}),
        interpreter=FakeInterpreter(),
    )
    assert res["basic_mode"] is False and res["proposal"]["summary"]
    ev = (
        await env["db"].execute(
            select(TeachingEvidenceRow).where(TeachingEvidenceRow.id == uuid.UUID(res["evidence_id"]))
        )
    ).scalar_one()
    assert ev.source == "ai_suggestion" and ev.ev_kind == "interpretation"
    detail = await env["service"].get_profile(
        env["db"], adapter=env["adapter"], token=TOKEN, session_id=sid, profile_id=first["profile_id"]
    )
    assert detail["revision"] == first["revision"]  # 解释不改变画像版本


async def test_authority_unreachable_fail_closed(r19_env) -> None:
    """8000 5xx → 权威不可达（fail closed），不产出降级/伪造画像。"""
    from app.teacher_prep.service import AuthorityUnreachableError

    env = r19_env
    env["transport"].fail_5xx = True
    sid = await _create_session(env)
    with pytest.raises(AuthorityUnreachableError):
        await _recalc(env, sid, key=2)
