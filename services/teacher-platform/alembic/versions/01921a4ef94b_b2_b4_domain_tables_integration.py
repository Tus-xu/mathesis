"""b2 b4 domain tables integration

rescue 整合迁移（2026-09-07，Chief Integration Agent）：
只创建缺失的 B2/B4 域表。相对 autogenerate 原始输出做了以下裁决性修剪：

1. 【移除】drop_table('teacher_session_contexts') —— B1 运行时自建表，含真实会话数据，
   且该表归 MIGRATION_REQUEST-001 收编管线管理，不得由本迁移删除。
2. 【移除】kp_nodes / quiz_questions 的 alter/drop/create_index —— B2 运行时自建表与
   B2 模型存在漂移（stem_hash/stem_plain/类型宽度），涉及 785 道真实题目，
   漂移对齐单独立项（见 RESCUE_STATE MOCK_DEBT/SCHEMA DEBT），禁止盲改。

Revision ID: 01921a4ef94b
Revises: e155e6a302bf
Create Date: 2026-09-07 16:54:39.228298
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '01921a4ef94b'
down_revision = 'e155e6a302bf'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('classroom_sessions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('teacher_id', sa.Uuid(), nullable=False),
    sa.Column('class_id', sa.Uuid(), nullable=False),
    sa.Column('join_code', sa.String(length=6), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('created','open','ended','archived')", name='ck_csession_status'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('join_code')
    )
    op.create_index('ix_csession_teacher', 'classroom_sessions', ['teacher_id', 'created_at'], unique=False)
    op.create_table('classroom_summaries',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('stats', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('insight', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id')
    )
    op.create_table('deck_templates',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('style', sa.String(length=32), nullable=False),
    sa.Column('slide_count', sa.Integer(), nullable=False),
    sa.Column('thumb', sa.String(length=256), nullable=True),
    sa.Column('slots', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('draw_library',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('thumb', sa.String(length=256), nullable=True),
    sa.Column('records', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('expr', sa.Text(), nullable=True),
    sa.Column('shared', sa.Boolean(), nullable=False),
    sa.Column('owner_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('figure_presets',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('desc', sa.Text(), nullable=False),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('params_schema', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('folders',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('desc', sa.Text(), nullable=True),
    sa.Column('owner_id', sa.Uuid(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('grading_assignments',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=False),
    sa.Column('class_id', sa.Uuid(), nullable=False),
    sa.Column('total', sa.Integer(), nullable=False),
    sa.Column('submitted', sa.Integer(), nullable=False),
    sa.Column('graded', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('lesson_templates',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('boards', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('quality_report', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('photos',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('owner_id', sa.Uuid(), nullable=False),
    sa.Column('original_key', sa.String(length=256), nullable=False),
    sa.Column('enhanced_key', sa.String(length=256), nullable=True),
    sa.Column('ocr_blocks', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('plans',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('topic', sa.String(length=256), nullable=False),
    sa.Column('class_id', sa.Uuid(), nullable=True),
    sa.Column('lesson_type', sa.String(length=32), nullable=False),
    sa.Column('boards', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('outline', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('confirmed', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('textbooks',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('provenance', sa.String(length=32), nullable=False),
    sa.Column('chapters', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('classroom_activities',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('question_id', sa.Uuid(), nullable=True),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('ord', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('pushed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('locked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('question','poll','game','photo_submit') AND status IN ('draft','ready','pushed','collecting','locked','revealed','completed')", name='ck_cactivity_kind_status'),
    sa.ForeignKeyConstraint(['session_id'], ['classroom_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_cactivity_session', 'classroom_activities', ['session_id', 'ord'], unique=False)
    op.create_table('classroom_participants',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('session_id', sa.Uuid(), nullable=False),
    sa.Column('student_name', sa.String(length=64), nullable=False),
    sa.Column('anon_id', sa.String(length=32), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['session_id'], ['classroom_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id', 'anon_id', name='uq_cparticipant_session_anon')
    )
    op.create_table('decks',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('title', sa.String(length=256), nullable=False),
    sa.Column('template_id', sa.Uuid(), nullable=True),
    sa.Column('source', sa.String(length=32), nullable=False),
    sa.Column('class_id', sa.Uuid(), nullable=True),
    sa.Column('slides', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('created_by', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['template_id'], ['deck_templates.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('folder_question_refs',
    sa.Column('folder_id', sa.Uuid(), nullable=False),
    sa.Column('question_id', sa.Uuid(), nullable=False),
    sa.Column('added_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['folder_id'], ['folders.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['question_id'], ['quiz_questions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('folder_id', 'question_id'),
    sa.UniqueConstraint('folder_id', 'question_id', name='uq_folder_question')
    )
    op.create_table('grading_questions',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('assignment_id', sa.Uuid(), nullable=False),
    sa.Column('kp_code', sa.String(length=64), nullable=False),
    sa.Column('stem_latex', sa.Text(), nullable=False),
    sa.Column('error_rate', sa.Numeric(precision=5, scale=4), nullable=True),
    sa.ForeignKeyConstraint(['assignment_id'], ['grading_assignments.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('recipes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('preset_id', sa.String(length=64), nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('school_shared', sa.Boolean(), nullable=False),
    sa.Column('note', sa.Text(), nullable=False),
    sa.Column('owner_id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['preset_id'], ['figure_presets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('classroom_ai_generations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('activity_id', sa.Uuid(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('content', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('verifier_result', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('ai_run_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('not_requested','generating','verifying','awaiting_teacher','approved','pushed','rejected')", name='ck_caigen_status'),
    sa.ForeignKeyConstraint(['activity_id'], ['classroom_activities.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('classroom_responses',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('activity_id', sa.Uuid(), nullable=False),
    sa.Column('participant_id', sa.Uuid(), nullable=False),
    sa.Column('answer', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('image_key', sa.String(length=256), nullable=True),
    sa.Column('attempt_no', sa.Integer(), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['activity_id'], ['classroom_activities.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['participant_id'], ['classroom_participants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('activity_id', 'participant_id', 'attempt_no', name='uq_cresponse_attempt')
    )
    op.create_table('grading_answers',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('assignment_id', sa.Uuid(), nullable=False),
    sa.Column('question_id', sa.Uuid(), nullable=False),
    sa.Column('student_name', sa.String(length=64), nullable=False),
    sa.Column('image_key', sa.String(length=256), nullable=False),
    sa.Column('cluster_id', sa.Uuid(), nullable=True),
    sa.ForeignKeyConstraint(['assignment_id'], ['grading_assignments.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['question_id'], ['grading_questions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_ganswer_assignment', 'grading_answers', ['assignment_id', 'question_id'], unique=False)
    op.create_table('grading_clusters',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('question_id', sa.Uuid(), nullable=False),
    sa.Column('summary', sa.Text(), nullable=False),
    sa.Column('feedback', sa.Text(), nullable=True),
    sa.Column('confirmed', sa.Boolean(), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('tag', sa.String(length=16), nullable=True),
    sa.Column('members', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['question_id'], ['grading_questions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('recognition_jobs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('photo_ids', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('config', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('blocks', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=True),
    sa.Column('deck_id', sa.Uuid(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['deck_id'], ['decks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('slide_patch_logs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('deck_id', sa.Uuid(), nullable=False),
    sa.Column('slide_id', sa.String(length=64), nullable=False),
    sa.Column('patch', postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), 'sqlite'), nullable=False),
    sa.Column('modifier', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['deck_id'], ['decks.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('slide_patch_logs')
    op.drop_table('recognition_jobs')
    op.drop_table('grading_clusters')
    op.drop_index('ix_ganswer_assignment', table_name='grading_answers')
    op.drop_table('grading_answers')
    op.drop_table('classroom_responses')
    op.drop_table('classroom_ai_generations')
    op.drop_table('recipes')
    op.drop_table('grading_questions')
    op.drop_table('folder_question_refs')
    op.drop_table('decks')
    op.drop_table('classroom_participants')
    op.drop_index('ix_cactivity_session', table_name='classroom_activities')
    op.drop_table('classroom_activities')
    op.drop_table('textbooks')
    op.drop_table('plans')
    op.drop_table('photos')
    op.drop_table('lesson_templates')
    op.drop_table('grading_assignments')
    op.drop_table('folders')
    op.drop_table('figure_presets')
    op.drop_table('draw_library')
    op.drop_table('deck_templates')
    op.drop_table('classroom_summaries')
    op.drop_index('ix_csession_teacher', table_name='classroom_sessions')
    op.drop_table('classroom_sessions')
