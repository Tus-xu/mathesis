"""DeckPatch 协议与乐观并发应用（R10，F3 修改流程）。

修改流程：natural language → DeckPatchProposal → validate → human/apply policy →
TeachingDeckSpec revision+1 → dependency graph → only affected slides/layout/render/QA。

不可变事实（patch 层强制）：
- slide.id / slide.revision 由系统维护，禁止 patch 直改；
- asset.object_key / sha256 / kind / preserve_original 不可变（TEACHER_UPLOAD 原件保护）；
- deck 每次成功 apply：revision+1 + change_log 追加 + 影响面（AffectedSlideSet）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ppt_v2.domain.dependencies import AffectedSlideSet, compute_affected
from app.ppt_v2.domain.errors import (
    PatchValidationError,
    PreserveOriginalViolationError,
    RevisionConflictError,
    UnknownAssetError,
    UnknownSlideError,
)
from app.ppt_v2.domain.spec import (
    ASSET_MUTABLE_FIELDS,
    SCHEMA_VERSION,
    SLIDE_MUTABLE_FIELDS,
    AssetRef,
    ChangeLogEntry,
    DeckMetadata,
    OutlineEntry,
    SelectedExample,
    SlideSpec,
    TeachingDeckSpec,
    TeachingStrategy,
    ThemeRef,
)

# patch 操作动词（语义化，杜绝直接写像素/旧 V3 结构）
PatchVerb = Literal[
    "set_metadata",
    "set_theme",
    "add_slide",
    "remove_slide",
    "set_slide",
    "move_slide",
    "set_outline",
    "set_strategy",
    "set_selected_examples",
    "add_asset",
    "set_asset",
    "remove_asset",
]

# deck 级元数据可改字段（白名单）
_DECK_LEVEL_METADATA_FIELDS = {
    "title",
    "grade",
    "duration_minutes",
    "language",
    "course_context_ref",
}
_SLIDE_ID_FIELD = "id"


class PatchOp(BaseModel):
    """单项语义操作。 """

    model_config = ConfigDict(extra="ignore")

    op: PatchVerb  # type: ignore[valid-type]
    target: str | None = Field(default=None, min_length=1)  # slide / asset / outline 条目 id
    field: str | None = Field(default=None, min_length=1)  # slide / metadata 字段名（白名单）
    value: Any = None  # 目标值（dict 为部分字段更新，重放后需经领域校验）


class DeckPatchProposal(BaseModel):
    """一次修改提案：deck_id + expected_revision（乐观并发门票）+ 操作列表。"""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    deck_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=0)
    summary: str = Field(min_length=1)
    source: Literal["human", "ai"] = "human"
    created_by: str | None = None
    operations: list[PatchOp] = Field(min_length=1)


class DeckPatch(BaseModel):
    """一次成功 apply 的结果元数据（含影响面，供上层追踪/replay）。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    deck_id: str = Field(min_length=1)
    base_revision: int = Field(ge=0)
    result_revision: int = Field(ge=0)
    summary: str = Field(min_length=1)
    applied_at: datetime
    affected: AffectedSlideSet


def new_patch_id() -> str:
    return str(uuid.uuid4())


def apply_proposal(spec: TeachingDeckSpec, proposal: DeckPatchProposal, *, patch_id: str | None = None) -> tuple[
    TeachingDeckSpec, DeckPatch, ChangeLogEntry
]:
    """在副本上应用 proposal；成功则返回 (新 spec, patch 元数据, change log 追加条目)。

    - 乐观并发：spec.revision != expected_revision → RevisionConflictError（不产生任何副作用）；
    - 任一 op 非法 → PatchValidationError 家族异常（spec 保持不变，调用方可整体放弃）；
    - 影响面合并后写入 AffectedSlideSet 与 change_log，供只重算受影响页。
    """
    if spec.schema_version != SCHEMA_VERSION:
        # 防御：apply 只在当前契约版本上执行（reader 之后域对象始终为当前版本）
        from app.ppt_v2.domain.errors import UnsupportedSchemaVersion

        raise UnsupportedSchemaVersion(spec.schema_version, SCHEMA_VERSION)

    if spec.revision != proposal.expected_revision:
        raise RevisionConflictError(expected=proposal.expected_revision, actual=spec.revision)

    if spec.id != proposal.deck_id:
        raise PatchValidationError(f"deck_id 不匹配：proposal={proposal.deck_id} spec={spec.id}")

    try:
        return _apply_validated(spec, proposal, patch_id=patch_id)
    except PatchValidationError:
        raise
    except ValidationError as exc:
        # 把底层 pydantic 校验失败映射到领域分类（原件保护违规单独定名）
        if "preserve_original" in str(exc):
            raise PreserveOriginalViolationError(
                f"TEACHER_UPLOAD 原件保护被关闭（patch={proposal.summary}）: {exc}"
            ) from exc
        raise PatchValidationError(f"patch 未通过领域校验（deck={proposal.deck_id}）: {exc}") from exc


