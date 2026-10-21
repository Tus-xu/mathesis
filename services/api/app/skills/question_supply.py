"""题库供给层（题库优先：真题检索 → AI 生成只补缺口）

供给规则：
- 按 kp_codes 数组重叠（GIN）+ q_type + difficulty 过滤，按稳定 hash 顺序取；
- 指定难度供不满时可放宽难度再补（题型与知识点不放宽——刷偏知识点比刷偏难度危害大）；
- exclude_hashes 排除本次已选题（同卷/同题组内不重复出题）；
- kp 粒度对齐：expand_kp_codes 将章节码/小节码双向展开（父码展开子码、子码回溯父码），
  题库标注粒度与 knowledge_points 树粒度不一致时仍能命中。

日限口径（daily_ai_used）：只计 AI 生成题，题库真题不占额度——
作答计题（非试卷提交明细中对应 AI 题）+ 组卷计题（exam:* 今日成卷的 AI 题，生成即计）。
practice/start 与 exam/generate 共用本实现，双端口径一致。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

import structlog
from sqlalchemy import and_, case, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coursework import Quiz, QuizItem, Submission, SubmissionItem
from app.models.knowledge_point import KnowledgePoint
from app.models.question_bank import QuestionBank

logger = structlog.get_logger(__name__)

# 演示批次黑名单：这些批次按导入契约（scope=pending_qa 等质检门禁）不得进入
# student 供给；hybrid 批量标注存在整批错配，宁缺勿错。新增演示导入批次时在此登记。
_DEMO_BATCHES = {"cmm_math_v1"}


async def expand_kp_codes(db: AsyncSession, codes: list[str]) -> list[str]:
    """kp code 双向粒度展开：自身 + 父码 + 直接子码（章节码 ↔ 小节码对齐题库标注粒度）"""
    uniq = [c for c in dict.fromkeys(codes) if c]
    if not uniq:
        return []
    rows = (
        await db.execute(select(KnowledgePoint.id, KnowledgePoint.code, KnowledgePoint.parent_id))
    ).all()
    code_by_id = {r.id: r.code for r in rows}
    parent_of: dict[str, str] = {}
    children_of: dict[str, list[str]] = {}
    for r in rows:
        if r.parent_id is not None and r.parent_id in code_by_id:
            parent_of[r.code] = code_by_id[r.parent_id]
            children_of.setdefault(code_by_id[r.parent_id], []).append(r.code)
    expanded: set[str] = set(uniq)
    for c in uniq:
        if c in parent_of:
            expanded.add(parent_of[c])
        expanded.update(children_of.get(c, []))
    return sorted(expanded)


async def expand_kp_subtree(db: AsyncSession, codes: list[str]) -> list[str]:
    """Return requested nodes and all descendants, never their ancestors or siblings.

    Assessment assembly uses this narrower expansion: a request for a leaf must
    not be widened to a parent-tagged or sibling-tagged bank question.
    Unknown codes are retained so imported bank rows remain addressable.
    """
    requested = [code for code in dict.fromkeys(codes) if code]
    if not requested:
        return []
    rows = (await db.execute(select(KnowledgePoint.id, KnowledgePoint.code, KnowledgePoint.parent_id))).all()
    children: dict[uuid.UUID, list[uuid.UUID]] = {}
    code_by_id = {row.id: row.code for row in rows}
    ids_by_code = {row.code: row.id for row in rows}
    for row in rows:
        if row.parent_id is not None:
            children.setdefault(row.parent_id, []).append(row.id)

    allowed = set(requested)
    pending = [ids_by_code[code] for code in requested if code in ids_by_code]
    while pending:
        node_id = pending.pop()
        for child_id in children.get(node_id, []):
            allowed.add(code_by_id[child_id])
            pending.append(child_id)
    return sorted(allowed)


async def supply_questions(
    db: AsyncSession,
    *,
    kp_codes: list[str],
    q_type: str | None = None,
    difficulty: str | None = None,
    count: int,
    exclude_hashes: set[str] | None = None,
    scope: str = "student",
    strict_kp_subtree: bool = False,
    publishable_only: bool = False,
    selection_seed: str | None = None,
    relax_difficulty: bool = True,
) -> list[QuestionBank]:
    """从题库稳定供题：kp 数组重叠 + 题型/难度过滤；难度不足放宽再补；如实际命中数返回。

    ``publishable_only`` 将教师组卷的完整性要求下推到 SQL：题干和答案
    非空；选择题须为 JSON object，含至少两个非空（规范化后不同）
    key/value，且答案能匹配有效 key。因而 ``LIMIT`` 永远只作用于合格候选，
    不会因坏题比例高而误报库存不足。候选以 selection seed 派生的 hash pivot
    为起点，按 hash 两段环回；同一 seed 可复现，不同请求会轮换，且从不进行
    全表 ``random()`` 排序。未传 seed 的共享调用方每次生成请求级 seed。

    返回的 QuestionBank 即题库原题（is_real_exam 真题标记随行），调用方转为 QuizItem 时
    ai_generated=False、source 透传真题来源。

    scope 过滤（迭代09+ 题库治理）：只供指定端可见题（默认 student），
    并排除竞赛/超纲标记题——防止 TAL 竞赛题等 research 端素材供给学生。
    """
    if count <= 0 or not kp_codes:
        return []
    codes = (
        await expand_kp_subtree(db, kp_codes)
        if strict_kp_subtree
        else await expand_kp_codes(db, kp_codes)
    )
    excluded = set(exclude_hashes or set())
    pivot = hashlib.sha256((selection_seed or uuid.uuid4().hex).encode()).hexdigest()

    def _trim_sql(value):
        """SQL counterpart of strip for ASCII spaces, tabs, CR/LF and form/vertical feed."""
        return func.regexp_replace(value, r"^[[:space:]]+|[[:space:]]+$", "", "g")

    def _publishable_sql_predicate():
        # PostgreSQL evaluates boolean expressions in planner-chosen order, so
        # `jsonb_typeof(...) = 'object' AND jsonb_each_text(...)` is unsafe.
        # Feed non-objects a typed empty object before expanding JSON pairs.
        safe_options = case(
            (func.jsonb_typeof(QuestionBank.options) == "object", QuestionBank.options),
            else_=cast(literal("{}"), JSONB),
        )
        option_pairs = func.jsonb_each_text(safe_options).table_valued("key", "value").alias("option_pairs")
        valid_pair = and_(
            func.length(_trim_sql(option_pairs.c.key)) > 0,
            func.length(_trim_sql(option_pairs.c.value)) > 0,
        )
        normalized_key = func.lower(_trim_sql(option_pairs.c.key))
        valid_key_count = (
            select(func.count(func.distinct(normalized_key)))
            .select_from(option_pairs)
            .where(valid_pair)
            .scalar_subquery()
        )
        answer_matches_option = (
            select(1)
            .select_from(option_pairs)
            .where(
                valid_pair,
                normalized_key == func.lower(_trim_sql(QuestionBank.answer)),
            )
            .exists()
        )
        choice_publishable = and_(
            func.jsonb_typeof(QuestionBank.options) == "object",
            valid_key_count >= 2,
            answer_matches_option,
        )
        return and_(
            func.length(_trim_sql(QuestionBank.stem)) > 0,
            func.length(_trim_sql(QuestionBank.answer)) > 0,
            or_(QuestionBank.q_type != "choice", choice_publishable),
        )

    def _statement(diff: str | None, extra: set[str]):
        stmt = (
            select(QuestionBank)
            .where(
                QuestionBank.deleted_at.is_(None),
                QuestionBank.kp_codes.overlap(codes),
                QuestionBank.scope == scope,
                QuestionBank.is_competition.is_(False),
                QuestionBank.out_of_syllabus.is_(False),
                # 演示批次不入学生供给（tel 热修）：cmm_math_v1 为许可证未声明的
                # 内部演示数据，导入契约即 scope=pending_qa 待质检；其 hybrid 批量
                # 标注与题面内容严重错配（"1.1 集合的概念"名下混入算法初步/组合
                # 数学等 500+ 题），供题链一律排除——宁缺勿错。
                or_(
                    QuestionBank.source_batch.is_(None),
                    QuestionBank.source_batch.not_in(_DEMO_BATCHES),
                ),
            )
        )
        if q_type:
            stmt = stmt.where(QuestionBank.q_type == q_type)
        if diff:
            stmt = stmt.where(QuestionBank.difficulty == diff)
        hashes = excluded | extra
        if hashes:
            stmt = stmt.where(QuestionBank.hash.not_in(hashes))
        if publishable_only:
            stmt = stmt.where(_publishable_sql_predicate())
        return stmt

    async def _query(diff: str | None, limit: int, extra: set[str]) -> list[QuestionBank]:
        if limit <= 0:
            return []
        # Two index-friendly range scans create a cyclic order around the
        # pivot. Every segment has its own exact LIMIT; no candidate overfetch.
        first_stmt = _statement(diff, extra).where(QuestionBank.hash >= pivot).order_by(QuestionBank.hash.asc()).limit(limit)
        rows = list((await db.execute(first_stmt)).scalars().all())
        if len(rows) < limit:
            second_stmt = _statement(diff, extra).where(QuestionBank.hash < pivot).order_by(QuestionBank.hash.asc()).limit(limit - len(rows))
            rows.extend((await db.execute(second_stmt)).scalars().all())
        return rows

    picked = await _query(difficulty, count, set())
    if relax_difficulty and difficulty and len(picked) < count:
        # 难度放宽补缺口（kp/题型不放宽）；已选题不再重复
        picked.extend(await _query(None, count - len(picked), {p.hash for p in picked}))
    return picked


def quiz_item_from_bank(
    row: QuestionBank,
    *,
    quiz_id: uuid.UUID,
    item_no: int,
    kp_code: str | None = None,
) -> QuizItem:
    """题库行 → QuizItem（ai_generated=False；source 真题来源透传；source_chunk_id 留 qb: 溯源引用）

    kp_code 缺省取题库行首个标注码；组卷场景传槽位 kp（掌握度回填与排布计划对齐）。
    """
    return QuizItem(
        quiz_id=quiz_id,
        item_no=item_no,
        q_type=row.q_type,
        question_text=row.stem,
        options=row.options if isinstance(row.options, dict) else None,
        answer=row.answer,
        answer_analysis=row.analysis,
        kp_code=kp_code or (row.kp_codes[0] if row.kp_codes else None),
        difficulty=row.difficulty,
        ai_generated=False,
        source=row.source,
        source_chunk_id=f"qb:{row.id}",
        image=row.image or [],
    )


async def daily_ai_used(db: AsyncSession, user_id: uuid.UUID) -> int:
    """今日 AI 生成题已用量（日限唯一口径：只计 AI 题，题库真题不占额度）

    两路合并（沿用存量口径，仅追加 ai_generated 过滤）：
    - 作答计题：今日非试卷提交明细中对应 AI 题；无法溯源的明细（QuizItem 缺失/对话临时题）
      按 AI 计（保守口径，防漏计）；
    - 组卷计题：今日成卷（exam:*）中的 AI 题（生成即计，其后试卷作答不再重复计）。
    当日口径与存量一致：服务器本地日期零点。
    """
    today_start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    sub_count = (
        await db.execute(
            select(func.count())
            .select_from(SubmissionItem)
            .join(Submission, SubmissionItem.submission_id == Submission.id)
            .outerjoin(Quiz, Submission.quiz_id == Quiz.id)
            .outerjoin(
                QuizItem,
                (QuizItem.quiz_id == Submission.quiz_id)
                & (QuizItem.item_no == SubmissionItem.item_no),
            )
            .where(
                Submission.user_id == user_id,
                SubmissionItem.created_at >= today_start,
                # 排除试卷提交（试卷 AI 题已在组卷时计入，防双计）
                (Quiz.id.is_(None)) | (~Quiz.source.like("exam:%")),
                # 题库题（ai_generated=False）不计；无法溯源的按 AI 计
                (QuizItem.id.is_(None)) | (QuizItem.ai_generated.is_(True)),
            )
        )
    ).scalar() or 0
    exam_count = (
        await db.execute(
            select(func.count())
            .select_from(QuizItem)
            .join(Quiz, QuizItem.quiz_id == Quiz.id)
            .where(
                Quiz.user_id == user_id,
                Quiz.source.like("exam:%"),
                Quiz.deleted_at.is_(None),
                QuizItem.deleted_at.is_(None),
                QuizItem.ai_generated.is_(True),
                QuizItem.created_at >= today_start,
            )
        )
    ).scalar() or 0
    return int(sub_count) + int(exam_count)
