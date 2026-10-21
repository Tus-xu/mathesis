"""教材 V2 语义提取（R07，P0 缩减版）：chunk → content_units + problems。

- 纯语义提取、零图片：图引用只做占位 unit + review 记录，图题资产整块移交 R13；
- rule 分类可解释：每个 unit 记录 extraction_method（rule:<命中信号>）与 confidence；
- LLM 只进 DTO（llm 参数注入），None 时 rule 路径独立成立；
- dry-run = plan_extraction_section；staged = staged_extract_section（幂等）。
"""

from app.textbook_v2.extraction.dto import (  # noqa: F401
    ExtractionPlan,
    ProblemCandidate,
    ReviewEntry,
    UnitCandidate,
)
from app.textbook_v2.extraction.rules import (  # noqa: F401
    EXTRACTOR_VERSION,
    classify_block,
    detect_figure_refs,
    extract_latex,
    is_solution_start,
    split_markdown_blocks,
    split_problem_text,
)
from app.textbook_v2.extraction.service import (  # noqa: F401
    plan_extraction_section,
    staged_extract_section,
)

__all__ = [
    "EXTRACTOR_VERSION",
    "ExtractionPlan",
    "UnitCandidate",
    "ProblemCandidate",
    "ReviewEntry",
    "classify_block",
    "detect_figure_refs",
    "extract_latex",
    "is_solution_start",
    "split_markdown_blocks",
    "split_problem_text",
    "plan_extraction_section",
    "staged_extract_section",
]
