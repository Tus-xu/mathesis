"""RV05 · 解析应用服务：授权 → 入队/同步解析 → 落库 anchors → stale 投影。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..api.deps import (
    AccessContext,
    READ_CAPABILITY,
    WRITE_CAPABILITY,
    require_capability,
)
from ..assets.models import ResearchAssetRow
from ..assets.service import authorize_asset, get_asset
from ..domain.errors import DomainError, TenantMismatch
from .anchors import KIND_TO_ANCHOR, classify_stale, make_anchor_id, validate_bbox
from .models import (
    PARSE_FAILED,
    PARSE_PARTIAL,
    PARSE_SUCCEEDED,
    AssetAnchorRow,
    AssetParseRunRow,
)
from .provider import ParseDocument, ParserProviderError, content_fingerprint
from .isolation import SubprocessParserProvider, get_parser_provider


class ParseNotFound(DomainError):
    code = "PARSE_NOT_FOUND"
    http_status = 404


class ParseAssetNotReady(DomainError):
    code = "ASSET_NOT_READY"
    http_status = 409


class AnchorStale(DomainError):
    code = "ANCHOR_STALE"
    http_status = 409


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _page_size_map(doc: ParseDocument) -> dict[int, tuple[float, float]]:
    return {p.page: (p.width, p.height) for p in doc.pages}


def persist_parse_result(
    db: Session,
    *,
    ctx: AccessContext,
    asset: ResearchAssetRow,
    document: ParseDocument,
    identity_dict: dict[str, Any],
    input_hash: str,
    output_hash: str,
    job_id: uuid.UUID | None = None,
    isolation: str = "subprocess",
) -> tuple[AssetParseRunRow, list[AssetAnchorRow]]:
    authorize_asset(db, ctx, asset, capability=WRITE_CAPABILITY)
    if document.is_partial:
        status = PARSE_PARTIAL
    elif document.failed_pages:
        status = PARSE_FAILED
    else:
        status = PARSE_SUCCEEDED

    run = AssetParseRunRow(
        id=uuid.uuid4(),
        tenant_id=asset.tenant_id,
        project_id=asset.project_id,
        owner_id=asset.owner_id,
        session_id=asset.session_id,
        asset_id=asset.id,
        asset_version=asset.version,
        asset_content_hash=asset.content_hash,
        job_id=job_id,
        status=status,
        parser_name=str(identity_dict.get("name") or ""),
        parser_version=str(identity_dict.get("version") or ""),
        parser_digest=str(identity_dict.get("digest") or ""),
        parser_license=str(identity_dict.get("license") or ""),
        parser_config=dict(identity_dict.get("config") or {}),
        parser_config_hash=str(identity_dict.get("config_hash") or ""),
        input_hash=input_hash,
        output_hash=output_hash,
        isolation=isolation,
        page_count=len(document.pages),
        succeeded_pages=list(document.succeeded_pages),
        failed_pages=list(document.failed_pages),
        missing_kinds=list(document.missing_kinds),
        warnings=list(document.warnings),
        section_tree=document.section_tree(),
        started_at=_utcnow(),
        finished_at=_utcnow(),
    )
    db.add(run)
    db.flush()

    sizes = _page_size_map(document)
    rows: list[AssetAnchorRow] = []
    for el in document.elements:
        anchor_type = KIND_TO_ANCHOR.get(el.kind)
        if anchor_type is None:
            continue
        w, h = sizes.get(el.page, (0.0, 0.0))
        bbox_warn = ""
        try:
            if w > 0 and h > 0:
                bbox = validate_bbox(el.bbox, w, h)
            else:
                bbox = tuple(float(v) for v in el.bbox)
        except ValueError as e:
            bbox = tuple(float(v) for v in el.bbox)
            bbox_warn = f"bbox_invalid: {e}"
        th = el.text_hash()
        rh = el.region_hash(w, h) if w and h else content_fingerprint  # type: ignore[assignment]
        if not isinstance(rh, str):
            rh = el.region_hash(612.0, 792.0)
        aid = make_anchor_id(
            asset_id=str(asset.id),
            asset_version=asset.version,
            content_hash=asset.content_hash,
            parse_run_id=str(run.id),
            page=el.page,
            kind=anchor_type.value,
            text_hash=th,
            region_hash=rh,
        )
        warns = list(el.warnings)
        if bbox_warn:
            warns.append(bbox_warn)
        rows.append(
            AssetAnchorRow(
                id=uuid.uuid4(),
                anchor_id=aid,
                tenant_id=asset.tenant_id,
                asset_id=asset.id,
                asset_version=asset.version,
                content_hash=asset.content_hash,
                parse_run_id=run.id,
                parser_name=run.parser_name,
                parser_version=run.parser_version,
                type=anchor_type.value,
                page=el.page,
                bbox=list(bbox),
                section_path=list(el.section_path),
                text_preview=el.text[:500],
                text_hash=th,
                region_hash=rh,
                page_width=w,
                page_height=h,
                confidence=float(el.confidence),
                warnings=warns,
                payload={"kind": el.kind, "level": el.level, **({} if not el.meta else {"meta": el.meta})},
            )
        )
    db.add_all(rows)
    db.flush()
    return run, rows


def run_parse_on_asset(
    db: Session,
    *,
    ctx: AccessContext,
    asset_id: uuid.UUID,
    pdf_path: Path,
    provider: SubprocessParserProvider | None = None,
    job_id: uuid.UUID | None = None,
) -> tuple[AssetParseRunRow, list[AssetAnchorRow]]:
    asset = get_asset(db, asset_id)
    if asset is None:
        raise ParseNotFound("资产不存在")
    authorize_asset(db, ctx, asset, capability=WRITE_CAPABILITY)
    if asset.media_type not in ("application/pdf", "application/x-pdf") and not str(
        asset.original_filename
    ).lower().endswith(".pdf"):
        raise ParseAssetNotReady("仅支持 PDF 资产解析")
    if asset.revoked_at is not None:
        raise ParseAssetNotReady("资产已撤权")

    on_disk_hash = content_fingerprint(pdf_path)
    if on_disk_hash != asset.content_hash:
        # 文件已更换：旧 anchor 不得静默复用
        raise ParseAssetNotReady("资产 content hash 与磁盘文件不一致，请重新上传版本")

    provider = provider or get_parser_provider()
    try:
        detailed = provider.parse_detailed(pdf_path)
    except ParserProviderError as e:
        run = AssetParseRunRow(
            id=uuid.uuid4(),
            tenant_id=asset.tenant_id,
            project_id=asset.project_id,
            owner_id=asset.owner_id,
            session_id=asset.session_id,
            asset_id=asset.id,
            asset_version=asset.version,
            asset_content_hash=asset.content_hash,
            job_id=job_id,
            status=PARSE_FAILED,
            parser_name=e.code,
            parser_version="",
            parser_digest="",
            parser_license="",
            parser_config={},
            parser_config_hash="",
            input_hash=on_disk_hash,
            output_hash="",
            error_code=e.code,
            error_message=e.message,
            finished_at=_utcnow(),
        )
        db.add(run)
        db.flush()
        return run, []

    return persist_parse_result(
        db,
        ctx=ctx,
        asset=asset,
        document=detailed.document,
        identity_dict=detailed.identity.to_dict(),
        input_hash=detailed.input_hash,
        output_hash=detailed.output_hash,
        job_id=job_id,
        isolation="subprocess",
    )


def list_parse_runs(db: Session, asset_id: uuid.UUID, *, tenant_id: uuid.UUID) -> list[AssetParseRunRow]:
    return list(
        db.execute(
            select(AssetParseRunRow)
            .where(AssetParseRunRow.asset_id == asset_id, AssetParseRunRow.tenant_id == tenant_id)
            .order_by(AssetParseRunRow.created_at.desc())
        ).scalars()
    )


def get_parse_run(db: Session, run_id: uuid.UUID) -> AssetParseRunRow | None:
    return db.get(AssetParseRunRow, run_id)


def list_anchors(
    db: Session,
    *,
    ctx: AccessContext,
    asset_id: uuid.UUID,
    parse_run_id: uuid.UUID | None = None,
    anchor_type: str | None = None,
    include_stale: bool = True,
) -> list[dict[str, Any]]:
    asset = get_asset(db, asset_id)
    if asset is None:
        raise ParseNotFound("资产不存在")
    authorize_asset(db, ctx, asset, capability=READ_CAPABILITY)

    q = select(AssetAnchorRow).where(
        AssetAnchorRow.asset_id == asset_id,
        AssetAnchorRow.tenant_id == ctx.tenant_id,
    )
    if parse_run_id:
        q = q.where(AssetAnchorRow.parse_run_id == parse_run_id)
    if anchor_type:
        q = q.where(AssetAnchorRow.type == anchor_type)
    q = q.order_by(AssetAnchorRow.page, AssetAnchorRow.id)
    rows = list(db.execute(q).scalars())
    out: list[dict[str, Any]] = []
    for row in rows:
        stale, reason = classify_stale(
            anchor_asset_version=row.asset_version,
            anchor_content_hash=row.content_hash,
            current_asset_version=asset.version,
            current_content_hash=asset.content_hash,
            asset_revoked=asset.revoked_at is not None,
        )
        if stale and not include_stale:
            continue
        out.append(row.to_public_dict(stale=stale, stale_reason=reason))
    return out


def resolve_anchor(
    db: Session,
    *,
    ctx: AccessContext,
    anchor_id: str,
) -> dict[str, Any]:
    row = db.execute(select(AssetAnchorRow).where(AssetAnchorRow.anchor_id == anchor_id)).scalar_one_or_none()
    if row is None:
        raise ParseNotFound("anchor 不存在")
    if row.tenant_id != ctx.tenant_id:
        raise TenantMismatch("跨 tenant anchor 禁止解析")
    asset = get_asset(db, row.asset_id)
    if asset is None:
        raise ParseNotFound("anchor 关联资产不存在")
    authorize_asset(db, ctx, asset, capability=READ_CAPABILITY)
    stale, reason = classify_stale(
        anchor_asset_version=row.asset_version,
        anchor_content_hash=row.content_hash,
        current_asset_version=asset.version,
        current_content_hash=asset.content_hash,
        asset_revoked=asset.revoked_at is not None,
    )
    public = row.to_public_dict(stale=stale, stale_reason=reason)
    public["current_asset_version"] = str(asset.version)
    public["current_asset_hash"] = asset.content_hash
    return public


def parse_run_public(run: AssetParseRunRow) -> dict[str, Any]:
    """对外投影：PARTIAL **不得**显示为「解析完成」。"""
    return {
        "id": str(run.id),
        "asset_id": str(run.asset_id),
        "asset_version": str(run.asset_version),
        "asset_hash": run.asset_content_hash,
        "status": run.status,
        "is_complete": run.status == PARSE_SUCCEEDED,
        "is_partial": run.status == PARSE_PARTIAL,
        "parser": {
            "name": run.parser_name,
            "version": run.parser_version,
            "digest": run.parser_digest,
            "license": run.parser_license,
            "config_hash": run.parser_config_hash,
            "config": run.parser_config,
            "isolation": run.isolation,
        },
        "input_hash": run.input_hash,
        "output_hash": run.output_hash,
        "page_count": run.page_count,
        "succeeded_pages": list(run.succeeded_pages or []),
        "failed_pages": list(run.failed_pages or []),
        "missing_kinds": list(run.missing_kinds or []),
        "warnings": list(run.warnings or []),
        "section_tree": list(run.section_tree or []),
        "error": {"code": run.error_code, "message": run.error_message} if run.error_code else None,
        "job_id": str(run.job_id) if run.job_id else None,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
