"""CLI entry point: ``python -m tools.corpus_integrity``.

Read-only corpus semantic-integrity auditor (WP-B). See ``core.py`` for the
defect definition and ``--help`` for the exit-code contract.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:  # `python -m tools.corpus_integrity` (package import)
    from .core import (
        DEFAULT_DATABASE_URL,
        DOC_SOURCE_TYPE_MAX_LEN,
        ENV_KEYS,
        EXIT_CLEAN,
        EXIT_DIVERGENCE,
        EXIT_EMPTY_CORPUS,
        EXIT_ERROR,
        SCHEMA_VERSION,
        CorpusAuditError,
        audit_sync,
        exit_code_for,
        write_artifacts,
    )
except ImportError:  # `python tools/corpus_integrity/__main__.py` (direct run)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from corpus_integrity.core import (  # type: ignore[no-redef]
        DEFAULT_DATABASE_URL,
        DOC_SOURCE_TYPE_MAX_LEN,
        ENV_KEYS,
        EXIT_CLEAN,
        EXIT_DIVERGENCE,
        EXIT_EMPTY_CORPUS,
        EXIT_ERROR,
        SCHEMA_VERSION,
        CorpusAuditError,
        audit_sync,
        exit_code_for,
        write_artifacts,
    )

_EPILOG = f"""\
exit codes (CI gate contract)
  0  corpus CLEAN        no doc-level/chunk-level source-kind divergence
  1  DIVERGENCE DETECTED at least one knowledge_docs row whose source_type does
                         not match the semantic kind(s) of its own chunks
  3  EMPTY CORPUS        knowledge_docs has 0 rows; nothing to audit
  2  OPERATIONAL ERROR   DB unreachable, table missing, bad DSN, bad arguments

connection resolution order
  1. --db-url
  2. ${ENV_KEYS[0]}
  3. ${ENV_KEYS[1]}          (repo convention, see services/api/app/config.py:19)
  4. builtin local dev default: {DEFAULT_DATABASE_URL}

This tool is strictly READ-ONLY: it issues SELECT statements only and never
modifies the database. Passwords are masked in all output.

schema_version: {SCHEMA_VERSION}
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.corpus_integrity",
        description=(
            "Audit divergence between knowledge_docs.source_type (doc level, "
            f"String({DOC_SOURCE_TYPE_MAX_LEN})) and chunks.meta->>'source_kind' (chunk level, real "
            "provenance). Read-only."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="PostgreSQL DSN (postgresql://... or postgresql+asyncpg://...). "
        f"Falls back to ${ENV_KEYS[0]} / ${ENV_KEYS[1]} / local default.",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        default=None,
        metavar="PATH",
        help="write the machine-readable audit to PATH (JSON, schema_version "
        f"{SCHEMA_VERSION}); parent directories are created",
    )
    parser.add_argument(
        "--markdown",
        "--md",
        dest="md_path",
        default=None,
        metavar="PATH",
        help="write the human-readable audit to PATH (Markdown)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="suppress the stdout summary (exit code still carries the verdict)",
    )
    return parser


def _print_summary(result) -> None:
    s = result.summary
    rc = result.root_cause
    print(
        f"corpus-integrity :: {result.dsn_summary.get('url_masked')} "
        f"(provenance {result.provenance})"
    )
    print(
        f"  docs={s['total_docs']} chunks={s['total_chunks']} "
        f"divergent_docs={s['docs_with_divergence']} "
        f"distinct_doc_source_types={s['distinct_doc_source_types']} "
        f"distinct_chunk_source_kinds={s['distinct_chunk_source_kinds']}"
    )
    print(
        f"  chunks_missing_source_kind={s['chunks_missing_source_kind']} "
        f"doc_source_type_is_lossy={s['is_lossy']}"
    )
    print(
        f"  docs_with_width_blocked_kind={s['docs_with_width_blocked_kind']} "
        f"chunks_under_them={s['chunks_with_width_blocked_kind']}"
    )
    for value, n in s["chunk_source_kind_counts"].items():
        ln = rc["observed_kind_lengths"].get(value, len(value))
        flag = "" if ln <= rc["declared_max_len"] else f"  <-- does NOT fit {rc['declared_type']}"
        print(f"    {value:<20} chunks={n:<6} len={ln}{flag}")
    for warning in result.warnings:
        print(f"  WARN {warning}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = audit_sync(args.db_url)
    except CorpusAuditError as exc:
        print(f"corpus-integrity: ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:  # noqa: BLE001 - never dump a traceback on infra issues
        print(f"corpus-integrity: ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR

    try:
        write_artifacts(result, args.json_path, args.md_path)
    except OSError as exc:
        print(f"corpus-integrity: ERROR: cannot write artifact: {exc}", file=sys.stderr)
        return EXIT_ERROR

    code = exit_code_for(result)
    if not args.quiet:
        _print_summary(result)
        if args.json_path:
            print(f"  json     -> {args.json_path}")
        if args.md_path:
            print(f"  markdown -> {args.md_path}")
        if code == EXIT_DIVERGENCE:
            print(
                f"  VERDICT: DOC-LEVEL/CHUNK-LEVEL SOURCE-KIND DIVERGENCE DETECTED "
                f"({result.summary['docs_with_divergence']}/{result.summary['total_docs']} docs) "
                f"-> exit {code}"
            )
        elif code == EXIT_EMPTY_CORPUS:
            print(f"  VERDICT: corpus empty -> exit {code}")
        elif code == EXIT_CLEAN:
            print(f"  VERDICT: clean -> exit {code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
