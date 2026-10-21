"""R07 语义提取 DTO：提取结果只进结构化 DTO，分类必须可解释（可回溯规则/置信度）。

设计约定（对应交接书 r07-handoff / 决策 D-007）：
- 纯语义提取、零图片；图引用只做占位 unit 与 review 记录，asset 资产归 R13；
- 切片无印刷页码 → page_from/page_to/page_ref 沿用 chunk 序号（1-based，与 R06
  page_start 同口径的顺序稳定锚，r07-handoff「沿用 chunk 序号区间」）；
- 每个 candidate 携带 extraction_method（rule:<命中信号> 或 llm:<model>）与 confidence；
- 不确定/低置信/边界 case 必进 review 队列，不假装确定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UnitCandidate:
    """textbook_content_units 候选行。"""

    section_id: Any
    chunk_idx_from: int  # 锚 chunk 序号（区间起始）
    chunk_idx_to: int  # 区间结束
    type: str  # 10 类之一（CONTENT_UNIT_TYPES）
    text: str
    latex: str | None = None
    confidence: float | None = None
    extraction_method: str | None = None
    review_status: str = "pending"  # pending/reviewed/rejected
    review_reason: str | None = None


@dataclass
class ProblemCandidate:
    """textbook_problems 候选行：题干/提示/解答/答案分离，保留顺序与跨 chunk 关联。"""

    section_id: Any
    kind: str  # example/exercise/inquiry
    order: int  # 节内稳定顺序（唯一约束 (section_id, order)）
    chunk_idx: int  # 锚 chunk 序号（题干起点）
    stem_text: str
    solution_text: str | None = None  # 提示+解答+答案合并（保留原顺序）
    chunks_span: list[int] = field(default_factory=list)  # 跨 chunk 关联（题干/解答所在 chunk）
    knowledge_tags: list[str] = field(default_factory=list)
    difficulty: str | None = None
    provenance: str | None = None
    review_reason: str | None = None


@dataclass
class ReviewEntry:
    """review 队列条目：低置信/跨页/图题关联硬件问题等。"""

    chunk_idx: int
    reason: str
    detail: str = ""
    kind: str = "unit"  # unit/problem


@dataclass
class ExtractionPlan:
    """一次 section 提取的完整计划（dry-run 与 staged 共用）。"""

    section_id: Any
    section_title: str = ""
    source_id: Any = None
    chunk_count: int = 0
    units: list[UnitCandidate] = field(default_factory=list)
    problems: list[ProblemCandidate] = field(default_factory=list)
    reviews: list[ReviewEntry] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """无敏感内容的统计摘要（供报告/日志）。"""
        types: dict[str, int] = {}
        for u in self.units:
            types[u.type] = types.get(u.type, 0) + 1
        return {
            "section_id": str(self.section_id),
            "section_title": self.section_title,
            "chunk_count": self.chunk_count,
            "unit_count": len(self.units),
            "unit_types": types,
            "problem_count": len(self.problems),
            "problem_kinds": {k: sum(1 for p in self.problems if p.kind == k) for k in ("example", "exercise", "inquiry")},
            "review_count": len(self.reviews),
            "issues": self.issues,
        }
