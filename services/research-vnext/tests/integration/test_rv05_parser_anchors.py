"""RV05 integration · Docling/pypdf 解析落库 + version-bound anchor + ACL/stale/PARTIAL。

**真实 PostgreSQL**（Alembic `0007_rv05_parser_anchors` schema，`it_schema` fixture 建好后跑）。
不起 MinIO：`run_parse_on_asset` 只读本地 PDF 路径，因此本文件聚焦
**DB/授权/版本绑定/PARTIAL 语义**；解析质量由 `evidence/docling-standard-acceptance.json` 承担。

覆盖任务书要求：
- 真实解析 → `asset_parse_run` + `asset_anchor` 落库并版本绑定
- asset hash 变化 / 撤权 → anchor stale，不静默重绑
- 跨 tenant anchor 403（服务层 + 真实 HTTP）
- 磁盘 hash ≠ asset hash → 拒绝解析
- PARTIAL 落库且投影不称「完成」
- 过期 signed delivery → 410
- document.parse handler：INVALID_INPUT / 安全点取消
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from research_vnext.core.api.deps import AccessContext
from research_vnext.core.assets.models import ResearchAssetRow
from research_vnext.core.assets import service as asset_svc
from research_vnext.core.db.engine import session_scope
from research_vnext.core.domain.errors import TenantMismatch
from research_vnext.core.parser import service as parse_svc
from research_vnext.core.parser.models import (
    PARSE_PARTIAL,
    PARSE_SUCCEEDED,
    AssetAnchorRow,
)
from research_vnext.core.parser.provider import ParseDocument, ParseElement, PageMeta

FIXTURE_PDF = Path(__file__).resolve().parents[1] / "fixtures" / "rv05-sample.pdf"

READ_CAP = "workspace.research.enter"
WRITE_CAP = "research.session.write"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _ctx_dict_to_access(c: dict) -> AccessContext:
    return AccessContext(
        actor_id=uuid.UUID(c["actor_id"]),
        tenant_id=uuid.UUID(c["tenant_id"]),
        workspace="research",
        domain="research-vnext",
        capabilities=list(c["capabilities"]),
        resource_scopes=list(c.get("resource_scopes") or []),
    )


def _insert_asset(
    db,
    ctx: AccessContext,
    *,
    content_hash: str,
    project_id: uuid.UUID,
    media_type: str = "application/pdf",
    filename: str = "rv05-sample.pdf",
    revoked: bool = False,
) -> ResearchAssetRow:
    """直接插资产行（真实 PG），绕过 MinIO —— 解析只依赖本地文件路径。"""
    row = ResearchAssetRow(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        project_id=project_id,
        owner_id=ctx.actor_id,
        version=1,
        content_hash=content_hash,
        media_type=media_type,
        claimed_media_type="application/pdf",
        size_bytes=FIXTURE_PDF.stat().st_size,
        original_filename=filename,
        storage_backend="minio",
        storage_key=f"rvs/{ctx.tenant_id}/{uuid.uuid4()}/v1",
        acl={"owner": str(ctx.actor_id), "readers": [], "public": False},
        lineage={"upload_source": "test"},
        rights={"status": "PUBLIC", "license": "test", "notes": ""},
        retention={"policy": "default", "expires_at": None},
        scan_status="CLEAN",
        scan_detail={},
        revoked_at=None,
    )
    if revoked:
        from datetime import datetime, timezone

        row.revoked_at = datetime.now(timezone.utc)
        row.revoked_reason = "test-revoke"
    db.add(row)
    db.flush()
    return row


def _fixture_hash() -> str:
    return hashlib.sha256(FIXTURE_PDF.read_bytes()).hexdigest()


@pytest.fixture()
def fixture_pdf() -> Path:
    assert FIXTURE_PDF.is_file(), f"缺夹具 PDF: {FIXTURE_PDF}"
    return FIXTURE_PDF


class _StubProvider:
    """确定性 provider：用于强制 PARTIAL / 失败页语义（不依赖真实解析器）。"""

    def __init__(self, *, failed_pages=(), missing_kinds=(), elements=None, warnings=()):
        self._failed = list(failed_pages)
        self._missing = list(missing_kinds)
        self._elements = elements
        self._warnings = list(warnings)

    def parse_detailed(self, pdf_path: Path):  # noqa: ANN201
        from research_vnext.core.parser.isolation import SubprocessParseResult
        from research_vnext.core.parser.provider import ParserIdentity

        els = self._elements
        if els is None:
            els = [
                ParseElement(kind="paragraph", page=1, bbox=(72, 700, 500, 720), text="ok page 1"),
                ParseElement(kind="section", page=1, bbox=(72, 730, 400, 750), text="1 Intro", level=0),
            ]
        doc = ParseDocument(
            pages=[PageMeta(page=1, width=612, height=792), PageMeta(page=2, width=612, height=792)],
            elements=els,
            warnings=self._warnings,
            succeeded_pages=[1],
            failed_pages=self._failed,
            missing_kinds=self._missing,
        )
        return SubprocessParseResult(
            document=doc,
            identity=ParserIdentity(
                name="stub-parser", version="0.0.1", digest="deadbeef", license="MIT", config={"stub": True}
            ),
            input_hash=hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            output_hash=hashlib.sha256(b"stub-output").hexdigest(),
            engine="stub-parser",
        )


# --------------------------------------------------------------------------
# 1. 真实解析落库 + 版本绑定
# --------------------------------------------------------------------------


def test_parse_persists_run_and_version_bound_anchors(app_env, ctx_factory, fixture_pdf):
    ctx = _ctx_dict_to_access(ctx_factory())
    h = _fixture_hash()
    with session_scope() as db:
        asset = _insert_asset(db, ctx, content_hash=h, project_id=uuid.uuid4())
        run, anchors = parse_svc.run_parse_on_asset(
            db,
            ctx=ctx,
            asset_id=asset.id,
            pdf_path=fixture_pdf,
            provider=__import__(
                "research_vnext.core.parser.isolation", fromlist=["SubprocessParserProvider"]
            ).SubprocessParserProvider(engine="pypdf"),
        )
        run_id, asset_id = run.id, asset.id
        assert run.status == PARSE_SUCCEEDED
        assert run.input_hash == h
        assert run.output_hash
        assert run.parser_name  # 解析器身份已记录
        assert run.isolation == "subprocess"
        assert anchors, "应产出 anchors"
        for a in anchors:
            assert a.asset_id == asset_id
            assert a.asset_version == asset.version == 1
            assert a.content_hash == h
            assert a.parse_run_id == run_id
            assert a.page >= 1
            assert a.text_hash
            assert a.region_hash
            assert a.page_width and a.page_height
            assert a.parser_version  # anchor 亦带解析器版本

    view = None
    with session_scope() as db:
        run = parse_svc.get_parse_run(db, run_id)
        view = parse_svc.parse_run_public(run)
    assert view["is_complete"] is True
    assert view["is_partial"] is False
    assert view["parser"]["isolation"] == "subprocess"


def test_every_anchor_kind_is_persisted_and_filterable(app_env, ctx_factory, fixture_pdf):
    ctx = _ctx_dict_to_access(ctx_factory())
    h = _fixture_hash()
    els = [
        ParseElement(kind="section", page=1, bbox=(60, 700, 400, 720), text="1 Intro", level=0),
        ParseElement(kind="paragraph", page=1, bbox=(60, 660, 500, 680), text="body text"),
        ParseElement(kind="table", page=1, bbox=(60, 500, 400, 620), text="A,B\n1,2"),
        ParseElement(kind="figure", page=1, bbox=(60, 400, 400, 480), text=""),
        ParseElement(kind="formula", page=1, bbox=(60, 360, 200, 380), text="E=mc^2"),
    ]
    with session_scope() as db:
        asset = _insert_asset(db, ctx, content_hash=h, project_id=uuid.uuid4())
        run, anchors = parse_svc.run_parse_on_asset(
            db, ctx=ctx, asset_id=asset.id, pdf_path=fixture_pdf, provider=_StubProvider(elements=els)
        )
        assert {a.type for a in anchors} == {"section", "paragraph", "table", "figure", "formula"}
        asset_id, run_id = asset.id, run.id

    with session_scope() as db:
        only_tables = parse_svc.list_anchors(db, ctx=ctx, asset_id=asset_id, anchor_type="table")
        assert len(only_tables) == 1
        assert only_tables[0]["type"] == "table"
        assert only_tables[0]["stale"] is False

        scoped = parse_svc.list_anchors(db, ctx=ctx, asset_id=asset_id, parse_run_id=run_id)
        assert len(scoped) == 5

        resolved = parse_svc.resolve_anchor(db, ctx=ctx, anchor_id=scoped[0]["id"])
        assert resolved["asset_version"] == "1"
        assert resolved["current_asset_version"] == "1"
        assert resolved["stale"] is False


# --------------------------------------------------------------------------
# 2. stale：hash 变化 / 版本变化 / 撤权
# --------------------------------------------------------------------------


def test_anchor_stale_after_content_hash_change(app_env, ctx_factory, fixture_pdf):
    """文件被替换（hash 变）→ 旧 anchor 必须 stale，不静默指向新内容。"""
    ctx = _ctx_dict_to_access(ctx_factory())
    old_hash = _fixture_hash()
    new_hash = hashlib.sha256(b"replaced-content").hexdigest()

    with session_scope() as db:
        asset = _insert_asset(db, ctx, content_hash=old_hash, project_id=uuid.uuid4())
        run, anchors = parse_svc.run_parse_on_asset(
            db, ctx=ctx, asset_id=asset.id, pdf_path=fixture_pdf, provider=_StubProvider()
        )
        asset_id = asset.id
        anchor_id = anchors[0].anchor_id

    # 模拟「换了文件」：同 asset_id 但 content_hash 变了
    with session_scope() as db:
        row = db.get(ResearchAssetRow, asset_id)
        row.content_hash = new_hash
        row.version = 2

    with session_scope() as db:
        listed = parse_svc.list_anchors(db, ctx=ctx, asset_id=asset_id)
        assert listed, "默认应包含 stale anchor（便于前端提示）"
        assert all(a["stale"] is True for a in listed)
        reasons = {a["stale_reason"] for a in listed}
        assert reasons == {"asset_hash_changed"}
        assert all(a["asset_available"] is False for a in listed)

        # 不含 stale 时为空
        fresh_only = parse_svc.list_anchors(db, ctx=ctx, asset_id=asset_id, include_stale=False)
        assert fresh_only == []

        resolved = parse_svc.resolve_anchor(db, ctx=ctx, anchor_id=anchor_id)
        assert resolved["stale"] is True
        assert resolved["stale_reason"] == "asset_hash_changed"
        assert resolved["asset_hash"] == old_hash
        assert resolved["current_asset_hash"] == new_hash


def test_anchor_stale_when_asset_revoked(app_env, ctx_factory, fixture_pdf):
    """解析在**可用**状态下完成；随后撤权 → 旧 anchor 全部 stale。"""
    ctx = _ctx_dict_to_access(ctx_factory())
    h = _fixture_hash()
    with session_scope() as db:
        asset = _insert_asset(db, ctx, content_hash=h, project_id=uuid.uuid4())
        run, anchors = parse_svc.run_parse_on_asset(
            db, ctx=ctx, asset_id=asset.id, pdf_path=fixture_pdf, provider=_StubProvider()
        )
        assert anchors
        asset_id = asset.id

    with session_scope() as db:
        asset_svc.revoke_asset(db, ctx, asset_id, reason="rv05-it-revoke")

    with session_scope() as db:
        listed = parse_svc.list_anchors(db, ctx=ctx, asset_id=asset_id)
        assert listed
        assert all(a["stale"] is True for a in listed)
        assert {a["stale_reason"] for a in listed} == {"asset_revoked"}
        assert all(a["asset_available"] is False for a in listed)


def test_revoked_asset_cannot_be_parsed_again(app_env, ctx_factory, fixture_pdf):
    """撤权后不得再解析（防止绕开撤权重新产出 anchor）。"""
    ctx = _ctx_dict_to_access(ctx_factory())
    h = _fixture_hash()
    with session_scope() as db:
        asset = _insert_asset(db, ctx, content_hash=h, project_id=uuid.uuid4(), revoked=True)
        with pytest.raises(parse_svc.ParseAssetNotReady) as ei:
            parse_svc.run_parse_on_asset(
                db, ctx=ctx, asset_id=asset.id, pdf_path=fixture_pdf, provider=_StubProvider()
            )
        assert ei.value.http_status == 409


def test_disk_hash_mismatch_is_rejected(app_env, ctx_factory, fixture_pdf, tmp_path):
    """asset 记录的 hash 与磁盘文件不一致 → 拒绝，防「用错文件重绑 anchor」。"""
    ctx = _ctx_dict_to_access(ctx_factory())
    with session_scope() as db:
        asset = _insert_asset(db, ctx, content_hash="0" * 64, project_id=uuid.uuid4())
        with pytest.raises(parse_svc.ParseAssetNotReady) as ei:
            parse_svc.run_parse_on_asset(
                db, ctx=ctx, asset_id=asset.id, pdf_path=fixture_pdf, provider=_StubProvider()
            )
        assert ei.value.http_status == 409


# --------------------------------------------------------------------------
# 3. 跨 tenant 403
# --------------------------------------------------------------------------


def test_cross_tenant_anchor_listing_and_resolve_403(app_env, ctx_factory, fixture_pdf):
    owner = _ctx_dict_to_access(ctx_factory())
    other = _ctx_dict_to_access(ctx_factory())
    assert owner.tenant_id != other.tenant_id
    h = _fixture_hash()

    with session_scope() as db:
        asset = _insert_asset(db, owner, content_hash=h, project_id=uuid.uuid4())
        run, anchors = parse_svc.run_parse_on_asset(
            db, ctx=owner, asset_id=asset.id, pdf_path=fixture_pdf, provider=_StubProvider()
        )
        asset_id = asset.id
        anchor_id = anchors[0].anchor_id

    with session_scope() as db, pytest.raises(TenantMismatch) as ei:
        parse_svc.list_anchors(db, ctx=other, asset_id=asset_id)
    assert ei.value.http_status == 403
    assert ei.value.code == "TENANT_MISMATCH"

    with session_scope() as db, pytest.raises(TenantMismatch) as ei2:
        parse_svc.resolve_anchor(db, ctx=other, anchor_id=anchor_id)
    assert ei2.value.http_status == 403


def test_anchor_http_endpoints_return_403_across_tenant(app_env, client, ctx_factory, fixture_pdf):
    """真实 HTTP：跨 tenant 读 anchors / resolve anchor 都是 403。"""
    from tests.conftest import auth_headers

    owner_dict = ctx_factory()
    other_dict = ctx_factory()
    owner = _ctx_dict_to_access(owner_dict)
    h = _fixture_hash()

    with session_scope() as db:
        asset = _insert_asset(db, owner, content_hash=h, project_id=uuid.uuid4())
        run, anchors = parse_svc.run_parse_on_asset(
            db, ctx=owner, asset_id=asset.id, pdf_path=fixture_pdf, provider=_StubProvider()
        )
        asset_id = asset.id
        anchor_id = anchors[0].anchor_id

    r1 = client.get(f"/api/research-vnext/assets/{asset_id}/anchors", headers=auth_headers(other_dict))
    assert r1.status_code == 403, r1.text
    assert r1.json()["error"]["code"] == "TENANT_MISMATCH"

    r2 = client.get(f"/api/research-vnext/anchors/{anchor_id}", headers=auth_headers(other_dict))
    assert r2.status_code == 403, r2.text

    # owner 自己读得到，并且投影带 stale 字段
    ok = client.get(f"/api/research-vnext/assets/{asset_id}/anchors", headers=auth_headers(owner_dict))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["count"] >= 1
    assert "stale_count" in body
    assert body["anchors"][0]["stale"] is False


def test_anchor_http_requires_access_context(app_env, client, ctx_factory, fixture_pdf):
    """没有网关注入的 AccessContext → 拒绝（不能凭 JWT/匿名放行）。"""
    owner = _ctx_dict_to_access(ctx_factory())
    h = _fixture_hash()
    with session_scope() as db:
        asset = _insert_asset(db, owner, content_hash=h, project_id=uuid.uuid4())
        asset_id = asset.id
    r = client.get(f"/api/research-vnext/assets/{asset_id}/anchors")
    assert r.status_code in (401, 403), r.text


# --------------------------------------------------------------------------
# 4. PARTIAL 不称完成
# --------------------------------------------------------------------------


def test_partial_run_lands_partial_and_never_claims_complete(app_env, ctx_factory, fixture_pdf):
    ctx = _ctx_dict_to_access(ctx_factory())
    h = _fixture_hash()
    stub = _StubProvider(
        failed_pages=[2],
        missing_kinds=["table", "formula"],
        warnings=["page 2 failed: parse error"],
    )
    with session_scope() as db:
        asset = _insert_asset(db, ctx, content_hash=h, project_id=uuid.uuid4())
        run, anchors = parse_svc.run_parse_on_asset(
            db, ctx=ctx, asset_id=asset.id, pdf_path=fixture_pdf, provider=stub
        )
        assert run.status == PARSE_PARTIAL
        assert run.is_partial is True
        assert run.is_complete_success is False
        assert run.failed_pages == [2]
        assert run.succeeded_pages == [1]
        assert set(run.missing_kinds) >= {"table", "formula"}
        view = parse_svc.parse_run_public(run)
        run_id = run.id

    assert view["status"] == "PARTIAL"
    assert view["is_complete"] is False, "PARTIAL 绝不能投影成完成"
    assert view["is_partial"] is True
    assert view["failed_pages"] == [2]
    assert view["succeeded_pages"] == [1]
    assert view["missing_kinds"]
    assert view["warnings"]

    with session_scope() as db:
        again = parse_svc.parse_run_public(parse_svc.get_parse_run(db, run_id))
    assert again["is_complete"] is False


def test_run_endpoint_reports_partial_for_owner_but_403_for_other_tenant(app_env, client, ctx_factory, fixture_pdf):
    from tests.conftest import auth_headers

    owner_dict = ctx_factory()
    other_dict = ctx_factory()
    ctx = _ctx_dict_to_access(owner_dict)
    h = _fixture_hash()
    with session_scope() as db:
        asset = _insert_asset(db, ctx, content_hash=h, project_id=uuid.uuid4())
        run, _ = parse_svc.run_parse_on_asset(
            db, ctx=ctx, asset_id=asset.id, pdf_path=fixture_pdf, provider=_StubProvider(failed_pages=[2])
        )
        run_id = run.id

    own = client.get(f"/api/research-vnext/parse-runs/{run_id}", headers=auth_headers(owner_dict))
    assert own.status_code == 200, own.text
    assert own.json()["parse_run"]["is_complete"] is False

    other = client.get(f"/api/research-vnext/parse-runs/{run_id}", headers=auth_headers(other_dict))
    assert other.status_code == 403, other.text


# --------------------------------------------------------------------------
# 5. 过期 signed delivery → 410
# --------------------------------------------------------------------------


def test_expired_signed_delivery_is_410(app_env, ctx_factory, monkeypatch):
    monkeypatch.setenv("RESEARCH_ASSET_SIGNING_SECRET", "rv05-it-sign")
    monkeypatch.setenv("RESEARCH_GATEWAY_HMAC_SECRET", "rv05-it-sign")
    from research_vnext.core.assets.signing import mint_token
    from research_vnext.core.assets.service import AssetGone

    ctx = _ctx_dict_to_access(ctx_factory())
    h = _fixture_hash()
    with session_scope() as db:
        asset = _insert_asset(db, ctx, content_hash=h, project_id=uuid.uuid4())
        token = mint_token(asset_id=asset.id, version=asset.version, ttl_seconds=-30).to_token()

    with session_scope() as db, pytest.raises(AssetGone) as ei:
        asset_svc.deliver_via_token(db, token=token)
    assert ei.value.http_status == 410
    assert ei.value.code == "ASSET_GONE"


# --------------------------------------------------------------------------
# 6. document.parse handler：INVALID_INPUT / 安全点取消（不触存储）
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_document_parse_handler_rejects_invalid_input(app_env):
    from research_vnext.core.worker.parser_handlers import document_parse_handler
    from research_vnext.core.worker.runtime import HandlerContext

    ctx = HandlerContext(
        job_id=uuid.uuid4(),
        fencing_token=0,
        attempt=1,
        session_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        input_payload={"asset_id": "not-a-uuid"},
        job_type="document.parse",
    )
    out = await document_parse_handler(ctx)
    assert out.error_code == "INVALID_INPUT"
    assert out.failed_stages == ["parse_input"]


@pytest.mark.asyncio
async def test_document_parse_handler_cancels_at_safe_point_before_storage(app_env, ctx_factory):
    """已领走 + 请求取消 → handler 在安全点返回 CANCELLED，且**未**去下载对象。"""
    from research_vnext.core.application import job_service as js
    from research_vnext.core.worker.parser_handlers import document_parse_handler
    from research_vnext.core.worker.runtime import HandlerContext

    c = ctx_factory()
    with session_scope() as db:
        job = js.enqueue(
            db,
            session_id=uuid.uuid4(),
            tenant_id=uuid.UUID(c["tenant_id"]),
            job_type="document.parse",
            input_payload={"asset_id": str(uuid.uuid4())},
            user_id=uuid.UUID(c["actor_id"]),
            project_id=uuid.uuid4(),
        )
        job_id = job.id

    with session_scope() as db:
        claimed = js.claim_job(db, job_id=job_id, worker="rv05-it-worker")
        fencing = claimed.fencing_token

    with session_scope() as db:
        js.request_cancel(db, job_id=job_id, reason="it-cancel")

    hctx = HandlerContext(
        job_id=job_id,
        fencing_token=fencing,
        attempt=1,
        session_id=uuid.uuid4(),
        tenant_id=uuid.UUID(c["tenant_id"]),
        input_payload={
            "asset_id": str(uuid.uuid4()),
            "actor_id": c["actor_id"],
            "project_id": str(uuid.uuid4()),
        },
        job_type="document.parse",
    )
    out = await document_parse_handler(hctx)
    assert out.error_code == "CANCELLED"
    assert "cancelled" in out.failed_stages


def test_anchor_rows_carry_region_and_text_hash_for_source_trace(app_env, ctx_factory, fixture_pdf):
    """anchor 必须能回源：page + bbox + text_hash + region_hash 齐备且稳定。

    同一资产**重复解析**（真实场景：重跑解析）→ 回源指纹必须一致。
    """
    ctx = _ctx_dict_to_access(ctx_factory())
    h = _fixture_hash()
    with session_scope() as db:
        asset = _insert_asset(db, ctx, content_hash=h, project_id=uuid.uuid4())
        asset_id = asset.id

    def _parse_once():
        with session_scope() as db:
            run, anchors = parse_svc.run_parse_on_asset(
                db, ctx=ctx, asset_id=asset_id, pdf_path=fixture_pdf, provider=_StubProvider()
            )
            first = sorted(anchors, key=lambda a: (a.page, a.type, a.text_hash))[0]
            assert first.bbox and first.page_width and first.page_height
            return (first.page, tuple(first.bbox), first.text_hash, first.region_hash)

    a = _parse_once()
    b = _parse_once()
    assert a == b, "同一内容两次解析 → page/bbox/text_hash/region_hash 必须一致"
    assert all(isinstance(v, (int, float)) for v in a[1])
