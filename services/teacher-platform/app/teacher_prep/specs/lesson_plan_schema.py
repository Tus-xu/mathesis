"""R20 LessonPlanSpec 域模型（8100 事实权威；独立 schema_version/revision/purpose/编辑历史）。

铁律（DECISIONS.md §6/§3）：
- 从同一 approved blueprint revision + immutable context snapshot 投影；
  只引用 Blueprint 事实，不反推、不改写 Blueprint 事实；
- 所有教材引用一律 SourceLocator（source_ref），禁止 page_refs/pNN；
- `change_log` 为教案自己的编辑历史（与 DeckSpec.change_log 独立）；
- 与 TeachingDeckSpec 共享 `blueprint_ref`/`context_ref` 事实引用，
  但 schema_version / revision / purpose / change_log 各自独立。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# FROZEN-CONTRACT §F.3：板书设计的中列复用 Deck 的参数化数学对象（数据→图形→公式同源）。
# 只读依赖，无环（app.ppt_v2.domain.spec 不反向 import teacher_prep）。
from app.ppt_v2.domain.spec import MathObjectSpec

LESSON_PLAN_SCHEMA_VERSION = "1.0.0"

StageKind = Literal[
    "introduction", "new_content", "example", "exercise", "summary", "homework",
]
StratifiedGroup = Literal["foundation", "standard", "advanced"]
# FROZEN-CONTRACT §F.1：重难点小胶囊类型（key=重点 / difficult=难点）；None 由渲染层显示态拆分
PlanItemKind = Literal["key", "difficult"]


class _LessonPlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BlueprintRef(_LessonPlanModel):
    """共享事实引用：approved blueprint 锚点。"""

    blueprint_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    revision: int = Field(ge=1)


class ContextRef(_LessonPlanModel):
    """共享事实引用：immutable context snapshot 锚点 + section 级 SourceLocator。"""

    context_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    section_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    locator: str = Field(min_length=1, max_length=512)  # section 级 canonical SourceLocator


class SpecProjectionRef(_LessonPlanModel):
    """投影血缘：approved blueprint/context hash + prompt/model/run lineage。"""

    schema_version: Literal["1.0.0"] = "1.0.0"
    projector: str = Field(min_length=1, max_length=64)  # 投影器版本（如 teacher_prep.specs.v1）
    approved_blueprint_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    approved_blueprint_revision: int = Field(ge=1)
    context_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_release_id: str | None = Field(default=None, max_length=64)
    ai_run_id: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    run_hash: str = Field(pattern=r"^[0-9a-f]{64}$")  # 投影输入规范化 hash
    body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")  # spec body 规范化 hash
    projected_at: datetime


class LessonPlanMetadata(_LessonPlanModel):
    title: str = Field(min_length=1, max_length=160)
    grade: str | None = Field(default=None, max_length=64)
    duration_minutes: int | None = Field(default=None, ge=1)
    language: str = Field(default="zh-CN", max_length=16)
    lesson_scope: str | None = Field(default=None, max_length=200)
    # R3e meta pill 行（Golden 教案：学科｜教材｜班级｜课时 四项真实）：
    # subject=产品范围常量「高中数学」（R14 裁定 3.1）；textbook=去重教材展示名（R14 3.3）；
    # class_name=真实班级名或真实年级（R14 3.2：按真实绑定，绝不硬编码占位班级）。
    subject: str | None = Field(default=None, max_length=32)
    textbook: str | None = Field(default=None, max_length=120)
    class_name: str | None = Field(default=None, max_length=64)


class PlanItem(_LessonPlanModel):
    """一条教案内容项（text + 可解析 SourceLocator + 证据 refs）。"""

    text: str = Field(min_length=1, max_length=400)
    source_ref: str | None = Field(default=None, max_length=512)  # SourceLocator
    evidence_refs: list[str] = Field(default_factory=list, max_length=12)
    # §F.1：重难点小胶囊类型（key=重点 / difficult=难点；None → 渲染层按顺序拆分，仅显示不写库）
    kind: PlanItemKind | None = Field(default=None)  # type: ignore[valid-type]


class StageColumns(_LessonPlanModel):
    """教学过程表五列中的三列内容列（§F.2；均可选默认空）。

    表格行 = `lesson_flow` 的一个 stage（行数与教学路径节点一一对应，KERNEL-RUBRIC §2.2）。
    """

    teacher_activity: list[PlanItem] = Field(default_factory=list, max_length=20)
    student_activity: list[PlanItem] = Field(default_factory=list, max_length=20)
    design_intent: list[PlanItem] = Field(default_factory=list, max_length=20)


class LessonPlanStage(_LessonPlanModel):
    stage: StageKind  # type: ignore[valid-type]
    title: str = Field(min_length=1, max_length=80)
    # W1.3 additive（可选、默认空，与蓝图 LessonStage.summary 对齐）：环节描述性摘要；
    # 教案过程表「设计意图」列缺 hints 时可用它兜底（specs/projector.py::_stage_columns）。
    summary: str | None = Field(default=None, max_length=120)
    duration_minutes: int | None = Field(default=None, ge=1, le=120)
    items: list[PlanItem] = Field(default_factory=list, max_length=20)
    # §F.2 五列表：教师活动 / 学生活动 / 设计意图（时间列 = duration_minutes，环节列 = title）
    columns: StageColumns = Field(default_factory=StageColumns)


class StratifiedSupport(_LessonPlanModel):
    group: StratifiedGroup  # type: ignore[valid-type]
    label: str = Field(min_length=1, max_length=120)
    items: list[PlanItem] = Field(default_factory=list, max_length=12)


class DiagnosticResponse(_LessonPlanModel):
    finding_ref: str = Field(min_length=1, max_length=64)  # profile evidence finding id
    response: str = Field(min_length=1, max_length=500)
    source_ref: str | None = Field(default=None, max_length=512)


class BoardDesign(_LessonPlanModel):
    """板书设计（黄金图 §④；三栏：文字要点 / 数学图形 / 公式区）。

    §F.3：三列均须有内容方可判 PASS；`math_object` 为 None 时中列渲染诚实空态，
    **不得贴图占位**。`math_object` 复用 Deck 的 MathObjectSpec（与 PPT 同源）。
    """

    text_points: list[PlanItem] = Field(default_factory=list, max_length=20)
    math_object: MathObjectSpec | None = Field(default=None)
    formulas: list[PlanItem] = Field(default_factory=list, max_length=20)


class ChangeLogEntry(_LessonPlanModel):
    revision: int = Field(ge=1)
    ts: datetime
    kind: str = Field(min_length=1, max_length=32)  # create | patch | ...
    summary: str = Field(min_length=1, max_length=300)


class LessonPlanSpec(_LessonPlanModel):
    """教案 Spec（8100 权威）：独立 schema_version/revision/purpose/编辑历史。"""

    schema_version: Literal["1.0.0"] = "1.0.0"
    id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    revision: int = Field(default=1, ge=1)
    purpose: Literal["lesson_plan"] = "lesson_plan"
    metadata: LessonPlanMetadata
    blueprint_ref: BlueprintRef
    context_ref: ContextRef
    objectives: list[PlanItem] = Field(default_factory=list, max_length=12)
    key_difficult_points: list[PlanItem] = Field(default_factory=list, max_length=12)
    lesson_flow: list[LessonPlanStage] = Field(default_factory=list, max_length=20)
    # §F.1 六章扩展（additive 可选默认空）：④板书设计 / ⑤作业布置 / ⑥教学反思。
    # 反幻觉：无依据时留空（渲染层给诚实空态），禁止编造；见 specs/projector.py。
    board_design: BoardDesign = Field(default_factory=BoardDesign)
    homework: list[PlanItem] = Field(default_factory=list, max_length=12)
    reflection: list[PlanItem] = Field(default_factory=list, max_length=12)
    activities: list[PlanItem] = Field(default_factory=list, max_length=12)
    examples: list[PlanItem] = Field(default_factory=list, max_length=12)
    stratified_support: list[StratifiedSupport] = Field(default_factory=list, max_length=12)
    diagnostic_response: list[DiagnosticResponse] = Field(default_factory=list, max_length=12)
    assessment: list[PlanItem] = Field(default_factory=list, max_length=12)
    evidence: list[PlanItem] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=30)
    source_refs: list[str] = Field(default_factory=list, max_length=100)  # 全部 SourceLocator 聚合
    projection: SpecProjectionRef
    change_log: list[ChangeLogEntry] = Field(default_factory=list)


__all__ = [
    "LESSON_PLAN_SCHEMA_VERSION",
    "LessonPlanSpec",
    "LessonPlanMetadata",
    "LessonPlanStage",
    "StageColumns",
    "BoardDesign",
    "PlanItem",
    "PlanItemKind",
    "StratifiedSupport",
    "DiagnosticResponse",
    "BlueprintRef",
    "ContextRef",
    "SpecProjectionRef",
    "ChangeLogEntry",
]
