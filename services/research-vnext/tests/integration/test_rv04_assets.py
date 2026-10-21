"""RV04 integration · asset upload / hash dedup / ACL / Range / signed / revoke。

真实 PostgreSQL（Alembic schema）+ 真实 MinIO（teacher-minio:9002）。
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from research_vnext.core.api.deps import AccessContext
from research_vnext.core.assets import service as asset_svc
from research_vnext.core.db.engine import session_scope
from research_vnext.core.domain.errors import TenantMismatch


def _pdf_bytes(payload: bytes = b"hello-rv04") -> bytes:
    return b"%PDF-1.4\n" + payload + b"\n%%EOF"


def _access_from_ctx_dict(c: dict) -> AccessContext:
    return AccessContext(
        actor_id=uuid.UUID(c["actor_id"]),
        tenant_id=uuid.UUID(c["tenant_id"]),
        workspace="research",
        domain="research-vnext",
        capabilities=list(c["capabilities"]),
        resource_scopes=list(c.get("resource_scopes") or []),
    )


def test_upload_hash_dedup_same_tenant(app_env, tmp_path: Path, ctx_factory):
    c = ctx_factory()
    ctx = _access_from_ctx_dict(c)
    project_id = uuid.uuid4()
    data = _pdf_bytes(b"same-content")
    p1 = tmp_path / "a.pdf"
    p2 = tmp_path / "b.pdf"
    p1.write_bytes(data)
    p2.write_bytes(data)

    with session_scope() as db:
        a1, d1 = asset_svc.upload_asset(
            db, ctx, project_id=project_id, data_path=p1, filename="a.pdf", claimed_media_type="application/pdf"
        )
        a2, d2 = asset_svc.upload_asset(
            db, ctx, project_id=project_id, data_path=p2, filename="b.pdf", claimed_media_type="application/pdf"
        )
        assert d1 is False
        assert d2 is True
        assert a1.id == a2.id
        assert a1.content_hash == a2.content_hash == hashlib.sha256(data).hexdigest()


def test_cross_tenant_403(app_env, tmp_path: Path, ctx_factory):
    c1 = ctx_factory()
    c2 = ctx_factory()
    ctx1 = _access_from_ctx_dict(c1)
    ctx2 = _access_from_ctx_dict(c2)
    assert ctx1.tenant_id != ctx2.tenant_id

    p = tmp_path / "x.pdf"
    p.write_bytes(_pdf_bytes(b"cross"))
    with session_scope() as db:
        asset, _ = asset_svc.upload_asset(
            db, ctx1, project_id=uuid.uuid4(), data_path=p, filename="x.pdf", claimed_media_type="application/pdf"
        )

    with session_scope() as db, pytest.raises(TenantMismatch) as ei:
        asset_svc.authorize_asset(db, ctx2, asset)
    assert ei.value.code == "TENANT_MISMATCH"
    assert ei.value.http_status == 403


def test_range_and_signed_and_revoke(app_env, tmp_path: Path, ctx_factory, monkeypatch):
    monkeypatch.setenv("RESEARCH_ASSET_SIGNING_SECRET", "it-asset-sign")
    monkeypatch.setenv("RESEARCH_GATEWAY_HMAC_SECRET", "it-asset-sign")
    c = ctx_factory()
    ctx = _access_from_ctx_dict(c)
    payload = b"%PDF-1.4\n" + b"0123456789" * 20 + b"\n%%EOF"
    p = tmp_path / "r.pdf"
    p.write_bytes(payload)

    with session_scope() as db:
        asset, _ = asset_svc.upload_asset(
            db, ctx, project_id=uuid.uuid4(), data_path=p, filename="r.pdf", claimed_media_type="application/pdf"
        )
        assert asset.scan_status == "CLEAN"

    with session_scope() as db:
        _row, body, headers = asset_svc.deliver_direct(db, ctx, asset.id, range_header="bytes=0-9")
        assert body == payload[0:10]
        assert headers["Content-Range"].startswith("bytes 0-9/")
        assert "9002" not in str(headers)
        assert headers.get("X-Delivery") == "gateway-only"

    with session_scope() as db:
        url, _r = asset_svc.issue_signed_token(db, ctx, asset.id, ttl_seconds=120)
        assert url.startswith("/api/research-vnext/assets/")
        assert "127.0.0.1" not in url
        assert "9002" not in url
        token = url.split("token=", 1)[1]
        _a, body2, _h = asset_svc.deliver_via_token(db, token=token)
        assert body2 == payload

    with session_scope() as db:
        asset_svc.revoke_asset(db, ctx, asset.id, reason="test-revoke")

    from research_vnext.core.assets.service import AssetGone, SignedUrlGone

    with session_scope() as db, pytest.raises((AssetGone, SignedUrlGone)):
        asset_svc.deliver_via_token(db, token=token)


def test_rights_missing_is_unknown(app_env, tmp_path: Path, ctx_factory):
    c = ctx_factory()
    ctx = _access_from_ctx_dict(c)
    p = tmp_path / "rights.pdf"
    p.write_bytes(_pdf_bytes(b"rights"))
    with session_scope() as db:
        asset, _ = asset_svc.upload_asset(
            db, ctx, project_id=uuid.uuid4(), data_path=p, filename="rights.pdf"
        )
        assert asset.rights_status == "UNKNOWN"
        pub = asset_svc.asset_public_dict(asset)
        assert pub["rights"]["status"] == "UNKNOWN"
        assert "storage_key" not in pub


def test_http_upload_via_gateway(app_env, client, ctx_factory):
    from tests.integration.conftest import auth_headers

    c = ctx_factory()
    headers = auth_headers(c)
    headers.pop("Content-Type", None)
    files = {"file": ("doc.pdf", _pdf_bytes(b"http"), "application/pdf")}
    data = {"project_id": str(uuid.uuid4())}
    r = client.post("/api/research-vnext/assets", headers=headers, files=files, data=data)
    assert r.status_code in {200, 201}, r.text
    body = r.json()
    assert body["asset"]["delivery"] == "gateway-only"
    assert "storage_key" not in body["asset"]
    assert body["asset"]["media_type"] == "application/pdf"
    # 重复上传同文件 → dedup
    r2 = client.post("/api/research-vnext/assets", headers=headers, files=files, data=data)
    assert r2.status_code == 200
    assert r2.json()["deduplicated"] is True
    assert r2.json()["asset"]["id"] == body["asset"]["id"]


def test_signed_url_expiry_via_http(app_env, client, ctx_factory):
    import time

    from tests.integration.conftest import auth_headers

    # 不改 RESEARCH_GATEWAY_HMAC_SECRET：auth_headers 与 app 共用 app_env 注入的密钥
    c = ctx_factory()
    headers = auth_headers(c)
    headers.pop("Content-Type", None)
    project_id = str(uuid.uuid4())
    files = {"file": ("e.pdf", _pdf_bytes(b"expire-me"), "application/pdf")}
    data = {"project_id": project_id}
    r = client.post("/api/research-vnext/assets", headers=headers, files=files, data=data)
    assert r.status_code in {200, 201}, r.text
    aid = r.json()["asset"]["id"]

    from research_vnext.core.assets.signing import mint_token

    tok = mint_token(asset_id=aid, version=1, ttl_seconds=1, now=int(time.time()) - 5)
    g = client.get(f"/api/research-vnext/assets/{aid}/content?token={tok.to_token()}")
    assert g.status_code == 410
    assert g.json()["error"]["code"] in {"ASSET_GONE", "SIGNED_URL_EXPIRED"}


def test_security_floor_blocks_asset_read(app_env, tmp_path: Path, ctx_factory):
    """撤权后：同 actor 的 asset 授权必须失败（对齐 authorize_session）。"""
    from research_vnext.core.domain.errors import SecurityVersionRevoked

    c = ctx_factory(security_version=1)
    ctx = _access_from_ctx_dict(c)
    p = tmp_path / "floor.pdf"
    p.write_bytes(_pdf_bytes(b"floor"))
    with session_scope() as db:
        asset, _ = asset_svc.upload_asset(
            db, ctx, project_id=uuid.uuid4(), data_path=p, filename="floor.pdf", claimed_media_type="application/pdf"
        )

    from research_vnext.core.api import deps

    deps.set_security_floor(ctx.actor_id, 2)
    try:
        with session_scope() as db, pytest.raises(SecurityVersionRevoked):
            asset_svc.authorize_asset(db, ctx, asset)
    finally:
        # 清 floor，避免污染其它用例
        from research_vnext.core.application import session_service as svc

        with session_scope() as db:
            svc.set_security_floor(db, ctx.actor_id, 1)


def test_concurrent_same_hash_integrity_fallback(app_env, tmp_path: Path, ctx_factory):
    """模拟并发撞唯一键：begin_nested + IntegrityError → 返回既有行。"""
    from sqlalchemy.exc import IntegrityError

    c = ctx_factory()
    ctx = _access_from_ctx_dict(c)
    project = uuid.uuid4()
    data = _pdf_bytes(b"race-me")
    p = tmp_path / "race.pdf"
    p.write_bytes(data)
    content_hash = hashlib.sha256(data).hexdigest()

    with session_scope() as db:
        first, _ = asset_svc.upload_asset(
            db, ctx, project_id=project, data_path=p, filename="race.pdf", claimed_media_type="application/pdf"
        )
        assert first.content_hash == content_hash

    # 直接插入同 hash 行会 IntegrityError；服务应兜底
    with session_scope() as db:
        second, deduped = asset_svc.upload_asset(
            db, ctx, project_id=project, data_path=p, filename="race2.pdf", claimed_media_type="application/pdf"
        )
        assert deduped is True
        assert second.id == first.id
