"""RV05 · document.parse 作业 handler（RV03 JobContract）。

- 下载资产字节到临时文件（经 storage，不经浏览器）；
- 隔离子进程跑 Docling/pypdf；
- PARTIAL 列出成功页/失败页/缺失元素；
- 取消/超时/crash 走标准 job 状态。
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path

from .runtime import HandlerContext, HandlerOutcome


async def document_parse_handler(ctx: HandlerContext) -> HandlerOutcome:
    payload = ctx.input_payload or {}
    try:
        asset_id = uuid.UUID(str(payload.get("asset_id") or ""))
        tenant_id = uuid.UUID(str(payload.get("tenant_id") or ctx.tenant_id))
        actor_id = uuid.UUID(str(payload.get("actor_id") or ""))
        project_id = uuid.UUID(str(payload.get("project_id") or ""))
    except ValueError as e:
        return HandlerOutcome(
            error_code="INVALID_INPUT",
            error_message=f"document.parse 参数非法: {e}",
            failed_stages=["parse_input"],
        )

    if ctx.stopped():
        return HandlerOutcome(
            error_code="CANCELLED",
            error_message="取消请求在安全点生效",
            failed_stages=["cancelled"],
        )

    from ..api.deps import AccessContext
    from ..assets.service import get_asset
    from ..db.engine import session_scope
    from ..parser.provider import ParserProviderError
    from ..parser.service import parse_run_public, run_parse_on_asset

    access = AccessContext(
        actor_id=actor_id,
        tenant_id=tenant_id,
        workspace="research",
        domain="research-vnext",
        capabilities=["workspace.research.enter", "research.session.write"],
        resource_scopes=list(payload.get("resource_scopes") or []),
    )

    await ctx.report(stage="load_asset", progress=5)
    with session_scope() as db:
        asset = get_asset(db, asset_id)
        if asset is None:
            return HandlerOutcome(
                error_code="ASSET_NOT_FOUND",
                error_message="资产不存在",
                failed_stages=["load_asset"],
            )
        storage_key = asset.storage_key
        content_hash = asset.content_hash

    from ..assets.storage import MinioObjectStorage, StorageConfig

    tmp = Path(tempfile.mkstemp(suffix=".pdf", prefix="rv05-asset-")[1])
    try:
        st = MinioObjectStorage(StorageConfig.from_env())
        tmp.write_bytes(st.get_bytes(storage_key))
        await ctx.heartbeat()
        await ctx.report(stage="isolated_parse", progress=20)
        with session_scope() as db:
            try:
                run, anchors = run_parse_on_asset(
                    db,
                    ctx=access,
                    asset_id=asset_id,
                    pdf_path=tmp,
                    job_id=ctx.job_id,
                )
            except ParserProviderError as e:
                return HandlerOutcome(
                    error_code=e.code,
                    error_message=e.message,
                    failed_stages=["isolated_parse"],
                    result={"detail": e.detail, "retryable": e.retryable},
                )
            view = parse_run_public(run)
        await ctx.report(stage="persist_anchors", progress=90)
    finally:
        tmp.unlink(missing_ok=True)

    if ctx.stopped():
        return HandlerOutcome(
            succeeded_stages=["load_asset", "isolated_parse"],
            failed_stages=["cancelled"],
            error_code="CANCELLED",
            error_message="取消请求在安全点生效",
            result={"parse_run_id": view["id"]},
        )

    succeeded = ["load_asset", "isolated_parse", "persist_anchors"]
    failed: list[str] = []
    if view["is_partial"]:
        return HandlerOutcome(
            result={
                "parse_run": view,
                "anchor_count": len(anchors),
                "succeeded_pages": view["succeeded_pages"],
                "failed_pages": view["failed_pages"],
                "missing_kinds": view["missing_kinds"],
                "partial": True,
            },
            succeeded_stages=succeeded,
            failed_stages=["pages:" + ",".join(str(p) for p in view["failed_pages"])],
            error_code="PARSE_PARTIAL",
            error_message=f"部分页面解析失败: {view['failed_pages']}",
        )
    if view["status"] == "FAILED":
        return HandlerOutcome(
            error_code=(view.get("error") or {}).get("code") or "PARSER_CRASH",
            error_message=(view.get("error") or {}).get("message") or "解析失败",
            failed_stages=["isolated_parse"],
            result={"parse_run": view},
        )
    return HandlerOutcome(
        result={
            "parse_run": view,
            "anchor_count": len(anchors),
            "succeeded_pages": view["succeeded_pages"],
            "failed_pages": [],
            "missing_kinds": view["missing_kinds"],
            "partial": False,
        },
        succeeded_stages=succeeded,
    )


def register_parser_jobs() -> None:
    from .runtime import register

    register("document.parse", document_parse_handler)
