"""TeachingDeckSpec v2.0.0 领域契约（R10，P0）—— V2 唯一事实来源。

来源权威：本文件（Pydantic）是唯一权威；JSON Schema 与 TypeScript 类型由
`app/ppt_v2/domain/gen/` 生成并提交，任何一侧漂移由 contract test 即刻拦截。

铁律（不可违背）：
1. V2 禁止出现引擎像素坐标（x/y/w/h）——layout_hint/visual_intent 只能是语义枚举/描述；
2. `TEACHER_UPLOAD.preserve_original=true` 默认且不可被普通 transform 关闭；
3. 教材源铁律：deck 必须可追溯教材（textbook_sources 非空），fixture 一律用合成/虚构内容；
4. 未知字段策略：reader 在同一 major 内向前兼容（extra=ignore 容忍），未知 major fail closed；
5. 不再依赖旧 V3 schema / 旧 Deck.slides，本文件与旧域零耦合。

字段命名注意：所有 slide / asset / deck 字段名不得是 x/y/w/h（契约测试断言 schema）。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 当前契约版本；跨 major 变更必须 bump（reader fail closed）
SCHEMA_VERSION = "2.0.0"

# ── 语义枚举（Slide Director 消费的是语义意图，不是坐标）──────────────────────
AssetKind = Literal["TEXTBOOK", "TEACHER_UPLOAD", "MATH_RENDER", "AI_IMAGE"]
RightsStatus = Literal["QUARANTINED", "STAGED", "ACTIVE"]
SlideType = Literal[
    "title",
    "introduction",
    "concept",
    "definition",
    "derivation",
    "worked-example",
    "comparison",
    "image-focus",
    "practice",
    "inquiry",
    "summary",
]
LayoutHint = Literal[
    "title",
    "introduction",
    "concept",
    "definition",
    "derivation",
    "worked-example",
    "comparison",
    "image-focus",
    "practice",
    "inquiry",
    "summary",
    "overview",
]
SlideStatus = Literal["draft", "confirmed", "generated", "failed"]
QaStatus = Literal["pending", "passed", "failed", "unknown"]
ExampleUsage = Literal["guided", "demo", "practice"]

# SlideSpec 语义元素 role 枚举（FROZEN-CONTRACT §A.2，冻结）——渲染层从 role 派生版式
ElementRole = Literal[
    "definition",
    "formula",
    "knowledge_card",
    "figure",
    "bullets",
    "note",
    "steps",
    "table",
]

# 引擎坐标字段名单：V2 契约一刀切禁止（任何 slides/模板映射都不得直写像素）
ENGINE_GEOMETRY_FIELD_NAMES = ("x", "y", "w", "h")

# patch 允许修改的 slide 字段（不可变：id / revision 由系统维护）
SLIDE_MUTABLE_FIELDS = (
    "type",
    "teaching_goal",
    "title",
    "content",
    "source_refs",
    "assets",
    "visual_intent",
    "layout_hint",
    "speaker_note",
    "status",
    "qa_status",
    "math_object",
    "elements",
)
# patch 允许修改的 asset 元数据字段（不可变：object_key/sha256/kind/preserve_original）
ASSET_MUTABLE_FIELDS = ("rights_status", "rights_evidence_ref", "source_ref")


class _DomainModel(BaseModel):
    """全部 domain 模型基类：容忍未知字段（向前兼容），字段去首尾空白。"""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# ─────────────────────────────────────────────────────────────────────────────
# AssetRef
# ─────────────────────────────────────────────────────────────────────────────


class AssetTransform(_DomainModel):
    """一次派生变换记录（派生物必须生成新 AssetRef，原件对象 key 不可变）。"""

    op: Literal["crop", "resize", "compress", "watermark", "bg_removal"]
    version: str = Field(default="1")
    params: dict[str, Any] = Field(default_factory=dict)


class AssetRef(_DomainModel):
    """资源引用：TEXTBOOK / TEACHER_UPLOAD / MATH_RENDER / AI_IMAGE。

    object_key/sha256/kind/preserve_original 为不可变事实（patch 层禁止修改）；
    rights_status 默认 QUARANTINED（未授权不得进任何生产渲染/检索）。
    """

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
            raise ValueError(
                "TEACHER_UPLOAD.preserve_original 必须为 true：老师上传原件不可变，"
                "裁剪/压缩等派生必须生成新 AssetRef"
            )
        return self


# ─────────────────────────────────────────────────────────────────────────────
# SlideSpec 与教学结构
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# 参数化数学对象（R4/MathObject：数据 → 图形 → 公式 同源，禁止贴图）
# ─────────────────────────────────────────────────────────────────────────────


class MathObjectStyle(_DomainModel):
    """图形样式（渲染层直接消费；线型语义枚举，禁止像素坐标）。"""

    color: str = Field(default="#2563EB", pattern=r"^#[0-9a-fA-F]{6}$")
    stroke_px: float = Field(default=2, ge=0.5, le=12)
    dash: Literal["solid", "dashed", "dotted"] = "solid"  # type: ignore[valid-type]


class MathObjectToggles(_DomainModel):
    """图形要素显隐开关（黄金图 PPT 页右面板 4 项；末项默认关）。"""

    show_foci: bool = Field(default=True)        # 显示焦点 F₁F₂
    show_axes: bool = Field(default=True)        # 显示坐标轴
    show_aux_lines: bool = Field(default=True)   # 显示辅助线 (PF₁、PF₂)
    show_value_labels: bool = Field(default=False)  # 显示数值标注（默认关）


class MathObjectSpec(_DomainModel):
    """参数化数学对象：参数是唯一事实，图形/焦点/公式由渲染层实时派生。

    数学红线（R3e 推广到全部 kind）：**只存独立参数**，派生值不落库——
    - ellipse：只存 a/b（半焦距 c = √(a²−b²) 派生），构造强校验 a > b > 0；
    - line：只存 k/x0/y0（截距 b = y0 − k·x0、斜率三角形派生），k 必须有限；
    - line_family：只存 b（样本斜率为渲染展示常量），直线系 y = kx + b 恒过 (0, b)。

    渲染现状（不改 8201 的前提）：8201 引擎当前只渲染 kind=ellipse（`math_object_figure`），
    **deck 页图形对 line/line_family 走 MATH_RENDER 资产通道**（同源参数、服务端确定性出图），
    slide.math_object 不得挂非 ellipse kind（否则导出可见失败）；教案板书（BoardDesign）
    复用本模型渲染进 PDF/DOCX。
    """

    kind: Literal["ellipse", "line", "line_family"]  # type: ignore[valid-type]
    params: dict[str, float] = Field(default_factory=dict)  # 只存独立参数；派生值只读
    toggles: MathObjectToggles = Field(default_factory=MathObjectToggles)
    style: MathObjectStyle = Field(default_factory=MathObjectStyle)

    @model_validator(mode="after")
    def _validate_math(self) -> MathObjectSpec:
        if self.kind == "ellipse":
            a = self.params.get("a")
            b = self.params.get("b")
            unknown = set(self.params) - {"a", "b"}
            if unknown:
                raise ValueError(f"椭圆 math_object 只接受参数 a/b，收到未知参数: {sorted(unknown)}（c 为派生值禁止写入）")
            if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
                raise ValueError("椭圆 math_object 必须提供数值参数 a（长半轴）与 b（短半轴）")
            if not (a > b > 0):
                raise ValueError(f"椭圆参数必须满足 a > b > 0（数学红线），收到 a={a}, b={b}")
        elif self.kind == "line":
            unknown = set(self.params) - {"k", "x0", "y0"}
            if unknown:
                raise ValueError(f"直线 math_object 只接受参数 k/x0/y0，收到未知参数: {sorted(unknown)}（截距为派生值禁止写入）")
            for name in ("k", "x0", "y0"):
                value = self.params.get(name)
                if not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
                    raise ValueError(f"直线 math_object 参数 {name} 必须是有限数值，收到 {value!r}")
        elif self.kind == "line_family":
            unknown = set(self.params) - {"b"}
            if unknown:
                raise ValueError(f"直线系 math_object 只接受参数 b，收到未知参数: {sorted(unknown)}")
            b = self.params.get("b")
            if not isinstance(b, (int, float)):
                raise ValueError(f"直线系 math_object 参数 b 必须是数值，收到 {b!r}")
        return self

    @property
    def semi_major(self) -> float:
        return float(self.params["a"])

    @property
    def semi_minor(self) -> float:
        return float(self.params["b"])

    @property
    def focal_distance(self) -> float:
        """半焦距 c = √(a²−b²)（只读派生，构造校验保证 a>b>0 → 实数）。"""
        a, b = self.semi_major, self.semi_minor
        return (a * a - b * b) ** 0.5

    @property
    def intercept(self) -> float:
        """直线纵截距 b = y0 − k·x0（只读派生；仅 kind=line）。"""
        if self.kind != "line":
            raise ValueError("intercept 仅适用于 kind=line")
        return float(self.params["y0"]) - float(self.params["k"]) * float(self.params["x0"])


# ─────────────────────────────────────────────────────────────────────────────
# SlideElement（FROZEN-CONTRACT §A：承载黄金图版式要素的语义结构）
# ─────────────────────────────────────────────────────────────────────────────
# 设计动机：黄金图 PPT 页需要「定义带 / 公式胶囊 / 知识卡 / 参数化图形 / 页脚」
# 等要素，而旧的 `content`（`\n` 分隔纯文本）无法承载结构 → 渲染层只能当普通
# 段落排。本模型把这些要素建模为**语义**结构。
# 铁律：**严禁坐标**（x/y/w/h）——版式一律由渲染层从 `role` 派生（§A.2/§A.4）。


class SlideElement(_DomainModel):
    """一个语义化版式元素（字段定义见 §A.3，全部无语义枚举之外的量纲）。

    - `role`：语义角色（§A.2 冻结枚举），渲染层据此派生版式；
    - `text`：正文（≤400 字，可含行内 `$...$` KaTeX）；
    - `latex`：显示式 LaTeX 源码（≤2000，不含 `$` 定界符）；
    - `title`：仅 `knowledge_card` 用的卡片标题（≤60 字）；
    - `items`：列表项（≤10 项，每项 ≤200 字；`table` 时每项以 `|` 分隔单元格）；
    - `figure_ref`：仅 `figure` 用，值固定 `"math_object"`（参数化 SVG 读 slide.math_object）。
    """

    role: ElementRole  # type: ignore[valid-type]
    text: str | None = Field(default=None, max_length=400)
    latex: str | None = Field(default=None, max_length=2000)
    title: str | None = Field(default=None, max_length=60)
    items: list[str] = Field(default_factory=list, max_length=10)
    figure_ref: Literal["math_object"] | None = None

    @field_validator("items")
    @classmethod
    def _each_item_bounded(cls, v: list[str]) -> list[str]:
        """§A.3：items 每项 ≤200 字（整表 max_length 由 Field 承接）。"""
        for item in v:
            if len(item) > 200:
                raise ValueError("SlideElement.items 每项 ≤200 字（FROZEN-CONTRACT §A.3）")
        return v


class SlideSpec(_DomainModel):
    """页蓝图：只承载教学语义，禁止出现任何引擎坐标（x/y/w/h）。"""

    id: str = Field(min_length=1)
    type: SlideType  # type: ignore[valid-type]
    teaching_goal: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(default="")
    # FROZEN-CONTRACT §A：语义化版式元素（新增，可选，默认空）。向后兼容：旧的
    # 只有 content 的 spec 读取后 elements=[]，渲染层按老路径排纯文本段落。
    elements: list[SlideElement] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)  # → AssetRef.id
    visual_intent: str = Field(default="")
    layout_hint: LayoutHint | None = Field(default=None)  # type: ignore[valid-type]
    speaker_note: str | None = None
    revision: int = Field(default=0, ge=0)  # 影响本页的每次 patch 自增
    status: SlideStatus = Field(default="draft")  # type: ignore[valid-type]
    qa_status: QaStatus = Field(default="pending")  # type: ignore[valid-type]
    # R4 参数化数学对象（可选；确定性投影器按页语义生成，patch 通道可改 params/toggles/style）
    math_object: MathObjectSpec | None = None
    # 注意：x/y/w/h 禁止出现 —— extra="ignore" 下引擎坐标字段会被静默丢弃（reader 策略），
    # 但 schema 层契约测试会断言生成 JSON Schema 中不存在名为 x/y/w/h 的属性，双保险。


class DeckMetadata(_DomainModel):
    """deck 元数据（无教学内容字段，标题变更只影响版式带）。"""

    title: str = Field(min_length=1)
    grade: str | None = None
    duration_minutes: int | None = Field(default=None, ge=1)
    language: str = Field(default="zh-CN")
    course_context_ref: str | None = None


class TextbookSourceRef(_DomainModel):
    """教材溯源引用（deck → 教材 section/chunk）。

    R12 起 direct-chunk 新 writer 只写 `chunk_refs`（canonical SourceLocator v1 ref）；
    `page_refs` 仅 legacy 兼容读取（旧 deck 打开），新 writer 必须留空（page_refs=0）。
    `locator` 为可选 canonical 定位串（source/section 级）。
    """

    textbook_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    page_refs: list[str] = Field(default_factory=list)
    chunk_refs: list[str] = Field(default_factory=list)
    locator: str | None = None

    @classmethod
    def from_locator(cls, locator_str: str) -> TextbookSourceRef:
        """direct-chunk 新 writer：从 canonical v1 locator ref 构造溯源（page_refs 必空）。"""
        from app.textbook_v2.locator import SourceLocator

        loc = SourceLocator.from_ref(locator_str)
        if loc is None or loc.section_id is None:
            raise ValueError(f"locator 必须可解析且含 section 级信息: {locator_str!r}")
        return cls(
            textbook_id=str(loc.source_id),
            section_id=str(loc.section_id),
            page_refs=[],  # 新 writer 零假页码
            chunk_refs=[locator_str],
            locator=locator_str,
        )


class TeachingStrategy(_DomainModel):
    """教学策略：intro/concept_progression/derivation_chain/checks。"""

    intro: str = Field(default="")
    concept_progression: list[str] = Field(default_factory=list)
    derivation_chain: list[str] = Field(default_factory=list)
    checks_for_understanding: list[str] = Field(default_factory=list)


class OutlineEntry(_DomainModel):
    """大纲条目：goal + 引用的 slide ids。"""

    id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    slide_ids: list[str] = Field(default_factory=list)


class SelectedExample(_DomainModel):
    """选中例题（问题来自教材/题库，usage 语义枚举）。"""

    problem_id: str = Field(min_length=1)
    usage: ExampleUsage = Field(default="guided")  # type: ignore[valid-type]


class ThemeRef(_DomainModel):
    """主题引用（版本化模板），不内嵌像素布局。"""

    theme_id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class ChangeLogEntry(_DomainModel):
    """每次 apply patch 追加一条：new revision + 影响面。"""

    revision: int = Field(ge=0)
    ts: datetime
    kind: str = Field(min_length=1)  # create | patch | theme | ...
    summary: str = Field(min_length=1)
    patch_ids: list[str] = Field(default_factory=list)
    affected_slide_ids: list[str] = Field(default_factory=list)


class SpecProjectionRef(_DomainModel):
    """R20 投影血缘（approved blueprint/context hash + prompt/model/run lineage）。

    可选字段（默认 None）：旧 DeckSpec（R10/R15 写入，无投影血缘）读取完全兼容；
    新投影写入（R20）绑定该块，「双 Spec 的 approved blueprint/context hash 一致」锚点。
    """

    schema_version: Literal["1.0.0"] = "1.0.0"
    projector: str = Field(min_length=1, max_length=64)  # 投影器版本（teacher_prep.specs.v1）
    approved_blueprint_id: str = Field(pattern=r"^[0-9a-fA-F-]{36}$")
    approved_blueprint_revision: int = Field(ge=1)
    context_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_release_id: str | None = Field(default=None, max_length=64)
    ai_run_id: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    run_hash: str = Field(pattern=r"^[0-9a-f]{64}$")  # 投影输入规范化 hash
    body_hash: str = Field(pattern=r"^[0-9a-f]{64}$")  # spec body 规范化 hash
    projected_at: datetime


# ─────────────────────────────────────────────────────────────────────────────
# TeachingDeckSpec
# ─────────────────────────────────────────────────────────────────────────────


class TeachingDeckSpec(_DomainModel):
    """V2 唯一事实来源（F3）。所有修改只能通过 DeckPatch（expected_revision 乐观并发）。"""

    schema_version: Literal["2.0.0"] = "2.0.0"
    id: str = Field(min_length=1)
    revision: int = Field(default=0, ge=0)
    metadata: DeckMetadata
    textbook_sources: list[TextbookSourceRef] = Field(min_length=1)  # 教材源铁律：至少 1 条溯源
    teaching_strategy: TeachingStrategy = Field(default_factory=TeachingStrategy)
    outline: list[OutlineEntry] = Field(default_factory=list)
    selected_examples: list[SelectedExample] = Field(default_factory=list)
    theme: ThemeRef | None = None
    slides: list[SlideSpec] = Field(default_factory=list)
    assets: list[AssetRef] = Field(default_factory=list)
    speaker_notes: dict[str, str] = Field(default_factory=dict)
    change_log: list[ChangeLogEntry] = Field(default_factory=list)
    projection: SpecProjectionRef | None = Field(default=None)  # R20 投影血缘（可选）

    @model_validator(mode="after")
    def _check_references(self) -> TeachingDeckSpec:
        """引用完整性：slide/asset/outline id 唯一，slide.assets 与 speaker_notes 必须可解析。"""
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
