"""逐页 QA 编排（T24）：把独立检测器的结论落库，并支持"只重试失败页"。

检测器本身是纯函数（不碰网络/DB）；本模块负责：写临时文件 → 调检测器 → 按
(deck, spec_revision, page_no) 收敛入库。单页重试只覆盖目标页行，
其它页行原样不动 —— 这正是"局部重试没有顺手改别的页"的可核对证据。
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError, ErrorCode
from app.ppt_v2.persistence.models import PptV2PageQa

BASELINE = {
    "name": "tp-h1",
    "vpl_version": "r21.1-vpl-1.0.0",
    "rubric": "VISUAL-RUBRIC.md",
    "schema": "tp-h1-visual-qa/v1",
}

# 检测维（视觉 + R8-T 内容维）：供前端/chip 展示真实判定范围。
CHECKS = [
    "overflow", "overlap", "font", "formula", "graphic", "duplicate", "contrast",
    "off_slide", "blank", "density",
    # R8-T 内容维
    "title_empty", "content_thin", "teacher_annotation", "internal_term", "bare_latex",
    "cross_page_duplicate",
]


def _detector():
    """延迟导入：检测器是独立交付物，缺了要给出可诊断的失败而不是 500。"""
    try:
        from app.ppt_v2.qa import detector
    except ImportError as exc:  # pragma: no cover - 集成期缺件路径
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE, f"逐页 QA 检测器不可用: {exc}") from exc
    return detector


async def _spec_pages(
    session: AsyncSession, *, deck_id: uuid.UUID, spec_revision: int
) -> dict[int, dict[str, Any]]:
    """读 deck spec → 内容 QA 视图（元素/role/跨页重复判定源）。

    读不到（deck 不存在 / revision 不匹配 / spec 读取失败）返回空 dict：内容维降级为
    只判 PPTX 文本维，**绝不**因此报「通过」。
    """
    try:
        from app.ppt_v2.qa import content as content_qa
        from app.ppt_v2.service import spec_edit

        row = await spec_edit.load_deck_row(session, str(deck_id))
        if int(row.revision) != int(spec_revision):
            return {}
        return content_qa.spec_pages_from_slides(spec_edit.load_spec(row).slides)
    except Exception:  # noqa: BLE001 - 内容维降级，不阻断视觉维
        return {}


def _page_status(defects: list[dict[str, Any]]) -> str:
    return "fail" if any(d.get("severity") == "p0" for d in defects) else "pass"


def _summary(pages: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, int] = {}
    p0 = 0
    p1 = 0
    for page in pages:
        for d in page.get("defects") or []:
            by_kind[d["kind"]] = by_kind.get(d["kind"], 0) + 1
            if d.get("severity") == "p0":
                p0 += 1
            elif d.get("severity") == "p1":
                p1 += 1
    return {
        "p0_count": p0,
        "p1_count": p1,
        "failed_pages": sorted(p["page_no"] for p in pages if p.get("status") == "fail"),
        "by_kind": by_kind,
    }


async def _upsert(
    session: AsyncSession,
    *,
    deck_id: uuid.UUID,
    spec_revision: int,
    page: dict[str, Any],
    detector_version: str,
    artifact_id: uuid.UUID | None,
    bump_attempts: bool,
) -> PptV2PageQa:
    """按 (deck, spec_revision, page_no) 原子收敛 QA 行。

    用 PG `INSERT ... ON CONFLICT DO UPDATE` 而非「先查后写」：导出作业可能被 taskiq
    重投/并发执行（实测同一 job 被执行多次），先查后写在并发下会撞
    `uq_pptqa_deck_rev_page` 唯一约束（IntegrityError）——DB 级 upsert 使重跑幂等。
    """
    values = {
        "deck_id": deck_id,
        "spec_revision": spec_revision,
        "page_no": page["page_no"],
        "status": page["status"],
        "defects": page.get("defects") or [],
        "metrics": page.get("metrics") or {},
        "detector_version": detector_version,
        "artifact_id": artifact_id,
        "attempts": 1,
    }
    stmt = pg_insert(PptV2PageQa).values(**values)
    set_: dict[str, Any] = {
        "status": stmt.excluded.status,
        "defects": stmt.excluded.defects,
        "metrics": stmt.excluded.metrics,
        "detector_version": stmt.excluded.detector_version,
        "artifact_id": stmt.excluded.artifact_id,
        "checked_at": datetime.now(UTC),
    }
    if bump_attempts:
        set_["attempts"] = PptV2PageQa.attempts + 1
    await session.execute(
        stmt.on_conflict_do_update(
            index_elements=["deck_id", "spec_revision", "page_no"], set_=set_
        )
    )
    await session.flush()
    return (
        await session.execute(
            select(PptV2PageQa).where(
                PptV2PageQa.deck_id == deck_id,
                PptV2PageQa.spec_revision == spec_revision,
                PptV2PageQa.page_no == page["page_no"],
            )
        )
    ).scalar_one()


def _row_view(row: PptV2PageQa) -> dict[str, Any]:
    return {
        "page_no": row.page_no,
        "status": row.status,
        "defects": row.defects,
        "metrics": row.metrics,
        "attempts": row.attempts,
        "detector_version": row.detector_version,
        "checked_at": row.checked_at.isoformat() if row.checked_at else None,
    }


async def run_full_qa(
    session: AsyncSession,
    *,
    deck_id: uuid.UUID,
    spec_revision: int,
    pptx_bytes: bytes,
    pages_png: dict[int, bytes],
    plan: dict[str, Any] | None = None,
    artifact_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    detector = _detector()
    spec_pages = await _spec_pages(session, deck_id=deck_id, spec_revision=spec_revision)
    with tempfile.TemporaryDirectory(prefix="t24-qa-") as tmp:
        tmp_path = Path(tmp)
        pptx_path = tmp_path / "deck.pptx"
        pptx_path.write_bytes(pptx_bytes)
        pages_dir = tmp_path / "pages"
        pages_dir.mkdir()
        for page_no, data in pages_png.items():
            (pages_dir / f"page-{page_no:02d}.png").write_bytes(data)
        result = detector.detect_deck_qa(pptx_path, pages_dir, plan=plan, spec_pages=spec_pages)

    for page in result["pages"]:
        await _upsert(
            session,
            deck_id=deck_id,
            spec_revision=spec_revision,
            page={**page, "status": page.get("status") or _page_status(page.get("defects") or [])},
            detector_version=result.get("detector_version", detector.DETECTOR_VERSION),
            artifact_id=artifact_id,
            bump_attempts=False,
        )
    return await get_qa(session, deck_id, spec_revision=spec_revision, artifact_id=artifact_id)


async def retry_page_qa(
    session: AsyncSession,
    *,
    deck_id: uuid.UUID,
    spec_revision: int,
    page_no: int,
    pptx_bytes: bytes,
    page_png: bytes,
    plan: dict[str, Any] | None = None,
    artifact_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """局部重试：只重算目标页。其它页行不读不写，调用方可用 attempts/checked_at 核对。"""
    detector = _detector()
    spec_pages = await _spec_pages(session, deck_id=deck_id, spec_revision=spec_revision)
    with tempfile.TemporaryDirectory(prefix="t24-qa-page-") as tmp:
        tmp_path = Path(tmp)
        pptx_path = tmp_path / "deck.pptx"
        pptx_path.write_bytes(pptx_bytes)
        png_path = tmp_path / f"page-{page_no:02d}.png"
        png_path.write_bytes(page_png)
        page = detector.detect_page_qa(pptx_path, page_no, png_path, plan=plan, spec_pages=spec_pages)

    page = {**page, "status": page.get("status") or _page_status(page.get("defects") or [])}
    row = await _upsert(
        session,
        deck_id=deck_id,
        spec_revision=spec_revision,
        page=page,
        detector_version=page.get("detector_version", detector.DETECTOR_VERSION),
        artifact_id=artifact_id,
        bump_attempts=True,
    )
    return {
        **_row_view(row),
        "schema": page.get("schema", "tp-h1-visual-qa/v1"),
        "others_unchanged": True,
    }


async def get_qa(
    session: AsyncSession,
    deck_id: str | uuid.UUID,
    *,
    spec_revision: int | None = None,
    artifact_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    uid = deck_id if isinstance(deck_id, uuid.UUID) else uuid.UUID(str(deck_id))
    stmt = select(PptV2PageQa).where(PptV2PageQa.deck_id == uid)
    if spec_revision is not None:
        stmt = stmt.where(PptV2PageQa.spec_revision == spec_revision)
    rows = (await session.execute(stmt.order_by(PptV2PageQa.spec_revision, PptV2PageQa.page_no))).scalars().all()
    if not rows:
        return {
            "deck_id": str(uid),
            "spec_revision": spec_revision,
            "artifact_id": str(artifact_id) if artifact_id else None,
            "baseline": BASELINE,
            "checks": CHECKS,
            "pages": [],
            "summary": {"p0_count": 0, "p1_count": 0, "failed_pages": [], "by_kind": {}},
            "status": "unknown",
        }
    revision = spec_revision if spec_revision is not None else rows[-1].spec_revision
    selected = [r for r in rows if r.spec_revision == revision]
    pages = [_row_view(r) for r in selected]
    summary = _summary(pages)
    return {
        "deck_id": str(uid),
        "spec_revision": revision,
        "artifact_id": str(artifact_id) if artifact_id else (str(selected[0].artifact_id) if selected[0].artifact_id else None),
        "baseline": BASELINE,
        "checks": CHECKS,
        "pages": pages,
        "summary": summary,
        "status": "fail" if summary["p0_count"] else "pass",
    }
