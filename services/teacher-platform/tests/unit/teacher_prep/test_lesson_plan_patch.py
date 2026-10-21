"""T24 教案 block 级 patch：乐观并发(INV-1) / 局部性(INV-2) / Blueprint 不可改(INV-3) / 块编址稳定性。

测试自带确定性 spec 构造（不依赖 DB / 投影器），逐字节断言「只改声明块」。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.teacher_prep.specs.lesson_plan_schema import (
    BlueprintRef,
    ChangeLogEntry,
    ContextRef,
    DiagnosticResponse,
    LessonPlanMetadata,
    LessonPlanSpec,
    LessonPlanStage,
    PlanItem,
    SpecProjectionRef,
    StratifiedSupport,
)
from app.teacher_prep.specs.patch import (
    LessonPlanPatchOp,
    LessonPlanPatchProposal,
    PatchValidationError,
    RevisionConflictError,
    apply_proposal,
    lesson_plan_blocks,
)

SPEC_ID = "11111111-1111-1111-1111-111111111111"
OTHER_SPEC_ID = "99999999-9999-9999-9999-999999999999"
BP_ID = "22222222-2222-2222-2222-222222222222"
SOURCE_ID = "33333333-3333-3333-3333-333333333333"
SECTION_ID = "44444444-4444-4444-4444-444444444444"
CTX_HASH = "a" * 64
NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

_LIST_BUCKETS = (
    "objectives",
    "key_difficult_points",
    "activities",
    "examples",
    "assessment",
    "evidence",
)


def _ref(n: int) -> str:
    return f"tb://v1/abc/sections/sec-1#chunk-{n:02d}"


def _spec(*, revision: int = 3, objectives: int = 3, long_flow: bool = False) -> LessonPlanSpec:
    """确定性教案：全部桶非空（覆盖每种 block_id 前缀）。"""
    lesson_flow = [
        LessonPlanStage(
            stage="introduction",
            title="情境导入",
            duration_minutes=5,
            items=[
                PlanItem(text="从生活实例引入集合", source_ref=_ref(3), evidence_refs=["finding-1"]),
            ],
        ),
        LessonPlanStage(
            stage="new_content",
            title="新知探究",
            duration_minutes=25,
            items=[PlanItem(text="元素的确定性/互异性/无序性", source_ref=_ref(4))],
        ),
    ]
    if long_flow:
        # 真实分页证据：6 个环节 × 20 条目（均为合法 stage 值，地址唯一）
        lesson_flow = [
            LessonPlanStage(
                stage=stage,
                title=f"环节 {i}：{stage} 专项推进",
                duration_minutes=8,
                items=[
                    PlanItem(
                        text=(
                            f"第 {i}-{j} 条课堂任务：请学生独立完成列举法表示，"
                            f"并在小组内互评定义域与元素归属关系是否清晰（第 {j} 组）"
                        ),
                        source_ref=_ref(200 + i * 20 + j),
                    )
                    for j in range(20)
                ],
            )
            for i, stage in enumerate(
                ("introduction", "new_content", "example", "exercise", "summary", "homework"), start=1
            )
        ]
    return LessonPlanSpec(
        id=SPEC_ID,
        revision=revision,
        metadata=LessonPlanMetadata(
            title="1.1 集合的概念 教案", grade="高一", duration_minutes=45, lesson_scope="集合的概念"
        ),
        blueprint_ref=BlueprintRef(blueprint_id=BP_ID, revision=2),
        context_ref=ContextRef(
            context_snapshot_hash=CTX_HASH,
            source_id=SOURCE_ID,
            section_id=SECTION_ID,
            locator="tb://v1/abc/sections/sec-1",
        ),
        objectives=[
            PlanItem(
                text=f"目标 {i}：理解集合与元素的关系",
                source_ref=_ref(i) if i % 2 == 0 else None,
                evidence_refs=["finding-1"] if i == 0 else [],
            )
            for i in range(objectives)
        ],
        key_difficult_points=[PlanItem(text="重点：集合的三条性质", source_ref=_ref(2))],
        lesson_flow=lesson_flow,
        activities=[PlanItem(text="小组列举集合实例", source_ref=_ref(5))],
        examples=[PlanItem(text="例题 1：用列举法表示集合", source_ref=_ref(6))],
        stratified_support=[
            StratifiedSupport(
                group="foundation",
                label="基础支持",
                items=[PlanItem(text="提供半成品填空支架", source_ref=_ref(7))],
            )
        ],
        diagnostic_response=[
            DiagnosticResponse(finding_ref="finding-1", response="先补元素概念", source_ref=_ref(8))
        ],
        assessment=[PlanItem(text="当堂检测 3 题", source_ref=_ref(9))],
        evidence=[PlanItem(text="finding-1 明细", source_ref=_ref(10), evidence_refs=["finding-1"])],
        warnings=["合成数据"],
        source_refs=[_ref(3), _ref(4)],
        projection=SpecProjectionRef(
            projector="teacher_prep.specs.v1",
            approved_blueprint_id=BP_ID,
            approved_blueprint_revision=2,
            context_snapshot_hash=CTX_HASH,
            run_hash="b" * 64,
            body_hash="c" * 64,
            projected_at=NOW,
        ),
        change_log=[ChangeLogEntry(revision=1, ts=NOW, kind="create", summary="从 approved blueprint 投影")],
    )


def _proposal(
    expected_revision: int,
    ops: list[LessonPlanPatchOp],
    *,
    summary: str = "微调教学目标",
    spec_id: str = SPEC_ID,
    source: str = "human",
) -> LessonPlanPatchProposal:
    return LessonPlanPatchProposal(
        spec_id=spec_id, expected_revision=expected_revision, summary=summary, source=source, operations=ops
    )


def _fingerprints(spec: LessonPlanSpec) -> dict[str, str]:
    """每个块的模型级序列化值（block_id 编址与 lesson_plan_blocks 完全一致）。"""
    out: dict[str, str] = {}
    for bucket in _LIST_BUCKETS:
        for i, item in enumerate(getattr(spec, bucket)):
            out[f"{bucket}.{i}"] = item.model_dump_json()
    for stage in spec.lesson_flow:
        out[f"stage.{stage.stage}"] = stage.model_dump_json()
    for support in spec.stratified_support:
        out[f"support.{support.group}"] = support.model_dump_json()
    for i, diag in enumerate(spec.diagnostic_response):
        out[f"diagnostic.{i}"] = diag.model_dump_json()
    return out


def _block_texts(spec: LessonPlanSpec) -> dict[str, str]:
    return {
        b["block_id"]: json.dumps(b, ensure_ascii=False, sort_keys=True) for b in lesson_plan_blocks(spec)
    }


# ─────────────────────────────────────────────────────────────────────────────
# INV-1 乐观并发：冲突零副作用
# ─────────────────────────────────────────────────────────────────────────────


def test_expected_revision_mismatch_conflict_without_side_effect() -> None:
    spec = _spec()  # revision=3
    before = spec.model_dump_json()
    before_dump = spec.model_dump()

    with pytest.raises(RevisionConflictError) as exc:
        apply_proposal(
            spec,
            _proposal(2, [LessonPlanPatchOp(op="set_block", target="objectives.0", field="text", value="改")]),
        )

    assert exc.value.expected == 2
    assert exc.value.actual == 3
    assert spec.model_dump() == before_dump  # 逐字节（结构级）不变
    assert spec.model_dump_json() == before  # 串行化也不变
    assert spec.revision == 3
    assert len(spec.change_log) == 1


def test_spec_id_mismatch_rejected() -> None:
    spec = _spec()
    with pytest.raises(PatchValidationError, match="spec_id"):
        apply_proposal(
            spec,
            _proposal(
                3,
                [LessonPlanPatchOp(op="set_block", target="objectives.0", field="text", value="改")],
                spec_id=OTHER_SPEC_ID,
            ),
        )
    assert spec.model_dump_json() == _spec().model_dump_json()


# ─────────────────────────────────────────────────────────────────────────────
# INV-2 局部性：只改声明块，其余块逐字节不变
# ─────────────────────────────────────────────────────────────────────────────


def test_single_block_patch_leaves_all_other_blocks_identical() -> None:
    spec = _spec()
    blocks_before = _block_texts(spec)
    fps_before = _fingerprints(spec)
    assert set(blocks_before) == set(fps_before)  # 编址一致（UI 与 diff 共用）

    new_spec, patch, log = apply_proposal(
        spec,
        _proposal(
            3,
            [
                LessonPlanPatchOp(
                    op="set_block",
                    target="objectives.1",
                    field="text",
                    value="目标 1：能用列举法与描述法表示集合",
                )
            ],
            summary="改写第 2 条教学目标",
        ),
        patch_id="patch-lp-objectives-1",
    )

    assert new_spec.revision == 4
    assert patch.id == "patch-lp-objectives-1"
    assert patch.spec_id == SPEC_ID
    assert patch.base_revision == 3 and patch.result_revision == 4
    assert patch.affected.block_ids == ["objectives.1"]
    assert patch.affected.whole_plan is False
    assert patch.affected.rationale == "改写第 2 条教学目标"

    # 逐块断言：唯一变化的块就是被声明的那一块
    blocks_after = _block_texts(new_spec)
    assert set(blocks_after) == set(blocks_before)
    assert {k for k in blocks_before if blocks_before[k] != blocks_after[k]} == {"objectives.1"}
    fps_after = _fingerprints(new_spec)
    assert {k for k in fps_before if fps_before[k] != fps_after[k]} == {"objectives.1"}

    target = next(b for b in lesson_plan_blocks(new_spec) if b["block_id"] == "objectives.1")
    assert target["text"] == "目标 1：能用列举法与描述法表示集合"
    # 未声明的块：内容与可追溯信息原样保留
    assert next(b for b in lesson_plan_blocks(new_spec) if b["block_id"] == "objectives.0")["text"] == "目标 0：理解集合与元素的关系"
    assert fps_after["stage.new_content"] == fps_before["stage.new_content"]
    assert log["revision"] == 4 and log["kind"] == "patch"
    assert log["summary"] == "改写第 2 条教学目标"


def test_set_block_items_replaces_list_keeping_source_ref() -> None:
    spec = _spec()
    old_stage = next(s for s in spec.lesson_flow if s.stage == "new_content")
    assert old_stage.items[0].source_ref == _ref(4)

    new_spec, patch, _ = apply_proposal(
        spec,
        _proposal(
            3,
            [
                LessonPlanPatchOp(
                    op="set_block",
                    target="stage.new_content",
                    field="items",
                    value=[{"text": "元素的确定性"}, {"text": "元素的互异性", "source_ref": _ref(41)}],
                )
            ],
        ),
    )
    stage = next(s for s in new_spec.lesson_flow if s.stage == "new_content")
    assert [i.text for i in stage.items] == ["元素的确定性", "元素的互异性"]
    # 缺失的 source_ref/evidence_refs 按位置继承旧值（保住 SourceLocator 可追溯性）
    assert stage.items[0].source_ref == _ref(4)
    assert stage.items[0].evidence_refs == old_stage.items[0].evidence_refs
    assert stage.items[1].source_ref == _ref(41)
    assert stage.title == old_stage.title and stage.duration_minutes == old_stage.duration_minutes
    assert patch.affected.block_ids == ["stage.new_content"]


def test_set_block_items_on_single_item_block_rejected() -> None:
    spec = _spec()
    with pytest.raises(PatchValidationError, match="items"):
        apply_proposal(
            spec,
            _proposal(
                3,
                [LessonPlanPatchOp(op="set_block", target="objectives.0", field="items", value=["x"])],
            ),
        )
    assert spec.model_dump_json() == _spec().model_dump_json()


def test_add_and_remove_block_keeps_existing_ids_stable() -> None:
    spec = _spec()
    ids_before = [b["block_id"] for b in lesson_plan_blocks(spec)]

    added, patch_add, _ = apply_proposal(
        spec,
        _proposal(
            3,
            [
                LessonPlanPatchOp(
                    op="add_block",
                    target="objectives.3",
                    value={"text": "目标 3：体会集合语言的简洁性", "source_ref": _ref(11)},
                )
            ],
            summary="新增一条教学目标",
        ),
    )
    ids_after_add = [b["block_id"] for b in lesson_plan_blocks(added)]
    # 追加只影响同桶尾部（objectives 桶在枚举顺序中位于 key_difficult_points 之前）
    assert ids_after_add == [*ids_before[:3], "objectives.3", *ids_before[3:]]
    assert patch_add.affected.block_ids == ["objectives.3"]
    assert added.revision == 4

    removed, patch_rm, _ = apply_proposal(
        added,
        _proposal(4, [LessonPlanPatchOp(op="remove_block", target="objectives.0")], summary="删除第 1 条目标"),
    )
    ids_after_rm = [b["block_id"] for b in lesson_plan_blocks(removed)]
    # 索引即地址：删除后同桶后续块整体重编号（旧地址失效，见下面的 affected 声明）
    assert ids_after_rm == [
        *(f"objectives.{i}" for i in range(3)),
        *(i for i in ids_after_add if not i.startswith("objectives.")),
    ]
    # 同桶后续块编址平移：旧地址失效也算受影响（UI 需重排）
    assert patch_rm.affected.block_ids == ["objectives.0", "objectives.1", "objectives.2", "objectives.3"]
    assert removed.revision == 5
    assert len(removed.change_log) == 3


def test_add_block_appends_only_at_end() -> None:
    spec = _spec()
    with pytest.raises(PatchValidationError, match="追加"):
        apply_proposal(
            spec,
            _proposal(3, [LessonPlanPatchOp(op="add_block", target="objectives.1", value={"text": "插队"})]),
        )


# ─────────────────────────────────────────────────────────────────────────────
# INV-3 不可变字段 + 非法 op / 未知目标
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field",
    ["projection", "blueprint_ref", "context_ref", "schema_version", "id", "revision"],
)
def test_system_maintained_fields_rejected(field: str) -> None:
    spec = _spec()
    with pytest.raises(PatchValidationError, match="禁止"):
        apply_proposal(
            spec,
            _proposal(3, [LessonPlanPatchOp(op="set_field", target="metadata", field=field, value="x")]),
        )
    with pytest.raises(PatchValidationError, match="禁止"):
        apply_proposal(
            spec,
            _proposal(3, [LessonPlanPatchOp(op="set_block", target="objectives.0", field=field, value="x")]),
        )
    assert spec.model_dump_json() == _spec().model_dump_json()
    assert spec.projection.approved_blueprint_id == BP_ID
    assert spec.projection.approved_blueprint_revision == 2


def test_unknown_op_rejected() -> None:
    spec = _spec()
    # 主防线：schema 校验拒绝未知 op
    with pytest.raises(ValidationError):
        LessonPlanPatchProposal.model_validate(
            {
                "spec_id": SPEC_ID,
                "expected_revision": 3,
                "summary": "坏 op",
                "operations": [{"op": "explode", "target": "objectives.0", "field": "text", "value": "x"}],
            }
        )

    # 次防线：从 tp_lesson_plan_edit_proposals.operations JSONB 反序列化的历史行绕过 schema 后，
    # apply 必须在写前拒绝（PatchValidationError，而不是穿透 pydantic ValidationError）
    raw_op = LessonPlanPatchOp.model_construct(op="explode", target="objectives.0", field="text", value="x")
    proposal = LessonPlanPatchProposal.model_construct(
        spec_id=SPEC_ID, expected_revision=3, summary="坏 op", source="human", operations=[raw_op]
    )
    with pytest.raises(PatchValidationError):
        apply_proposal(spec, proposal)
    assert spec.model_dump_json() == _spec().model_dump_json()


def test_unknown_block_and_bad_metadata_target_rejected() -> None:
    spec = _spec()
    with pytest.raises(PatchValidationError, match="block"):
        apply_proposal(
            spec,
            _proposal(3, [LessonPlanPatchOp(op="set_block", target="objectives.99", field="text", value="x")]),
        )
    with pytest.raises(PatchValidationError, match="block_id"):
        apply_proposal(
            spec,
            _proposal(3, [LessonPlanPatchOp(op="set_block", target="objectives", field="text", value="x")]),
        )
    with pytest.raises(PatchValidationError, match="metadata"):
        apply_proposal(
            spec,
            _proposal(3, [LessonPlanPatchOp(op="set_field", target="objectives.0", field="title", value="x")]),
        )


def test_metadata_field_whitelist_and_type_check() -> None:
    spec = _spec()
    with pytest.raises(PatchValidationError, match="白名单"):
        apply_proposal(
            spec,
            _proposal(3, [LessonPlanPatchOp(op="set_field", target="metadata", field="language", value="en-US")]),
        )
    with pytest.raises(PatchValidationError, match="duration_minutes"):
        apply_proposal(
            spec,
            _proposal(3, [LessonPlanPatchOp(op="set_field", target="metadata", field="duration_minutes", value=-5)]),
        )
    assert spec.model_dump_json() == _spec().model_dump_json()


# ─────────────────────────────────────────────────────────────────────────────
# 元数据 set_field：revision+1、change_log 追加、文档级影响面
# ─────────────────────────────────────────────────────────────────────────────


def test_set_field_metadata_bumps_revision_and_appends_change_log() -> None:
    spec = _spec()
    blocks_before = _block_texts(spec)
    fps_before = _fingerprints(spec)

    new_spec, patch, log = apply_proposal(
        spec,
        _proposal(
            3,
            [
                LessonPlanPatchOp(op="set_field", target="metadata", field="title", value="1.1 集合的概念（修订）"),
                LessonPlanPatchOp(op="set_field", target="metadata", field="duration_minutes", value=40),
            ],
            summary="调整标题与课时",
        ),
    )

    assert new_spec.metadata.title == "1.1 集合的概念（修订）"
    assert new_spec.metadata.duration_minutes == 40
    assert new_spec.metadata.grade == "高一" and new_spec.metadata.lesson_scope == "集合的概念"
    assert new_spec.revision == 4
    assert patch.affected.block_ids == [] and patch.affected.whole_plan is True
    # 文档级变更不得改动任何块
    assert {k for k in blocks_before if blocks_before[k] != _block_texts(new_spec)[k]} == set()
    assert _fingerprints(new_spec) == fps_before

    assert [e.revision for e in new_spec.change_log] == [1, 4]
    assert new_spec.change_log[-1].kind == "patch"
    assert new_spec.change_log[-1].summary == "调整标题与课时"
    assert log == {
        "revision": 4,
        "ts": new_spec.change_log[-1].ts.isoformat().replace("+00:00", "Z"),
        "kind": "patch",
        "summary": "调整标题与课时",
    }


def test_regen_source_maps_to_regen_change_log_kind() -> None:
    spec = _spec()
    new_spec, _, log = apply_proposal(
        spec,
        _proposal(
            3,
            [LessonPlanPatchOp(op="set_block", target="stage.introduction", field="text", value="复习导入")],
            summary="局部重生成导入环节",
            source="regen",
        ),
    )
    assert log["kind"] == "regen"
    assert new_spec.change_log[-1].kind == "regen"
    assert new_spec.lesson_flow[0].title == "复习导入"
    assert new_spec.lesson_flow[1].title == "新知探究"


# ─────────────────────────────────────────────────────────────────────────────
# 块枚举稳定性（UI / diff 共用地址）
# ─────────────────────────────────────────────────────────────────────────────


def test_lesson_plan_blocks_order_kind_and_purity() -> None:
    spec = _spec()
    blocks = lesson_plan_blocks(spec)
    assert [b["block_id"] for b in blocks] == [
        "objectives.0",
        "objectives.1",
        "objectives.2",
        "key_difficult_points.0",
        "stage.introduction",
        "stage.new_content",
        "activities.0",
        "examples.0",
        "support.foundation",
        "diagnostic.0",
        "assessment.0",
        "evidence.0",
    ]
    assert {b["kind"] for b in blocks} == {
        "objectives",
        "key_difficult_points",
        "lesson_flow",
        "activities",
        "examples",
        "stratified_support",
        "diagnostic_response",
        "assessment",
        "evidence",
    }
    assert all(set(b) == {"block_id", "kind", "label_zh", "text", "items"} for b in blocks)
    assert lesson_plan_blocks(spec) == blocks  # 纯函数（同输入同输出）
    assert lesson_plan_blocks(spec) != [] and isinstance(blocks[0]["items"], list)
    stage_block = next(b for b in blocks if b["block_id"] == "stage.new_content")
    assert stage_block["text"] == "新知探究" and stage_block["items"] == ["元素的确定性/互异性/无序性"]
    assert next(b for b in blocks if b["block_id"] == "support.foundation")["text"] == "基础支持"
    diagnostic = next(b for b in blocks if b["block_id"] == "diagnostic.0")
    assert diagnostic["text"] == "先补元素概念" and diagnostic["items"] == []
    # revision 不参与编址（块地址跨版本稳定）
    bumped = lesson_plan_blocks(spec.model_copy(update={"revision": 9}, deep=True))
    assert [b["block_id"] for b in bumped] == [b["block_id"] for b in blocks]
    json.dumps(blocks, ensure_ascii=False)  # UI/diff 契约：可 JSON 序列化


def test_lesson_plan_blocks_empty_buckets_yield_no_blocks() -> None:
    spec = _spec(objectives=0)
    ids = [b["block_id"] for b in lesson_plan_blocks(spec)]
    assert "objectives.0" not in ids
    assert ids[0] == "key_difficult_points.0"
    assert lesson_plan_blocks(_spec(objectives=0, long_flow=True)) != lesson_plan_blocks(spec)


def test_t24_orm_rows_registered_with_required_columns() -> None:
    """迁移（主代理所有）必须与 ORM 行形状一致：这里把列/约束钉死。"""
    from sqlalchemy import CheckConstraint, UniqueConstraint

    from app.teacher_prep.specs.models import LessonPlanEditProposalRow, LessonPlanSpecRevisionRow

    revisions = LessonPlanSpecRevisionRow.__table__
    assert LessonPlanSpecRevisionRow.__tablename__ == "tp_lesson_plan_revisions"
    assert [c.name for c in revisions.columns] == [
        "id",
        "spec_id",
        "revision",
        "kind",
        "body",
        "body_sha256",
        "base_revision",
        "summary",
        "actor_id",
        "created_at",
    ]
    assert any(
        isinstance(c, UniqueConstraint) and [col.name for col in c.columns] == ["spec_id", "revision"]
        for c in revisions.constraints
    )
    assert any(isinstance(c, CheckConstraint) and "revision >= 1" in str(c.sqltext) for c in revisions.constraints)
    fk = next(iter(revisions.foreign_keys))
    assert fk.target_fullname == "tp_lesson_plan_specs.id" and fk.ondelete == "CASCADE"

    proposals = LessonPlanEditProposalRow.__table__
    assert LessonPlanEditProposalRow.__tablename__ == "tp_lesson_plan_edit_proposals"
    assert [c.name for c in proposals.columns] == [
        "id",
        "spec_id",
        "status",
        "source",
        "expected_revision",
        "base_revision",
        "summary",
        "instruction",
        "operations",
        "refs",
        "affected",
        "actor_id",
        "error",
        "idempotency_key",
        "created_at",
        "decided_at",
        "applied_revision",
    ]
    assert any(
        isinstance(c, UniqueConstraint) and [col.name for col in c.columns] == ["idempotency_key"]
        for c in proposals.constraints
    )
    status_check = next(c for c in proposals.constraints if isinstance(c, CheckConstraint))
    for status in ("proposed", "applied", "rejected", "superseded", "failed"):
        assert status in str(status_check.sqltext)


# ─────────────────────────────────────────────────────────────────────────────
# §F 六章块 patch（T7 P0：FE 反思编辑 422 根治）
# ─────────────────────────────────────────────────────────────────────────────


def test_reflection_add_set_remove_full_lifecycle() -> None:
    """FE 反思编辑三形态（usePlanEditor.saveReflection）：add→set→remove 全链路 + 重进还原。"""
    from app.teacher_prep.specs.lesson_plan_schema import BoardDesign, PlanItem as PI

    spec = _spec()
    assert spec.reflection == []

    # ① add_block reflection.0（FE 空态保存：value={text}）
    new_spec, patch, _ = apply_proposal(
        spec,
        _proposal(
            3,
            [LessonPlanPatchOp(op="add_block", target="reflection.0", value={"text": "本节探究活动用时略超预期"})],
            summary="编辑教学反思",
        ),
    )
    assert new_spec.revision == 4
    assert new_spec.reflection[0].text == "本节探究活动用时略超预期"
    assert patch.affected.block_ids == ["reflection.0"]
    assert any(b["block_id"] == "reflection.0" for b in lesson_plan_blocks(new_spec))

    # ② 重进还原：序列化落库再反序列化，块地址与文本稳定（QA 复验口径）
    reloaded = LessonPlanSpec.model_validate(new_spec.model_dump(mode="json"))
    block = next(b for b in lesson_plan_blocks(reloaded) if b["block_id"] == "reflection.0")
    assert block["text"] == "本节探究活动用时略超预期"

    # ③ set_block reflection.0（FE 已有块再编辑：field=text）
    edited, patch_set, _ = apply_proposal(
        reloaded,
        _proposal(
            4,
            [LessonPlanPatchOp(op="set_block", target="reflection.0", field="text", value="改：探究环节压缩 3 分钟")],
            summary="编辑教学反思",
        ),
    )
    assert edited.revision == 5
    assert edited.reflection[0].text == "改：探究环节压缩 3 分钟"
    # INV-2 局部性：其余块逐字节不变（objectives 为例）
    assert edited.objectives == reloaded.objectives

    # ④ remove_block reflection.0（FE 清空保存）
    cleared, patch_rm, _ = apply_proposal(
        edited,
        _proposal(
            5,
            [LessonPlanPatchOp(op="remove_block", target="reflection.0")],
            summary="清空教学反思",
        ),
    )
    assert cleared.revision == 6
    assert cleared.reflection == []
    assert patch_rm.affected.block_ids == ["reflection.0"]


def test_reflection_add_only_append_and_rejects_bad_index() -> None:
    """add_block 只允许追加（索引=当前块数）；非法索引/越界/坏地址均 422 语义。"""
    spec = _spec()
    with pytest.raises(PatchValidationError, match="只支持追加"):
        apply_proposal(
            spec,
            _proposal(3, [LessonPlanPatchOp(op="add_block", target="reflection.1", value={"text": "x"})]),
        )
    with pytest.raises(PatchValidationError, match="非法 block_id"):
        apply_proposal(
            spec,
            _proposal(3, [LessonPlanPatchOp(op="set_block", target="teaching_reflection.0", field="text", value="x")]),
        )


def test_homework_block_patch_text_and_items_forbidden() -> None:
    """homework.<i> 与列表块同编址：text 可改；单条项无 items 层。"""
    from app.teacher_prep.specs.lesson_plan_schema import PlanItem as PI

    spec = _spec().model_copy(
        update={"homework": [PI(text="教材习题 1.1 第 1、3 题", source_ref=_ref(11))]}, deep=True
    )
    new_spec, patch, _ = apply_proposal(
        spec,
        _proposal(
            3,
            [LessonPlanPatchOp(op="set_block", target="homework.0", field="text", value="教材习题 1.1 第 1、3、7 题")],
            summary="调整课后作业",
        ),
    )
    assert new_spec.revision == 4
    assert new_spec.homework[0].text == "教材习题 1.1 第 1、3、7 题"
    # 保源：source_ref 继承（SourceLocator 可追溯性不被 patch 抹掉）
    assert new_spec.homework[0].source_ref == _ref(11)
    assert patch.affected.block_ids == ["homework.0"]
    with pytest.raises(PatchValidationError, match="没有 items"):
        apply_proposal(
            new_spec,
            _proposal(4, [LessonPlanPatchOp(op="set_block", target="homework.0", field="items", value=[])]),
        )


def test_board_design_subfield_patch_set_add_remove() -> None:
    """board_design.<sub>.<i>：text_points/formulas 子栏可 patch；math_object 不开放块级 patch。"""
    from app.teacher_prep.specs.lesson_plan_schema import BoardDesign

    spec = _spec().model_copy(
        update={
            "board_design": BoardDesign(
                text_points=[PlanItem(text="椭圆定义：平面内到两定点距离之和为定值")],
                formulas=[PlanItem(text="标准方程见公式区")],
            )
        },
        deep=True,
    )
    ids = [b["block_id"] for b in lesson_plan_blocks(spec)]
    assert "board_design.text_points.0" in ids and "board_design.formulas.0" in ids

    # set text
    edited, _, _ = apply_proposal(
        spec,
        _proposal(
            3,
            [LessonPlanPatchOp(op="set_block", target="board_design.text_points.0", field="text", value="椭圆定义（第一定义）")],
            summary="修订板书文字要点",
        ),
    )
    assert edited.board_design.text_points[0].text == "椭圆定义（第一定义）"
    assert edited.board_design.formulas[0].text == "标准方程见公式区"  # 另一子栏不受影响

    # add 追加（index 必须等于当前块数）
    added, patch_add, _ = apply_proposal(
        edited,
        _proposal(
            4,
            [LessonPlanPatchOp(op="add_block", target="board_design.formulas.1", value={"text": "$\\\\frac{x^2}{a^2}+\\\\frac{y^2}{b^2}=1$"})],
            summary="补板书公式",
        ),
    )
    assert added.board_design.formulas[1].text.endswith("=1$")
    assert patch_add.affected.block_ids == ["board_design.formulas.1"]
    with pytest.raises(PatchValidationError, match="只支持追加"):
        apply_proposal(
            added,
            _proposal(5, [LessonPlanPatchOp(op="add_block", target="board_design.formulas.3", value={"text": "x"})]),
        )

    # remove：同子栏后续地址平移一并声明受影响
    removed, patch_rm, _ = apply_proposal(
        added,
        _proposal(
            5,
            [LessonPlanPatchOp(op="remove_block", target="board_design.formulas.0")],
            summary="删板书公式",
        ),
    )
    assert len(removed.board_design.formulas) == 1
    assert patch_rm.affected.block_ids == ["board_design.formulas.0", "board_design.formulas.1"]

    # math_object 不是块地址（不存在性检查即拒）
    with pytest.raises(PatchValidationError, match="不存在"):
        apply_proposal(
            removed,
            _proposal(6, [LessonPlanPatchOp(op="set_block", target="board_design.math_object.0", field="text", value="x")]),
        )
