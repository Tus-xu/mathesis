#!/usr/bin/env python3
"""教师端固定 Demo 数据世界（调研版准则：一个老师/一个班/一节课/一组学情）。

为开发库 math_arena 注入一致的演示世界，让今日首页/教案/批改/洞察/课堂全链路有数据可走：
- 教师：李老师（13900001001，approve teacher binding，幂等）
- 班级：高二（3）班 · 导数单元（46 名学生 S01..S46）
- 课程/任务：Assignment A《导数基础巩固作业》（type=pre_class 学前作业，含
               choice/blank拍照/solution 混合题型；13 人在 a=0 参数分类题失分）
               Assignment B《导数单元周测》（type=homework 课后习题；14 人同题失分，
               其中 11 人重复；21 份主观题待批）
- 教案：lesson_plan artifact（45 分钟 6 环节《导数与函数单调性》，check 环节手工锁定作为"缺口"演示）

tel §10.5.2 ADAPT（2026-10-12）：A/B 挂 assignment_targets（学生任务中心可见的前提，
此前缺失导致任务列表恒空）；A 升级为 pre_class 混合题型并含 1 份 blank 拍照待批
（真实 PNG 落盘 local 存储可查看）；B 改为 homework。

幂等：班级名已存在则整体跳过。仅用于开发/演示库；生产不得运行。
"""
from __future__ import annotations

import asyncio
import base64
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.models.class_ import Class
from app.models.class_member import ClassMember
from app.models.coursework import (
    Assignment,
    AssignmentTarget,
    Quiz,
    QuizItem,
    Submission,
    SubmissionItem,
)
from app.models.database import async_session_factory
from app.models.file import File
from app.models.role_binding import RoleBinding
from app.models.teacher import TeachingArtifact
from app.models.user import User

TEACHER_PHONE = "13900001001"
CLASS_NAME = "高二（3）班 · 导数单元"
STUDENT_BASE = 13900002000

# tel 种子：blank 拍照作答的 1x1 PNG（真实可查看图片，落 local 存储目录）
_PHOTO_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

# a=0 参数分类讨论：两次作业都错的公共错题（error_cluster 依据）
PARAM_QUESTION = "讨论函数 f(x)=ln x − ax (a∈R) 的单调性；参数边界 a=0 时如何分类？"
PARAM_ANSWER = "当 a≤0 时 f(x) 在 (0,+∞) 单调递增；当 a>0 时在 (0,1/a) 递增、(1/a,+∞) 递减；a=0 并入 a≤0。"

# 确定性错题人数：A 错 13 人、B 错 14 人、重叠 11 人（→ 并集 16 人，符合「连续失分、多人重复」）
WRONG_A = set(range(0, 13))            # 学生索引 0..12
WRONG_B = set(range(0, 11)) | {18, 19, 20}  # 0..10 ∪ 18..20 → 14 人


def _now() -> datetime:
    return datetime.now(UTC)


