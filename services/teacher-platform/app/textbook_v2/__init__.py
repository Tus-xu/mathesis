"""教材资源库 V2（R05 域）：provenance / rights / domain schema。

与旧 curriculum / quiz_bank / ppt_slots 混合集合隔离，不复用其业务语义；
仅复用平台 PostgreSQL + pgvector + MinIO 基础设施。原始资产 immutable，
派生资产一律新建 ref（见 models.py 字段约定与 R07 资产提取）。
"""

from app.textbook_v2.backfill_metadata import (  # noqa: F401
    BackfillStats,
    backfill_chunk_refs,
    backfill_section_chunk_range,
    backfill_source_metadata,
)
from app.textbook_v2.context_writer import (  # noqa: F401
    read_legacy_context_artifact,
    write_direct_chunk_context,
)
from app.textbook_v2.legacy_guards import (  # noqa: F401
    TextbookPageWriteError,
    guard_textbook_page_read_only,
    install_textbook_page_guard,
    legacy_textbook_page_write_allowed,
)
from app.textbook_v2.locator import (  # noqa: F401
    LOCATOR_SCHEMA_VERSION,
    SourceLocator,
    chunk_locator,
    problem_locator,
    resolve_locator,
    resolve_source_ref,
    section_locator,
    source_locator,
    unit_locator,
)
from app.textbook_v2.models import (  # noqa: F401
    TextbookChunk,
    TextbookContentUnit,
    TextbookPage,
    TextbookProblem,
    TextbookSection,
    TextbookSource,
)
from app.textbook_v2.reconciliation import (  # noqa: F401
    reconcile_corpus,
    reconcile_corpus_json,
)
from app.textbook_v2.rights import (  # noqa: F401
    RightsStatus,
    active_scope,
    assert_can_transition,
    can_transition,
    visible_scope,
)

__all__ = [
    "RightsStatus",
    "can_transition",
    "assert_can_transition",
    "active_scope",
    "visible_scope",
    "TextbookSource",
    "TextbookPage",
    "TextbookSection",
    "TextbookContentUnit",
    "TextbookProblem",
    "TextbookChunk",
    "LOCATOR_SCHEMA_VERSION",
    "SourceLocator",
    "source_locator",
    "section_locator",
    "chunk_locator",
    "unit_locator",
    "problem_locator",
    "resolve_locator",
    "resolve_source_ref",
    "read_legacy_context_artifact",
    "write_direct_chunk_context",
    "BackfillStats",
    "backfill_source_metadata",
    "backfill_section_chunk_range",
    "backfill_chunk_refs",
    "reconcile_corpus",
    "reconcile_corpus_json",
    "TextbookPageWriteError",
    "guard_textbook_page_read_only",
    "install_textbook_page_guard",
    "legacy_textbook_page_write_allowed",
]