def _apply_validated(
    spec: TeachingDeckSpec, proposal: DeckPatchProposal, *, patch_id: str | None
) -> tuple[TeachingDeckSpec, DeckPatch, ChangeLogEntry]:
    # 预校验全部 op（先验证再应用，保证原子性）
    # 同一提案内的 add_slide 目标对后续 op 是合法 target（如 add_slide + move_slide 组合）
    pending_new_ids = {op.target for op in proposal.operations if op.op == "add_slide" and op.target}
    for op in proposal.operations:
        _validate_op(spec, op, pending_new_ids=pending_new_ids)

    # 逐 op 应用到副本
    builder = _PatchBuilder(spec)
    for op in proposal.operations:
        builder.apply(op)

    new_spec = builder.spec
    # 全部 op 应用后统一做域级完整性校验（引用可解析、字段枚举合法等）
    new_spec = _validate_deck_references(new_spec)
    new_revision = spec.revision + 1
    affected = builder.merged_affected(rationale=proposal.summary)

    now = datetime.now(UTC)
    log_entry = ChangeLogEntry(
        revision=new_revision,
        ts=now,
        kind="patch",
        summary=proposal.summary,
        patch_ids=[patch_id or new_patch_id()],
        affected_slide_ids=sorted(affected.slide_ids),
    )

    final_spec = new_spec.model_copy(
        update={"revision": new_revision, "change_log": [*new_spec.change_log, log_entry]},
        deep=True,
    )

    patch = DeckPatch(
        id=patch_id or new_patch_id(),
        deck_id=proposal.deck_id,
        base_revision=spec.revision,
        result_revision=new_revision,
        summary=proposal.summary,
        applied_at=now,
        affected=affected,
    )
    return final_spec, patch, log_entry


# ─────────────────────────────────────────────────────────────────────────────
# op 预校验（不产生副作用）
# ─────────────────────────────────────────────────────────────────────────────


def _validate_op(spec: TeachingDeckSpec, op: PatchOp, *, pending_new_ids: frozenset[str] | set[str] = frozenset()) -> None:
    slide_ids = {s.id for s in spec.slides} | set(pending_new_ids)
    asset_ids = {a.id for a in spec.assets}
    slide_count = len(spec.slides) + sum(1 for sid in pending_new_ids)

    if op.op == "set_metadata":
        if op.field not in _DECK_LEVEL_METADATA_FIELDS:
            raise PatchValidationError(f"set_metadata 字段 {op.field!r} 不在白名单 {sorted(_DECK_LEVEL_METADATA_FIELDS)}")
        _validate_metadata_value(op.field, op.value)

    elif op.op == "set_theme":
        _validate_theme_value(op.value)

    elif op.op == "add_slide":
        if not op.target:
            raise PatchValidationError("add_slide 必须提供 target=新 slide id")
        _validate_new_slide(op.target, op.value)

    elif op.op == "remove_slide":
        _require_existing_slide(op.target, slide_ids)

    elif op.op == "set_slide":
        _require_existing_slide(op.target, slide_ids)
        if op.field not in SLIDE_MUTABLE_FIELDS:
            raise PatchValidationError(
                f"set_slide 字段 {op.field!r} 不可变/不在白名单；允许: {sorted(SLIDE_MUTABLE_FIELDS)}"
            )
        if op.field == _SLIDE_ID_FIELD:
            raise PatchValidationError("slide.id 由系统维护，禁止 patch 修改")
        _validate_slide_field_value(spec, op.target, op.field, op.value)

    elif op.op == "move_slide":
        _require_existing_slide(op.target, slide_ids)
        if not isinstance(op.value, dict) or not isinstance(op.value.get("index"), int):
            raise PatchValidationError("move_slide 的 value 必须是 {index: int}（0 基目标位置）")
        index = op.value["index"]
        if not (0 <= index < slide_count):
            raise PatchValidationError(f"move_slide 目标位置 {index} 越界（页数 {slide_count}）")

    elif op.op == "set_outline":
        if not isinstance(op.value, list):
            raise PatchValidationError("set_outline 的 value 必须是 OutlineEntry 列表")
        from app.ppt_v2.domain.spec import OutlineEntry

        for entry in op.value:
            OutlineEntry.model_validate(entry)

    elif op.op == "set_strategy":
        if not isinstance(op.value, (dict, type(None))):
            raise PatchValidationError("set_strategy 的 value 必须是 dict 与现有策略合并")

    elif op.op == "set_selected_examples":
        if not isinstance(op.value, list):
            raise PatchValidationError("set_selected_examples 的 value 必须是 SelectedExample 列表")
        from app.ppt_v2.domain.spec import SelectedExample

        for ex in op.value:
            SelectedExample.model_validate(ex)

    elif op.op == "add_asset":
        if not op.target:
            raise PatchValidationError("add_asset 必须提供 target=新 asset id")
        new_asset = {"id": op.target, **(op.value or {})}
        AssetRef.model_validate(new_asset)  # TEACHER_UPLOAD 原件保护在此强校验

    elif op.op == "set_asset":
        _require_asset(op.target, asset_ids)
        if op.field not in ASSET_MUTABLE_FIELDS:
            raise PatchValidationError(
                f"set_asset 字段 {op.field!r} 为不可变事实（object_key/sha256/kind/preserve_original 禁止修改）"
            )

    elif op.op == "remove_asset":
        _require_asset(op.target, asset_ids)


