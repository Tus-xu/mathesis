"""开发种子：张老师账号 + 示例班级（真实结构，非占位内容）。

张老师 id 与 `app/core/security.py SEED_TEACHER` 的 uuid5 一致（login 签发同一 sub）。
"""
from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.core.security import SEED_TEACHER
from app.db.models import Classes, Users
from app.db.session import session_factory


async def main() -> None:
    async with session_factory()() as session:
        # 张老师（school_id 用稳定 uuid5，班级同校）
        school_id = uuid.uuid5(uuid.NAMESPACE_URL, "teacher-platform/seed/school-001")
        exists = (await session.execute(select(Users).where(Users.id == uuid.UUID(SEED_TEACHER["id"])))).scalar_one_or_none()
        if exists is None:
            session.add(
                Users(
                    id=uuid.UUID(SEED_TEACHER["id"]),
                    name=SEED_TEACHER["name"],
                    role=SEED_TEACHER["role"],
                    school_id=school_id,
                )
            )
            print(f"seeded user: {SEED_TEACHER['name']} ({SEED_TEACHER['role']})")
        else:
            print("user exists, skip")

        # 示例班级（真实来源：张老师任教班——仅结构种子，学生数按真实班额）
        existing_classes = (await session.execute(select(Classes))).scalars().all()
        if not existing_classes:
            for name, grade, count in [("高二(3)班", "高二", 48), ("高二(5)班", "高二", 45), ("高一(1)班", "高一", 50)]:
                session.add(Classes(name=name, grade=grade, student_count=count, school_id=school_id))
            print("seeded classes: 高二(3)班 / 高二(5)班 / 高一(1)班")
        else:
            print("classes exist, skip")

        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())
