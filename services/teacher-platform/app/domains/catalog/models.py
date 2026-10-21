# coding: utf-8
"""catalog 域 models（02-ARCHITECTURE §4.1：目录与题库）。

Base 来自 app.db.base（B3 拥有）；表结构变更走 IFC。幂等唯一约束是 schema 的一部分（§14）。
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# PG 生产用 JSONB；SQLite 测试环境退化为通用 JSON（B4 规范 §8 协议 fake 口径，列类型导入面不变）
JsonB = JSONB().with_variant(JSON(), 'sqlite')
KpPathArray = ARRAY(Text()).with_variant(JSON(), 'sqlite')


def _uuid() -> UUID:
    return uuid4()


# users/classes 唯一定义在 app/db/models/platform.py（B3 迁移底座，真实库即此结构）。
# 域内别名复用，禁止在共享 Base 上重复建表（rescue 整合裁决 2026-09-07）。
from app.db.models.platform import Classes as Clazz, Users as User  # noqa: E402


class KpNode(Base):
    __tablename__ = 'kp_nodes'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    parent_id: Mapped[UUID | None] = mapped_column(ForeignKey('kp_nodes.id'), nullable=True)
    path: Mapped[list[str]] = mapped_column(KpPathArray, default=list)  # 物化路径（code 数组）
    source: Mapped[str] = mapped_column(String(32), default='builtin')  # builtin/teacher
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Textbook(Base):
    __tablename__ = 'textbooks'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128))
    provenance: Mapped[str] = mapped_column(String(32), default='preset')  # knowledge_base/preset
    chapters: Mapped[list] = mapped_column(JsonB, default=list)


class QuizQuestion(Base):
    __tablename__ = 'quiz_questions'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    kp_code: Mapped[str] = mapped_column(String(64))
    kp_path: Mapped[list[str]] = mapped_column(KpPathArray, default=list)
    q_type: Mapped[str] = mapped_column(String(16))    # choice/fill/solve/image
    difficulty: Mapped[str] = mapped_column(String(16))
    stem_latex: Mapped[str] = mapped_column(Text)
    stem_plain: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)  # IFC-002：LaTeX 剥离冗余列
    stem_image: Mapped[str | None] = mapped_column(String(256), nullable=True)   # MinIO key，原图锚定（R8）
    options: Mapped[list | None] = mapped_column(JsonB, nullable=True)
    answer: Mapped[str] = mapped_column(Text)
    solution_image: Mapped[str | None] = mapped_column(String(256), nullable=True)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32))    # 校本/区库/拍照入库/自编/AI配题
    year: Mapped[str | None] = mapped_column(String(16), nullable=True)
    teacher_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[UUID | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, onupdate=func.now())

    __table_args__ = (
        CheckConstraint("q_type IN ('choice','fill','solve','image')", name='ck_quiz_q_type'),
        Index('ix_quiz_kp_code', 'kp_code'),
        Index('ix_quiz_kp_path_gin', 'kp_path', postgresql_using='gin'),
    )


class Folder(Base):
    __tablename__ = 'folders'

    id: Mapped[UUID] = mapped_column(primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128))
    desc: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[UUID] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FolderQuestionRef(Base):
    """引用式专题夹（移动=加引用不复制，B4 规范 §3.1）。"""

    __tablename__ = 'folder_question_refs'

    folder_id: Mapped[UUID] = mapped_column(ForeignKey('folders.id', ondelete='CASCADE'), primary_key=True)
    question_id: Mapped[UUID] = mapped_column(ForeignKey('quiz_questions.id', ondelete='CASCADE'), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint('folder_id', 'question_id', name='uq_folder_question'),)
