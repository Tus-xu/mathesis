"""R08 检索 DTO：SectionCandidate / TextbookTeachingContext / Fact / SourceView / ProblemCandidate。

约定（对应 r08-handoff）：
- 每条事实带 source_ref（可被 resolve_source_ref 解析成 SourceView）；
- 教材事实纯净：本模块只承载教材域数据，禁止混入 web/搜索内容；
- 页序：切片源无原页（D-002），page_from/page_to/page_ref/order 均以 chunk 全局
  1-based 行号为锚（与 R06 page_start / R07 page_ref 同口径）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceInfo:
    """教材源信息（R08 检索上下文头部）。"""

    id: Any
    publisher: str
    edition: str
    grade: str
    volume: str
    rights_status: str
    # R12：direct-chunk 溯源（batch_id / manifest_sha256 / source_kind）
    batch_id: str | None = None
    manifest_sha256: str | None = None
    source_kind: str = "direct-chunk"


@dataclass
class SectionInfo:
    """章节节点信息。"""

    id: Any
    textbook_id: Any
    title: str
    section_path: str | None
    level: int
    order: int
    page_range: list[int]
    # R12：direct-chunk 定位列（全局 1-based 行号区间；page_range 为 legacy 兼容）
    chunk_start: int | None = None
    chunk_end: int | None = None


@dataclass
class SectionCandidate:
    """locate_sections 命中的章节候选（anchor → section）。"""

    section_id: Any
    textbook_id: Any
    section_path: str | None
    title: str
    level: int
    page_range: list[int]
    score: float
    rights_status: str
    source_volume: str


@dataclass
class Fact:
    """一条教学内容事实（unit 或 problem），带可解析 source_ref / chunk_ref。"""

    kind: str  # unit | problem
    fact_type: str  # unit: 10 类之一；problem: example/exercise/inquiry
    text: str | None
    solution: str | None = None  # problems 的解答（R07 stem/solution 关联保真）
    latex: str | None = None
    page_from: int | None = None
    page_to: int | None = None
    page_ref: int | None = None  # problems：锚 chunk 行号（legacy 兼容）
    # R12：direct-chunk 定位（新 writer 权威锚；page_* 仅 legacy 读取）
    chunk_ref: str | None = None
    chunk_start: int | None = None
    chunk_end: int | None = None
    confidence: float | None = None
    review_status: str | None = None
    provenance: str | None = None
    source_ref: str = ""
    section_ref: str = ""


@dataclass
class ChunkPreview:
    """chunk 文本预览（替代原页缩略图；D-002 无原页）。"""

    chunk_id: Any
    order: int | None
    page_range: list[int] | None
    text_preview: str
    source_ref: str
    # R12：canonical direct-chunk 定位 ref（新 writer 权威锚）
    chunk_ref: str | None = None


@dataclass
class WarningEntry:
    """缺失/低置信警告（教材事实纯净：警告不携带任何搜索内容）。"""

    code: str
    message: str
    ref: str | None = None


@dataclass
class TextbookTeachingContext:
    """完整有序 section 教学上下文（R08 核心输出）。"""

    source: SourceInfo
    section: SectionInfo
    parent: SectionInfo | None
    children: list[SectionInfo]
    facts: list[Fact]  # 教材原序聚合（units + problems）
    definitions: list[Fact] = field(default_factory=list)
    theorems: list[Fact] = field(default_factory=list)
    formulas: list[Fact] = field(default_factory=list)
    derivations: list[Fact] = field(default_factory=list)
    examples: list[Fact] = field(default_factory=list)
    exercises: list[Fact] = field(default_factory=list)
    inquiries: list[Fact] = field(default_factory=list)
    figures: list[Fact] = field(default_factory=list)
    introductions: list[Fact] = field(default_factory=list)
    summaries: list[Fact] = field(default_factory=list)
    ordered_page_refs: list[int] = field(default_factory=list)  # 按教材原序的页（chunk 行号；legacy 兼容）
    chunks: list[ChunkPreview] = field(default_factory=list)
    warnings: list[WarningEntry] = field(default_factory=list)
    # R12：direct-chunk 定位（新 writer 权威锚；legacy 读取时可为空）
    locator: str | None = None
    chunk_order: list[int] = field(default_factory=list)  # 全局 1-based chunk 行号（有序）


@dataclass
class ProblemCandidate:
    """list_example_candidates 输出（复用 R07 textbook_problems）。"""

    problem_id: Any
    section_id: Any
    kind: str
    order: int
    stem_text: str | None
    solution_text: str | None
    page_ref: int
    knowledge_tags: list[str]
    difficulty: str | None
    provenance: str | None
    source_ref: str


@dataclass
class SourceView:
    """resolve_source_ref / resolve_locator 的可读视图（文本仅截断预览，不泄露全文）。"""

    kind: str  # source/section/chunk/unit/problem
    source_ref: str
    textbook: str  # publisher · edition · volume
    section_title: str | None = None
    section_path: str | None = None
    order: int | None = None  # chunk 序号（页序锚）
    page_range: list[int] | None = None
    text_preview: str = ""
    rights_status: str = ""
    extraction_method: str | None = None
    # R12：direct-chunk 定位（canonical v1 ref；legacy 解析时为 None）
    locator: str | None = None
    chunk_ref: str | None = None
