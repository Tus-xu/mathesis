"""Canonical corpus semantic-integrity auditor (READ-ONLY).

WP-B / AGENT-B (`PLAT`).

The defect this tool makes machine-detectable
---------------------------------------------
``knowledge_docs.source_type`` was declared ``String(16)`` (see
``app/models/knowledge_doc.py``) and is a *retrieval filter*
(``app/kernel/rag.py:510``, ``:557``, ``:650``). Chunk-level provenance
(``chunks.meta->>'source_kind'``) is correct and complete and uses 6 values, the
longest of which is 19 characters (``CURRICULUM_STANDARD``) -- it could not fit
in ``String(16)``. Every ``knowledge_docs`` row therefore carried the flattened
value ``'textbook'``, which turned the doc-level filter into a no-op.

2026-10-12 (PLAN §7.4.4, B3 deliberate re-baseline): the declared width was
widened 16 → 32 in the model (``knowledge_doc.py:18``) together with migration
``tel_001_two_end_link`` (B1). The *structural* half of the defect (kinds that
cannot be spelled in the column) is thereby fixed.

2026-10-12 (tel F6, B1 data re-stamp landed): the *data* half is fixed by
``services/api/scripts/backfill_knowledge_doc_source_type.py`` -- doc-level
values were re-stamped to the lowercase seven-enum content_type the RAG filter
actually compares against (``rag.py:524``/``:674`` exact match), following the
repo's own import convention (``import_textbook_slices._BOOK_TYPE_MAP``):
TEXTBOOK→textbook / CURRICULUM_STANDARD→standard / TEACHER_GUIDE→lesson_plan /
TRAINING_MATERIAL→method_card. Because doc-level and chunk-level use two
vocabularies (kb seven-enum vs chunk UPPER_SNAKE kinds), the semantic
comparison below maps doc-level values back to kinds via
``DOC_CONTENT_TYPE_TO_KIND`` before comparing. The only remaining divergence
is the 5 MIT ``ADVANCED_REFERENCE`` docs, which have no honest seven-enum
equivalent (their exclusion is the chunk-level ``source_kinds`` filter,
``rag.py:169``) -- recorded deliberately in ``tests/test_corpus_integrity.py``.

This module only ever issues ``SELECT`` statements. It never writes to the
database, never reads ``.env`` values other than the connection URL key name,
and masks the password in every artifact it emits.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

import asyncpg

# ---------------------------------------------------------------------------
# Schema / contract constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "1.0.0"

TOOL_NAME = "corpus-integrity-auditor"

#: The model-level declared width of ``knowledge_docs.source_type``.
#: Source: ``services/api/app/models/knowledge_doc.py:18`` -> ``String(32)``
#: (widened 16→32 by tel_001_two_end_link, PLAN §3.1/§7.4.4). The tool tracks
#: the model declaration; a database whose DDL still predates the migration
#: will show the *data* divergence only (width analysis is advisory, SELECT-only).
DOC_SOURCE_TYPE_MAX_LEN = 32

#: The 6 real semantic kinds carried by ``chunks.meta->>'source_kind'``.
#: Kept as an explicit expectation so a *new* kind appearing in the corpus is
#: visible in the audit diff instead of being silently absorbed.
EXPECTED_SOURCE_KINDS = (
    "TEXTBOOK",
    "ADVANCED_REFERENCE",
    "CURRICULUM_STANDARD",
    "TEACHER_GUIDE",
    "TRAINING_MATERIAL",
    "METHOD_CARD",
)

#: doc 级 content_type（kb_router 七枚举，小写）→ chunk 级语义 kind（UPPER_SNAKE）。
#: 依据 = 仓库既有导入惯例（``import_textbook_slices._BOOK_TYPE_MAP``）；
#: tel F6 回填后 doc 级与 chunk 级是**两套词表**，语义比对必须先归一。
#: 未列出的 doc 值回落大小写折叠比对（兼容 doc 级直存 kind 的形态）。
DOC_CONTENT_TYPE_TO_KIND: dict[str, str] = {
    "textbook": "TEXTBOOK",
    "standard": "CURRICULUM_STANDARD",
    "lesson_plan": "TEACHER_GUIDE",
    "method_card": "TRAINING_MATERIAL",
}

#: Local dev default (host port from ``deploy/docker-compose.yml``; the root
#: ``.env`` key is ``DATABASE_URL`` and points at 54329). Password is the
#: documented local dev password -- never a real secret.
DEFAULT_DATABASE_URL = (
    "postgresql://postgres:postgres@localhost:54329/math_arena"
)

ENV_KEYS = ("MATH_ARENA_DATABASE_URL", "DATABASE_URL")

#: Exit codes -- these are part of the tool's contract, see ``python -m
#: tools.corpus_integrity --help``.
EXIT_CLEAN = 0
EXIT_DIVERGENCE = 1
EXIT_ERROR = 2
EXIT_EMPTY_CORPUS = 3

_PASSWORD_RE = re.compile(r"://(?P<user>[^:/@]*):(?P<pw>[^@/]*)@")


# ---------------------------------------------------------------------------
# URL handling
# ---------------------------------------------------------------------------


def mask_url(url: str) -> str:
    """Return ``url`` with any password replaced by ``***``.

    Every artifact this tool writes goes through here, so no password can leak
    into ``corpus-integrity.json``, the Markdown report, or stdout.
    """
    if not url:
        return ""
    return _PASSWORD_RE.sub(lambda m: f"://{m.group('user')}:***@", url)


def resolve_database_url(explicit: str | None = None, env: dict[str, str] | None = None) -> tuple[str, str]:
    """Resolve a connection URL without reading any secret from disk.

    Returns ``(dsn_for_asyncpg, provenance)``. The ``postgresql+asyncpg://``
    SQLAlchemy form used by the repo is rewritten to the plain
    ``postgresql://`` form asyncpg accepts.
    """
    import os

    source_map = os.environ if env is None else env
    if explicit:
        chosen, provenance = explicit, "cli:--db-url"
    else:
        chosen, provenance = "", ""
        for key in ENV_KEYS:
            value = source_map.get(key)
            if value:
                chosen, provenance = value, f"env:{key}"
                break
        if not chosen:
            chosen, provenance = DEFAULT_DATABASE_URL, "builtin-default(localhost:54329)"
    dsn = chosen.replace("postgresql+asyncpg://", "postgresql://")
    dsn = dsn.replace("postgresql+psycopg://", "postgresql://")
    if "://" not in dsn:
        dsn = "postgresql://" + dsn
    return dsn, provenance


def summarize_dsn(dsn: str) -> dict[str, str | int | None]:
    """Non-secret structural summary of a DSN (for the JSON artifact)."""
    parts = urlsplit(dsn)
    return {
        "scheme": parts.scheme,
        "host": parts.hostname or "",
        "port": parts.port,
        "database": (parts.path or "").lstrip("/"),
        "user": parts.username or "",
        "url_masked": mask_url(dsn),
    }


def _urlsplit_safe(dsn: str):
    return urlsplit(dsn)


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocRow:
    """Per-document audit row."""

    doc_id: str
    title: str
    doc_source_type: str
    chunk_count: int
    chunks_missing_source_kind: int
    chunk_source_kinds: tuple[str, ...]
    chunk_source_kind_counts: dict[str, int]
    diverges: bool
    divergence_reason: str
    has_zero_chunks: bool
    string_width_blocked: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "doc_source_type": self.doc_source_type,
            "chunk_count": self.chunk_count,
            "chunks_missing_source_kind": self.chunks_missing_source_kind,
            "chunk_source_kinds": list(self.chunk_source_kinds),
            "chunk_source_kind_counts": dict(self.chunk_source_kind_counts),
            "diverges": self.diverges,
            "divergence_reason": self.divergence_reason,
            "has_zero_chunks": self.has_zero_chunks,
            "string_width_blocked": self.string_width_blocked,
        }


@dataclass
class AuditResult:
    """Full audit payload; ``as_dict`` is the stable JSON contract."""

    dsn_summary: dict[str, Any]
    provenance: str
    generated_at: str
    docs: list[DocRow] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    root_cause: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "tool": TOOL_NAME,
            "tool_mode": "read-only (SELECT only)",
            "generated_at": self.generated_at,
            "database": {**self.dsn_summary, "url_provenance": self.provenance},
            "summary": self.summary,
            "root_cause": self.root_cause,
            "documents": [d.as_dict() for d in self.docs],
            "warnings": list(self.warnings),
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=indent, sort_keys=False) + "\n"


class CorpusAuditError(RuntimeError):
    """Operational failure with a human-readable message (no traceback dump)."""

    def __init__(self, message: str, *, exit_code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.exit_code = exit_code


# ---------------------------------------------------------------------------
# SQL (read-only, SELECT only)
# ---------------------------------------------------------------------------

_TABLE_EXISTS_SQL = """
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public' AND table_name = ANY($1::text[])
"""

_DOC_SQL = """
WITH per_doc AS (
    SELECT
        c.doc_id,
        count(*) AS chunk_count,
        count(*) FILTER (
            WHERE c.meta->>'source_kind' IS NULL
               OR c.meta->>'source_kind' = ''
        ) AS chunks_missing_source_kind,
        COALESCE(
            array_agg(DISTINCT c.meta->>'source_kind' ORDER BY c.meta->>'source_kind')
                FILTER (WHERE c.meta->>'source_kind' IS NOT NULL
                          AND c.meta->>'source_kind' <> ''),
            ARRAY[]::text[]
        ) AS chunk_source_kinds
    FROM chunks c
    GROUP BY c.doc_id
),
per_doc_kind_counts AS (
    SELECT
        c.doc_id,
        c.meta->>'source_kind' AS sk,
        count(*) AS n
    FROM chunks c
    WHERE c.meta->>'source_kind' IS NOT NULL
      AND c.meta->>'source_kind' <> ''
    GROUP BY c.doc_id, c.meta->>'source_kind'
),
kind_counts AS (
    SELECT
        doc_id,
        jsonb_object_agg(sk, n) AS chunk_source_kind_counts
    FROM per_doc_kind_counts
    GROUP BY doc_id
)
SELECT
    d.id::text                                AS doc_id,
    d.title                                   AS title,
    d.source_type                             AS doc_source_type,
    COALESCE(p.chunk_count, 0)                AS chunk_count,
    COALESCE(p.chunks_missing_source_kind, 0) AS chunks_missing_source_kind,
    COALESCE(p.chunk_source_kinds, ARRAY[]::text[]) AS chunk_source_kinds,
    COALESCE(kc.chunk_source_kind_counts, '{}'::jsonb) AS chunk_source_kind_counts
