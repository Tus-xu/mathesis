"""R19 确定性学情统计引擎（StudentSubmissionFacts → ClassLearningProfile 统计块）。

铁律（R19 人工指令 + DECISION-IMPLEMENTATION-DELTA D3/D7/D10/D11/D14/D15）：
- **纯函数**：同输入 + 同 now → 同输出（oracle golden 测试锚定）；零 LLM、零网络、零时钟
  读取（now 一律由入参给定）；
- 统计数字全部由本模块确定性计算；AI 只在解释层（interpret_profile）产 proposal；
- 低样本（<MIN_SAMPLE）不得下肯定结论：对应指标值 = None（显式 unknown）+ low_sample
  warning + 教师动作；
- PII 边界：输入已是伪名化素材（facts.py）；本模块输出只含计数/比率/分档/typed refs，
  无学生标识、无答案内容；
- 重复事件：同 submission_id 或同 (student_key, client_submit_id) 的重复 → 保留
  submitted_at 最新（平局取 submission_id 字典序最大），其余丢弃并计数；
- 异常值：题目正确率 IQR 围栏（1.5×IQR，最近秩四分位）外的题目**不参与班级均值**，
  计数 outliers_excluded，题目行保留并标记 outlier=True。

口径详见 `R19/PROFILE-METRICS.md`（公式/阈值/unknown 语义/教师动作映射）。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.teacher_prep.profile.facts import ItemFacts, SubmissionFacts, SubmissionFactsBundle
from app.teacher_prep.profile.schema import (
    AttemptPolicyMetrics,
    ConfidenceMetrics,
    CoverageMetrics,
    ItemDifficulty,
    ObservedAnomalies,
    ProfileMetricsInfo,
    SampleMetrics,
    TeacherAction,
)
from app.teacher_prep_v2.adapters.mapping import stable_digest
from app.teacher_prep_v2.contracts.models import KpMastery, Misconception

# 计算版本：口径/阈值语义变化必须 bump（可复算血缘）
CALC_VERSION = "r19-metrics-1.0.0"
# 低样本阈值：任一指标样本量低于该值 → 值=None + low_sample（不下肯定结论）
MIN_SAMPLE = 5
# 题目难度分档（correct_rate；judged >= MIN_SAMPLE 才给档）
DIFFICULTY_EASY_RATE, DIFFICULTY_MEDIUM_RATE = 0.8, 0.5
# confidence 分档（与 R20 aggregator 同阈值）
_LOW_BOUND, _MID_BOUND = 0.6, 0.85
# IQR 离群围栏系数；离群检测最少题目数
IQR_FACTOR, MIN_ITEMS_FOR_OUTLIER = 1.5, 4


class MetricsInput(BaseModel):
    """一次确定性计算的输入（全部显式；缺失=unknown，不猜测）。"""

    model_config = ConfigDict(extra="forbid")

    bundle: SubmissionFactsBundle
    kp_by_item: dict[str, list[int]] = Field(default_factory=dict)  # kp_code → item_nos
    attempt_policy: AttemptPolicyMetrics | None = None  # None → attempt_policy_unknown
    prior_eligible_count: int | None = None  # 上一 revision 的 eligible（成员变更检测）
    now: datetime
    run_id: str = Field(min_length=1, max_length=128)
    expires_at: datetime | None = None


class MetricsResult(BaseModel):
    """确定性计算输出（统计块 + 聚合 + 指纹；warnings 由 schema 统一推导）。"""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    metrics: ProfileMetricsInfo
    mastery_by_kp: list[KpMastery]
    misconceptions: list[Misconception]
    input_digest: str
    metrics_fingerprint: str
    evidence_start: datetime | None
    evidence_end: datetime | None
    facts_refs: dict[str, list[dict[str, Any]]]  # metric_key → typed ref entries
    deduped_submission_count: int = Field(default=0, ge=0)  # 去重后有效提交行数
    kp_item_nos: dict[str, list[int]] = Field(default_factory=dict)  # kp_code → item_nos


# ── 去重与有效性 ──────────────────────────────────────────────────────────────


def _naive(dt: datetime | None) -> datetime | None:
    """时间归一化：aware → naive（按 UTC 语义剥 tzinfo），保证排序/极值确定性。"""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


def _dedupe_submissions(submissions: list[SubmissionFacts]) -> tuple[list[SubmissionFacts], int]:
    """重复事件去重：先按 submission_id（行重复），再按 (student_key, client_submit_id)。"""
    by_row: dict[str, SubmissionFacts] = {}
    dropped = 0
    for s in submissions:
        existing = by_row.get(s.submission_id)
        if existing is None:
            by_row[s.submission_id] = s
            continue
        dropped += 1
        if _newer(s, existing):
            by_row[s.submission_id] = s
    by_student: dict[tuple[str, str], SubmissionFacts] = {}
    for s in by_row.values():
        key = (s.student_key, s.client_submit_id or s.submission_id)
        existing = by_student.get(key)
        if existing is None:
            by_student[key] = s
            continue
        dropped += 1
        if _newer(s, existing):
            by_student[key] = s
    ordered = sorted(
        by_student.values(),
        key=lambda s: (_naive(s.submitted_at) or datetime.min, s.submission_id),
    )
    return ordered, dropped


def _newer(a: SubmissionFacts, b: SubmissionFacts) -> bool:
    """平局确定性：时间新者胜；同时刻取 submission_id 字典序最大者。"""
    ta = _naive(a.submitted_at) or datetime.min
    tb = _naive(b.submitted_at) or datetime.min
    if ta != tb:
        return ta > tb
    return a.submission_id > b.submission_id


def _is_valid(s: SubmissionFacts) -> bool:
    """有效提交：排除 drafted/archived（未真正作答的事实行）。"""
    return s.status not in ("drafted", "archived")


# ── IQR 离群 ─────────────────────────────────────────────────────────────────


def _quartile(sorted_vals: list[float], q: int) -> float:
    """最近秩四分位（确定性、零插值）：Q1=第 ⌈n/4⌉ 个，Q3=第 ⌈3n/4⌉ 个。"""
    n = len(sorted_vals)
    idx = max(0, min(n - 1, -(-n * q // 4) - 1))
    return sorted_vals[idx]


def _detect_outliers(rates: dict[int, float]) -> set[int]:
    """IQR 围栏外的 item_no 集合（<MIN_ITEMS_FOR_OUTLIER 题 → 不检测，确定性 no-op）。"""
    if len(rates) < MIN_ITEMS_FOR_OUTLIER:
        return set()
    vals = sorted(rates.values())
    q1, q3 = _quartile(vals, 1), _quartile(vals, 3)
    iqr = q3 - q1
    lo, hi = q1 - IQR_FACTOR * iqr, q3 + IQR_FACTOR * iqr
    return {item_no for item_no, r in rates.items() if r < lo or r > hi}


# ── 主计算 ───────────────────────────────────────────────────────────────────


def compute_profile_metrics(inp: MetricsInput) -> MetricsResult:
    """确定性计算全部 R19 统计（口径见 PROFILE-METRICS.md；禁止任何随机性）。"""
    bundle = inp.bundle
    deduped, duplicates_dropped = _dedupe_submissions(
        [s for s in bundle.submissions if _is_valid(s)]
    )
    # 成员变更口径：统计总体 = 当前 eligible 成员；非成员提交不计入任何统计
    #（只留观测计数；成员变化本身由 eligible_changed → membership_changed 表达）
    eligible_keys = sorted(set(bundle.eligible_student_keys))
    eligible_set = set(eligible_keys)
    if eligible_set:
        non_member = sum(1 for s in deduped if s.student_key not in eligible_set)
        valid = [s for s in deduped if s.student_key in eligible_set]
    else:
        # eligible 为空（班级无当前学生成员）→ 无统计总体
        non_member = len(deduped)
        valid = []
    duplicates_dropped += 0  # 语义保持：duplicates 仅统计重复事件

    answered_keys = {s.student_key for s in valid}
    eligible = len(eligible_keys)
    answered = len(answered_keys)
    not_submitted = max(0, eligible - answered)
    submission_rate = (
        round(answered / eligible, 4) if eligible > 0 else None
    )
    low_coverage = eligible > 0 and submission_rate is not None and submission_rate < 0.5
    eligible_changed = (
        None if inp.prior_eligible_count is None else inp.prior_eligible_count != eligible
    )

    # 逐题观测（按 submission 去重后的 items）
    item_nos: set[int] = set()
    per_item: dict[int, dict[str, int]] = defaultdict(
        lambda: {"answered": 0, "correct": 0, "wrong": 0, "pending": 0, "no_answer": 0, "conf_missing": 0}
    )
    conf_values: list[float] = []
    conf_missing = 0
    late_count = 0
    resubmit_count = 0
    facts_refs: dict[str, list[dict[str, Any]]] = {}

    for s in valid:
        if s.late_seconds is not None and s.late_seconds > 0 or s.status == "late":
            late_count += 1
        if (s.attempt_number is not None and s.attempt_number >= 2) or s.status == "resubmitted":
            resubmit_count += 1
        seen_items: set[int] = set()
        for it in s.items:
            if it.item_no in seen_items:
                continue  # 单份提交内 item_no 重复 → 只计一次（transport 重复防护）
            seen_items.add(it.item_no)
            item_nos.add(it.item_no)
            row = per_item[it.item_no]
            row["answered"] += 1
            if it.is_correct:
                row["correct"] += 1
            elif it.is_wrong:
                row["wrong"] += 1
            else:
                row["pending"] += 1
            if not it.has_answer:
                row["no_answer"] += 1
            if it.suggestion_confidence is None:
                row["conf_missing"] += 1
                conf_missing += 1
            else:
                conf_values.append(float(it.suggestion_confidence))
            facts_refs.setdefault(f"item:{it.item_no}", []).append(
                {"kind": "submission", "external_id": s.submission_id, "item_no": it.item_no}
            )

    # 题目正确率（judged=correct+wrong；pending 不参与，避免未批改污染比率）
    rates: dict[int, float] = {}
    judged_counts: dict[int, int] = {}
    for no in item_nos:
        row = per_item[no]
        judged = row["correct"] + row["wrong"]
        judged_counts[no] = judged
        if judged > 0:
            rates[no] = round(row["correct"] / judged, 4)
    outliers = _detect_outliers(rates)

    difficulty_by_item: list[ItemDifficulty] = []
    mean_pool: list[float] = []
    for no in sorted(item_nos):
        row = per_item[no]
        judged = judged_counts[no]
        rate = rates.get(no)
        is_outlier = no in outliers
        if rate is not None and not is_outlier:
            mean_pool.append(rate)
        difficulty: str | None = None
        if rate is not None and judged >= MIN_SAMPLE and not is_outlier:
            difficulty = (
                "easy" if rate >= DIFFICULTY_EASY_RATE
                else "medium" if rate >= DIFFICULTY_MEDIUM_RATE
                else "hard"
            )
        difficulty_by_item.append(
            ItemDifficulty(
                item_no=no,
                answered=row["answered"],
                judged=judged,
                correct=row["correct"],
                wrong=row["wrong"],
                pending=row["pending"],
                correct_rate=rate,
                difficulty=difficulty,
                outlier=is_outlier,
            )
        )
    class_mean = round(sum(mean_pool) / len(mean_pool), 4) if mean_pool else None
    pending_review_count = sum(r["pending"] for r in per_item.values())
    missing_answers_count = sum(r["no_answer"] for r in per_item.values())

    # 前置知识掌握（kp 映射缺失 → 不给 mastery，显式 unknown）
    kp_map_provided = bool(inp.kp_by_item)
    mastery_by_kp: list[KpMastery] = []
    misconceptions: list[Misconception] = []
    if kp_map_provided:
        items_by_no: dict[int, list[ItemFacts]] = defaultdict(list)
        for s in valid:
            seen: set[int] = set()
            for it in s.items:
                if it.item_no not in seen:
                    seen.add(it.item_no)
                    items_by_no[it.item_no].append(it)
        for kp_code in sorted(inp.kp_by_item):
            nos = sorted(set(inp.kp_by_item[kp_code]))
            correct = wrong = 0
            wrong_error_types: Counter[str] = Counter()
            for no in nos:
                correct += per_item[no]["correct"]
                wrong += per_item[no]["wrong"]
            for no in nos:
                for it in items_by_no.get(no, []):
                    if it.is_wrong:
                        wrong_error_types[it.error_type or "unclassified_wrong"] += 1
            judged = correct + wrong
            mastery = round(correct / judged, 4) if judged > 0 else None
            if judged < MIN_SAMPLE:
                mastery = None  # 低样本不下肯定结论
            mastery_by_kp.append(
                KpMastery(kp_code=kp_code, mastery=mastery, sample_size=judged, confidence=None)
            )
            if wrong > 0:
                label = max(sorted(wrong_error_types.items()), key=lambda kv: (kv[1], kv[0]))[0]
                misconceptions.append(Misconception(kp_code=kp_code, label=label, count=wrong))
        misconceptions.sort(key=lambda m: (-m.count, m.kp_code))
        misconceptions = misconceptions[:20]

    # confidence 汇总
    conf_dist = Counter(
        "low" if c < _LOW_BOUND else "mid" if c < _MID_BOUND else "high" for c in conf_values
    )
    confidence = ConfidenceMetrics(
        mean=round(sum(conf_values) / len(conf_values), 4) if conf_values else None,
        low=conf_dist["low"],
        mid=conf_dist["mid"],
        high=conf_dist["high"],
        unknown=0,
        missing_count=conf_missing,
    )

    # attempt policy（缺失 → known=False，不猜测）
    if inp.attempt_policy is not None:
        attempt_policy = inp.attempt_policy.model_copy(
            update={"late_count": late_count, "resubmit_count": resubmit_count}
        )
    else:
        attempt_policy = AttemptPolicyMetrics(
            known=False, late_count=late_count, resubmit_count=resubmit_count
        )

    metrics = ProfileMetricsInfo(
        calc_version=CALC_VERSION,
        run_id=inp.run_id,
        computed_at=inp.now,
        metrics_fingerprint="0" * 64,  # 占位：先算指纹再回填
        coverage=CoverageMetrics(
            eligible=eligible,
            answered=answered,
            not_submitted=not_submitted,
            submission_rate=submission_rate,
            low_coverage=low_coverage,
            eligible_changed=eligible_changed,
        ),
        sample=SampleMetrics(
            sample_size=answered, sufficient=answered >= MIN_SAMPLE
        ),
        attempt_policy=attempt_policy,
        observed=ObservedAnomalies(
            duplicates_dropped=duplicates_dropped,
            outliers_excluded=len(outliers),
            pending_review_count=pending_review_count,
            missing_answers_count=missing_answers_count,
            non_member_submissions=non_member,
        ),
        confidence=confidence,
        difficulty_by_item=difficulty_by_item,
        class_mean_correct_rate=class_mean,
        kp_map_provided=kp_map_provided,
    )

    input_digest = stable_digest(
        "prep:profile:input",
        {
            "assignment_id": bundle.assignment_id,
            "class_id": bundle.class_id,
            "deadline": bundle.deadline.isoformat() if bundle.deadline else None,
            "eligible_count": eligible,
            # 内容感知：每份有效提交的判定/分数/置信/作答布尔全部进入指纹
            #（保证「同输入 → 同统计」的内容级血缘，而非仅提交 id 集合）
            "submissions": sorted(
                (
                    s.submission_id,
                    s.status,
                    s.late_seconds,
                    tuple(
                        (it.item_no, it.verdict, it.score, it.teacher_final_score,
                         it.suggestion_confidence, it.has_answer, it.needs_review, it.error_type)
                        for it in s.items
                    ),
                )
                for s in valid
            ),
            "kp_by_item": {k: sorted(v) for k, v in sorted(inp.kp_by_item.items())},
            "calc_version": CALC_VERSION,
        },
    )
    fingerprint = _metrics_fingerprint(metrics)
    metrics = metrics.model_copy(update={"metrics_fingerprint": fingerprint})

    evidence_start = _naive(min((_naive(s.submitted_at) for s in valid if s.submitted_at), default=None))
    evidence_end = _naive(max((_naive(s.submitted_at) for s in valid if s.submitted_at), default=None))

    return MetricsResult(
        metrics=metrics,
        mastery_by_kp=mastery_by_kp,
        misconceptions=misconceptions,
        input_digest=input_digest,
        metrics_fingerprint=fingerprint,
        evidence_start=evidence_start,
        evidence_end=evidence_end,
        facts_refs=facts_refs,
        deduped_submission_count=len(valid),
        kp_item_nos={k: sorted(set(v)) for k, v in sorted(inp.kp_by_item.items())},
    )


def _metrics_fingerprint(metrics: ProfileMetricsInfo) -> str:
    """统计指纹：stats 全量 canonical hash（排除指纹自身）；重算幂等的比较锚。

    排除项：metrics_fingerprint（自指）/ computed_at（计算时刻）/ run_id（执行实例）
    / coverage.eligible_changed（上下文派生标记——成员数变化本身已通过
    coverage.eligible 进入指纹；None→False 的先例出现不得视为统计变化）。
    """
    payload = metrics.model_dump(mode="json")
    payload.pop("metrics_fingerprint", None)
    payload.pop("computed_at", None)  # 计算时刻不参与内容指纹（stats 本身确定性）
    payload.pop("run_id", None)
    if isinstance(payload.get("coverage"), dict):
        payload["coverage"].pop("eligible_changed", None)
    return stable_digest("prep:profile:metrics", payload)


# warning 码 → 可采取的教师动作（确定性映射；口径见 PROFILE-METRICS.md §教师动作）
_TEACHER_ACTIONS: dict[str, str] = {
    "no_data": "尚无作答数据：请先发布诊断/课堂任务，待学生提交后重算画像",
    "low_profile_coverage": "提交覆盖率低：提醒未提交学生补交，或扩大时间窗后重算",
    "low_sample": "样本量不足，画像不构成教学结论：可布置补充练习扩大样本后重算",
    "membership_changed": "班级成员已变化：eligible 口径已按当前成员重算，如需历史可比请固定时间窗",
    "missing_confidence": "部分作答缺少识别置信度：请在批改工作台人工复核后重算",
    "missing_answers": "存在空作答：请确认学生是否漏交或遇到技术问题后重算",
    "duplicates_dropped": "重复提交事件已去重：如数量异常请检查学生端重复提交",
    "outliers_excluded": "离群作答已从班级均值剔除：可在批改工作台复核这些作答",
    "pending_review": "存在待复核作答：完成批改确认后重算，掌握度将更准确",
    "attempt_policy_unknown": "任务的重交/迟交策略未知：请补全任务策略后重算",
    "missing_item_kp_map": "题目→前置知识映射缺失：请在蓝图/诊断草稿补全后重算",
    "late_submissions": "存在迟交：可结合迟交数量决定是否安排补充辅导",
    "resubmissions": "存在重交：请关注重交后的掌握变化",
    "stale_profile": "画像已过时效：请重算后再用于教学决策",
    "evidence_conflict": "证据存在冲突或缺失：请复核批改记录后重算",
    "low_confidence": "存在低置信作答：建议人工复核后重算",
    "missing_locator": "部分证据缺少教材定位：不影响统计，可按需补充",
    "missing_score": "部分作答未评分：完成评分后重算",
}


def teacher_actions_for(warnings: list[str]) -> list[TeacherAction]:
    """按 warning 生成确定性教师动作列表（顺序与 warnings 一致，去重）。"""
    out: list[TeacherAction] = []
    seen: set[str] = set()
    for code in warnings:
        action = _TEACHER_ACTIONS.get(code)
        if action is None or code in seen:
            continue
        seen.add(code)
        out.append(TeacherAction(code=code, action=action))
    return out


__all__ = [
    "CALC_VERSION",
    "MIN_SAMPLE",
    "MetricsInput",
    "MetricsResult",
    "compute_profile_metrics",
    "teacher_actions_for",
    "_dedupe_submissions",
    "_detect_outliers",
    "_metrics_fingerprint",
]
