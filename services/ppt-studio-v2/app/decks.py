"""Deck ingest（8200 消费 approved TeachingDeckSpec）。

闸门（任一失败 → 422，绝不带病入库）：
1. TeachingDeckSpec schema 校验（8200 中立契约拷贝）；
2. approved 证据：projection lineage 含 approved_blueprint_id / context_snapshot_hash / body_hash
   （R20：未经教师确认不可投影 → 有 projection 即 approved 证据；fixture 需明确标注）；
3. 页预算：8..12 页；
4. 教材铁律：page_refs 新写入必须为空（仅兼容形状 []）；chunk locator 必须是版本化
   textbook_v2/locator/v1/...（direct-chunk）；
5. 学情引用：learning_profile/v1/... 引用必须含 sample/window/coverage/confidence 四元组；
6. 内容寻址：spec_sha256（canonical JSON）幂等 —— 同 spec 重复 ingest 返回同一 deck。

资产：assets_inline（asset_id → b64）落本地 artifact store（pps2/assets/<sha256>）并登记
ppt_v2_assets + ppt_v2_artifacts(kind=ASSET)。
"""
from __future__ import annotations

import base64
import hashlib
import re
import uuid
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.artifact_store import LocalArtifactStore
from app.config import get_settings
from app.contracts.deck import TeachingDeckSpec
from app.errors import ApiError, ErrorCode
from app.persistence.models import PptV2Artifact, PptV2AssetRecord, PptV2DeckSpecRecord

LOCATOR_RE = re.compile(r"^textbook_v2/locator/v1/source/[0-9a-fA-F-]+/section/[0-9a-fA-F-]+/chunk(?:-range)?/.+$")
PROFILE_RE = re.compile(
    r"^learning_profile/v1/(?P<profile_id>[^/]+)/sample/(?P<sample>[^/]+)/window/(?P<window>[^/]+)"
    r"/coverage/(?P<coverage>0(?:\.\d+)?|1(?:\.0+)?)/confidence/(?P<confidence>0(?:\.\d+)?|1(?:\.0+)?)$"
)


def spec_sha256_of(spec_raw: dict[str, Any]) -> str:
    payload = {k: v for k, v in spec_raw.items() if k != "assets_inline"}
    return LocalArtifactStore.canonical_sha256(payload)


def _require_projection(spec_raw: dict[str, Any]) -> dict[str, Any]:
    proj = spec_raw.get("projection") or {}
    required = ("approved_blueprint_id", "context_snapshot_hash", "body_hash")
    missing = [k for k in required if not proj.get(k)]
    if missing:
        raise ApiError(
            ErrorCode.VALIDATION,
            f"deck 未携带 approved projection lineage（缺 {missing}）——未确认的 deck 不可生成 PPT",
            error_key="deck_not_approved",
        )
    return proj


