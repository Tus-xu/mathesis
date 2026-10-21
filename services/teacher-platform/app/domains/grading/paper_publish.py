# coding: utf-8
"""组卷中心 → 8000 课堂任务（真实下发，不再写演示表）。

链路（全部真实、可重放）：

    QuizQuestion(8100 题库 786 题)
      → 类型/选项/分值确定性映射
      → Api8000Adapter.create_assignment_inline(...)   # 8000 draft + quiz_items
      → Api8000Adapter.publish_assignment(...)         # draft → published
      → 学生 /api/student/assignments 可见

为什么放在 8100：
- 题库事实在 8100（8000 question_bank 当前为空），组卷 UI 也读 8100；
- 8000 是作业下发的唯一权威（targets/status/学生可见性/批改/学情）；
- 令牌透传：8000 继续按当前 JWT + ClassMember 裁决，不在 8100 伪造身份。

不变量：
- 只接受 `question_ids`，题目内容由 8100 DB 读取，**不信任前端题目正文**；
- 图片题（q_type=image）本轮明确拒绝（8000 QuizItem 无图片字段），不降级成假文本；
- 创建默认 draft，必须显式 publish；任何一步失败都 fail-closed。
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ApiError, ErrorCode
from app.domains.catalog.models import QuizQuestion
from app.teacher_prep_v2.adapters.api8000 import Api8000Adapter, HttpxApiTransport
from app.teacher_prep_v2.adapters.errors import raise_api_error
from app.teacher_prep_v2.adapters.mapping import InMemoryMappingRegistry
from app.teacher_prep_v2.contracts.errors import ContractError

MAX_QUESTIONS = 50
# 与组卷预览口径一致：选择/填空 5 分，解答 12 分（教师可在后续版本自定义）
SCORE_BY_TYPE = {"choice": 5.0, "fill": 5.0, "solve": 12.0}
TYPE_MAP = {"choice": "choice", "fill": "blank", "solve": "solution"}
ALLOWED_DIFFICULTY = {"easy", "medium", "hard"}

_LEADING_SCORE = re.compile(r"^\s*[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]\s*")
_LEADING_NUMBER = re.compile(r"^\s*\d{1,3}\s*[.、．]\s*(?!\d)")


class PaperPublishRequest(BaseModel):
    """组卷发布请求（只传题目 id 与发布参数）。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    class_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    question_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_QUESTIONS)
    deadline: datetime | None = None
    type: str = Field(default="homework", pattern="^(quiz|pre_class|homework)$")
    client_request_id: str = Field(min_length=8, max_length=128)


def _clean_stem(value: str | None) -> str:
    text = str(value or "").strip()
    text = _LEADING_SCORE.sub("", text, count=1)
    text = _LEADING_NUMBER.sub("", text, count=1)
    return text.strip()


def _options_for_8000(raw: Any) -> dict[str, str] | None:
    """8100 options 可能是 list[str] 或 dict[str,str]；8000 学生端按 dict 渲染。"""
    if isinstance(raw, dict):
        out = {str(k): str(v) for k, v in raw.items() if str(v).strip()}
        return out or None
    if isinstance(raw, list):
        out: dict[str, str] = {}
        for index, item in enumerate(raw):
            text = str(item or "").strip()
            if not text:
                continue
            out[chr(65 + index) if index < 26 else str(index + 1)] = text
        return out or None
    return None