def _require_existing_slide(target: str | None, slide_ids: set[str]) -> None:
    if not target or target not in slide_ids:
        raise UnknownSlideError(f"slide {target!r} 不存在")


def _require_asset(target: str | None, asset_ids: set[str]) -> None:
    if not target or target not in asset_ids:
        raise UnknownAssetError(f"asset {target!r} 不存在")


def _validate_metadata_value(field: str, value: Any) -> None:
    try:
        DeckMetadata.model_validate({field: value})
    except ValidationError as exc:  # pragma: no cover - 实际由 pydantic 校验细节驱动
        raise PatchValidationError(f"set_metadata.{field} 非法: {exc}") from exc


def _validate_theme_value(value: Any) -> None:
    try:
        ThemeRef.model_validate(value)
    except ValidationError as exc:
        raise PatchValidationError(f"set_theme value 非法: {exc}") from exc


def _validate_new_slide(slide_id: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise PatchValidationError("add_slide 的 value 必须是 SlideSpec 字段 dict")
    try:
        SlideSpec.model_validate({"id": slide_id, **value})
    except ValidationError as exc:
        raise PatchValidationError(f"add_slide 非法: {exc}") from exc


def _validate_slide_field_value(spec: TeachingDeckSpec, slide_id: str, field: str, value: Any) -> None:
    existing = next((s for s in spec.slides if s.id == slide_id), None)
    if existing is None:
        raise PatchValidationError(f"set_slide 目标 {slide_id!r} 在当前 spec 中不存在（本提案先 add 再 set 不受支持）")
    try:
        SlideSpec.model_validate({**existing.model_dump(), field: value})
    except ValidationError as exc:
        raise PatchValidationError(f"set_slide.{field} 非法: {exc}") from exc


# ─────────────────────────────────────────────────────────────────────────────
# 逐 op 应用（在副本上构造）
# ─────────────────────────────────────────────────────────────────────────────


class _PatchBuilder:
    """在 TeachingDeckSpec 副本上应用 ops 并累积影响面。"""

    _SLIDE_CONTENT_FIELDS = frozenset({"content", "teaching_goal", "source_refs", "assets", "type", "elements"})
    _SLIDE_LAYOUT_FIELDS = frozenset({"title", "layout_hint", "visual_intent"})
    _SLIDE_META_FIELDS = frozenset({"speaker_note", "status", "qa_status"})

    def __init__(self, spec: TeachingDeckSpec) -> None:
        self.spec = spec
        self._changed_slide_revisions: set[str] = set()
        self._slide_ids: set[str] = {s.id for s in spec.slides}
        self._asset_ids: set[str] = {a.id for a in spec.assets}
        self._affected_sets: list[AffectedSlideSet] = []

    def apply(self, op: PatchOp) -> None:
        self._affected_sets.append(compute_affected(self.spec, op=op.op, target=op.target, field=op.field))

        if op.op == "set_metadata":
            merged = {**(self.spec.metadata.model_dump()), op.field: op.value}  # type: ignore[index]
            self.spec = self.spec.model_copy(update={"metadata": DeckMetadata.model_validate(merged)}, deep=True)

        elif op.op == "set_theme":
            self.spec = self.spec.model_copy(update={"theme": ThemeRef.model_validate(op.value)}, deep=True)

        elif op.op == "add_slide":
            slide = SlideSpec.model_validate({"id": op.target, **(op.value or {})})
            self.spec = self.spec.model_copy(update={"slides": [*self.spec.slides, slide]}, deep=True)
            self._slide_ids.add(slide.id)

        elif op.op == "remove_slide":
            assert op.target is not None
            slides = [s for s in self.spec.slides if s.id != op.target]
            notes = {k: v for k, v in self.spec.speaker_notes.items() if k != op.target}
            # 同时把被删 slide 从大纲条目引用中摘除，保持引用完整性
            outline = [
                OutlineEntry.model_validate(
                    {**e.model_dump(), "slide_ids": [sid for sid in e.slide_ids if sid != op.target]}
                )
                for e in self.spec.outline
            ]
            self.spec = self.spec.model_copy(
                update={"slides": slides, "speaker_notes": notes, "outline": outline}, deep=True
            )
            self._slide_ids.discard(op.target)

        elif op.op == "set_slide":
            self._apply_set_slide(op)

        elif op.op == "move_slide":
            assert op.target is not None
            index = int(op.value["index"])  # 已由 _validate_op 预校验
            slides = [s for s in self.spec.slides if s.id != op.target]
            moved = next(s for s in self.spec.slides if s.id == op.target)
            slides.insert(index, moved)
            self.spec = self.spec.model_copy(update={"slides": slides}, deep=True)

        elif op.op == "set_outline":
            outline = [OutlineEntry.model_validate(e) for e in (op.value or [])]
            self.spec = self.spec.model_copy(update={"outline": outline}, deep=True)

        elif op.op == "set_strategy":
            existing = self.spec.teaching_strategy.model_dump()
            existing.update(op.value or {})
            self.spec = self.spec.model_copy(
                update={"teaching_strategy": TeachingStrategy.model_validate(existing)}, deep=True
            )

        elif op.op == "set_selected_examples":
            examples = [SelectedExample.model_validate(e) for e in (op.value or [])]
            self.spec = self.spec.model_copy(update={"selected_examples": examples}, deep=True)

        elif op.op == "add_asset":
            new_asset = AssetRef.model_validate({"id": op.target, **(op.value or {})})
            self.spec = self.spec.model_copy(update={"assets": [*self.spec.assets, new_asset]}, deep=True)
            self._asset_ids.add(new_asset.id)

        elif op.op == "set_asset":
            assert op.target is not None and op.field is not None
            assets: list[AssetRef] = []
            for asset in self.spec.assets:
                if asset.id == op.target:
                    updated = asset.model_copy(update={op.field: op.value}, deep=True)  # type: ignore[arg-type]
                    # model_copy 不校验，必须重放 AssetRef 校验（字段枚举/原件保护）
                    updated = AssetRef.model_validate(updated.model_dump())
                    asset = updated
                assets.append(asset)
            self.spec = self.spec.model_copy(update={"assets": assets}, deep=True)

        elif op.op == "remove_asset":
            assert op.target is not None
            assets = [a for a in self.spec.assets if a.id != op.target]
            self.spec = self.spec.model_copy(update={"assets": assets}, deep=True)
            self._asset_ids.discard(op.target)

    def _apply_set_slide(self, op: PatchOp) -> None:
        assert op.target is not None
        slides: list[SlideSpec] = []
        for slide in self.spec.slides:
            if slide.id == op.target:
                merged = {**slide.model_dump(), op.field: op.value, "revision": slide.revision + 1}  # type: ignore[index]
                slide = SlideSpec.model_validate(merged)
                self._changed_slide_revisions.add(slide.id)
            slides.append(slide)
        self.spec = self.spec.model_copy(update={"slides": slides}, deep=True)
        # 引用完整性由 apply_proposal 尾部统一 _validate_deck_references 兜底（多 op 中间态不提前校验）

    def merged_affected(self, *, rationale: str) -> AffectedSlideSet:
        layout = frozenset().union(*(s.layout_affected for s in self._affected_sets), frozenset())
        content = frozenset().union(*(s.content_affected for s in self._affected_sets), frozenset())
        assets = frozenset().union(*(s.assets_affected for s in self._affected_sets), frozenset())
        whole = any(s.whole_deck_layout for s in self._affected_sets)
        slide_ids = layout | content
        if whole and slide_ids:
            slide_ids = slide_ids | frozenset(self._slide_ids)
        return AffectedSlideSet(
            slide_ids=slide_ids,
            layout_affected=layout,
            content_affected=content,
            assets_affected=assets,
            whole_deck_layout=whole,
            rationale=rationale,
        )


def _validate_deck_references(spec: TeachingDeckSpec) -> TeachingDeckSpec:
    # 触发 model_validator（引用完整性）而不改变内容
    try:
        return TeachingDeckSpec.model_validate(spec.model_dump())
    except ValidationError as exc:
        raise PatchValidationError(f"patch 应用后依赖完整性被破坏: {exc}") from exc
