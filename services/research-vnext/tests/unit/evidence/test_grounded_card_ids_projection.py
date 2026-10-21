"""RV07 · `grounded_card_ids_for_session` 的投影收窄守卫。

该函数挂在**每条提案**的准备路径上（`proposal_handlers.prepare`），
若退回全量载入（`snippet`(Text) + `locator`/`source_ref`/`provenance`/`rights`/
`confidence` 等 JSONB）会随会话证据量线性放大 —— 而它只需要两个字段。
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[2]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from research_vnext.core.evidence.service import (  # noqa: E402
    grounded_card_ids_for_session,
)


class _Row:
    """只带裁决所需 3 列的行替身。"""

    def __init__(self, id: uuid.UUID, status: str, stale_state: str) -> None:
        self.id = id
        self.status = status
        self.stale_state = stale_state


class _Result:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def all(self) -> list[_Row]:
        return list(self._rows)


class _CapturingSession:
    """只记录被执行的语句，不做真实查询。"""

    def __init__(self, rows: list[_Row]) -> None:
        self.rows = rows
        self.statement = None

    def execute(self, stmt):  # noqa: ANN001, ANN201
        self.statement = stmt
        return _Result(self.rows)


ACTIVE_ID = uuid.UUID("a0000000-0000-0000-0000-00000000000a")
STALE_ID = uuid.UUID("a0000000-0000-0000-0000-00000000000b")
RETIRED_ID = uuid.UUID("a0000000-0000-0000-0000-00000000000c")


def test_selects_only_the_columns_needed_for_grounding() -> None:
    """退回 `select(EvidenceCardV2Row)` 会把 Text/JSONB 全量拉回 —— 本用例钉死它。"""
    session = _CapturingSession([])
    grounded_card_ids_for_session(session, session_id=uuid.uuid4())

    sql = str(session.statement)
    for heavy in ("snippet", "locator", "provenance", "rights", "confidence", "source_ref"):
        assert heavy not in sql, f"投影不该包含 {heavy}：{sql}"
    for needed in ("id", "status", "stale_state"):
        assert needed in sql, f"投影缺少裁决所需的 {needed}：{sql}"


def test_filters_to_active_and_not_stale() -> None:
    session = _CapturingSession(
        [
            _Row(ACTIVE_ID, "ACTIVE", "FRESH"),
            _Row(STALE_ID, "ACTIVE", "STALE"),
            _Row(RETIRED_ID, "RETIRED", "FRESH"),
        ]
    )
    grounded = grounded_card_ids_for_session(session, session_id=uuid.uuid4())

    assert grounded == {str(ACTIVE_ID)}


def test_reuses_is_grounded_card_semantics_case_insensitively() -> None:
    """判据复用 `is_grounded_card`（会 upper）—— 不在 SQL 里重写规则。"""
    session = _CapturingSession(
        [
            _Row(ACTIVE_ID, "active", "fresh"),
            _Row(STALE_ID, "ACTIVE", "stale"),
        ]
    )
    grounded = grounded_card_ids_for_session(session, session_id=uuid.uuid4())

    assert grounded == {str(ACTIVE_ID)}


def test_blank_stale_state_counts_as_fresh() -> None:
    """`stale_state` 为空时按 FRESH 处理（与 `is_grounded_card` 的 `or "FRESH"` 一致）。"""
    session = _CapturingSession([_Row(ACTIVE_ID, "ACTIVE", "")])
    grounded = grounded_card_ids_for_session(session, session_id=uuid.uuid4())

    assert grounded == {str(ACTIVE_ID)}


def test_empty_session_yields_empty_set() -> None:
    session = _CapturingSession([])
    assert grounded_card_ids_for_session(session, session_id=uuid.uuid4()) == set()


def test_tenant_filter_is_applied_when_given() -> None:
    session = _CapturingSession([])
    tenant_id = uuid.uuid4()
    grounded_card_ids_for_session(session, session_id=uuid.uuid4(), tenant_id=tenant_id)

    assert tenant_id in session.statement.compile().params.values()


def test_tenant_filter_is_absent_when_not_given() -> None:
    """不传 `tenant_id` 时只按 session 过滤（session 已在上游授权）。"""
    session = _CapturingSession([])
    grounded_card_ids_for_session(session, session_id=uuid.uuid4())

    sql = str(session.statement)
    assert "tenant_id" not in sql