async def _ensure_teacher(db) -> User:
    teacher = (
        await db.execute(select(User).where(User.phone == TEACHER_PHONE, User.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if teacher is None:
        raise SystemExit(f"教师 {TEACHER_PHONE} 不存在，请先注册")
    binding = (
        await db.execute(
            select(RoleBinding).where(
                RoleBinding.user_id == teacher.id,
                RoleBinding.role == "teacher",
                RoleBinding.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if binding is None:
        db.add(
            RoleBinding(
                user_id=teacher.id,
                role="teacher",
                status="approved",
                verified=True,
            )
        )
        await db.flush()
    return teacher


async def _ensure_students(db, count: int = 46) -> list[User]:
    lo, hi = str(STUDENT_BASE + 1), str(STUDENT_BASE + count)
    existing = (
        await db.scalar(
            select(func.count(User.id)).where(User.phone.between(lo, hi))
        )
    )
    if existing == count:
        return list(
            (await db.execute(select(User).where(User.phone.between(lo, hi)).order_by(User.phone))).scalars()
        )
    users = []
    for i in range(1, count + 1):
        phone = str(STUDENT_BASE + i)
        u = (
            await db.execute(select(User).where(User.phone == phone))
        ).scalar_one_or_none()
        if u is None:
            u = User(phone=phone, nickname=f"学生 S{i:02d}")
            db.add(u)
            await db.flush()
            db.add(RoleBinding(user_id=u.id, role="student", status="approved", verified=True))
        users.append(u)
    return users


async def _make_quiz_with_items(db, owner_id: uuid.UUID, title: str, *, include_param: bool) -> Quiz:
    quiz = Quiz(
        user_id=owner_id,
        source="ai_generated",
        title=title,
        kp_codes=["KP-DERIV-MONO", "KP-DERIV-PARAM"],
        status="active",
    )
    db.add(quiz)
    await db.flush()
    choice_items = [
        ("函数 f(x)=x³−3x 的单调递增区间为 ( )", "(−∞,−1) 与 (1,+∞)", "choice", "easy"),
        ("下列求导正确的是 ( )", "若 f(x)=x²，则 f′(x)=2x", "choice", "easy"),
        ("f′(x)>0 是 f(x) 单调递增的____条件", "充分不必要", "choice", "easy"),
        ("若 f(x)=ln x 在 (1,+∞)，则 f′(x) 的符号为", "恒为正", "choice", "easy"),
        ("曲线 y=x² 在 (1,1) 处切线斜率是", "2", "choice", "easy"),
    ]
    blank_items = [
        ("f(x)=x³ 的导数 f′(x)=____", "3x²", "blank", "easy"),
        ("f(x)=e^x 在 (0,1) 处切线方程为____", "y=x+1", "blank", "easy"),
    ]
    solution_items = [
        ("求函数 f(x)=x³−3x²+2x 的单调区间。", "f′(x)=3x²−6x+2，令 f′>0 得…", "solution", "medium"),
    ]
    items = choice_items + blank_items + solution_items
    if include_param:
        items.append((PARAM_QUESTION, PARAM_ANSWER, "solution", "hard"))
    for idx, (q_text, ans, q_type, diff) in enumerate(items, start=1):
        db.add(
            QuizItem(
                quiz_id=quiz.id,
                item_no=idx,
                q_type=q_type,
                question_text=q_text,
                answer=ans,
                answer_analysis=ans,
                kp_code="KP-DERIV-PARAM" if q_text == PARAM_QUESTION else "KP-DERIV-MONO",
                difficulty=diff,
                ai_generated=True,
                max_score=8.0 if q_type == "solution" else 3.0,
                grading_rubric=(
                    [
                        {"id": "r1", "criterion": "步骤完整、结论正确", "points": 8, "evidence_hint": "分类讨论完整"},
                        {"id": "r2", "criterion": "分类讨论清晰", "points": 5, "evidence_hint": "含 a=0 边界"},
                    ]
                    if q_type == "solution"
                    else None
                ),
            )
        )
    await db.flush()
    return quiz


async def _make_assignment(
    db,
    teacher_id,
    class_id,
    quiz: Quiz,
    title: str,
    *,
    dead_hours: int,
    assignment_type: str = "quiz",
) -> Assignment:
    a = Assignment(
        class_id=class_id,
        creator_id=teacher_id,
        title=title,
        type=assignment_type,
        quiz_id=quiz.id,
        deadline=_now() + timedelta(hours=dead_hours),
        status="published",
        client_assignment_id=f"demo:{title}",
    )
    db.add(a)
    await db.flush()
    # tel §10.5.2：班级定向 target——学生任务列表（/api/student/assignments）
    # 以 assignment_targets(target_type=class) 为可见性前提，此前缺失导致列表恒空。
    db.add(AssignmentTarget(assignment_id=a.id, target_type="class", target_id=class_id))
    await db.flush()
    return a


def _write_local_photo(user_id, file_id) -> str:
    """tel 种子：把真实 PNG 写入 local 存储目录，返回 storage_uri（local:<token>）。

    与 files/router.py 的本地存储布局一致：LOCAL_UPLOAD_ROOT/<user_id>/<file_id>.bin，
    教师批改台经 /api/teacher/grading/{item}/file 可真实打开。
    """
    from app.domains.files.router import LOCAL_UPLOAD_ROOT

    target_dir = LOCAL_UPLOAD_ROOT / str(user_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / f"{file_id}.bin").write_bytes(base64.b64decode(_PHOTO_PNG_B64))
    return f"local:{secrets.token_urlsafe(24)}"


async def _seed_submissions(
    db,
    teacher_id: uuid.UUID,
    assignment: Assignment,
    quiz: Quiz,
    students: list[User],
    items: list[QuizItem],
    *,
    param_wrong: set[int],
    pending_review_count: int,
    photo_blank: bool = False,
) -> None:
    param_item = next((it for it in items if it.question_text == PARAM_QUESTION), None)
    solution_items = [it for it in items if it.q_type == "solution"]
    first_blank = next((it for it in items if it.q_type == "blank"), None)
    for si, stu in enumerate(students):
        sub = Submission(
            user_id=stu.id,
            quiz_id=quiz.id,
            assignment_id=assignment.id,
            client_submit_id=f"demo:{assignment.title}:{stu.phone}",
            status="graded",
            total_score=80.0,
        )
        db.add(sub)
        await db.flush()
        for item in items:
            wrong = param_wrong is not None and param_item is not None and item.item_no == param_item.item_no and si in param_wrong
            needs_review = False
            verdict = "wrong" if wrong else "correct"
            # tel 拍照兜底：assignment A 的 S01 首道 blank 走「拍照手写」链路——
            # 有图无文字 → pending_review（degraded=photo_attached 同款口径），
            # 教师工作台看原图后人工给分（愿景 4 演示主链）。
            photo_item = (
                photo_blank
                and si == 0
                and first_blank is not None
                and item.item_no == first_blank.item_no
            )
            if photo_item:
                file_id = uuid.uuid4()
                storage_uri = _write_local_photo(stu.id, file_id)
                db.add(
                    File(
                        id=file_id,
                        user_id=stu.id,
                        filename="handwritten-answer.png",
                        mime="image/png",
                        size_bytes=len(base64.b64decode(_PHOTO_PNG_B64)),
                        sha256="seed" + uuid.uuid4().hex[:60],
                        storage_uri=storage_uri,
                        file_type="image",
                        status="parsed",
                    )
                )
                await db.flush()
                db.add(
                    SubmissionItem(
                        submission_id=sub.id,
                        item_no=item.item_no,
                        q_type=item.q_type,
                        answer_text=None,
                        file_id=file_id,
                        attachments=[
                            {
                                "file_id": str(file_id),
                                "filename": "handwritten-answer.png",
                                "mime": "image/png",
                                "status": "photo",
                            }
                        ],
                        verdict="pending_review",
                        score=None,
                        ai_pregraded=False,
                        needs_review=True,
                    )
                )
                sub.status = "pending_review"
                continue
            # 待批仅作用在非参数分类的主观题上，避免吞掉「共同错题」的判错口径
            if (
                pending_review_count > 0
                and item in solution_items
                and (param_item is None or item.item_no != param_item.item_no)
            ):
                pending_review_count -= 1
                needs_review = True
                verdict = "pending_review"
            suggested_score = None if needs_review else (0.0 if wrong else float(item.max_score or 3))
            db.add(
                SubmissionItem(
                    submission_id=sub.id,
                    item_no=item.item_no,
                    q_type=item.q_type,
                    answer_text="(草稿作答占位)" if needs_review else (PARAM_ANSWER if param_item is not None and item.item_no == param_item.item_no and not wrong else "f′(x)=…，解得单调区间…"),
                    verdict=verdict,
                    score=None if needs_review else suggested_score,
                    ai_pregraded=not needs_review,
                    error_type="concept" if wrong else None,
                    suggested_score=suggested_score,
                    suggestion_rationale=({"得分点": ["求导", "临界点", "区间判断"]} if not needs_review else None),
                    suggestion_feedback="步骤完整，可再补充边界讨论。" if wrong else None,
                    suggestion_confidence=0.9 if not needs_review else None,
                    suggestion_status="draft" if needs_review else ("applied" if not wrong else "draft"),
                    needs_review=needs_review,
                )
            )
    await db.flush()


async def _seed_lesson_plan(db, teacher_id, class_id) -> TeachingArtifact:
    art = TeachingArtifact(
        logical_id=uuid.uuid4(),
        artifact_type="lesson_plan",
        owner_id=teacher_id,
        class_id=class_id,
        scene="teacher.prep",
        version=1,
        status="confirmed",
        payload={
            "topic": "导数与函数单调性",
            "objectives": ["理解导数符号与单调性的关系", "会求函数单调区间", "掌握参数分类讨论"],
            "material_notes": "基于上次同类课与教材 5.3 节改编",
            "segments": [
                {
                    "id": str(uuid.uuid4()),
                    "title": "复习旧知：斜率与变化率",
                    "duration_min": 6,
                    "kind": "import",
                    "learning_objective": "激活导数几何意义",
                    "teacher_action": "提问：导数几何意义？单调性定义？",
                    "student_action": "口答并举例",
                    "core_question": "导数如何刻画函数变化？",
                    "content": "复习切线斜率与瞬时变化率，衔接单调性定义。",
                    "assessment_check": "两道口答题即时反馈",
                    "source": "adapted",
                },
                {
                    "id": str(uuid.uuid4()),
                    "title": "探究发现：导数符号与单调性",
                    "duration_min": 10,
                    "kind": "concept",
                    "learning_objective": "建立 f′(x) 符号与单调性联系",
                    "teacher_action": "几何画板演示 f′(x)>0/<0 的曲线形态",
                    "student_action": "观察、猜想、归纳",
                    "core_question": "f′(x)>0 是否等价于单调递增？",
                    "content": "从一次/指数/对数函数特例抽象一般结论；强调零点孤立不影响单调性。",
                    "assessment_check": "学生复述结论",
                    "source": "adapted",
                },
                {
                    "id": str(uuid.uuid4()),
                    "title": "例题精讲：求单调区间",
                    "duration_min": 10,
                    "kind": "example",
                    "learning_objective": "会用四步法求单调区间",
                    "teacher_action": "板书规范四步：定义域→求导→解不等式→结论",
                    "student_action": "跟练 + 1 道学生命题",
                    "core_question": "为什么先定义域？",
                    "content": "例：f(x)=x³−3x²+2x 的单调区间；学生命制一道题。",
                    "assessment_check": "1 道同型题当堂检测",
                    "source": "adapted",
                },
                {
                    "id": str(uuid.uuid4()),
                    "title": "难点突破：参数边界 a=0 分类讨论",
                    "duration_min": 8,
                    "kind": "intervention",
                    "learning_objective": "掌握含参单调性的边界分类",
                    "teacher_action": "展示两种典型错误，强调 a=0 并入 a≤0",
                    "student_action": "独立判断 a=0 情形 30 秒",
                    "core_question": "参数边界要不要单独分类？",
                    "content": "例：f(x)=ln x−ax 的单调性；a=0 边界处理。",
                    "assessment_check": "同类变式 1 题",
                    "source": "ai_suggested",
                    "linked_insights": ["error_cluster"],
                },
                {
                    "id": str(uuid.uuid4()),
                    "title": "巩固练习：分层检测",
                    "duration_min": 8,
                    "kind": "practice",
                    "learning_objective": "迁移应用导数判断单调性",
                    "teacher_action": "巡堂观察错误分布",
                    "student_action": "分层练习，同桌互评",
                    "core_question": "单调区间端点怎么处理？",
                    "content": "A 层基础 4 题、B 层提升 2 题（含参数）。",
                    "assessment_check": "当堂对答案",
                    "source": "adapted",
                },
                {
                    "id": str(uuid.uuid4()),
                    "title": "总结升华：Exit Ticket",
                    "duration_min": 3,
                    "kind": "check",
                    "learning_objective": "检验本节掌握度",
                    "teacher_action": "发放 Exit Ticket",
                    "student_action": "2 分钟内作答并上交",
                    "core_question": "今天最不确定的地方是？",
                    "content": "",  # 刻意留空：演示「还差 Exit Ticket」缺口
                    "assessment_check": "回收统计",
                    "source": "adapted",
                    "locked": False,
                },
            ],
            "assignment": "一本通 P68 1-8",
        },
        source_refs=[],
        validation={},
        warnings=[],
        engine="local",
        degraded=False,
    )
    db.add(art)
    await db.flush()
    return art


async def main() -> None:
    async with async_session_factory() as db:
        exists = await db.scalar(select(func.count(Class.id)).where(Class.name == CLASS_NAME, Class.deleted_at.is_(None)))
        if exists:
            print("已存在演示班级，跳过（幂等）。")
            return
        teacher = await _ensure_teacher(db)
        students = await _ensure_students(db)

        clazz = Class(
            name=CLASS_NAME,
            invite_code="DEMO" + uuid.uuid4().hex[:4].upper(),
            owner_id=teacher.id,
            grade="高二",
            subject="math",
            status="active",
        )
        db.add(clazz)
        await db.flush()
        for stu in students:
            db.add(
                ClassMember(class_id=clazz.id, user_id=stu.id, member_role="student", confirmed=True)
            )
        await db.flush()

        quiz_a = await _make_quiz_with_items(db, teacher.id, "导数基础作业", include_param=True)
        quiz_b = await _make_quiz_with_items(db, teacher.id, "导数单元周测", include_param=True)
        items_a = (await db.execute(select(QuizItem).where(QuizItem.quiz_id == quiz_a.id).order_by(QuizItem.item_no))).scalars().all()
        items_b = (await db.execute(select(QuizItem).where(QuizItem.quiz_id == quiz_b.id).order_by(QuizItem.item_no))).scalars().all()
        assign_a = await _make_assignment(
            db, teacher.id, clazz.id, quiz_a, "导数基础即作业（作业A）", dead_hours=2,
            assignment_type="pre_class",
        )
        assign_b = await _make_assignment(
            db, teacher.id, clazz.id, quiz_b, "导数单元周测（作业B）", dead_hours=30,
            assignment_type="homework",
        )
        # 显式错开创建时间（10 分钟）：保证「最近作业」排序确定，避免同毫秒并列造成执行计划漂移
        assign_a.created_at = _now() - timedelta(minutes=10)
        assign_b.created_at = _now()
        await _seed_submissions(
            db, teacher.id, assign_a, quiz_a, students, items_a,
            param_wrong=WRONG_A, pending_review_count=0, photo_blank=True,
        )
        await _seed_submissions(
            db, teacher.id, assign_b, quiz_b, students, items_b, param_wrong=WRONG_B, pending_review_count=21
        )
        await _seed_lesson_plan(db, teacher.id, clazz.id)
        await db.commit()
        print(
            f"演示世界就绪：{CLASS_NAME}（46 人），作业 A（pre_class 含 choice/blank拍照/solution）/ "
            f"B（homework），21 份待批，教案《导数与函数单调性》45 分钟。"
        )


asyncio.run(main())
