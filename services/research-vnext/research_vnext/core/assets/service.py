"""RV04 · 资产应用服务：上传 / 去重 / 对象级授权 / Range / 签名交付 / 撤权。"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..api.deps import (
    AccessContext,
    READ_CAPABILITY,
    WRITE_CAPABILITY,
    check_revocation,
    require_capability,
)
from ..domain.errors import (
    CapabilityMissing,
    DomainError,
    ObjectScopeMissing,
    ProjectMismatch,
    TenantMismatch,
)
from ..observability.trace import TraceContext
from .models import (
    RIGHTS_UNKNOWN,
    AssetSignedUrlRow,
    ResearchAssetRow,
)
from .security import AssetRejected, DEFAULT_MAX_BYTES, scan_upload
from .signing import (
    SignedUrlExpired,
    SignedUrlInvalid,
    default_ttl_seconds,
    mint_token,
    parse_and_verify,
)
from .storage import MinioObjectStorage, StorageConfig


class AssetNotFound(DomainError):
    code = "ASSET_NOT_FOUND"
    http_status = 404


class AssetGone(DomainError):
    code = "ASSET_GONE"
    http_status = 410


class AssetConflict(DomainError):
    code = "ASSET_CONFLICT"
    http_status = 409


class AssetRejectedError(DomainError):
    code = "ASSET_REJECTED"
    http_status = 400

    def __init__(self, message: str = "", *, reject_code: str | None = None, **detail) -> None:
        super().__init__(message, **detail)
        if reject_code:
            self.code = reject_code
            self.detail = {**self.detail, "reject_code": reject_code}


class RangeNotSatisfiable(DomainError):
    code = "RANGE_NOT_SATISFIABLE"
    http_status = 416


class SignedUrlGone(DomainError):
    code = "SIGNED_URL_REVOKED"
    http_status = 410


def max_upload_bytes() -> int:
    return int(os.getenv("RESEARCH_ASSET_MAX_BYTES", str(DEFAULT_MAX_BYTES)))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _storage() -> MinioObjectStorage:
    return MinioObjectStorage(StorageConfig.from_env())


def authorize_asset(
    db: Session,
    ctx: AccessContext,
    asset: ResearchAssetRow,
    *,
    capability: str = READ_CAPABILITY,
) -> ResearchAssetRow:
    """对象级授权：workspace → 撤权 floor → tenant → project scope → owner/ACL → capability。"""
    if ctx.workspace != "research":
        raise CapabilityMissing(f"workspace={ctx.workspace!r} 无权访问 research 域")
    # 与 authorize_session/authorize_create 对齐：长会话撤权后不得继续读写
    check_revocation(db, ctx)
    if asset.tenant_id != ctx.tenant_id:
        raise TenantMismatch(
            "资产不属于当前 tenant",
            asset_tenant=str(asset.tenant_id),
            actor_tenant=str(ctx.tenant_id),
        )
    if ctx.project_scope_ids and str(asset.project_id) not in ctx.project_scope_ids:
        raise ProjectMismatch("scope 中的 project 与资产 project 不一致")

    is_owner = asset.owner_id == ctx.actor_id
    readers = set((asset.acl or {}).get("readers") or [])
    if capability == READ_CAPABILITY and not is_owner and str(ctx.actor_id) not in readers:
        if not ctx.project_scope_ids or str(asset.project_id) not in ctx.project_scope_ids:
            raise ObjectScopeMissing("缺少对象级 scope/ACL（禁止仅凭 identity 放行）")
        raise ObjectScopeMissing("ACL 未授予读取权")

    if capability == WRITE_CAPABILITY and not is_owner:
        raise ObjectScopeMissing("仅 owner 可写/撤权")

    require_capability(ctx, capability)
    return asset


def find_by_hash(db: Session, tenant_id: uuid.UUID, content_hash: str) -> ResearchAssetRow | None:
    return db.execute(
        select(ResearchAssetRow).where(
            ResearchAssetRow.tenant_id == tenant_id,
            ResearchAssetRow.content_hash == content_hash,
        )
    ).scalar_one_or_none()


def upload_asset(
    db: Session,
    ctx: AccessContext,
    *,
    project_id: uuid.UUID,
    data_path: Path,
    filename: str = "",
    claimed_media_type: str = "",
    session_id: uuid.UUID | None = None,
    rights: dict | None = None,
    retention: dict | None = None,
    client_request_id: str = "",
    trace: TraceContext | None = None,
    storage: MinioObjectStorage | None = None,
) -> tuple[ResearchAssetRow, bool]:
    """返回 (asset, deduplicated)。同 tenant 同 hash → 返回既有，不复制字节。"""
    check_revocation(db, ctx)
    require_capability(ctx, WRITE_CAPABILITY)
    try:
        scan = scan_upload(
            data_path,
            claimed_media_type=claimed_media_type,
            max_bytes=max_upload_bytes(),
        )
    except AssetRejected as e:
        raise AssetRejectedError(e.message, reject_code=e.code, **e.detail) from e

    if scan.scan_status == "REJECTED":
        raise AssetRejectedError("上传被扫描拒绝")

    existing = find_by_hash(db, ctx.tenant_id, scan.content_hash)
    if existing is not None:
        authorize_asset(db, ctx, existing, capability=READ_CAPABILITY)
        return existing, True

    asset_id = uuid.uuid4()
    st = storage or _storage()
    key = st.build_key(str(ctx.tenant_id), str(asset_id), 1)
    with data_path.open("rb") as f:
        st.put_stream(key, f, scan.size_bytes)

    rights_doc = rights or {"status": RIGHTS_UNKNOWN, "license": "", "notes": "未声明 rights → UNKNOWN"}
    if rights_doc.get("status") not in {"UNKNOWN", "PUBLIC", "RESTRICTED"}:
        rights_doc = {**rights_doc, "status": RIGHTS_UNKNOWN}

    row = ResearchAssetRow(
        id=asset_id,
        tenant_id=ctx.tenant_id,
        project_id=project_id,
        owner_id=ctx.actor_id,
        session_id=session_id,
        version=1,
        content_hash=scan.content_hash,
        media_type=scan.media_type,
        claimed_media_type=claimed_media_type or "",
        size_bytes=scan.size_bytes,
        original_filename=filename[:512],
        storage_backend="minio",
        storage_key=key,
        acl={"owner": str(ctx.actor_id), "readers": [], "public": False},
        lineage={
            "upload_source": "gateway",
            "parent_asset_id": None,
            "client_request_id": client_request_id,
        },
        rights=rights_doc,
        retention=retention or {"policy": "default", "expires_at": None},
        scan_status=scan.scan_status,
        scan_detail=scan.to_scan_detail(),
        client_request_id=client_request_id,
        trace_id=trace.trace_id if trace else "",
    )
    db.add(row)
    try:
        # savepoint：并发同 hash 上传会撞 uq_research_asset_tenant_hash
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raced = find_by_hash(db, ctx.tenant_id, scan.content_hash)
        if raced is not None:
            try:
                st.delete(key)
            except Exception:  # noqa: BLE001
                pass
            authorize_asset(db, ctx, raced, capability=READ_CAPABILITY)
            return raced, True
        raise
    return row, False


def get_asset(db: Session, asset_id: uuid.UUID) -> ResearchAssetRow | None:
    return db.get(ResearchAssetRow, asset_id)


def revoke_asset(
    db: Session,
    ctx: AccessContext,
    asset_id: uuid.UUID,
    *,
    reason: str = "",
) -> ResearchAssetRow:
    asset = get_asset(db, asset_id)
    if asset is None:
        raise AssetNotFound(f"资产不存在: {asset_id}")
    authorize_asset(db, ctx, asset, capability=WRITE_CAPABILITY)
    asset.revoked_at = _utcnow()
    asset.revoked_reason = (reason or "revoked")[:512]
    for row in db.execute(
        select(AssetSignedUrlRow).where(
            AssetSignedUrlRow.asset_id == asset.id,
            AssetSignedUrlRow.revoked_at.is_(None),
        )
    ).scalars():
        row.revoked_at = asset.revoked_at
    db.flush()
    return asset


def issue_signed_token(
    db: Session,
    ctx: AccessContext,
    asset_id: uuid.UUID,
    *,
    ttl_seconds: int | None = None,
) -> tuple[str, ResearchAssetRow]:
    asset = get_asset(db, asset_id)
    if asset is None:
        raise AssetNotFound(f"资产不存在: {asset_id}")
    authorize_asset(db, ctx, asset, capability=READ_CAPABILITY)
    if asset.revoked_at is not None:
        raise AssetGone("资产已撤权")
    if asset.scan_status != "CLEAN":
        raise AssetGone("资产未通过扫描，不可交付")

    tok = mint_token(asset_id=asset.id, version=asset.version, ttl_seconds=ttl_seconds)
    db.add(
        AssetSignedUrlRow(
            id=uuid.uuid4(),
            asset_id=asset.id,
            tenant_id=asset.tenant_id,
            version=asset.version,
            jti=tok.jti,
            expires_at=datetime.fromtimestamp(tok.exp, tz=timezone.utc),
            created_by=ctx.actor_id,
        )
    )
    db.flush()
    return f"/api/research-vnext/assets/{asset.id}/content?token={tok.to_token()}", asset


def deliver_via_token(
    db: Session,
    *,
    token: str,
    range_header: str | None = None,
    storage: MinioObjectStorage | None = None,
) -> tuple[ResearchAssetRow, bytes, dict]:
    try:
        tok = parse_and_verify(token)
    except SignedUrlExpired as e:
        raise AssetGone(str(e)) from e
    except SignedUrlInvalid as e:
        raise DomainError(str(e)) from e

    try:
        asset_id = uuid.UUID(tok.asset_id)
    except ValueError as e:
        raise DomainError("asset_id 非法") from e

    asset = get_asset(db, asset_id)
    if asset is None:
        raise AssetNotFound("资产不存在")
    if asset.revoked_at is not None:
        raise SignedUrlGone("资产已撤权")
    if asset.scan_status != "CLEAN":
        raise AssetGone("资产未通过扫描")

    jti_row = db.execute(
        select(AssetSignedUrlRow).where(AssetSignedUrlRow.jti == tok.jti)
    ).scalar_one_or_none()
    if jti_row is not None and jti_row.revoked_at is not None:
        raise SignedUrlGone("签名已撤权")

    if tok.version != asset.version:
        raise AssetConflict("版本不匹配")

    return _stream_body(asset, storage=storage, range_header=range_header)


def deliver_direct(
    db: Session,
    ctx: AccessContext,
    asset_id: uuid.UUID,
    *,
    range_header: str | None = None,
    storage: MinioObjectStorage | None = None,
) -> tuple[ResearchAssetRow, bytes, dict]:
    asset = get_asset(db, asset_id)
    if asset is None:
        raise AssetNotFound(f"资产不存在: {asset_id}")
    authorize_asset(db, ctx, asset, capability=READ_CAPABILITY)
    if asset.revoked_at is not None:
        raise AssetGone("资产已撤权")
    if asset.scan_status != "CLEAN":
        raise AssetGone("资产未通过扫描")
    return _stream_body(asset, storage=storage, range_header=range_header)


def _stream_body(
    asset: ResearchAssetRow,
    *,
    storage: MinioObjectStorage | None,
    range_header: str | None,
) -> tuple[ResearchAssetRow, bytes, dict]:
    st = storage or _storage()
    size = asset.size_bytes
    headers = {
        "Content-Type": asset.media_type,
        "Accept-Ranges": "bytes",
        "X-Asset-Id": str(asset.id),
        "X-Content-Type-Options": "nosniff",
        "X-Delivery": "gateway-only",
    }
    if not range_header:
        body = st.get_bytes(asset.storage_key)
        headers["Content-Length"] = str(len(body))
        return asset, body, headers
    start, end = _parse_range(range_header, size)
    if start is None or end is None:
        raise RangeNotSatisfiable("Range 无法满足")
    body = st.get_range(asset.storage_key, start, end)
    headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    headers["Content-Length"] = str(len(body))
    return asset, body, headers


def _parse_range(header: str, size: int) -> tuple[int | None, int | None]:
    h = (header or "").strip()
    if not h.startswith("bytes="):
        return None, None
    spec = h[6:].split(",")[0].strip()
    if "-" not in spec:
        return None, None
    a, b = spec.split("-", 1)
    try:
        if a == "":
            n = int(b)
            if n <= 0 or size == 0:
                return None, None
            return max(0, size - n), size - 1
        start = int(a)
        end = int(b) if b else size - 1
    except ValueError:
        return None, None
    if start < 0 or start >= size or end < start:
        return None, None
    return start, min(end, size - 1)


def asset_public_dict(row: ResearchAssetRow) -> dict:
    return {
        "id": str(row.id),
        "tenant_id": str(row.tenant_id),
        "project_id": str(row.project_id),
        "owner_id": str(row.owner_id),
        "session_id": str(row.session_id) if row.session_id else None,
        "version": row.version,
        "content_hash": row.content_hash,
        "media_type": row.media_type,
        "claimed_media_type": row.claimed_media_type,
        "size_bytes": row.size_bytes,
        "original_filename": row.original_filename,
        "acl": row.acl,
        "lineage": row.lineage,
        "rights": {**(row.rights or {}), "status": row.rights_status},
        "retention": row.retention,
        "scan_status": row.scan_status,
        "revoked": row.revoked_at is not None,
        "revoked_reason": row.revoked_reason if row.revoked_at else "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "delivery": "gateway-only",
    }