def _map_item(question: QuizQuestion, item_no: int) -> dict[str, Any]:
    q_type = str(question.q_type or "").strip()
    if q_type == "image":
        raise ApiError(
            ErrorCode.VALIDATION,
            f"第 {item_no} 题为图片题：学生作业契约暂不支持图片题干，请先从试卷移除该题再发布",
        )
    mapped_type = TYPE_MAP.get(q_type)
    if mapped_type is None:
        raise ApiError(ErrorCode.VALIDATION, f"第 {item_no} 题类型 {q_type!r} 不支持发布")
    answer = str(question.answer or "").strip()
    if not answer:
        raise ApiError(ErrorCode.VALIDATION, f"第 {item_no} 题缺少标准答案，发布前必须补齐")
    stem = _clean_stem(question.stem_latex)
    if not stem:
        raise ApiError(ErrorCode.VALIDATION, f"第 {item_no} 题题干为空，发布前必须补齐")
    options = _options_for_8000(question.options)
    if mapped_type == "choice" and not options:
        raise ApiError(ErrorCode.VALIDATION, f"第 {item_no} 题是选择题但缺少选项")
    difficulty = str(question.difficulty or "medium").lower()
    if difficulty not in ALLOWED_DIFFICULTY:
        difficulty = "medium"
    return {
        "item_no": item_no,
        "q_type": mapped_type,
        "question_text": stem,
        "options": options,
        "answer": answer,
        "analysis": (str(question.analysis).strip() if question.analysis else None),
        "kp_code": (str(question.kp_code).strip() if question.kp_code else None),
        "difficulty": difficulty,
        "max_score": SCORE_BY_TYPE.get(q_type, 5.0),
    }


async def _load_ordered(session: AsyncSession, question_ids: list[uuid.UUID]) -> list[QuizQuestion]:
    unique_ids = list(dict.fromkeys(question_ids))
    rows = (
        (
            await session.execute(
                select(QuizQuestion).where(QuizQuestion.id.in_(unique_ids))
            )
        )
        .scalars()
        .all()
    )
    by_id = {row.id: row for row in rows}
    missing = [str(qid) for qid in unique_ids if qid not in by_id]
    if missing:
        raise ApiError(ErrorCode.NOT_FOUND, f"题库中不存在以下题目：{missing[:5]}")
    return [by_id[qid] for qid in unique_ids]


def _client_assignment_id(client_request_id: str, class_id: uuid.UUID) -> str:
    digest = hashlib.sha256(f"{client_request_id}|{class_id}".encode("utf-8")).hexdigest()
    return f"paper-{digest[:44]}"  # 50 chars，满足 8000 的 8..64


async def publish_paper(
    session: AsyncSession,
    *,
    token: str,
    body: PaperPublishRequest,
    adapter: Api8000Adapter | None = None,
) -> dict[str, Any]:
    """把 8100 试卷真实发布为 8000 班级作业（draft → published）。"""
    if not token:
        raise ApiError(ErrorCode.UNAUTHORIZED, "缺少统一登录令牌")

    questions = await _load_ordered(session, body.question_ids)
    items = [_map_item(question, index + 1) for index, question in enumerate(questions)]

    adapter = adapter or Api8000Adapter(
        HttpxApiTransport(get_settings().api8000_base_url),
        InMemoryMappingRegistry(),
    )
    try:
        membership = await adapter.require_class_membership(
            token=token, class_id=str(body.class_id)
        )
        # require_class_membership 已拒绝 student；这里只保留可读的失败上下文。
        if membership.member_role not in ("teacher", "owner", "assistant"):
            raise ApiError(
                ErrorCode.FORBIDDEN,
                "当前账号在该班不是教师/助教身份，不能以教师身份发布作业",
            )
        client_id = _client_assignment_id(body.client_request_id, body.class_id)
        assignment_id = await adapter.create_assignment_inline(
            token=token,
            class_id=str(body.class_id),
            title=body.title.strip(),
            quiz_items=items,
            client_assignment_id=client_id,
            deadline=body.deadline,
            assignment_type=body.type,
        )
        published_id = await adapter.publish_assignment(
            token=token,
            assignment_id=assignment_id,
            client_request_id=body.client_request_id[:128],
            idempotency_key=f"publish:{body.client_request_id}"[:256],
        )
    except ContractError as exc:
        raise_api_error(exc)
        raise  # pragma: no cover

    return {
        "assignment_id": published_id,
        "class_id": str(body.class_id),
        "title": body.title.strip(),
        "question_count": len(items),
        "status": "published",
        "deadline": body.deadline.isoformat() if body.deadline else None,
        "client_assignment_id": client_id,
    }


__all__ = ["PaperPublishRequest", "publish_paper", "TYPE_MAP", "SCORE_BY_TYPE"]
