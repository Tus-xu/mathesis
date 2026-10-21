"""LessonPlanSpec 块级 patch 协议与乐观并发应用（T24 · F3 教案修改流程）。

修改流程：block 级操作 → LessonPlanPatchProposal → 预校验 → apply →
LessonPlanSpec revision+1 → 只重渲染被声明的 block（文档导出按 revision 重生成）。

**block_id 编址方案（冻结，UI 与 diff 共用同一套地址）**
- 列表块 `objectives`/`key_difficult_points`/`activities`/`examples`/`assessment`/`evidence`
  → `"<bucket>.<index>"`（如 `objectives.0`），text 取 `PlanItem.text`；items 恒空（单条项无嵌套列表）。
- 阶段块 `lesson_flow` → `"stage.<stage>"`（`LessonPlanStage.stage` 枚举值，地址即身份），
  text 取 `title`，items 取 `items[].text`。
- 分层支持 `stratified_support` → `"support.<group>"`，text 取 `label`，items 取 `items[].text`。
- 诊断响应 `diagnostic_response` → `"diagnostic.<index>"`，text 取 `response`；items 恒空。
- §F 六章（additive）：`homework`/`reflection` → `"<bucket>.<index>"`（同列表块，text 取 `PlanItem.text`）；
  板书设计 `board_design` → `"board_design.<sub>.<index>"`（sub ∈ text_points / formulas，
  text 取 `PlanItem.text`；math_object 为参数化图形事实，不开放块级 patch）。
- 元数据无块地址：只能用 `set_field` 且 `target="metadata"`
  （field ∈ title / duration_minutes / grade / lesson_scope）。

**不变量**
- INV-1 乐观并发：`expected_revision` 不符 → `RevisionConflictError`，且零副作用（原文逐字节不变）；
- INV-2 局部性：一次 patch 只改声明的 block，其余块的模型序列化值逐字节不变；
- INV-3 Blueprint 不可变：`projection`/`blueprint_ref`/`context_ref`/`schema_version`/`id`/`revision`
  以及系统维护字段（purpose/change_log/source_refs）一律拒改；
- INV-4/5 由上层承接（proposal 表状态机 + `tp_lesson_plan_revisions` 快照），本模块只做确定性应用。

与 deck 侧 `app/ppt_v2/domain/patch.py` 同风格：先全量预校验，再在副本上逐 op 应用，
失败时原始 spec 不被触碰。预校验以提交时的 spec 为准，因此同一 proposal 内不支持
「先 add_block 再改同一块」（与 deck 的 add_slide+set_slide 语义一致）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .lesson_plan_schema import (
    ChangeLogEntry,
    DiagnosticResponse,
    LessonPlanMetadata,
    LessonPlanSpec,
    LessonPlanStage,
    PlanItem,
    StratifiedSupport,
)


class RevisionConflictError(Exception):
    """乐观并发冲突：调用方携带的 expected_revision 已过期（零副作用，可基于最新版本重放）。"""

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"revision 冲突：期望 {expected}，当前 {actual}（请基于最新 revision 重放 patch）")


class PatchValidationError(Exception):
    """proposal 结构或语义非法（目标块不存在、字段不可改、值不满足领域约束）。"""


# ── 契约模型 ─────────────────────────────────────────────────────────────────


class AffectedBlockSet(BaseModel):
    """一次 patch 的影响面（INV-2 的声明）：只允许这些块被重算/重渲染。"""

    model_config = ConfigDict(extra="ignore")

    block_ids: list[str] = Field(default_factory=list)
    whole_plan: bool = False  # 文档级变更（元数据/标题/时长），落到任何单个块之外的头部与分页
    rationale: str = ""


class LessonPlanPatchOp(BaseModel):
    """单项语义操作。"""

    model_config = ConfigDict(extra="ignore")

    op: Literal["set_block", "add_block", "remove_block", "set_field"]
    target: str | None = Field(default=None, min_length=1)  # block_id / "metadata"
    field: str | None = Field(default=None, min_length=1)  # text | items | 元数据字段名
    value: Any = None


class LessonPlanPatchProposal(BaseModel):
    """一次修改提案：spec_id + expected_revision（乐观并发门票）+ 操作列表。"""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    spec_id: str = Field(min_length=1)
    expected_revision: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=300)  # 直接落 change_log.summary（同上限）
    source: Literal["human", "ai", "regen"] = "human"
    created_by: str | None = None
    operations: list[LessonPlanPatchOp] = Field(min_length=1)


class LessonPlanPatch(BaseModel):
    """一次成功 apply 的结果元数据（含影响面，供上层追踪/replay）。"""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    spec_id: str = Field(min_length=1)
    base_revision: int = Field(ge=1)
    result_revision: int = Field(ge=1)
    summary: str = Field(min_length=1)
    applied_at: datetime
    affected: AffectedBlockSet


def new_patch_id() -> str:
    return str(uuid.uuid4())


# ── block 编址 ───────────────────────────────────────────────────────────────

_LIST_BUCKETS: tuple[str, ...] = (
    "objectives",
    "key_difficult_points",
    "activities",
    "examples",
    "assessment",
    "evidence",
    # §F 六章 additive：作业布置 / 教学反思（同列表块编址 <bucket>.<index>）
    "homework",
    "reflection",
)

# 板书设计（§F.3）：单对象三栏，可 patch 的子列表（中列 math_object 为参数化图形事实，不开放）
_BOARD_SUBFIELDS: tuple[str, ...] = ("text_points", "formulas")

# 块枚举顺序 = 教案文档顺序（UI 列表与导出章节同序），且跨 revision 稳定
# （§F 六章块追加在尾部：不改变既有块地址的顺序语义，additive 安全）
_BLOCK_ORDER: tuple[str, ...] = (
    "objectives",
    "key_difficult_points",
    "lesson_flow",
    "activities",
    "examples",
    "stratified_support",
    "diagnostic_response",
    "assessment",
    "evidence",
    "board_design",
    "homework",
    "reflection",
)

_ID_PREFIX: dict[str, str] = {
    "objectives": "objectives",
    "key_difficult_points": "key_difficult_points",
    "activities": "activities",
    "examples": "examples",
    "assessment": "assessment",
    "evidence": "evidence",
    "lesson_flow": "stage",
    "stratified_support": "support",
    "diagnostic_response": "diagnostic",
    "board_design": "board_design",
    "homework": "homework",
    "reflection": "reflection",
}
_PREFIX_BUCKET: dict[str, str] = {v: k for k, v in _ID_PREFIX.items()}

# 索引编址桶：删除会使同桶后续块地址平移（stage/support 以值编址，不受影响）
_INDEX_BUCKETS: tuple[str, ...] = (*_LIST_BUCKETS, "diagnostic_response")

_BUCKET_LABEL: dict[str, str] = {
    "objectives": "教学目标",
    "key_difficult_points": "重难点",
    "activities": "活动",
    "examples": "例题",
    "assessment": "评价",
    "evidence": "依据",
    "board_design": "板书设计",
    "homework": "作业布置",
    "reflection": "教学反思",
}
_GROUP_LABEL: dict[str, str] = {"foundation": "基础", "standard": "标准", "advanced": "进阶"}

# text 的落点：块的主文本字段（其余桶为 PlanItem.text）
_TEXT_FIELDS: dict[str, str] = {
    "lesson_flow": "title",
    "stratified_support": "label",
    "diagnostic_response": "response",
}

# 无块地址、只能经 set_field 改的元数据字段（白名单）
_METADATA_FIELDS: frozenset[str] = frozenset({"title", "duration_minutes", "grade", "lesson_scope"})

# 系统维护事实：patch 一律不得触碰（INV-3 + 血缘/审计完整性）
_IMMUTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "projection",
        "blueprint_ref",
        "context_ref",
        "schema_version",
        "id",
        "revision",
        "purpose",
        "change_log",
        "source_refs",
    }
)

_CHANGE_LOG_KIND: dict[str, str] = {"human": "patch", "ai": "patch", "regen": "regen"}


@dataclass(frozen=True)
class _BlockRef:
    """解析后的块地址：bucket 为 spec 字段名，prefix 为 block_id 前缀。"""

    bucket: str
    prefix: str
    key: str

    def block_id(self, index: int) -> str:
        return f"{self.prefix}.{index}"


def _parse_block_id(block_id: str) -> _BlockRef:
    prefix, sep, key = block_id.partition(".")
    bucket = _PREFIX_BUCKET.get(prefix)
    if not sep or not key or bucket is None:
        raise PatchValidationError(
            f"非法 block_id={block_id!r}（形如 objectives.0 / stage.new_content / "
            "support.foundation / diagnostic.0 / reflection.0 / board_design.text_points.0）"
        )
    return _BlockRef(bucket=bucket, prefix=prefix, key=key)


def _board_parts(key: str) -> tuple[str, int]:
    """解析板书设计块地址的子栏与索引：`"text_points.0"` → ("text_points", 0)。"""
    sub, sep, idx = key.partition(".")
    if not sep or sub not in _BOARD_SUBFIELDS:
        raise PatchValidationError(
            f"board_design 块地址形如 board_design.text_points.0 / board_design.formulas.0: {key!r}"
        )
    return sub, _parse_index(idx)


def _text_field(bucket: str) -> str:
    return _TEXT_FIELDS.get(bucket, "text")


def lesson_plan_blocks(spec: LessonPlanSpec) -> list[dict[str, Any]]:
    """稳定的 block 枚举（UI 与 diff 共用）：顺序与数量只由教案内容决定，与 revision 无关。"""
    blocks: list[dict[str, Any]] = []
    for bucket in _BLOCK_ORDER:
        if bucket == "lesson_flow":
            for i, stage in enumerate(spec.lesson_flow):
                blocks.append(
                    {
                        "block_id": f"{_ID_PREFIX[bucket]}.{stage.stage}",
                        "kind": bucket,
                        "label_zh": f"教学环节 {i + 1}",
                        "text": stage.title,
                        "items": [item.text for item in stage.items],
                    }
                )
        elif bucket == "stratified_support":
            for support in spec.stratified_support:
                label = _GROUP_LABEL.get(support.group, support.group)
                blocks.append(
                    {
                        "block_id": f"{_ID_PREFIX[bucket]}.{support.group}",
                        "kind": bucket,
                        "label_zh": f"分层支持（{label}）",
                        "text": support.label,
                        "items": [item.text for item in support.items],
                    }
                )
        elif bucket == "board_design":
            # §F.3 板书设计：文字要点/公式两个子栏逐条成块（中列 math_object 不开放块级 patch）
            for i, item in enumerate(spec.board_design.text_points):
                blocks.append(
                    {
                        "block_id": f"board_design.text_points.{i}",
                        "kind": bucket,
                        "label_zh": f"板书设计·文字要点 {i + 1}",
                        "text": item.text,
                        "items": [],
                    }
                )
            for i, item in enumerate(spec.board_design.formulas):
                blocks.append(
                    {
                        "block_id": f"board_design.formulas.{i}",
                        "kind": bucket,
                        "label_zh": f"板书设计·公式 {i + 1}",
                        "text": item.text,
                        "items": [],
                    }
                )
        elif bucket == "diagnostic_response":
            for i, diag in enumerate(spec.diagnostic_response):
                blocks.append(
                    {
                        "block_id": f"{_ID_PREFIX[bucket]}.{i}",
                        "kind": bucket,
                        "label_zh": f"诊断响应 {i + 1}",
                        "text": diag.response,
                        "items": [],
                    }
                )
        else:
            for i, item in enumerate(getattr(spec, bucket)):
                blocks.append(
                    {
                        "block_id": f"{_ID_PREFIX[bucket]}.{i}",
                        "kind": bucket,
                        "label_zh": f"{_BUCKET_LABEL[bucket]} {i + 1}",
                        "text": item.text,
                        "items": [],
                    }
                )
    return blocks


# ── 应用入口 ─────────────────────────────────────────────────────────────────


def apply_proposal(
    spec: LessonPlanSpec, proposal: LessonPlanPatchProposal, *, patch_id: str | None = None
) -> tuple[LessonPlanSpec, LessonPlanPatch, dict[str, Any]]:
    """在副本上应用 proposal；成功则返回 (新 spec, patch 元数据, change_log 追加条目 dict)。

    - 乐观并发：spec.revision != expected_revision → RevisionConflictError（零副作用）；
    - 任一 op 非法 → PatchValidationError（spec 保持不变，调用方可整体放弃）；
    - 第三项为 change_log 条目 dict（mode="json"），供上层原样写响应/revision 快照。
    """
    proposal = _revalidated(proposal)

    if spec.revision != proposal.expected_revision:
        raise RevisionConflictError(expected=proposal.expected_revision, actual=spec.revision)

    if spec.id != proposal.spec_id:
        raise PatchValidationError(f"spec_id 不匹配：proposal={proposal.spec_id} spec={spec.id}")

    try:
        return _apply_validated(spec, proposal, patch_id=patch_id)
    except PatchValidationError:
        raise
    except ValidationError as exc:
        # 底层 pydantic 校验失败（值不满足领域约束）统一映射到领域分类
        raise PatchValidationError(f"patch 未通过领域校验（spec={proposal.spec_id}）: {exc}") from exc


def _revalidated(proposal: LessonPlanPatchProposal) -> LessonPlanPatchProposal:
    """写前重放 schema 校验。

    proposal 常来自 `tp_lesson_plan_edit_proposals.operations`（JSONB 反序列化）或手工构造，
    未知 op 必须在落库/改动 spec 之前被拒绝，且不得以裸 ValidationError 穿透。
    """
    if not isinstance(proposal, LessonPlanPatchProposal):
        raise PatchValidationError(f"proposal 类型非法: {type(proposal).__name__}")
    try:
        payload = proposal.model_dump()
    except Exception as exc:  # pragma: no cover - model_dump 对合法模型不会失败
        raise PatchValidationError(f"proposal 无法序列化校验: {exc}") from exc
    try:
        return LessonPlanPatchProposal.model_validate(payload)
    except ValidationError as exc:
        raise PatchValidationError(f"proposal 未通过 schema 校验: {exc}") from exc


def _apply_validated(
    spec: LessonPlanSpec, proposal: LessonPlanPatchProposal, *, patch_id: str | None
) -> tuple[LessonPlanSpec, LessonPlanPatch, dict[str, Any]]:
    # 先预校验全部 op（以提交时的 spec 为准），保证任一 op 非法时整体不落
    for op in proposal.operations:
        _validate_op(spec, op)

    builder = _PatchBuilder(spec)
    for op in proposal.operations:
        builder.apply(op)

    new_spec = builder.spec
    # 全部 op 应用后统一做域级完整性校验（长度上限/枚举/引用合法等），失败即整体放弃
    try:
        new_spec = LessonPlanSpec.model_validate(new_spec.model_dump())
    except ValidationError as exc:
        raise PatchValidationError(f"patch 应用后教案 schema 被破坏: {exc}") from exc

    new_revision = spec.revision + 1
    now = datetime.now(UTC)
    log_entry = ChangeLogEntry(
        revision=new_revision,
        ts=now,
        kind=_CHANGE_LOG_KIND[proposal.source],
        summary=proposal.summary,
    )
    final_spec = new_spec.model_copy(
        update={"revision": new_revision, "change_log": [*new_spec.change_log, log_entry]},
        deep=True,
    )
    patch = LessonPlanPatch(
        id=patch_id or new_patch_id(),
        spec_id=proposal.spec_id,
        base_revision=spec.revision,
        result_revision=new_revision,
        summary=proposal.summary,
        applied_at=now,
        affected=AffectedBlockSet(
            block_ids=sorted(builder.changed_block_ids),
            whole_plan=builder.whole_plan,
            rationale=proposal.summary,
        ),
    )
    return final_spec, patch, log_entry.model_dump(mode="json")


# ─────────────────────────────────────────────────────────────────────────────
# op 预校验（不产生副作用）
# ─────────────────────────────────────────────────────────────────────────────


def _reject_immutable(op: LessonPlanPatchOp) -> None:
    if op.field in _IMMUTABLE_FIELDS:
        raise PatchValidationError(
            f"字段 {op.field!r} 是系统维护事实，禁止 patch 修改"
            "（INV-3：projection/blueprint_ref/context_ref/schema_version/id/revision）"
        )


def _validate_op(spec: LessonPlanSpec, op: LessonPlanPatchOp) -> None:
    _reject_immutable(op)

    if op.op == "set_field":
        _validate_set_field(spec, op)
    elif op.op == "set_block":
        _validate_set_block(spec, op)
    elif op.op == "add_block":
        _validate_add_block(spec, op)
    elif op.op == "remove_block":
        _resolve_existing(spec, op.target)  # 目标必须已存在
    else:
        raise PatchValidationError(f"未知 op={op.op!r}")


def _validate_set_field(spec: LessonPlanSpec, op: LessonPlanPatchOp) -> None:
    if op.target != "metadata":
        raise PatchValidationError('set_field 只改元数据：target 必须为 "metadata"（块内容请用 set_block）')
    if op.field not in _METADATA_FIELDS:
        raise PatchValidationError(
            f"set_field 字段 {op.field!r} 不在元数据白名单 {sorted(_METADATA_FIELDS)}"
        )
    try:
        LessonPlanMetadata.model_validate({**spec.metadata.model_dump(), op.field: op.value})
    except ValidationError as exc:
        raise PatchValidationError(f"set_field.{op.field} 非法: {exc}") from exc


def _validate_set_block(spec: LessonPlanSpec, op: LessonPlanPatchOp) -> None:
    ref = _resolve_existing(spec, op.target)
    if op.field not in ("text", "items"):
        raise PatchValidationError(
            f"set_block 字段 {op.field!r} 非法：只允许 text（主文本）或 items（块内条目列表）"
        )
    if (ref.bucket in _INDEX_BUCKETS or ref.bucket == "board_design") and op.field == "items":
        # 索引编址桶与板书设计子栏的块是单条 PlanItem/DiagnosticResponse，本身没有 items 层
        raise PatchValidationError(
            f"块 {op.target!r} 是单条内容项，没有 items（请用 field=\"text\" 改主文本）"
        )
    if op.field == "items":
        _rebuild_items(_existing_block_items(spec, ref), op.value)
        return

    try:
        _rebuild_block_with_text(spec, ref, op.value)
    except ValidationError as exc:
        raise PatchValidationError(f"set_block.text 非法（block={op.target}）: {exc}") from exc


def _validate_add_block(spec: LessonPlanSpec, op: LessonPlanPatchOp) -> None:
    if not op.target:
        raise PatchValidationError("add_block 必须提供 target=新块 block_id")
    ref = _parse_block_id(op.target)

    if ref.bucket in _INDEX_BUCKETS:
        index = _parse_index(ref.key)
        current = len(getattr(spec, ref.bucket))
        if index != current:
            # 保序插入会平移后续块地址（INV-2 局部性），因此只允许追加
            raise PatchValidationError(
                f"add_block 只支持追加：{op.target!r} 的 index 必须等于当前块数 {current}"
            )
        try:
            _build_new_block(ref, op.value, key=ref.key, index=index)
        except ValidationError as exc:
            raise PatchValidationError(f"add_block.{ref.prefix} 非法: {exc}") from exc
        return

    if ref.bucket == "lesson_flow":
        if any(stage.stage == ref.key for stage in spec.lesson_flow):
            raise PatchValidationError(f"教学环节 {ref.key!r} 已存在（stage 即块地址，不能重复）")
        try:
            _build_new_block(ref, op.value, key=ref.key, index=None)
        except ValidationError as exc:
            raise PatchValidationError(f"add_block.stage 非法: {exc}") from exc
        return

    if ref.bucket == "board_design":
        sub, index = _board_parts(ref.key)
        current_len = len(getattr(spec.board_design, sub))
        if index != current_len:
            # 保序插入会平移后续块地址（INV-2 局部性），因此只允许追加
            raise PatchValidationError(
                f"add_block 只支持追加：{op.target!r} 的 index 必须等于当前块数 {current_len}"
            )
        try:
            _build_new_block(ref, op.value, key=ref.key, index=index)
        except ValidationError as exc:
            raise PatchValidationError(f"add_block.{ref.prefix}.{sub} 非法: {exc}") from exc
        return

    # stratified_support
    if any(support.group == ref.key for support in spec.stratified_support):
        raise PatchValidationError(f"分层支持 {ref.key!r} 已存在（group 即块地址，不能重复）")
    try:
        _build_new_block(ref, op.value, key=ref.key, index=None)
    except ValidationError as exc:
        raise PatchValidationError(f"add_block.support 非法: {exc}") from exc


# ─────────────────────────────────────────────────────────────────────────────
# 块定位与值构造（预校验与 apply 共用同一套构造，避免两处语义漂移）
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_existing(spec: LessonPlanSpec, block_id: str | None) -> _BlockRef:
    if not block_id:
        raise PatchValidationError("必须提供 target=block_id")
    ref = _parse_block_id(block_id)
    if not any(block["block_id"] == block_id for block in lesson_plan_blocks(spec)):
        raise PatchValidationError(f"block {block_id!r} 不存在（该教案当前无此块）")
    if not _is_unique(spec, ref):
        # stage/support 以值编址：同值重复会让地址产生歧义，必须先由上层修数据
        raise PatchValidationError(f"block {block_id!r} 地址不唯一（同值块重复），无法定位")
    _find_index(spec, ref)  # 预校验阶段即做越界检查
    return ref


def _is_unique(spec: LessonPlanSpec, ref: _BlockRef) -> bool:
    if ref.bucket == "lesson_flow":
        return sum(1 for s in spec.lesson_flow if s.stage == ref.key) <= 1
    if ref.bucket == "stratified_support":
        return sum(1 for s in spec.stratified_support if s.group == ref.key) <= 1
    return True


def _parse_index(key: str) -> int:
    try:
        index = int(key)
    except ValueError as exc:
        raise PatchValidationError(f"block_id 索引必须是整数: {key!r}") from exc
    if index < 0:
        raise PatchValidationError(f"block_id 索引不得为负: {key!r}")
    return index


def _find_index(spec: LessonPlanSpec, ref: _BlockRef) -> int:
    if ref.bucket == "lesson_flow":
        for i, stage in enumerate(spec.lesson_flow):
            if stage.stage == ref.key:
                return i
        raise PatchValidationError(f"教学环节 {ref.key!r} 不存在")
    if ref.bucket == "stratified_support":
        for i, support in enumerate(spec.stratified_support):
            if support.group == ref.key:
                return i
        raise PatchValidationError(f"分层支持 {ref.key!r} 不存在")
    if ref.bucket == "board_design":
        sub, index = _board_parts(ref.key)
        if index >= len(getattr(spec.board_design, sub)):
            raise PatchValidationError(f"block 索引越界: {ref.key!r}")
        return index
    index = _parse_index(ref.key)
    if index >= len(getattr(spec, ref.bucket)):
        raise PatchValidationError(f"block 索引越界: {ref.key!r}")
    return index


def _current_block(spec: LessonPlanSpec, ref: _BlockRef, index: int) -> Any:
    if ref.bucket == "board_design":
        sub, _ = _board_parts(ref.key)
        return getattr(spec.board_design, sub)[index]
    return getattr(spec, ref.bucket)[index]


def _existing_block_items(spec: LessonPlanSpec, ref: _BlockRef) -> list[PlanItem]:
    return list(_current_block(spec, ref, _find_index(spec, ref)).items)


def _rebuild_items(existing: list[PlanItem], value: Any) -> list[PlanItem]:
    """value 中缺 source_ref/evidence_refs 的条目按位置继承旧值（保住 SourceLocator 可追溯性）。"""
    if not isinstance(value, list):
        raise PatchValidationError('set_block.field="items" 的 value 必须是列表')
    out: list[PlanItem] = []
    for i, raw in enumerate(value):
        old = existing[i] if i < len(existing) else None
        if isinstance(raw, str):
            payload: dict[str, Any] = {"text": raw}
        elif isinstance(raw, dict):
            payload = dict(raw)
        else:
            raise PatchValidationError(f"items[{i}] 必须是字符串或 PlanItem dict")
        if old is not None:
            payload.setdefault("source_ref", old.source_ref)
            payload.setdefault("evidence_refs", list(old.evidence_refs))
        out.append(PlanItem.model_validate(payload))
    return out


def _plan_item_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"text": value}
    if isinstance(value, dict):
        return dict(value)
    raise PatchValidationError("内容项 value 必须是字符串或 PlanItem dict")


def _rebuild_block_with_text(spec: LessonPlanSpec, ref: _BlockRef, value: Any) -> Any:
    """按块类型把新主文本落到对应字段（PlanItem.text / stage.title / support.label / response）。"""
    index = _find_index(spec, ref)
    current = _current_block(spec, ref, index)
    return type(current).model_validate({**current.model_dump(), _text_field(ref.bucket): value})


def _build_new_block(ref: _BlockRef, value: Any, *, key: str, index: int | None) -> Any:
    """由 op.value 构造新块（地址即身份：stage/group 强制与 block_id 一致）。"""
    if ref.bucket == "lesson_flow":
        if not isinstance(value, dict):
            raise PatchValidationError("add_block stage.<stage> 的 value 必须是 LessonPlanStage 字段 dict")
        return LessonPlanStage.model_validate({"stage": key, **value, "items": value.get("items", [])})
    if ref.bucket == "stratified_support":
        if not isinstance(value, dict):
            raise PatchValidationError("add_block support.<group> 的 value 必须是 StratifiedSupport 字段 dict")
        return StratifiedSupport.model_validate({"group": key, **value, "items": value.get("items", [])})
    if ref.bucket == "diagnostic_response":
        if not isinstance(value, dict):
            raise PatchValidationError("add_block diagnostic.<index> 的 value 必须是 DiagnosticResponse 字段 dict")
        return DiagnosticResponse.model_validate(dict(value))
    assert index is not None
    return PlanItem.model_validate(_plan_item_payload(value))


# ─────────────────────────────────────────────────────────────────────────────
# 逐 op 应用（在副本上构造，原始 spec 永不被触碰）
# ─────────────────────────────────────────────────────────────────────────────


class _PatchBuilder:
    """在 LessonPlanSpec 副本上应用 ops 并累积受影响块地址。"""

    def __init__(self, spec: LessonPlanSpec) -> None:
        self.spec = spec
        self.changed_block_ids: set[str] = set()
        self.whole_plan = False

    def apply(self, op: LessonPlanPatchOp) -> None:
        if op.op == "set_field":
            self._apply_set_field(op)
        elif op.op == "set_block":
            self._apply_set_block(op)
        elif op.op == "add_block":
            self._apply_add_block(op)
        else:
            self._apply_remove_block(op)

    def _apply_set_field(self, op: LessonPlanPatchOp) -> None:
        merged = {**self.spec.metadata.model_dump(), op.field: op.value}  # type: ignore[index]
        self.spec = self.spec.model_copy(
            update={"metadata": LessonPlanMetadata.model_validate(merged)}, deep=True
        )
        # 元数据落在文档头部/分页，不属于任何块：以 whole_plan 声明文档级影响面
        self.whole_plan = True

    def _apply_set_block(self, op: LessonPlanPatchOp) -> None:
        assert op.target is not None
        ref = _parse_block_id(op.target)
        index = _find_index(self.spec, ref)
        current = _current_block(self.spec, ref, index)

        if ref.bucket == "board_design":
            # 板书设计：容器是 spec.board_design 的子栏列表（单条 PlanItem，只改 text）
            sub, _ = _board_parts(ref.key)
            if not isinstance(op.value, str):
                raise PatchValidationError(f"set_block.text 的 value 必须是字符串（block={op.target}）")
            items = list(getattr(self.spec.board_design, sub))
            items[index] = type(current).model_validate({**current.model_dump(), "text": op.value})
            self.spec = self.spec.model_copy(
                update={
                    "board_design": self.spec.board_design.model_copy(update={sub: items}, deep=True)
                },
                deep=True,
            )
            self.changed_block_ids.add(op.target)
            return

        if op.field == "items":
            update_field = "items"
            payload: Any = _rebuild_items(_existing_block_items(self.spec, ref), op.value)
        else:
            if not isinstance(op.value, str):
                raise PatchValidationError(f"set_block.text 的 value 必须是字符串（block={op.target}）")
            update_field = _text_field(ref.bucket)
            payload = op.value

        blocks = list(getattr(self.spec, ref.bucket))
        blocks[index] = type(current).model_validate({**current.model_dump(), update_field: payload})
        self.spec = self.spec.model_copy(update={ref.bucket: blocks}, deep=True)
        self.changed_block_ids.add(op.target)

    def _apply_add_block(self, op: LessonPlanPatchOp) -> None:
        assert op.target is not None
        ref = _parse_block_id(op.target)
        if ref.bucket in _INDEX_BUCKETS:
            index = _parse_index(ref.key)
            new_block = _build_new_block(ref, op.value, key=ref.key, index=index)
            current = list(getattr(self.spec, ref.bucket))
            self.spec = self.spec.model_copy(update={ref.bucket: [*current, new_block]}, deep=True)
        elif ref.bucket == "board_design":
            # 板书设计子栏追加：容器是 spec.board_design 的子栏列表
            sub, index = _board_parts(ref.key)
            new_block = _build_new_block(ref, op.value, key=ref.key, index=index)
            current = list(getattr(self.spec.board_design, sub))
            self.spec = self.spec.model_copy(
                update={
                    "board_design": self.spec.board_design.model_copy(
                        update={sub: [*current, new_block]}, deep=True
                    )
                },
                deep=True,
            )
        elif ref.bucket == "lesson_flow":
            new_stage = _build_new_block(ref, op.value, key=ref.key, index=None)
            self.spec = self.spec.model_copy(
                update={"lesson_flow": [*self.spec.lesson_flow, new_stage]}, deep=True
            )
        else:
            new_support = _build_new_block(ref, op.value, key=ref.key, index=None)
            self.spec = self.spec.model_copy(
                update={"stratified_support": [*self.spec.stratified_support, new_support]}, deep=True
            )
        self.changed_block_ids.add(op.target)

    def _apply_remove_block(self, op: LessonPlanPatchOp) -> None:
        assert op.target is not None
        ref = _parse_block_id(op.target)
        index = _find_index(self.spec, ref)

        if ref.bucket == "lesson_flow":
            stages = [s for i, s in enumerate(self.spec.lesson_flow) if i != index]
            self.spec = self.spec.model_copy(update={"lesson_flow": stages}, deep=True)
        elif ref.bucket == "board_design":
            # 板书设计子栏删除：同子栏后续块地址整体平移，旧地址失效 → 一并声明受影响
            sub, index = _board_parts(ref.key)
            items = list(getattr(self.spec.board_design, sub))
            for shifted in range(index + 1, len(items)):
                self.changed_block_ids.add(f"board_design.{sub}.{shifted}")
            del items[index]
            self.spec = self.spec.model_copy(
                update={
                    "board_design": self.spec.board_design.model_copy(update={sub: items}, deep=True)
                },
                deep=True,
            )
        elif ref.bucket == "stratified_support":
            supports = [s for i, s in enumerate(self.spec.stratified_support) if i != index]
            self.spec = self.spec.model_copy(update={"stratified_support": supports}, deep=True)
        else:
            blocks = list(getattr(self.spec, ref.bucket))
            # 索引编址桶：同桶后续块地址整体平移，旧地址失效 → 一并声明受影响，UI 需重排
            for shifted in range(index + 1, len(blocks)):
                self.changed_block_ids.add(ref.block_id(shifted))
            del blocks[index]
            self.spec = self.spec.model_copy(update={ref.bucket: blocks}, deep=True)

        self.changed_block_ids.add(op.target)


__all__ = [
    "AffectedBlockSet",
    "LessonPlanPatch",
    "LessonPlanPatchOp",
    "LessonPlanPatchProposal",
    "PatchValidationError",
    "RevisionConflictError",
    "apply_proposal",
    "lesson_plan_blocks",
    "new_patch_id",
]