FROM knowledge_docs d
LEFT JOIN per_doc p ON p.doc_id = d.id
LEFT JOIN kind_counts kc ON kc.doc_id = d.id
ORDER BY d.title, d.id
"""


def _norm_kind(value: str | None) -> str | None:
    """Normalize a provenance value to a semantic kind for comparison.

    ``chunks.meta->>'source_kind'`` is UPPER_SNAKE (``ADVANCED_REFERENCE``);
    ``knowledge_docs.source_type`` uses the ``kb_router`` lowercase seven-enum
    (``textbook``) -- two vocabularies for one semantic field (tel F6 backfill
    re-stamped doc-level values to the seven-enum the RAG filter compares
    against). Doc-level values are first mapped through
    ``DOC_CONTENT_TYPE_TO_KIND``; anything unmapped falls back to case-folding
    (doc-level rows that already carry a kind name compare as before).
    """
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return DOC_CONTENT_TYPE_TO_KIND.get(stripped, stripped.upper())


def _classify(row: dict[str, Any]) -> tuple[bool, str]:
    """Decide whether one document's doc-level type diverges from its chunks."""
    doc_type_raw = (row.get("doc_source_type") or "").strip()
    doc_type = _norm_kind(doc_type_raw)
    kinds = tuple(k for k in (row.get("chunk_source_kinds") or []) if k)
    chunk_count = int(row.get("chunk_count") or 0)

    if chunk_count == 0:
        return False, "no_chunks_to_compare"
    if not kinds:
        return True, "all_chunks_missing_source_kind"
    if len(kinds) > 1:
        return True, "multiple_chunk_source_kinds:" + ",".join(sorted(kinds))
    only = _norm_kind(kinds[0])
    if only != doc_type:
        return True, f"doc_source_type={doc_type_raw!r} != chunk_source_kind={kinds[0]!r}"
    return False, "consistent"


