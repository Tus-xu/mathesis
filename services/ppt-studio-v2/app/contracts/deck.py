"""TeachingDeckSpec v2 中立契约（8200 库内自持拷贝，零 8100 依赖）。

内容与 8100 `app/ppt_v2/domain/spec.py` 语义同源（V2 唯一事实来源），
供 8200 独立服务使用；三端（8100/8200/前端）同步由后续业务轮负责对账。

铁律保持一致：
1. 禁止引擎像素坐标（x/y/w/h）——layout_hint/visual_intent 仅语义枚举/描述；
2. TEACHER_UPLOAD.preserve_original=true 默认且不可被普通 transform 关闭；
3. 教材溯源铁律：deck 必须可追溯（textbook_sources 非空），fixture 一律合成内容；
4. 未知字段策略：同 major 内向前兼容（extra=ignore），未知 major fail closed；
5. 与旧 V3 deck JSON（Deck.slides/element schema）零耦合。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = "2.0.0"

AssetKind = Literal["TEXTBOOK", "TEACHER_UPLOAD", "MATH_RENDER", "AI_IMAGE"]
RightsStatus = Literal["QUARANTINED", "STAGED", "ACTIVE"]
SlideType = Literal[
    "title", "introduction", "concept", "definition", "derivation", "worked-example",
    "comparison", "image-focus", "practice", "inquiry", "summary",
]
LayoutHint = Literal[
    "title", "introduction", "concept", "definition", "derivation", "worked-example",
    "comparison", "image-focus", "practice", "inquiry", "summary", "overview",
]
SlideStatus = Literal["draft", "confirmed", "generated", "failed"]
QaStatus = Literal["pending", "passed", "failed", "unknown"]
ExampleUsage = Literal["guided", "demo", "practice"]

ENGINE_GEOMETRY_FIELD_NAMES = ("x", "y", "w", "h")


class _DomainModel(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class AssetTransform(_DomainModel):
    op: Literal["crop", "resize", "compress", "watermark", "bg_removal"]
    version: str = Field(default="1")
    params: dict[str, Any] = Field(default_factory=dict)


class AssetRef(_DomainModel):
    id: str = Field(min_length=1)
    kind: AssetKind  # type: ignore[valid-type]
    object_key: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    mime_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    source_ref: str | None = None
    rights_status: RightsStatus = Field(default="QUARANTINED")  # type: ignore[valid-type]
    rights_evidence_ref: str | None = None
    preserve_original: bool = Field(default=True)
    created_by: str | None = None
    created_at: datetime | None = None
    transform_history: list[AssetTransform] = Field(default_factory=list)

    @model_validator(mode="after")
    def _protect_teacher_upload_original(self) -> AssetRef:
        if self.kind == "TEACHER_UPLOAD" and self.preserve_original is not True:
            raise ValueError("TEACHER_UPLOAD.preserve_original 必须为 true（原件不可变）")
        return self


class MathObjectStyle(_DomainModel):
    """图形样式（引擎渲染层消费；线型语义枚举，禁止像素坐标）。"""

    color: str = Field(default="#2563EB", pattern=r"^#[0-9a-fA-F]{6}$")
    stroke_px: float = Field(default=2, ge=0.5, le=12)
    dash: Literal["solid", "dashed", "dotted"] = "solid"  # type: ignore[valid-type]


class MathObjectToggles(_DomainModel):
    """图形要素显隐开关。"""

    show_foci: bool = Field(default=True)
    show_axes: bool = Field(default=True)
    show_aux_lines: bool = Field(default=True)
    show_value_labels: bool = Field(default=False)


class MathObjectSpec(_DomainModel):
    """参数化数学对象（R8-U）：参数是唯一事实，图形由引擎确定性渲染（非贴图）。

    数学红线与 8100 `app/ppt_v2/domain/spec.py` 同口径：椭圆只存独立参数 a/b，
    半焦距 c = √(a²−b²) 恒派生；构造强校验 a > b > 0。

    注意：本模型此前缺失 → `extra="ignore"` 会把 8100 投影产出的 math_object 静默丢弃，
    导致导出链路「数学对象 → 图形元素」无输入（课件/PPTX 无图）。
    """

    kind: Literal["ellipse"]  # type: ignore[valid-type]
    params: dict[str, float] = Field(default_factory=dict)
    toggles: MathObjectToggles = Field(default_factory=MathObjectToggles)
    style: MathObjectStyle = Field(default_factory=MathObjectStyle)

    @model_validator(mode="after")
    def _validate_ellipse_math(self) -> MathObjectSpec:
        if self.kind == "ellipse":
            a = self.params.get("a")
            b = self.params.get("b")
            unknown = set(self.params) - {"a", "b"}
            if unknown:
                raise ValueError(f"椭圆 math_object 只接受参数 a/b，收到未知参数: {sorted(unknown)}")
            if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
                raise ValueError("椭圆 math_object 必须提供数值参数 a（长半轴）与 b（短半轴）")
            if not (a > b > 0):
                raise ValueError(f"椭圆参数必须满足 a > b > 0（数学红线），收到 a={a}, b={b}")
        return self


class SlideSpec(_DomainModel):
    id: str = Field(min_length=1)
    type: SlideType  # type: ignore[valid-type]
    teaching_goal: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(default="")
    source_refs: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)
    visual_intent: str = Field(default="")
    layout_hint: LayoutHint | None = Field(default=None)  # type: ignore[valid-type]
    speaker_note: str | None = None
    revision: int = Field(default=0, ge=0)
    status: SlideStatus = Field(default="draft")  # type: ignore[valid-type]
    qa_status: QaStatus = Field(default="pending")  # type: ignore[valid-type]
    # R8-U：参数化数学对象（与 8100 同源字段；缺失会导致图形链路断在 8200 契约层）
    math_object: MathObjectSpec | None = None


class DeckMetadata(_DomainModel):
    title: str = Field(min_length=1)
    grade: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1)
    language: str = Field(default="zh-CN")
    course_context_ref: str | None = None


class TextbookSourceRef(_DomainModel):
    textbook_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    page_refs: list[str] = Field(default_factory=list)
    chunk_refs: list[str] = Field(default_factory=list)
    locator: str | None = None


class TeachingStrategy(_DomainModel):
    intro: str = Field(default="")
    concept_progression: list[str] = Field(default_factory=list)
    derivation_chain: list[str] = Field(default_factory=list)
    checks_for_understanding: list[str] = Field(default_factory=list)


class OutlineEntry(_DomainModel):
    id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    slide_ids: list[str] = Field(default_factory=list)


class SelectedExample(_DomainModel):
    problem_id: str = Field(min_length=1)
    usage: ExampleUsage = Field(default="guided")  # type: ignore[valid-type]


class ThemeRef(_DomainModel):
    theme_id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class ChangeLogEntry(_DomainModel):
    revision: int = Field(ge=0)
    ts: datetime
    kind: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    patch_ids: list[str] = Field(default_factory=list)
    affected_slide_ids: list[str] = Field(default_factory=list)


class TeachingDeckSpec(_DomainModel):
    schema_version: Literal["2.0.0"] = "2.0.0"
    id: str = Field(min_length=1)
    revision: int = Field(default=0, ge=0)
    metadata: DeckMetadata
    textbook_sources: list[TextbookSourceRef] = Field(min_length=1)  # 教材源铁律
    teaching_strategy: TeachingStrategy = Field(default_factory=TeachingStrategy)
    outline: list[OutlineEntry] = Field(default_factory=list)
    selected_examples: list[SelectedExample] = Field(default_factory=list)
    theme: ThemeRef | None = None
    slides: list[SlideSpec] = Field(default_factory=list)
    assets: list[AssetRef] = Field(default_factory=list)
    speaker_notes: dict[str, str] = Field(default_factory=dict)
    change_log: list[ChangeLogEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_references(self) -> TeachingDeckSpec:
        slide_ids = [s.id for s in self.slides]
        _reject_duplicates("slide", slide_ids)
        asset_ids = [a.id for a in self.assets]
        _reject_duplicates("asset", asset_ids)
        valid_slide_ids = set(slide_ids)
        valid_asset_ids = set(asset_ids)
        outline_ids = [e.id for e in self.outline]
        _reject_duplicates("outline", outline_ids)
        for entry in self.outline:
            _ensure_subset("outline.slide_ids", entry.slide_ids, valid_slide_ids)
        for slide in self.slides:
            _ensure_subset(f"slide[{slide.id}].assets", slide.assets, valid_asset_ids)
        for note_slide_id in self.speaker_notes:
            if note_slide_id not in valid_slide_ids:
                raise ValueError(f"speaker_notes 引用了不存在的 slide {note_slide_id!r}")
        return self


def _reject_duplicates(kind: str, values: list[str]) -> None:
    dups = sorted({v for v, c in Counter(values).items() if c > 1})
    if dups:
        raise ValueError(f"{kind} id 重复: {dups}")


def _ensure_subset(field_path: str, values: list[str], allowed: set[str]) -> None:
    missing = sorted(set(values) - allowed)
    if missing:
        raise ValueError(f"{field_path} 引用了不存在的 id: {missing}")
