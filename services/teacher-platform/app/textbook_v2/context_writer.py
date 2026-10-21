"""R12 TeachingContext 工件：legacy reader + direct-chunk new writer（P0）。

背景（DECISIONS.md §3）：切片源无原页（D-002），旧工件里的 page_ref/page_refs/pNN
是"假页码"（实为 chunk 全局 1-based 行号）。R12 起：

- **direct-chunk new writer**：`write_direct_chunk_context` 产出的工件只携带
  SourceLocator（canonical v1 ref）与 chunk_* 锚，`page_refs` 显式为空，
  永不产生假页码；
- **legacy reader**：`read_legacy_context_artifact` 可打开含 page_ref/pNN 的旧工件
  （兼容读取），并标记 `legacy_page_refs_present`；只读，不写任何 page 字段。

工件格式（v1）：
    artifact_type=textbook_teaching_context, schema_version=1
    source/section/facts/chunks/chunk_order/locator/page_refs
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.textbook_v2.locator import section_locator
from app.textbook_v2.retrieval.context import load_teaching_context

ARTIFACT_TYPE = "textbook_teaching_context"
ARTIFACT_SCHEMA_VERSION = 1

# legacy 工件中可能携带的"假页码"键（只读识别，禁止新写）
_LEGACY_PAGE_KEYS = (
    "page_ref",
    "page_refs",
    "page_from",
    "page_to",
    "pNN",
    "printed_page_no",
    "ordered_page_refs",
)


async def write_direct_chunk_context(
    session: AsyncSession,
    section_id: Any,
    *,
    course_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """direct-chunk new writer：产出一份无假页码的 TeachingContext 工件。

    - 越权/不可见 → None（不抛敏感错误）；
    - 输出只含 SourceLocator（locator/chunk_ref/chunk_start/chunk_end）与
      `page_refs: []` 显式空值；任何 page_* 键不出现。
    """
    ctx = await load_teaching_context(session, section_id, course_context=course_context)
    if ctx is None:
        return None

    from app.textbook_v2.legacy_guards import guard_textbook_page_read_only

    guard_textbook_page_read_only(session)  # R12：dormant 表零新写入守卫

    locator = ctx.locator or section_locator(
        ctx.source.id, ctx.section.id, chunk_start=ctx.section.chunk_start, chunk_end=ctx.section.chunk_end
    ).to_ref()

    return {
        "artifact_type": ARTIFACT_TYPE,
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "source": {
            "id": str(ctx.source.id),
            "publisher": ctx.source.publisher,
            "edition": ctx.source.edition,
            "grade": ctx.source.grade,
            "volume": ctx.source.volume,
            "rights_status": ctx.source.rights_status,
            "batch_id": ctx.source.batch_id,
            "manifest_sha256": ctx.source.manifest_sha256,
            "source_kind": ctx.source.source_kind,
        },
        "section": {
            "id": str(ctx.section.id),
            "title": ctx.section.title,
            "section_path": ctx.section.section_path,
            "chunk_start": ctx.section.chunk_start,
            "chunk_end": ctx.section.chunk_end,
        },
        "locator": locator,
        "chunk_order": ctx.chunk_order,
        "facts": [
            {
                "kind": f.kind,
                "fact_type": f.fact_type,
                "text": f.text,
                "solution": f.solution,
                "chunk_ref": f.chunk_ref,
                "chunk_start": f.chunk_start,
                "chunk_end": f.chunk_end,
                "provenance": f.provenance,
                "review_status": f.review_status,
            }
            for f in ctx.facts
        ],
        "chunks": [
            {
                "chunk_id": str(c.chunk_id),
                "order": c.order,
                "chunk_ref": c.chunk_ref,
                "text_preview": c.text_preview,
            }
            for c in ctx.chunks
        ],
        # 显式空：新 artifact 不产生假页码（验收断言 page_refs == 0）
        "page_refs": [],
    }


def read_legacy_context_artifact(raw: str | bytes | dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """legacy reader：打开旧 TeachingContext 工件（可含 page_ref/pNN 假页码）。

    返回 (normalized_dict, warnings)：
    - 旧工件（schema_version 缺省/0/1 均可读）原样投影到 dict；
    - 检测到任何 legacy 页码键 → warning + `legacy_page_refs_present=True`；
    - 只读兼容：本函数不产生任何新写入，也不把 legacy 页码提升为事实。
    """
    if isinstance(raw, (str, bytes)):
        import json

        data = json.loads(raw)
    else:
        data = dict(raw)

    warnings: list[str] = []
    legacy_keys = [k for k in _LEGACY_PAGE_KEYS if k in data or any(k in f for f in data.get("facts", []) if isinstance(f, dict))]

    version = data.get("schema_version", 0)
    if version not in (0, 1):
        warnings.append(f"schema_version={version} 超出支持范围（0/1），按兼容语义尽力读取")

    normalized = {
        "artifact_type": data.get("artifact_type", ARTIFACT_TYPE),
        "schema_version": version,
        "source": data.get("source"),
        "section": data.get("section"),
        "locator": data.get("locator"),
        "chunk_order": data.get("chunk_order") or [],
        "facts": data.get("facts", []),
        "chunks": data.get("chunks", []),
        "page_refs": data.get("page_refs") or [],
    }
    if legacy_keys:
        normalized["legacy_page_refs_present"] = True
        warnings.append(
            f"旧工件含 legacy 页码键 {sorted(set(legacy_keys))}：兼容读取，不产生新写入"
        )
    return normalized, warnings