# ---------------------------------------------------------------------------
# Audit driver
# ---------------------------------------------------------------------------


async def _fetch(url: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Run the two read-only queries. Raises CorpusAuditError on any failure."""
    try:
        conn = await asyncpg.connect(url, timeout=5.0)
    except Exception as exc:  # noqa: BLE001 - surfaced as a clean message
        raise CorpusAuditError(
            f"database unreachable: {type(exc).__name__}: {exc} "
            f"[{mask_url(url)}] -- start the container "
            f"(docker start math-arena-postgres) or pass --db-url",
            exit_code=EXIT_ERROR,
        ) from exc

    try:
        present = {
            r["table_name"]
            for r in await conn.fetch(_TABLE_EXISTS_SQL, ["knowledge_docs", "chunks"])
        }
        missing = sorted({"knowledge_docs", "chunks"} - present)
        if missing:
            raise CorpusAuditError(
                f"table(s) missing in database "
                f"'{_urlsplit_safe(url).path.lstrip('/')}': {', '.join(missing)} -- "
                f"corpus has not been migrated/seeded",
                exit_code=EXIT_ERROR,
            )
        rows = await conn.fetch(_DOC_SQL)
    finally:
        await conn.close()
    return [dict(r) for r in rows], []


def audit_sync(
    db_url: str | None = None,
    *,
    env: dict[str, str] | None = None,
    now: datetime | None = None,
) -> AuditResult:
    """Synchronous entry point (used by the CLI and by tests)."""
    import asyncio

    dsn, provenance = resolve_database_url(db_url, env)
    rows, warnings = asyncio.run(_fetch(dsn))
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    return build_result(rows, dsn, provenance, stamp, warnings)


def build_result(
    rows: Iterable[dict[str, Any]],
    dsn: str,
    provenance: str,
    generated_at: str,
    warnings: list[str] | None = None,
) -> AuditResult:
    """Pure function: raw DB rows -> AuditResult. No I/O, so it is unit-testable."""
    docs: list[DocRow] = []
    doc_type_counter: dict[str, int] = {}
    kind_counter: dict[str, int] = {}
    missing_kind_rows = 0
    total_chunks = 0
    divergent = 0
    width_blocked_docs = 0

    for raw in rows:
        counts_raw = raw.get("chunk_source_kind_counts") or {}
        if isinstance(counts_raw, str):  # asyncpg returns jsonb as str by default
            counts_raw = json.loads(counts_raw)
        counts = {str(k): int(v) for k, v in dict(counts_raw).items()}

        kinds = tuple(sorted(k for k in (raw.get("chunk_source_kinds") or []) if k))
        diverges, reason = _classify(raw)
        chunk_count = int(raw.get("chunk_count") or 0)
        missing = int(raw.get("chunks_missing_source_kind") or 0)
        doc_type = (raw.get("doc_source_type") or "").strip()
        # A doc whose real kind cannot be spelled inside String(16) *cannot* have
        # a correct doc-level value, regardless of how the data is fixed later
        # without a model/DDL change. This is the exposure set for the
        # "MIT notes retrievable as 高中教材" failure.
        blocked = any(len(k) > DOC_SOURCE_TYPE_MAX_LEN for k in kinds)

        docs.append(
            DocRow(
                doc_id=str(raw.get("doc_id")),
                title=str(raw.get("title") or ""),
                doc_source_type=doc_type,
                chunk_count=chunk_count,
                chunks_missing_source_kind=missing,
                chunk_source_kinds=kinds,
                chunk_source_kind_counts=counts,
                diverges=diverges,
                divergence_reason=reason,
                has_zero_chunks=chunk_count == 0,
                string_width_blocked=blocked,
            )
        )

        doc_type_counter[doc_type] = doc_type_counter.get(doc_type, 0) + 1
        for kind, n in counts.items():
            kind_counter[kind] = kind_counter.get(kind, 0) + n
        missing_kind_rows += missing
        total_chunks += chunk_count
        divergent += 1 if diverges else 0
        width_blocked_docs += 1 if blocked else 0

    # Root-cause evidence: the widest observed chunk-level kind vs String(16).
    observed_kinds = sorted(kind_counter)
    lens_by_kind = {k: len(k) for k in sorted(set(observed_kinds) | set(EXPECTED_SOURCE_KINDS))}
    longest = max(lens_by_kind, key=lambda k: (lens_by_kind[k], k)) if lens_by_kind else ""
    longest_len = lens_by_kind.get(longest, 0)
    doc_type_max_len = max((len(k) for k in doc_type_counter), default=0)
    is_lossy = any(lens_by_kind[k] > DOC_SOURCE_TYPE_MAX_LEN for k in observed_kinds)
    fits = sorted(k for k in observed_kinds if lens_by_kind[k] <= DOC_SOURCE_TYPE_MAX_LEN)
    nofit = sorted(k for k in observed_kinds if lens_by_kind[k] > DOC_SOURCE_TYPE_MAX_LEN)

    total_docs = len(docs)
    summary = {
        "total_docs": total_docs,
        "docs_with_divergence": divergent,
        "docs_without_divergence": total_docs - divergent,
        "divergence_ratio": round(divergent / total_docs, 6) if total_docs else 0.0,
        "distinct_doc_source_types": len(doc_type_counter),
        "doc_source_type_counts": dict(sorted(doc_type_counter.items())),
        "distinct_chunk_source_kinds": len(kind_counter),
        "chunk_source_kind_counts": dict(sorted(kind_counter.items())),
        "total_chunks": total_chunks,
        "chunks_missing_source_kind": missing_kind_rows,
        "is_lossy": is_lossy,
        "docs_with_width_blocked_kind": width_blocked_docs,
        "chunks_with_width_blocked_kind": sum(
            n for k, n in kind_counter.items() if len(k) > DOC_SOURCE_TYPE_MAX_LEN
        ),
        "docs_at_risk_of_wrong_retrieval": width_blocked_docs,
    }
    root_cause = {
        "field": "knowledge_docs.source_type",
        "declared_type": f"String({DOC_SOURCE_TYPE_MAX_LEN})",
        "declared_max_len": DOC_SOURCE_TYPE_MAX_LEN,
        "declaration_site": "services/api/app/models/knowledge_doc.py:16",
        "comparison_field": "chunks.meta->>'source_kind'",
        "observed_kind_lengths": lens_by_kind,
        "longest_observed_kind": longest,
        "longest_observed_kind_len": longest_len,
        "longest_fits_declared_width": longest_len <= DOC_SOURCE_TYPE_MAX_LEN if longest else None,
        "kinds_that_fit": fits,
        "kinds_that_do_not_fit": nofit,
        "distinct_doc_source_type_values": sorted(doc_type_counter),
        "distinct_chunk_source_kind_values": observed_kinds,
        "max_observed_doc_source_type_len": doc_type_max_len,
        "verdict": (
            f"cannot represent {len(nofit)} of {len(observed_kinds)} real chunk-level "
            f"kinds in String({DOC_SOURCE_TYPE_MAX_LEN}); longest is "
            f"{longest!r} ({longest_len} chars) -> doc-level field is lossy"
            if nofit
            else f"all {len(observed_kinds)} real chunk-level kinds fit in "
            f"String({DOC_SOURCE_TYPE_MAX_LEN})"
        ),
    }

    warn_list = list(warnings or [])
    if missing_kind_rows:
        warn_list.append(
            f"{missing_kind_rows} chunk(s) have NULL/empty meta->>'source_kind'; "
            "they are counted separately and excluded from kind sets"
        )
    zero = [d.doc_id for d in docs if d.has_zero_chunks]
    if zero:
        warn_list.append(f"{len(zero)} document(s) have zero chunks: {', '.join(sorted(zero))}")

    return AuditResult(
        dsn_summary=summarize_dsn(dsn),
        provenance=provenance,
        generated_at=generated_at,
        docs=docs,
        summary=summary,
        root_cause=root_cause,
        warnings=warn_list,
    )


def exit_code_for(result: AuditResult) -> int:
    """Exit-code semantics (CI gate contract).

    * ``0`` -- corpus clean: no doc-level/chunk-level divergence.
    * ``1`` -- divergence detected (the known-bad state this WP is about).
    * ``3`` -- corpus empty: no ``knowledge_docs`` rows, nothing to audit.
    * ``2`` -- operational error (unreachable DB, missing table, bad DSN).
    """
    if result.summary.get("total_docs", 0) == 0:
        return EXIT_EMPTY_CORPUS
    if result.summary.get("docs_with_divergence", 0) > 0:
        return EXIT_DIVERGENCE
    return EXIT_CLEAN


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def render_markdown(result: AuditResult) -> str:
    """Deterministic human-readable report (same numbers as the JSON)."""
    s = result.summary
    rc = result.root_cause
    out: list[str] = []
    add = out.append

    add("# Corpus semantic-integrity audit")
    add("")
    add(f"- **schema_version**: `{SCHEMA_VERSION}`")
    add(f"- **tool**: `{TOOL_NAME}` ({result.as_dict()['tool_mode']})")
    add(f"- **generated_at** (UTC): `{result.generated_at}`")
    add(f"- **database**: `{result.dsn_summary.get('url_masked')}` (provenance `{result.provenance}`)")
    add(f"- **verdict**: `{'DIVERGENCE DETECTED' if s['docs_with_divergence'] else 'clean'}` "
        f"(exit code `{exit_code_for(result)}`)")
    add("")
    add("## Summary")
    add("")
    add("| metric | value |")
    add("| --- | --- |")
    add(f"| total documents (`knowledge_docs`) | {s['total_docs']} |")
    add(f"| **documents with divergence** | **{s['docs_with_divergence']}** |")
    add(f"| documents consistent | {s['docs_without_divergence']} |")
    add(f"| divergence ratio | {s['divergence_ratio']} |")
    add(f"| distinct doc-level `source_type` values | {s['distinct_doc_source_types']} |")
    add(f"| distinct chunk-level `source_kind` values | {s['distinct_chunk_source_kinds']} |")
    add(f"| total chunks | {s['total_chunks']} |")
    add(f"| chunks with NULL/empty `source_kind` | {s['chunks_missing_source_kind']} |")
    add(f"| **doc-level field `is_lossy`** | **{s['is_lossy']}** |")
    add(f"| docs whose real kind CANNOT fit `String({DOC_SOURCE_TYPE_MAX_LEN})` | {s['docs_with_width_blocked_kind']} |")
    add(f"| chunks under such docs | {s['chunks_with_width_blocked_kind']} |")
    add("")
    add("Two distinct metrics, do not conflate them:")
    add("")
    add(f"1. `docs_with_divergence = {s['docs_with_divergence']}` -- docs whose current "
        "`source_type` value is literally not the semantic kind of their own chunks.")
    add(f"2. `docs_with_width_blocked_kind = {s['docs_with_width_blocked_kind']}` -- docs whose real "
        f"kind is longer than `String({DOC_SOURCE_TYPE_MAX_LEN})`, i.e. it is *impossible* to store "
        "correctly in the current column even with a perfect data fix. This is the exposure set "
        "for \"MIT lecture notes are retrievable as 高中教材\".")
    add("")
    add("### Doc-level `knowledge_docs.source_type` (what the retrieval filter sees)")
    add("")
    add("| value | rows |")
    add("| --- | --- |")
    for value, n in s["doc_source_type_counts"].items():
        add(f"| `{value}` | {n} |")
    add("")
    add("### Chunk-level `chunks.meta->>'source_kind'` (real provenance)")
    add("")
    add(f"| value | chunks | len | fits String({DOC_SOURCE_TYPE_MAX_LEN})? |")
    add("| --- | --- | --- | --- |")
    for value, n in s["chunk_source_kind_counts"].items():
        ln = rc["observed_kind_lengths"].get(value, len(value))
        add(f"| `{value}` | {n} | {ln} | {'yes' if ln <= DOC_SOURCE_TYPE_MAX_LEN else '**NO**'} |")
    add("")
    add("## Root cause: declared field width")
    add("")
    add(f"- Field: `{rc['field']}`, declared `{rc['declared_type']}` at `{rc['declaration_site']}`.")
    add(f"- Comparison field: `{rc['comparison_field']}`.")
    add(f"- Longest real kind: `{rc['longest_observed_kind']}` = **{rc['longest_observed_kind_len']} chars** "
        f"-> fits `String({rc['declared_max_len']})`: **{rc['longest_fits_declared_width']}**.")
    add(f"- Kinds that fit: {', '.join('`%s`' % k for k in rc['kinds_that_fit']) or '(none)'}.")
    add(f"- Kinds that do NOT fit: {', '.join('`%s`' % k for k in rc['kinds_that_do_not_fit']) or '(none)'}.")
    add(f"- Verdict: **{rc['verdict']}**.")
    add("")
    add("## Per-document detail")
    add("")
    add("| doc_id | title | doc source_type | chunks | chunk source_kinds | diverges | width-blocked | reason |")
    add("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for d in result.docs:
        kinds = ", ".join(f"`{k}`" for k in d.chunk_source_kinds) or "(none)"
        title = d.title.replace("|", "\\|")
        add(
            f"| `{d.doc_id}` | {title} | `{d.doc_source_type}` | {d.chunk_count} | {kinds} | "
            f"{'**YES**' if d.diverges else 'no'} | "
            f"{'**YES**' if d.string_width_blocked else 'no'} | `{d.divergence_reason}` |"
        )
    add("")
    if s["chunks_missing_source_kind"]:
        add("### Documents with chunk-level provenance gaps")
        add("")
        add("| doc_id | chunks missing `source_kind` |")
        add("| --- | --- |")
        for d in result.docs:
            if d.chunks_missing_source_kind:
                add(f"| `{d.doc_id}` | {d.chunks_missing_source_kind} |")
        add("")
    if result.warnings:
        add("## Warnings")
        add("")
        for w in result.warnings:
            add(f"- {w}")
        add("")
    return "\n".join(out).rstrip() + "\n"


def write_artifacts(result: AuditResult, json_path: str | Path | None, md_path: str | Path | None) -> None:
    if json_path:
        p = Path(json_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(result.to_json(), encoding="utf-8")
    if md_path:
        p = Path(md_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_markdown(result), encoding="utf-8")
