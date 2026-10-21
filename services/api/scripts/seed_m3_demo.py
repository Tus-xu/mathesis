"""M3 教师端演示数据种子（Docker 演示环境专用）。

幂等：重复执行跳过已存在数据。种子内容：
- 知识点（MATH-* 若干，供题库标注与检索）
- 教师账号（13900001001）：teacher（已认证）+ student 双角色，支持前端角色切换
- 学生账号（13900001002）：student
- 班级「高二（3）班」：教师 owner，学生为已确认成员
- 题库 24 题（函数/导数/数列/三角，覆盖 choice/blank/solution × easy/medium/hard）

登录方式：手机号 + 开发验证码 123456（app_env=development 默认）。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import uuid

TEACHER_PHONE = "13900001001"
STUDENT_PHONE = "13900001002"
DEV_SMS_CODE = "123456"

KP_SEEDS = [
    ("MATH-001", "集合与常用逻辑用语", None, "高中"),
    ("MATH-002", "函数概念与基本初等函数", None, "高中"),
    ("MATH-003", "函数的单调性与奇偶性", "MATH-002", "高中"),
    ("MATH-004", "指数函数与对数函数", "MATH-002", "高中"),
    ("MATH-005", "导数的概念与运算", None, "高中"),
    ("MATH-006", "导数的应用（单调性/极值/最值）", "MATH-005", "高中"),
    ("MATH-007", "数列的概念与等差数列", None, "高中"),
    ("MATH-008", "等比数列与数列求和", "MATH-007", "高中"),
    ("MATH-009", "三角函数的图象与性质", None, "高中"),
    ("MATH-010", "平面向量与解三角形", None, "高中"),
]


def _stem_hash(stem: str) -> str:
    normalized = re.sub(r"\s+", "", stem or "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


QUESTIONS: list[tuple[str, str, dict | None, str, str, str, list[str]]] = [
    # (题干, 题型, 选项, 答案, 难度, kp, kp_codes)
    ("设集合 $A=\\{x \\mid x^2-3x+2=0\\}$，则 $A$ 的子集个数为", "choice",
     {"A": "A. 2", "B": "B. 3", "C": "C. 4", "D": "D. 8"}, "C", "easy", "MATH-001", ["MATH-001"]),
    ("命题“$x>1$”是“$x^2>1$”的什么条件", "choice",
     {"A": "A. 充分不必要", "B": "B. 必要不充分", "C": "C. 充要", "D": "D. 既不充分也不必要"}, "A", "easy", "MATH-001", ["MATH-001"]),
    ("函数 $f(x)=\\sqrt{x-1}$ 的定义域为______", "blank", None, "$[1,+\\infty)$", "easy", "MATH-002", ["MATH-002"]),
    ("已知 $f(x)=2x+1$，则 $f(f(1))=$ ______", "blank", None, "5", "easy", "MATH-002", ["MATH-002"]),
    ("判断：函数 $y=x^2$ 在 $(-\\infty,0)$ 上单调递减。（对/错）", "choice",
     {"A": "A. 对", "B": "B. 错"}, "A", "easy", "MATH-003", ["MATH-003"]),
    ("函数 $f(x)=x^3-3x$ 的单调递增区间为", "choice",
     {"A": "A. $(-1,1)$", "B": "B. $(-\\infty,-1)$ 和 $(1,+\\infty)$", "C": "C. $(1,+\\infty)$", "D": "D. $(-\\infty,-1)$"},
     "B", "medium", "MATH-003", ["MATH-003", "MATH-006"]),
    ("证明：函数 $f(x)=x+\\frac{1}{x}$ 在 $(0,1)$ 上单调递减。", "solution", None,
     "任取 $0<x_1<x_2<1$，$f(x_1)-f(x_2)=(x_1-x_2)(1-\\frac{1}{x_1x_2})$，由 $0<x_1x_2<1$ 得 $1-\\frac{1}{x_1x_2}<0$，又 $x_1-x_2<0$，故差为正，函数递减。",
     "medium", "MATH-003", ["MATH-003"]),
    ("不等式 $\\log_2 x > 1$ 的解集为______", "blank", None, "$(2,+\\infty)$", "easy", "MATH-004", ["MATH-004"]),
    ("已知 $2^a=3$，$2^b=5$，则 $2^{a+b}=$ ______", "blank", None, "15", "easy", "MATH-004", ["MATH-004"]),
    ("求函数 $y=\\ln(x^2-2x+3)$ 的单调递减区间。", "solution", None,
     "令 $t=x^2-2x+3=(x-1)^2+2>0$ 恒成立；$t$ 在 $(-\\infty,1)$ 递减，且 $\\ln$ 递增，故 $y$ 的递减区间为 $(-\\infty,1)$。",
     "medium", "MATH-004", ["MATH-004", "MATH-003"]),
    ("$(x^2\\sin x)'=$ ______", "blank", None, "$2x\\sin x+x^2\\cos x$", "easy", "MATH-005", ["MATH-005"]),
    ("曲线 $y=x^3$ 在点 $(1,1)$ 处的切线方程为", "choice",
     {"A": "A. $y=3x-2$", "B": "B. $y=3x-1$", "C": "C. $y=x$", "D": "D. $y=2x-1$"}, "A", "easy", "MATH-005", ["MATH-005"]),
    ("求 $f(x)=x^3-3x^2+2$ 的极值。", "solution", None,
     "$f'(x)=3x^2-6x=3x(x-2)$，令 $f'=0$ 得 $x=0,2$；$x=0$ 为极大值点 $f(0)=2$，$x=2$ 为极小值点 $f(2)=-2$。",
     "medium", "MATH-006", ["MATH-006"]),
    ("函数 $f(x)=xe^{-x}$ 的最大值为", "choice",
     {"A": "A. $1/e$", "B": "B. $e$", "C": "C. $1$", "D": "D. $1/e^2$"}, "A", "hard", "MATH-006", ["MATH-006", "MATH-005"]),
    ("已知函数在闭区间 $[a,b]$ 上连续且 $(a,b)$ 内可导，则其最大值只可能在哪些点取得？（简答）", "solution", None,
     "极值点（导数为零的点或不可导点）或区间端点。", "medium", "MATH-006", ["MATH-006"]),
    ("等差数列 $\\{a_n\\}$ 中 $a_1=2$，$d=3$，则 $a_{10}=$ ______", "blank", None, "29", "easy", "MATH-007", ["MATH-007"]),
    ("等差数列前 $n$ 项和 $S_n$ 的公式为______", "blank", None, "$S_n=\\frac{n(a_1+a_n)}{2}$", "easy", "MATH-007", ["MATH-007"]),
    ("等比数列 $\\{b_n\\}$ 中 $b_1=1$，$q=2$，则 $b_1+b_2+\\cdots+b_5=$ ______", "blank", None, "31", "easy", "MATH-008", ["MATH-008"]),
    ("求数列 $1, \\frac{1}{2}, \\frac{1}{4}, \\cdots$ 的前 $n$ 项和。", "solution", None,
     "公比 $q=\\frac12$，$S_n=\\frac{1-(1/2)^n}{1-1/2}=2(1-2^{-n})$。", "medium", "MATH-008", ["MATH-008", "MATH-004"]),
    ("函数 $y=\\sin 2x$ 的最小正周期为", "choice",
     {"A": "A. $\\pi$", "B": "B. $2\\pi$", "C": "C. $\\pi/2$", "D": "D. $4\\pi$"}, "A", "easy", "MATH-009", ["MATH-009"]),
    ("将 $y=\\sin x$ 的图象向右平移 $\\pi/6$ 个单位得到的解析式为______", "blank", None, "$y=\\sin(x-\\pi/6)$", "medium", "MATH-009", ["MATH-009"]),
    ("已知角 $\\alpha$ 终边过点 $(3,4)$，则 $\\sin\\alpha=$ ______", "blank", None, "$4/5$", "easy", "MATH-009", ["MATH-009"]),
    ("在 $\\triangle ABC$ 中 $a=3$，$b=4$，$C=60°$，则 $c=$ ______", "blank", None, "$\\sqrt{13}$", "medium", "MATH-010", ["MATH-010"]),
    ("用余弦定理证明：三角形两边之和大于第三边。", "solution", None,
     "设三边 $a,b,c$，$c^2=a^2+b^2-2ab\\cos C$，因 $|\\cos C|<1$，$c^2<(a+b)^2$，故 $c<a+b$。",
     "hard", "MATH-010", ["MATH-010"]),
]


async def main() -> None:
    from sqlalchemy import select

    from app.models.class_ import Class
    from app.models.class_member import ClassMember
    from app.models.database import async_session_factory
    from app.models.knowledge_point import KnowledgePoint
    from app.models.question_bank import QuestionBank
    from app.models.role_binding import RoleBinding
    from app.models.user import User

    async with async_session_factory() as db:
        # ---- 知识点 ----
        existing_kp = {
            r.code for r in (await db.execute(select(KnowledgePoint))).scalars()
        }
        kp_by_code: dict[str, uuid.UUID] = {
            r.code: r.id for r in (await db.execute(select(KnowledgePoint))).scalars()
        }
        for code, name, parent_code, grade in KP_SEEDS:
            if code in existing_kp:
                continue
            kp = KnowledgePoint(
                code=code, name=name, grade=grade,
                parent_id=kp_by_code.get(parent_code) if parent_code else None,
                aliases=[],
            )
            db.add(kp)
        await db.flush()
        kp_by_code = {
            r.code: r.id for r in (await db.execute(select(KnowledgePoint))).scalars()
        }

        # ---- 用户与角色 ----
        # 教师账号：仅 teacher 绑定（登录 active_role=teacher，直达教师工作台）
        teacher = (
            await db.execute(select(User).where(User.phone == TEACHER_PHONE))
        ).scalar_one_or_none()
        if teacher is None:
            teacher = User(phone=TEACHER_PHONE, nickname="李老师")
            db.add(teacher)
            await db.flush()
        rb = (
            await db.execute(
                select(RoleBinding).where(
                    RoleBinding.user_id == teacher.id,
                    RoleBinding.role == "teacher",
                    RoleBinding.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if rb is None:
            db.add(RoleBinding(user_id=teacher.id, role="teacher", verified=True, org_name="演示高中"))

        # 学生账号：student + teacher 双绑定（登录进学生端，顶栏可切换到教师端，
        # 用于演示 /api/auth/role/switch 角色切换闭环）
        student = (
            await db.execute(select(User).where(User.phone == STUDENT_PHONE))
        ).scalar_one_or_none()
        if student is None:
            student = User(phone=STUDENT_PHONE, nickname="小婷")
            db.add(student)
            await db.flush()
        for role in ("student", "teacher"):
            rb = (
                await db.execute(
                    select(RoleBinding).where(
                        RoleBinding.user_id == student.id,
                        RoleBinding.role == role,
                        RoleBinding.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if rb is None:
                db.add(RoleBinding(user_id=student.id, role=role, verified=True))

        # ---- 班级与成员 ----
        clazz = (
            await db.execute(select(Class).where(Class.owner_id == teacher.id, Class.deleted_at.is_(None)))
        ).scalar_one_or_none()
        if clazz is None:
            clazz = Class(
                name="高二（3）班", invite_code=uuid.uuid4().hex[:8],
                owner_id=teacher.id, grade="高二", subject="math",
            )
            db.add(clazz)
            await db.flush()
        # 教师本人也写 class_members（/api/classes/mine 与教师端班级选择依赖成员行；
        # 与 POST /api/classes 建班行为一致）
        t_member = (
            await db.execute(
                select(ClassMember).where(
                    ClassMember.class_id == clazz.id,
                    ClassMember.user_id == teacher.id,
                )
            )
        ).scalar_one_or_none()
        if t_member is None:
            db.add(ClassMember(
                class_id=clazz.id, user_id=teacher.id,
                member_role="teacher", confirmed=True, join_via="code",
            ))
        member = (
            await db.execute(
                select(ClassMember).where(
                    ClassMember.class_id == clazz.id,
                    ClassMember.user_id == student.id,
                )
            )
        ).scalar_one_or_none()
        if member is None:
            db.add(ClassMember(
                class_id=clazz.id, user_id=student.id,
                member_role="student", confirmed=True, join_via="code",
            ))

        # ---- 题库 ----
        existing_hashes = set(
            (await db.execute(select(QuestionBank.hash))).scalars()
        )
        added = 0
        for stem, q_type, options, answer, difficulty, _kp, kp_codes in QUESTIONS:
            h = _stem_hash(stem)
            if h in existing_hashes:
                continue
            db.add(QuestionBank(
                stem=stem, q_type=q_type, options=options, answer=answer,
                difficulty=difficulty, kp_codes=kp_codes, scope="student",
                hash=h, is_real_exam=False,
            ))
            existing_hashes.add(h)
            added += 1

        await db.commit()
        print(f"[seed-m3] 完成：教师 {TEACHER_PHONE} / 学生 {STUDENT_PHONE} / 班级 {clazz.name} / 新增题库 {added} 题")
        print(f"[seed-m3] 登录验证码（开发环境）：{DEV_SMS_CODE}")


if __name__ == "__main__":
    asyncio.run(main())
