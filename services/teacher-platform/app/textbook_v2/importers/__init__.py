"""教材 V2 导入器（R06）：切片入 3 层表 Source→Section→Chunk。"""

from app.textbook_v2.importers.slice_importer import (  # noqa: F401
    RIGHTS_EVIDENCE_REF,
    ImportPlan,
    batch_import_id,
    build_section_tree,
    load_chunks,
    parse_section_title,
    plan_import,
    staged_import,
    validate_batch,
)

__all__ = [
    "RIGHTS_EVIDENCE_REF",
    "ImportPlan",
    "batch_import_id",
    "build_section_tree",
    "load_chunks",
    "parse_section_title",
    "plan_import",
    "staged_import",
    "validate_batch",
]
