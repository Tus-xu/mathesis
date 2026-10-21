"""Step 注册表（8200 自持）。

- `ppt.deck.prepare`（R15 空流程，保持不变）；
- `ppt.deck.generate`（R21 真实生成）：
  1) 读取 8200 库内 approved TeachingDeckSpec（ingest 闸门已保证 approved/页预算/locator）；
  2) 逐页：取消检查 + 租约续期 → checkpoint 复用（deck_id+page_no+引擎版本+8201 artifact
     存在性）→ 未命中则调 8201 render_page（构建+渲染+QA）→ 页级 checkpoint commit；
  3) 装配：8201 assemble（幂等 deck_key）→ 拉回字节校验 sha256 → 8200 本地留存 +
     ppt_v2_artifacts 登记（唯一 object_key → 同请求无重复 artifact）；
  4) 预算：页数 8..12、单页超时、装配超时、最终产物大小上限（超限非重试失败）。

失败语义（如实，不伪造成功）：
- PresentonUnavailable（8201 不可达/503）→ transient（退避重试；页 checkpoint 保证只补缺失页）；
- EngineRejected / 页 QA 不合格 / 预算超限 → 非 transient（FAILED，页级诊断写入 checkpoint 与事件，
  已完成页保留 → 失败不丢整套）；
- DeckCancelled → CANCELLED（已完成页 checkpoint 保留）。

新增 job_type 必须先在此注册并将类型加入 app/contracts/job_protocols.ALLOWED_JOB_TYPES。
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.artifact_store import LocalArtifactStore
from app.config import Settings, get_settings
from app.contracts.deck import TeachingDeckSpec
from app.persistence.models import PptV2Artifact, PptV2DeckPage, PptV2DeckSpecRecord, PptV2Job
from app.reliability.presenton_client import EngineRejected, PresentonUnavailable
from app.visual_planning import VERSION as VPL_VERSION
from app.visual_planning import build_visual_plan
from app.visual_planning.qa_deck import plan_to_json, run_deck_qa


class StepFailure(Exception):
    def __init__(self, message: str, retriable: bool = True):
        self.message = message
        self.retriable = retriable
        super().__init__(message)


class DeckCancelled(Exception):
    """作业在页间被取消（worker → CANCELLED；checkpoint 保留）。"""


class PresentonProbe(Protocol):
    async def probe(self) -> None: ...


@dataclass
class StepContext:
    """step 依赖注入点：8201 客户端 + （generate 需要）DB/引擎/配置/作业上下文。"""

    presenton: Any
    session_factory: async_sessionmaker | None = None
    engine: Any = None
    settings: Settings | None = None
    session: AsyncSession | None = None
    job_id: str | None = None
    claim_token: str | None = None
    trace_id: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def bind(self, session: AsyncSession, job_id: str, claim_token: str, trace_id: str | None = None) -> StepContext:
        return replace(self, session=session, job_id=job_id, claim_token=claim_token, trace_id=trace_id)


async def _prepare(payload: dict[str, Any], ctx: StepContext) -> dict[str, Any]:
    if payload.get("presenton_probe", True):
        try:
            await ctx.presenton.probe()
        except PresentonUnavailable as exc:
            raise StepFailure(f"presenton(8201) probe failed: {exc}", retriable=True) from exc
        except Exception as exc:  # noqa: BLE001
            raise StepFailure(f"presenton(8201) probe error: {type(exc).__name__}: {exc}", retriable=True) from exc
    return {"prepared": True, "presenton_probe_ok": payload.get("presenton_probe", True)}


# ────────────────────────── ppt.deck.generate ──────────────────────────


async def _load_deck(session: AsyncSession, deck_id: str) -> tuple[PptV2DeckSpecRecord, TeachingDeckSpec, str]:
    try:
        uid = UUID(deck_id)
    except ValueError as exc:
        raise StepFailure(f"非法 deck_id: {deck_id!r}", retriable=False) from exc
    row = await session.get(PptV2DeckSpecRecord, uid)
    if row is None:
        raise StepFailure(f"deck 不存在: {deck_id}", retriable=False)
    spec = TeachingDeckSpec.model_validate(row.spec)
    sha = row.spec_sha256 or LocalArtifactStore.canonical_sha256(row.spec)
    return row, spec, sha


async def _emit(ctx: StepContext, event_type: str, payload: dict[str, Any]) -> None:
    if ctx.engine is not None and ctx.session is not None and ctx.job_id:
        await ctx.engine.append_event(ctx.session, UUID(ctx.job_id), event_type, payload, ctx.trace_id)


async def _cancel_point(ctx: StepContext, where: str) -> None:
    if ctx.session is None or ctx.job_id is None:
        return
    requested = (
        await ctx.session.execute(select(PptV2Job.cancel_requested).where(PptV2Job.id == UUID(ctx.job_id)))
    ).scalar()
    if requested:
        raise DeckCancelled(f"cancel_requested at {where}")


async def _checkpoint_reuse(ctx: StepContext, deck_id: UUID, page_no: int, engine_version: str) -> PptV2DeckPage | None:
    """页级 checkpoint 复用：同 deck+页+引擎版本 且 8201 上 fragment 仍存在且哈希一致。"""
    assert ctx.session is not None
    row = (
        await ctx.session.execute(
            select(PptV2DeckPage)
            .where(
                PptV2DeckPage.deck_id == deck_id,
                PptV2DeckPage.page_no == page_no,
                PptV2DeckPage.status == "COMPLETED",
            )
            .order_by(PptV2DeckPage.updated_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None or not row.fragment_ref:
        return None
    if (row.qa or {}).get("builder_version") != engine_version:
        return None
    try:
        meta = await ctx.presenton.artifact_meta(row.fragment_ref)
    except PresentonUnavailable:
        raise
    except EngineRejected:
        return None
    if meta is None or meta.get("sha256") != row.fragment_sha256:
        return None
    return row


def _asset_bytes(spec: TeachingDeckSpec, artifact_root: str | None = None) -> tuple[dict[str, bytes], list[str]]:
    store = LocalArtifactStore(artifact_root) if artifact_root else LocalArtifactStore()
    got: dict[str, bytes] = {}
    missing: list[str] = []
    for a in spec.assets:
        data = store.get(f"pps2/assets/{a.sha256}")
        if data is None:
            missing.append(a.id)
        else:
            got[a.id] = data
    return got, missing


def _page_view(row: PptV2DeckPage) -> dict[str, Any]:
    return {
        "page_no": row.page_no,
        "slide_id": row.slide_id,
        "status": row.status,
        "content_sha256": row.content_sha256,
        "fragment_ref": row.fragment_ref,
        "fragment_sha256": row.fragment_sha256,
        "png_ref": row.png_ref,
        "png_sha256": row.png_sha256,
        "cache_hit": row.cache_hit,
        "elapsed_ms": row.elapsed_ms,
        "qa": row.qa,
        "last_error": row.last_error,
    }


async def _register_page_artifacts(
    session: AsyncSession, deck_id: UUID, job_id: UUID, slide_id: str, page_no: int, resp: dict[str, Any]
) -> None:
    """内容寻址幂等登记：object_key 已存在（同内容重渲染/重放）→ 跳过（无重复 artifact）。"""
    for kind, ref_key, sha_key, _ext, key_tpl in (
        ("PAGE_FRAGMENT", "fragment_ref", "fragment_sha256", "pptx", "pps2/pages/{ref}.pptx"),
        ("PAGE_PNG", "png_ref", "png_sha256", "png", "pps2/pages/{ref}.png"),
    ):
        ref = resp.get(ref_key)
        if not ref:
            continue
        object_key = key_tpl.format(ref=ref)
        existing = (
            await session.execute(select(PptV2Artifact.id).where(PptV2Artifact.object_key == object_key))
        ).scalar()
        if existing is not None:
            continue
        size = int(resp.get("fragment_size" if ref_key == "fragment_ref" else "png_size") or 0)
        session.add(
            PptV2Artifact(
                deck_id=deck_id,
                job_id=job_id,
                kind=kind,
                object_key=object_key,
                sha256=resp.get(sha_key) or ref,
                size_bytes=size,
                page_no=page_no,
                artifact_meta={"slide_id": slide_id, "content_sha256": resp.get("content_sha256")},
            )
        )


async def _persist_page_bytes(
    ctx: StepContext, store: LocalArtifactStore, resp: dict[str, Any]
) -> list[str]:
    """把逐页产物字节真正落到 8200 自己的 artifact store（修 T24 EXPORT-QA E1）。

    E1 缺口：`_register_page_artifacts` 只登记 `object_key`，字节留在 8201 的私有
    root 里 —— 于是 `GET /artifacts/{PAGE_PNG}/content` 恒 404：
    浏览器拿不到内核渲染的页面图，8100 的逐页 QA 也只能自己再用 COM 渲一遍。

    这里在页 success 分支把字节取回归档（sha256 逐字节复核，**不匹配就不写**，
    绝不落错字节）。失败**不**判页失败 —— 页本身是好的，只是预览字节缺失；
    按 `PAGE_BYTES_UNAVAILABLE` 事件如实披露。
    """
    problems: list[str] = []
    for ref_key, sha_key, ext in (
        ("fragment_ref", "fragment_sha256", "pptx"),
        ("png_ref", "png_sha256", "png"),
    ):
        ref = resp.get(ref_key)
        if not ref:
            continue
        object_key = f"pps2/pages/{ref}.{ext}"
        if store.exists(object_key):
            continue  # 内容寻址：同内容重渲染/重放不重复写
        try:
            data = await ctx.presenton.artifact_bytes(str(ref))
        except Exception as exc:  # noqa: BLE001 —— 预览字节缺失不应让整页失败
            problems.append(f"{ref_key}:{type(exc).__name__}")
            continue
        want = str(resp.get(sha_key) or "")
        if len(want) == 64:  # 只在与声明哈希可比时强制核对；缺声明时不冒充已验证
            got = LocalArtifactStore.sha256_bytes(data)
            if got != want:
                problems.append(f"{ref_key}:sha256_mismatch")
                continue
        if not data:
            problems.append(f"{ref_key}:empty_bytes")
            continue
        store.put(object_key, data)
    return problems


async def _mark_page_failed(
    session: AsyncSession, deck_id: UUID, job_id: UUID, page_no: int, slide_id: str,
    content_sha: str, qa: dict[str, Any] | None, error: str,
) -> None:
    """页失败登记（upsert，幂等）：诊断保留，供坏页修复与审计。"""
    row = (
        await session.execute(
            select(PptV2DeckPage).where(
                PptV2DeckPage.deck_id == deck_id,
                PptV2DeckPage.page_no == page_no,
                PptV2DeckPage.content_sha256 == content_sha,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = PptV2DeckPage(deck_id=deck_id, job_id=job_id, page_no=page_no,
                            slide_id=slide_id, content_sha256=content_sha)
        session.add(row)
        row.attempt = 0
    row.status = "FAILED"
    row.attempt = (row.attempt or 0) + 1
    row.qa = qa
    row.last_error = error


async def _upsert_page_row(
    session: AsyncSession, deck_id: UUID, job_id: UUID, page_no: int, slide_id: str, resp: dict[str, Any],
    engine_version: str,
) -> PptV2DeckPage:
    """checkpoint upsert：同 (deck, page, content) 幂等（重渲染同一内容不产生第二行）。"""
    content_sha = str(resp.get("content_sha256", ""))
    row = (
        await session.execute(
            select(PptV2DeckPage).where(
                PptV2DeckPage.deck_id == deck_id,
                PptV2DeckPage.page_no == page_no,
                PptV2DeckPage.content_sha256 == content_sha,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = PptV2DeckPage(
            deck_id=deck_id, job_id=job_id, page_no=page_no, slide_id=slide_id,
            content_sha256=content_sha,
        )
        session.add(row)
        row.attempt = 0
    row.status = "COMPLETED"
    row.attempt = (row.attempt or 0) + 1
    row.fragment_ref = resp.get("fragment_ref")
    row.png_ref = resp.get("png_ref")
    row.fragment_sha256 = resp.get("fragment_sha256") or resp.get("fragment_ref")
    row.png_sha256 = resp.get("png_sha256") or resp.get("png_ref")
    row.qa = {**(resp.get("qa") or {}), "builder_version": engine_version}
    row.cache_hit = False
    row.elapsed_ms = int(resp.get("elapsed_ms") or 0)
    row.last_error = None
    return row


async def _generate_deck(payload: dict[str, Any], ctx: StepContext) -> dict[str, Any]:
    if ctx.session is None or ctx.engine is None or ctx.job_id is None:
        raise StepFailure("generate step 缺少执行上下文（session/engine/job_id）", retriable=False)
    settings = ctx.settings or get_settings()
    session, engine, job_id = ctx.session, ctx.engine, UUID(ctx.job_id)

    deck_row, spec, spec_sha = await _load_deck(session, str(payload.get("deck_id", "")))
    total = len(spec.slides)
    if not (settings.min_deck_pages <= total <= settings.max_deck_pages):
        raise StepFailure(f"页数 {total} 超出预算 [{settings.min_deck_pages},{settings.max_deck_pages}]", retriable=False)

    assets_bytes, missing_assets = _asset_bytes(spec, settings.artifact_root)
    if missing_assets:
        raise StepFailure(f"资产字节缺失（8200 artifact store）: {missing_assets}", retriable=False)

    # ── R21.1 Visual Planning Layer：spec → ResolvedVisualPlan（引擎中立，无坐标）──
    visual_plan = build_visual_plan(spec)
    visual_plan["source"]["spec_sha256"] = spec_sha
    deck_qa = run_deck_qa(visual_plan)
    visual_plan["qa"] = deck_qa
    plan_store = LocalArtifactStore(settings.artifact_root)
    plan_key = f"pps2/plans/{deck_row.id}-{spec_sha[:12]}.json"
    plan_store.put(plan_key, plan_to_json(visual_plan))
    # 逐页字节归档用的 store（与装配阶段同一 root；E1 修复）
    page_store = LocalArtifactStore(settings.artifact_root)
    if not (deck_qa["visual"]["pass"] and deck_qa["narrative"]["pass"]):
        # 规划层 QA 失败 = 规划缺陷，非瞬态（不重试掩盖；诊断随事件落盘）
        raise StepFailure(
            "visual plan QA 未通过: "
            + "; ".join(deck_qa["visual"]["issues"] + deck_qa["narrative"]["issues"])[:500],
            retriable=False,
        )
    await _emit(ctx, "VISUAL_PLAN_READY", {
        "plan_object_key": plan_key, "planner": VPL_VERSION,
        "families": deck_qa["summary"]["families_used"],
        "layout_diversity": deck_qa["summary"]["layout_diversity"],
    })

    engine_version = await ctx.presenton.engine_version()
    t0 = time.monotonic()
    page_views: list[dict[str, Any]] = []
    page_failures: list[dict[str, Any]] = []  # 继续渲染收集失败，末尾统一裁决（不丢整套）
    reused = 0
    rendered = 0

    for page_no, slide in enumerate(spec.slides, start=1):
        await _cancel_point(ctx, f"before-page-{page_no}")
        await engine.renew(session, job_id, ctx.claim_token or "")

        reused_row = await _checkpoint_reuse(ctx, deck_row.id, page_no, engine_version)
        if reused_row is not None:
            reused += 1
            reused_row.cache_hit = True
            reused_row.job_id = job_id
            # E1 补位：复用路径同样补齐逐页字节（历史 deck 只登记过 object_key，
            # 8200 content 端点曾恒 404；重跑即自愈，无需重渲染）。
            reuse_problems = await _persist_page_bytes(ctx, page_store, {
                "fragment_ref": reused_row.fragment_ref,
                "fragment_sha256": reused_row.fragment_sha256,
                "png_ref": reused_row.png_ref,
                "png_sha256": reused_row.png_sha256,
            })
            if reuse_problems:
                await _emit(ctx, "PAGE_BYTES_UNAVAILABLE",
                            {"page_no": page_no, "problems": reuse_problems, "source": "checkpoint_reuse"})
            await _emit(ctx, "CHECKPOINT_REUSED", {"page_no": page_no, "slide_id": reused_row.slide_id,
                                                    "fragment_ref": reused_row.fragment_ref})
            page_views.append(_page_view(reused_row))
            await session.commit()
            continue

        await _emit(ctx, "PAGE_STARTED", {"page_no": page_no, "slide_id": slide.id, "engine": engine_version})
        body = {
            "deck_id": str(deck_row.id),
            "deck_title": spec.metadata.title,
            "page_no": page_no,
            "total_pages": total,
            "slide": slide.model_dump(mode="json"),
            "assets_bytes": {aid: base64.b64encode(blob).decode("ascii") for aid, blob in assets_bytes.items()},
            "visual_plan": visual_plan["slides"][page_no - 1],
            "deck_style_tokens": visual_plan["style_tokens"],
            "render": True,
        }
        try:
            resp = await ctx.presenton.render_page(body, timeout_s=settings.page_render_timeout_s)
        except PresentonUnavailable as exc:
            await _mark_page_failed(session, deck_row.id, job_id, page_no, slide.id, "unavailable", None,
                                    f"presenton unavailable: {exc}")
            await _emit(ctx, "PAGE_FAILED", {"page_no": page_no, "error": str(exc)[:300]})
            await session.commit()
            page_failures.append({"page_no": page_no, "kind": "transient", "error": str(exc)[:300]})
            continue
        except EngineRejected as exc:
            await _mark_page_failed(session, deck_row.id, job_id, page_no, slide.id, "rejected", None,
                                    f"engine rejected: {exc.detail[:300]}")
            await _emit(ctx, "PAGE_FAILED", {"page_no": page_no, "error": exc.detail[:300]})
            await session.commit()
            page_failures.append({"page_no": page_no, "kind": "permanent", "error": exc.detail[:300]})
            continue

        if not resp.get("ok"):
            qa_errors = "; ".join(resp.get("errors") or [])
            await _mark_page_failed(session, deck_row.id, job_id, page_no, slide.id,
                                    str(resp.get("content_sha256", "")), resp.get("qa"), qa_errors[:500])
            await _emit(ctx, "PAGE_FAILED", {"page_no": page_no, "errors": qa_errors[:500]})
            await session.commit()
            page_failures.append({"page_no": page_no, "kind": "permanent", "error": qa_errors[:300]})
            continue

        row = await _upsert_page_row(session, deck_row.id, job_id, page_no, slide.id, resp, engine_version)
        await _register_page_artifacts(session, deck_row.id, job_id, slide.id, page_no, resp)
        # E1 修复：把逐页字节取回落到 8200 自己的 store，使 GET /artifacts/{id}/content 可用
        byte_problems = await _persist_page_bytes(ctx, page_store, resp)
        if byte_problems:
            await _emit(ctx, "PAGE_BYTES_UNAVAILABLE", {"page_no": page_no, "problems": byte_problems})
        await _emit(ctx, "PAGE_COMPLETED", {
            "page_no": page_no, "slide_id": slide.id, "cache_hit": False,
            "content_sha256": resp.get("content_sha256"), "elapsed_ms": resp.get("elapsed_ms"),
        })
        await session.commit()
        rendered += 1
        page_views.append(_page_view(row))

    # 页级失败汇总裁决：任何 permanent（QA/契约）→ 非瞬态；全 transient → 瞬态。
    # 已完成页 checkpoint 全部保留（失败不丢整套）；下一 attempt 只补缺失页。
    if page_failures:
        kinds = {f["kind"] for f in page_failures}
        brief = "; ".join(f"p{f['page_no']}({f['kind']}): {f['error'][:120]}" for f in page_failures[:5])
        raise StepFailure(
            f"{len(page_failures)}/{total} 页失败 → {brief}",
            retriable=(kinds == {"transient"}),
        )

    # ── 装配 ──────────────────────────────────────────────────────
    await _cancel_point(ctx, "before-assemble")
    await engine.renew(session, job_id, ctx.claim_token or "")
    await _emit(ctx, "ASSEMBLE_STARTED", {"pages": len(page_views), "reused": reused, "rendered": rendered})
    # R3d：随装配下发逐页 QA 页图引用（png_ref）→ 8201 contact sheet 复用真实页图，
    # 免去整册二次渲染（去重复渲染，不减任何校验）。
    pages_payload = [
        {
            "page_no": v["page_no"],
            "page_ref": v["fragment_ref"],
            "content_sha256": v["content_sha256"],
            "png_ref": v.get("png_ref"),
        }
        for v in sorted(page_views, key=lambda v: v["page_no"])
    ]
    try:
        asm = await ctx.presenton.assemble(
            {"deck_id": str(deck_row.id), "deck_title": spec.metadata.title,
             "deck_spec_sha256": spec_sha, "pages": pages_payload},
            timeout_s=settings.assemble_timeout_s,
        )
    except PresentonUnavailable as exc:
        raise StepFailure(f"assemble unavailable: {exc}", retriable=True) from exc
    except EngineRejected as exc:
        raise StepFailure(f"assemble 引擎拒绝（非瞬态）: {exc.detail[:300]}", retriable=False) from exc

    if int(asm.get("size_bytes") or 0) > settings.max_artifact_bytes:
        raise StepFailure(f"产物 {asm.get('size_bytes')}B 超出预算 {settings.max_artifact_bytes}B", retriable=False)
    if int(asm.get("page_count") or 0) != total:
        raise StepFailure(f"装配页数不符: {asm.get('page_count')} != {total}", retriable=False)

    # 拉回字节 → 校验 sha256 → 8200 本地留存 + 登记（8200 是 Artifact 事实权威）
    deck_ref = str(asm.get("deck_ref") or asm.get("deck_sha256"))
    deck_bytes = await ctx.presenton.artifact_bytes(deck_ref)
    actual_sha = LocalArtifactStore.sha256_bytes(deck_bytes)
    if actual_sha != str(asm.get("deck_sha256")):
        raise StepFailure("最终产物 sha256 校验失败", retriable=False)
    store = LocalArtifactStore(settings.artifact_root)
    deck_key = str(asm["deck_key"])
    object_key = f"pps2/decks/{deck_key}.pptx"
    store.put(object_key, deck_bytes)
    if (
        await session.execute(
            select(PptV2Artifact.id).where(PptV2Artifact.object_key == object_key)
        )
    ).scalar() is None:
        session.add(PptV2Artifact(
            deck_id=deck_row.id, job_id=job_id, kind="DECK_PPTX", object_key=object_key,
            sha256=actual_sha, size_bytes=len(deck_bytes),
            artifact_meta={"deck_title": spec.metadata.title, "spec_sha256": spec_sha, "engine": asm.get("engine")},
        ))
    # contact sheet 引用（字节留在 8201；e2e/验收按 ref 取用）
    contact_ref = str(asm.get("contact_sheet_ref") or "")
    if contact_ref:
        contact_bytes = await ctx.presenton.artifact_bytes(contact_ref)
        contact_key = f"pps2/decks/{deck_key}-contact-sheet.png"
        store.put(contact_key, contact_bytes)
        if (
            await session.execute(
                select(PptV2Artifact.id).where(PptV2Artifact.object_key == contact_key)
            )
        ).scalar() is None:
            session.add(PptV2Artifact(
                deck_id=deck_row.id, job_id=job_id, kind="CONTACT_SHEET", object_key=contact_key,
                sha256=LocalArtifactStore.sha256_bytes(contact_bytes), size_bytes=len(contact_bytes),
                artifact_meta={"engine_ref": contact_ref},
            ))
    await _emit(ctx, "ASSEMBLE_COMPLETED", {
        "deck_key": deck_key, "deck_sha256": actual_sha, "size_bytes": len(deck_bytes),
        "page_count": asm.get("page_count"), "openable": asm.get("openable"),
    })

    total_ms = int((time.monotonic() - t0) * 1000)
    return {
        "deck_id": str(deck_row.id),
        "spec_sha256": spec_sha,
        "deck_key": deck_key,
        "artifact": {"object_key": object_key, "sha256": actual_sha, "size_bytes": len(deck_bytes)},
        "contact_sheet_ref": contact_ref,
        "openable": asm.get("openable"),
        "pages": page_views,
        "summary": {"total_pages": total, "rendered": rendered, "checkpoint_reused": reused},
        "engine": {"version": engine_version, "assemble": asm.get("engine")},
        "cost": {"total_ms": total_ms, "page_render_ms": [v["elapsed_ms"] for v in page_views],
                 "assemble_ms": asm.get("elapsed_ms")},
    }


STEP_REGISTRY: dict[str, Any] = {
    "ppt.deck.prepare": _prepare,
    "ppt.deck.generate": _generate_deck,
}


async def run_step(job_type: str, payload: dict[str, Any], ctx: StepContext) -> dict[str, Any]:
    handler = STEP_REGISTRY.get(job_type)
    if handler is None:
        raise StepFailure(f"未知 job_type {job_type!r}", retriable=False)
    return await handler(dict(payload), ctx)
