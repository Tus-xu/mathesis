"""错题正解一次生成、持久化缓存与配图降级回归测试。"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.butler import skills
from app.models.coursework import ErrorRecord


def test_error_record_declares_solution_cache_columns():
    assert hasattr(ErrorRecord, "generated_answer")
    assert hasattr(ErrorRecord, "solution_figure")
    assert hasattr(ErrorRecord, "solution_generated_at")


@pytest.mark.asyncio
async def test_error_detail_returns_saved_solution_without_model_call(monkeypatch):
    user_id = uuid.uuid4()
    record = SimpleNamespace(
        id=uuid.uuid4(), user_id=user_id, deleted_at=None, kp_code=None,
        error_type=None, question_text="求 x", answer_text="", source_channel="auto_judge",
        wrong_count=1, review_count=0, generated_answer="已保存的正解",
        solution_figure=[], solution_generated_at=None,
    )
    db = AsyncMock()
    db.get.return_value = record
    monkeypatch.setattr(skills.butler_llm, "generate", AsyncMock(side_effect=AssertionError("cache must skip LLM")))

    result = await skills.error_detail(db, user_id, record.id)

    assert result["generated_answer"] == "已保存的正解"
    assert result["cached"] is True


@pytest.mark.asyncio
async def test_error_detail_persists_answer_when_solution_figure_fails(monkeypatch):
    user_id = uuid.uuid4()
    record = SimpleNamespace(
        id=uuid.uuid4(), user_id=user_id, deleted_at=None, kp_code=None,
        error_type=None, question_text="如图，证明直线与平面垂直", answer_text="", source_channel="auto_judge",
        wrong_count=1, review_count=0, generated_answer=None, solution_figure=[], solution_generated_at=None,
    )
    db = AsyncMock()
    db.get.return_value = record
    monkeypatch.setattr(skills.butler_llm, "generate", AsyncMock(return_value="新正解"))
    monkeypatch.setattr("app.butler.skills.build_solution_figure", AsyncMock(side_effect=RuntimeError("figure unavailable")), raising=False)

    result = await skills.error_detail(db, user_id, record.id)

    assert result["generated_answer"] == "新正解"
    assert result["solution_figure"] == []
    assert record.generated_answer == "新正解"
    db.commit.assert_awaited_once()
