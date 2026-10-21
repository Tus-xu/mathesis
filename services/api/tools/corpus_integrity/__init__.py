"""Canonical corpus semantic-integrity auditor (WP-B, read-only).

Public surface:

* :func:`audit_sync` -- run the audit against a PostgreSQL corpus (SELECT only).
* :func:`build_result` -- pure row -> result transform (no I/O, unit-testable).
* :func:`exit_code_for` -- the CI-gate exit-code contract.
* :func:`render_markdown` -- human-readable rendering.

CLI: ``python -m tools.corpus_integrity --help``.
"""

from .core import (
    DOC_SOURCE_TYPE_MAX_LEN,
    ENV_KEYS,
    EXIT_CLEAN,
    EXIT_DIVERGENCE,
    EXIT_EMPTY_CORPUS,
    EXIT_ERROR,
    EXPECTED_SOURCE_KINDS,
    SCHEMA_VERSION,
    AuditResult,
    CorpusAuditError,
    DocRow,
    audit_sync,
    build_result,
    exit_code_for,
    mask_url,
    render_markdown,
    resolve_database_url,
    write_artifacts,
)

__all__ = [
    "DOC_SOURCE_TYPE_MAX_LEN",
    "ENV_KEYS",
    "EXIT_CLEAN",
    "EXIT_DIVERGENCE",
    "EXIT_EMPTY_CORPUS",
    "EXIT_ERROR",
    "EXPECTED_SOURCE_KINDS",
    "SCHEMA_VERSION",
    "AuditResult",
    "CorpusAuditError",
    "DocRow",
    "audit_sync",
    "build_result",
    "exit_code_for",
    "mask_url",
    "render_markdown",
    "resolve_database_url",
    "write_artifacts",
]
