"""M2 迁移：错题去重（m2_016_error_record_dedup）

Revision ID: m2_016_error_record_dedup
Revises: m2_015_figure_params
Create Date: 2026-08-15

变更：
1. 数据清理：同用户同题干（md5(btrim(question_text))）的活动（未软删）重复行合并——
   保留 wrong_count 最大者（并列取最早），wrong_count/review_count 累加，
   答案/错因/知识点/备注/配图取最新非空，next_review_at 取最早；
   其余重复行软删除（deleted_at=now）。
2. 唯一索引：uq_error_records_user_question ON (user_id, md5(btrim(question_text)))
   WHERE deleted_at IS NULL（应用层 _upsert_error_record 已先行去重，索引兜底并发竞态）。

幂等保护：数据清理与索引创建均可重复执行。
"""

import hashlib
import json

import sqlalchemy as sa

from alembic import op

revision = "m2_016_error_record_dedup"
down_revision = "m2_015_figure_params"
branch_labels = None
depends_on = None

_INDEX_NAME = "uq_error_records_user_question"


def _dedup() -> int:
    """合并活动重复行，返回软删除的行数。"""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, user_id, question_text, wrong_count, review_count, "
            "answer_text, error_type, kp_code, note, image, next_review_at "
            "FROM error_records WHERE deleted_at IS NULL "
            "ORDER BY user_id, created_at ASC, id ASC"
        )
    ).fetchall()

    groups: dict[tuple, list] = {}
    order: list[tuple] = []
    for r in rows:
        text = (r.question_text or "").strip()
        key = (
            str(r.user_id),
            hashlib.md5(text.encode("utf-8")).hexdigest() if text else f"__empty__:{r.id}",
        )
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(r)

    deleted = 0
    for key in order:
        grp = groups[key]
        if len(grp) < 2:
            continue
        keeper = max(grp, key=lambda r: int(r.wrong_count or 1))
        others = [r for r in grp if r.id != keeper.id]

        # 聚合值
        merged = {
            "wrong_count": sum(int(r.wrong_count or 1) for r in grp),
            "review_count": sum(int(r.review_count or 0) for r in grp),
        }
        for col in ("answer_text", "error_type", "kp_code", "note"):
            latest = next((getattr(r, col) for r in reversed(grp) if getattr(r, col) is not None), None)
            merged[col] = latest if latest is not None else getattr(keeper, col)
        images = [r.image for r in reversed(grp) if r.image]
        keeper_image = keeper.image
        merged["image"] = json.dumps(
            (images[0] if images else keeper_image) or [], ensure_ascii=False
        )
        nexts = [r.next_review_at for r in grp if r.next_review_at is not None]
        merged["next_review_at"] = min(nexts) if nexts else keeper.next_review_at

        bind.execute(
            sa.text(
                "UPDATE error_records SET wrong_count=:wc, review_count=:rc, "
                "answer_text=:at, error_type=:et, kp_code=:kc, note=:nt, "
                "image=:img, next_review_at=:nr WHERE id=:id"
            ),
            {
                "id": keeper.id,
                "wc": merged["wrong_count"],
                "rc": merged["review_count"],
                "at": merged["answer_text"],
                "et": merged["error_type"],
                "kc": merged["kp_code"],
                "nt": merged["note"],
                "img": merged["image"],
                "nr": merged["next_review_at"],
            },
        )
        ids = [r.id for r in others]
        bind.execute(
            sa.text("UPDATE error_records SET deleted_at = now() WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        deleted += len(ids)
    return deleted


def upgrade() -> None:
    deleted = _dedup()
    op.execute(
        sa.text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME} "
            "ON error_records (user_id, md5(btrim(question_text))) "
            "WHERE deleted_at IS NULL AND question_text IS NOT NULL AND btrim(question_text) <> ''"
        )
    )
    print(f"[m2_016] 错题去重：软删除 {deleted} 条重复记录，唯一索引已建")


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_INDEX_NAME}"))
