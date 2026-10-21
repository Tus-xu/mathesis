# coding: utf-8
"""域 models 约束单测（离线 DDL 断言：幂等唯一约束/检查约束/索引是 schema 的一部分）。"""
from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable, CreateIndex

from app.domains.catalog import models as cat
from app.domains.classroom import models as cr
from app.domains.contents import models as ct
from app.domains.figures import models as fg
from app.domains.grading import models as gr
from app.domains.recognition import models as rc


def _ddl(table) -> str:
    return str(CreateTable(table).compile(dialect=postgresql.dialect())).upper()


def test_all_tablenames_registered():
    expected = {
        'users', 'classes', 'kp_nodes', 'textbooks', 'quiz_questions', 'folders', 'folder_question_refs',
        'deck_templates', 'lesson_templates', 'decks', 'plans', 'export_jobs', 'slide_patch_logs',
        'figure_presets', 'recipes', 'draw_library',
        'photos', 'recognition_jobs',
        'grading_assignments', 'grading_questions', 'grading_answers', 'grading_clusters',
        'classroom_sessions', 'classroom_participants', 'classroom_activities',
        'classroom_responses', 'classroom_events', 'classroom_ai_generations', 'classroom_summaries',
    }
    actual = {t for m in (cat, ct, fg, gr, rc, cr) for t in m.Base.metadata.tables}
    # rescue 整合后 Base.metadata 为全平台共享（B3 platform / B2 knowledge 表同注册），
    # 断言语义改为：B4 全部域表必须已注册（子集断言，共享注册不视为错误）。
    assert expected <= actual, sorted(expected - actual)


def test_idempotency_unique_constraints():
    """G8：幂等靠 DB 唯一约束（§14）。"""
    resp = cr.ClassroomResponse.__table__
    uq = {c.name: c for c in resp.constraints if c.__class__.__name__ == 'UniqueConstraint'}
    cols = {col.name for col in uq['uq_cresponse_attempt'].columns}
    assert cols == {'activity_id', 'participant_id', 'attempt_no'}

    ev = cr.ClassroomEvent.__table__
    uq_ev = {c.name: c for c in ev.constraints if c.__class__.__name__ == 'UniqueConstraint'}
    # rescue 整合：classroom_events 以 app/db/models/platform.py 为唯一定义（constraint 名随 canonical）
    assert {col.name for col in uq_ev['uq_classroom_events_session_seq'].columns} == {'session_id', 'seq'}

    part = cr.ClassroomParticipant.__table__
    uq_p = {c.name: c for c in part.constraints if c.__class__.__name__ == 'UniqueConstraint'}
    assert {col.name for col in uq_p['uq_cparticipant_session_anon'].columns} == {'anon_id', 'session_id'}

    ddl = _ddl(cr.ClassroomSession.__table__)
    assert 'UNIQUE' in ddl and 'JOIN_CODE' in ddl  # join_code 6 位唯一

    ref = cat.FolderQuestionRef.__table__
    assert [c.name for c in ref.primary_key.columns] == ['folder_id', 'question_id']


def test_state_check_constraints():
    assert "STATUS IN ('CREATED','OPEN','ENDED','ARCHIVED')" in _ddl(cr.ClassroomSession.__table__)
    act_ddl = _ddl(cr.ClassroomActivity.__table__)
    assert "'PUSHED','COLLECTING','LOCKED','REVEALED','COMPLETED'" in act_ddl
    assert 'NOT_REQUESTED' in _ddl(cr.ClassroomAiGeneration.__table__)
    assert "Q_TYPE IN ('CHOICE','FILL','SOLVE','IMAGE')" in _ddl(cat.QuizQuestion.__table__)


def test_gin_index_on_kp_path():
    idx_sqls = [str(CreateIndex(ix).compile(dialect=postgresql.dialect())).upper()
                for ix in cat.QuizQuestion.__table__.indexes]
    assert any('GIN' in s and 'KP_PATH' in s for s in idx_sqls)


def test_attempt_no_defaults_to_1():
    col = cr.ClassroomResponse.__table__.c.attempt_no
    assert col.default.arg == 1


def test_figure_preset_pk_is_text():
    pk = fg.FigurePreset.__table__.primary_key
    assert [c.name for c in pk.columns] == ['id']
    assert str(pk.columns[0].type).upper().startswith('VARCHAR')