def _validate_gates(spec: TeachingDeckSpec, spec_raw: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    n = len(spec.slides)
    if not (settings.min_deck_pages <= n <= settings.max_deck_pages):
        raise ApiError(
            ErrorCode.VALIDATION,
            f"页数 {n} 超出教学 PPT 预算 [{settings.min_deck_pages}, {settings.max_deck_pages}]",
            error_key="page_budget_violation",
        )
    # 教材铁律：新写入零 page_refs + direct-chunk locator
    for src in spec.textbook_sources:
        if src.page_refs:
            raise ApiError(
                ErrorCode.VALIDATION,
                "新链路禁止写入 page_refs（DECISIONS §3）；deck 必须使用 direct-chunk locator",
                error_key="page_refs_forbidden",
            )
        locators = list(src.chunk_refs) + ([src.locator] if src.locator else [])
        if not locators:
            raise ApiError(
                ErrorCode.VALIDATION,
                f"textbook_source {src.textbook_id}/{src.section_id} 缺 chunk locator",
                error_key="locator_missing",
            )
        for loc in locators:
            if not LOCATOR_RE.match(loc):
                raise ApiError(
                    ErrorCode.VALIDATION,
                    f"locator 非版本化 direct-chunk 形式: {loc[:80]}…",
                    error_key="locator_format",
                )
    # 学情引用完整性
    profile_citations: list[dict[str, str]] = []
    for slide in spec.slides:
        for ref in slide.source_refs:
            if ref.startswith("learning_profile/"):
                m = PROFILE_RE.match(ref)
                if not m:
                    raise ApiError(
                        ErrorCode.VALIDATION,
                        f"学情引用缺少 sample/window/coverage/confidence 完整四元组: slide={slide.id}",
                        error_key="profile_citation_invalid",
                    )
                profile_citations.append({"slide_id": slide.id, **m.groupdict()})
    # slide 内容安全：引用的资产必须存在于 spec.assets
    asset_ids = {a.id for a in spec.assets}
    for slide in spec.slides:
        missing = sorted(set(slide.assets) - asset_ids)
        if missing:
            raise ApiError(
                ErrorCode.VALIDATION,
                f"slide {slide.id} 引用未注册资产: {missing}",
                error_key="asset_ref_missing",
            )
    return {"profile_citations": profile_citations, "page_count": n}


def _is_object_key_unique_conflict(exc: IntegrityError, table: str) -> bool:
    """仅识别 content-addressed object_key 唯一冲突（幂等语义）；其余约束冲突一律不吞。

    PG 命中约束名（uq_pptartifact_object_key / uq_pptasset_deck_key）；
    SQLite 命中 "UNIQUE constraint failed: <table>.<col>" 文本。
    """
    msg = str(getattr(exc, "orig", None) or exc).lower()
    if table == "ppt_v2_artifacts":
        return "uq_pptartifact_object_key" in msg or "ppt_v2_artifacts.object_key" in msg
    if table == "ppt_v2_assets":
        return "uq_pptasset_deck_key" in msg or (
            "ppt_v2_assets.deck_id" in msg and "ppt_v2_assets.object_key" in msg
        )
    return False


async def _persist_assets_inline(
    session: AsyncSession, deck: PptV2DeckSpecRecord, spec: TeachingDeckSpec, assets_inline: dict[str, str] | None
) -> list[dict[str, Any]]:
    if not assets_inline:
        return []
    store = LocalArtifactStore()
    out: list[dict[str, Any]] = []
    seen_object_keys: set[str] = set()
    for asset in spec.assets:
        b64 = assets_inline.get(asset.id)
        if not b64:
            continue  # 未随附字节的资产（如外部 object_key）不落盘，仅保留登记
        data = base64.b64decode(b64)
        sha = hashlib.sha256(data).hexdigest()
        if sha != asset.sha256:
            # 内容完整性闸门：字节必须与 AssetRef.sha256 一致（防调包/漂移）
            raise ApiError(
                ErrorCode.VALIDATION,
                f"asset {asset.id} 字节哈希与 AssetRef.sha256 不一致（{sha[:12]}… != {asset.sha256[:12]}…）",
                error_key="asset_hash_mismatch",
            )
        object_key = f"pps2/assets/{sha}"
        store.put(object_key, data)
        if object_key in seen_object_keys:
            # R21.1 缺陷修复 B：同请求内相同 content-addressed 资产（不同 asset_id 同内容）
            # 只登记一次 artifact + asset record；字节已落盘，按 sha 可取，功能无损。
            out.append({"asset_id": asset.id, "object_key": object_key, "sha256": sha,
                        "size": len(data), "deduped": True})
            continue
        seen_object_keys.add(object_key)
        # 内容寻址幂等：同字节资产已登记（其他 deck 已带入）→ 跳过，不产生重复 artifact。
        # 并发窗口防御：select 看不到未提交事务 → insert 撞唯一约束时以 savepoint 幂等吸收；
        # 仅吸收 object_key 相关唯一冲突，其余 IntegrityError 照常抛出（不吞真实冲突）。
        try:
            async with session.begin_nested():
                session.add(
                    PptV2Artifact(
                        deck_id=deck.id,
                        kind="ASSET",
                        object_key=object_key,
                        sha256=sha,
                        size_bytes=len(data),
                        artifact_meta={"asset_id": asset.id, "mime_type": asset.mime_type},
                    )
                )
                await session.flush()
        except IntegrityError as exc:
            if not _is_object_key_unique_conflict(exc, "ppt_v2_artifacts"):
                raise
            existing = (
                await session.execute(
                    select(PptV2Artifact.id).where(PptV2Artifact.object_key == object_key)
                )
            ).scalar()
            if existing is None:
                # 非幂等冲突（约束命中但查不到同键行）→ 真实冲突，重抛
                raise
        try:
            async with session.begin_nested():
                session.add(
                    PptV2AssetRecord(
                        deck_id=deck.id,
                        kind=asset.kind,
                        object_key=object_key,
                        sha256=sha,
                        mime_type=asset.mime_type,
                        size_bytes=len(data),
                        width=asset.width,
                        height=asset.height,
                        rights_status=asset.rights_status,
                        rights_evidence_ref=asset.rights_evidence_ref,
                        preserve_original=True,
                        source_ref=asset.source_ref,
                    )
                )
                await session.flush()
        except IntegrityError as exc:
            if not _is_object_key_unique_conflict(exc, "ppt_v2_assets"):
                raise
            # 同 (deck, object_key) 已有 asset record（并发/重放）→ 幂等跳过
        out.append({"asset_id": asset.id, "object_key": object_key, "sha256": sha, "size": len(data)})
    return out


async def ingest_deck(
    session: AsyncSession, spec_raw: dict[str, Any], assets_inline: dict[str, str] | None = None
) -> dict[str, Any]:
    proj = _require_projection(spec_raw)
    try:
        spec = TeachingDeckSpec.model_validate(spec_raw)
    except ValidationError as exc:
        raise ApiError(
            ErrorCode.VALIDATION,
            f"TeachingDeckSpec schema 校验失败: {str(exc.errors()[0].get('msg', 'invalid'))[:200]}",
            error_key="spec_invalid",
        ) from exc
    gate_info = _validate_gates(spec, spec_raw)
    sha = spec_sha256_of(spec_raw)

    existing = (
        await session.execute(select(PptV2DeckSpecRecord).where(PptV2DeckSpecRecord.spec_sha256 == sha))
    ).scalar_one_or_none()
    if existing is not None:
        return {
            "deck_id": str(existing.id),
            "spec_sha256": sha,
            "deduped": True,
            "page_count": len(spec.slides),
            "projection": proj,
            "profile_citations": gate_info["profile_citations"],
        }

    deck = PptV2DeckSpecRecord(
        school_id=None,
        schema_version=spec.schema_version,
        revision=spec.revision,
        spec=spec_raw,
        spec_sha256=sha,
    )
    session.add(deck)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ApiError(ErrorCode.CONFLICT, "同 spec 并发 ingest 冲突", error_key="deck_duplicate") from exc
    assets = await _persist_assets_inline(session, deck, spec, assets_inline)
    return {
        "deck_id": str(deck.id),
        "spec_sha256": sha,
        "deduped": False,
        "page_count": gate_info["page_count"],
        "projection": proj,
        "profile_citations": gate_info["profile_citations"],
        "assets": assets,
        "spec_id": spec.id,
    }


def deck_view(row: PptV2DeckSpecRecord) -> dict[str, Any]:
    spec = row.spec or {}
    return {
        "deck_id": str(row.id),
        "spec_sha256": row.spec_sha256,
        "schema_version": row.schema_version,
        "revision": row.revision,
        "title": (spec.get("metadata") or {}).get("title", ""),
        "page_count": len(spec.get("slides") or []),
        "projection": spec.get("projection"),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def get_deck(session: AsyncSession, deck_id: str) -> PptV2DeckSpecRecord | None:
    try:
        uid = uuid.UUID(deck_id)
    except ValueError:
        return None
    return await session.get(PptV2DeckSpecRecord, uid)
