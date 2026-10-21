"""L-221-2 · available_actions 暴露 project_specs / propose_blueprint_revision。"""
from __future__ import annotations

from app.teacher_prep.conversation.views import derive_available_actions


class _BP:
    def __init__(self, id: str, status: str) -> None:
        self.id = id
        self.status = status


def test_no_blueprint_no_specs_actions() -> None:
    actions = derive_available_actions(
        session_status="active", blueprint=None, runs=[], diagnostic=None
    )
    names = {a["action"] for a in actions}
    assert "project_specs" not in names
    assert "propose_blueprint_revision" not in names


def test_confirmed_blueprint_exposes_l2212_actions() -> None:
    bp = _BP("11111111-1111-1111-1111-111111111111", "confirmed")
    actions = derive_available_actions(
        session_status="active", blueprint=bp, runs=[], diagnostic=None  # type: ignore[arg-type]
    )
    by = {a["action"]: a for a in actions}
    assert "project_specs" in by
    assert "propose_blueprint_revision" in by
    assert by["project_specs"]["target"] == str(bp.id)
    assert by["project_specs"]["copy_key"] == "prep.action.project_specs"
    assert by["propose_blueprint_revision"]["copy_key"] == "prep.action.propose_blueprint_revision"


def test_proposal_blueprint_no_specs_actions() -> None:
    bp = _BP("11111111-1111-1111-1111-111111111111", "proposal")
    actions = derive_available_actions(
        session_status="active", blueprint=bp, runs=[], diagnostic=None  # type: ignore[arg-type]
    )
    names = {a["action"] for a in actions}
    assert "project_specs" not in names
    assert "propose_blueprint_revision" not in names
    assert "confirm_proposal" in names
