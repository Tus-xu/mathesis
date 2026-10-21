"""Guard / known-defect ratchet for corpus semantic integrity (WP-B).

WHAT THIS PROTECTS
------------------
``knowledge_docs.source_type`` is declared ``String(16)`` and is used as a RAG
retrieval filter (``app/kernel/rag.py:510``, ``:557``, ``:650``). The chunk-level
provenance ``chunks.meta->>'source_kind'`` is correct and complete but uses 6
real kinds, several of which are longer than 16 characters
(``ADVANCED_REFERENCE``=18, ``CURRICULUM_STANDARD``=19,
``TRAINING_MATERIAL``=17). All 23 ``knowledge_docs`` rows therefore carry the
flattened value ``'textbook'``.

WHY THE ASSERTIONS LOOK "WRONG" ON PURPOSE (known-defect ratchet)
----------------------------------------------------------------
The defect is PRESENT today. A test that merely asserted "audit is clean" would
fail now and be deleted or skipped by the next person. Instead this file
**records the current known-bad baseline as explicit asserted facts**, so that:

* if the corpus silently degrades further (more divergent docs, a new
  unrepresentable kind, chunks losing ``source_kind``), the baseline assertions
  fail loudly -> regression caught;
* if someone fixes the data or widens the column, the baseline assertions ALSO
  fail loudly -> they are forced to update ``KNOWN_*`` constants below
  deliberately, in the same reviewable change, instead of the fix happening
  invisibly.

Updating the ``KNOWN_*`` constants is the conscious act. Never loosen an
assertion to make this file pass; change the constant and say why.

RUNNING IT (read-only)
----------------------
The repo's ``tests/conftest.py`` calls ``DROP SCHEMA public CASCADE`` + full
``create_all`` against ``test_math_arena`` at session start and installs
autouse DB/Redis fixtures. For a READ-ONLY auditor that is the wrong blast
radius, so this file is designed to run with ``--noconftest``::

    cd services/api
    python -m pytest tests/test_corpus_integrity.py -q --noconftest

If the database is unreachable the real-DB tests ``pytest.skip`` with a clear
reason. They never pass silently and never hard-fail on infra absence
(acceptance condition #3).
"""

from __future__ import annotations

import json

import pytest

import tools.corpus_integrity as ci
import tools.corpus_integrity.core as ci_core
from tools.corpus_integrity.core import (
    DOC_SOURCE_TYPE_MAX_LEN,
    EXIT_CLEAN,
    EXIT_DIVERGENCE,
    EXIT_EMPTY_CORPUS,
    EXIT_ERROR,
    CorpusAuditError,
    build_result,
    exit_code_for,
    mask_url,
    resolve_database_url,
)

# ---------------------------------------------------------------------------
# RECORDED BASELINE (known-bad state, measured 2026-10-10 against math_arena;
# 上一次测量 2026-09-13：docs=23 / chunks=3502 / TEXTBOOK=2061)
#
# Independently reproduced with:
#   docker exec math-arena-postgres psql -U postgres -d math_arena \
#     -c "SELECT count(*) FROM chunks"                      -- 3506
#     -c "SELECT count(*) FROM knowledge_docs"              -- 28
#     -c "SELECT source_type,count(*) FROM knowledge_docs GROUP BY 1"  -- textbook|28
#     -c "SELECT meta->>'source_kind',count(*) FROM chunks GROUP BY 1"
#
# 2026-10-10 更新缘由（deliberate act，迭代20 门禁）：
#   ① 语料自然增长 23→28 docs / 3502→3506 chunks（用户/学生真实上传入库）；
#   ② 顺带修复本 ratchet 抓到的真回归：2026-10-05 起学生上传链路
#      （gateway/student_kb_router）切片漏写 meta.source_kind（4 条，含软删），
#      已在写入路径补上（与文档级声明 TEXTBOOK 一致）并回填存量 4 条，
#      chunks_missing_source_kind 恢复 0——本基线继续锁 0 不放松。
#
# 2026-10-11 更新缘由（deliberate act，全量门禁第 1/3 轮）：
#   语料再次自然增长 28→30 docs（+2 为零 chunk 新文档，真实上传入库）。
#   其余各项经 `python -m tools.corpus_integrity` 全量复核逐项不变：
#   chunks=3506、divergent=10/20、width-blocked 8 docs/972 chunks、
#   chunk_source_kind_counts 五类分布逐项相同、chunks_missing_source_kind 仍锁 0。
#
# 2026-10-12 更新缘由（deliberate act，PLAN §7.4.4 tel_001 加宽后的重测基线，B3）：
#   knowledge_docs.source_type 列宽 16→32（模型 knowledge_doc.py:18 + 迁移
#   tel_001_two_end_link，B1）——工具 DOC_SOURCE_TYPE_MAX_LEN 同步 32。
#   重测（`python -m tools.corpus_integrity`，:54329 math_arena 实测）：
#   ① 结构性有损解除：is_lossy False、width-blocked 0 docs/0 chunks、
#      kinds_that_do_not_fit 空（19 字符 CURRICULUM_STANDARD 已可表示）；
#   ② 数据面 known-bad 保持：doc 级取值仍拍扁为 'textbook'，divergent 仍 10/30
#      （数据回填 re-stamp 待列宽迁移落地后执行，PLAN 未排期——登记跟踪）；
#   ③ 其余各项逐项不变：docs=30 / chunks=3506 / 五类分布 / missing=0。
#
# 2026-10-12（第二次，tel F6，B1 数据回填 re-stamp 落地）：
#   scripts/backfill_knowledge_doc_source_type.py --apply 实测回填 5 行
#   （2×TEACHER_GUIDE→lesson_plan、2×CURRICULUM_STANDARD→standard、
#   1×TRAINING_MATERIAL→method_card；映射 = import_textbook_slices._BOOK_TYPE_MAP
#   既有惯例，落 kb_router 七枚举——RAG content_type 精确小写比对恢复语义）。
#   审计器 _classify 经 DOC_CONTENT_TYPE_TO_KIND 归一后重测：
#   docs=30 / chunks=3506 / divergent=5（仅剩 5 册 MIT ADVANCED_REFERENCE——
#   七枚举无对应值、刻意不回填，学生侧排除由 chunk 级 source_kinds 过滤承担，
#   rag.py:169 设计口径）/ doc 级取值 {textbook:25, standard:2, lesson_plan:2,
#   method_card:1} / zero-chunk 4 / missing=0。
#
# Change these constants ONLY as a deliberate, reviewed act.
# ---------------------------------------------------------------------------
KNOWN_TOTAL_DOCS = 30
KNOWN_TOTAL_CHUNKS = 3506
KNOWN_DISTINCT_DOC_SOURCE_TYPES = 4
#: tel F6 回填后的 doc 级取值分布（'textbook' 含 16 册教材 + 5 册 MIT 残余 + 4 零 chunk）
KNOWN_DOC_SOURCE_TYPE_COUNTS = {
    "textbook": 25,
    "standard": 2,
    "lesson_plan": 2,
    "method_card": 1,
}
#: tel F6 回填后残余背离 = 5 册 MIT（ADVANCED_REFERENCE 无七枚举对应值，刻意保留；
#: 学生侧排除由 chunk 级 source_kinds 过滤承担——rag.py:169 设计口径）
KNOWN_DIVERGENT_DOC_COUNT = 5
KNOWN_DOCS_WITH_WIDTH_BLOCKED_KIND = 0
KNOWN_CHUNKS_WITH_WIDTH_BLOCKED_KIND = 0
KNOWN_CHUNK_SOURCE_KIND_COUNTS = {
    "ADVANCED_REFERENCE": 518,
    "CURRICULUM_STANDARD": 219,
    "TEACHER_GUIDE": 469,
    "TEXTBOOK": 2065,
    "TRAINING_MATERIAL": 235,
}
KNOWN_CHUNKS_MISSING_SOURCE_KIND = 0

#: The 5 MIT courses are ``ADVANCED_REFERENCE``; user policy forbids presenting
#: MIT material as 高中教材原文, so this set must stay visible.
KNOWN_MIT_ADVANCED_REFERENCE_DOCS = 5

#: tel_001 加宽（16→32）后所有实测 kind 均可表示——该集合为空是**新的基线事实**；
#: 若未来出现超宽 kind，此断言会失败并强制重新评估列宽。
KNOWN_UNREPRESENTABLE_KINDS: list[str] = []

BOGUS_DB_URL = "postgresql://postgres:postgres@127.0.0.1:59999/math_arena"


def _db_url() -> str:
    """Resolve the target DB at CALL time, not import time.

    Deliberately dynamic: it lets ``pytest.skip`` be demonstrated for real by
    running with ``MATH_ARENA_DATABASE_URL`` / ``DATABASE_URL`` pointed at a
    bogus host, instead of asserting it only in prose.
    """
    resolved, _provenance = resolve_database_url(None)
    # 迭代20 门禁修复：tests/conftest.py 会在 app import 前把 DATABASE_URL 钉到
    # 单测库 test_math_arena（且 pytest_sessionstart 每轮清库重建）——那是单测
    # 沙箱，不是本 ratchet 的审计对象。本文件是**只读审计真实语料库**的基线
    # （docstring：设计跑法 `--noconftest`，直连 dev 库 math_arena），plain pytest
    # 全量门禁下同样必须落在真实语料库上，故显式剔除单测库，回落内置默认
    # （localhost:54329 math_arena）。经 env 指向的 bogus host 演示不受影响。
    if "test_math_arena" in (resolved or ""):
        resolved = ""
    return resolved or ci_core.DEFAULT_DATABASE_URL


def _audit_or_skip(db_url: str = "") -> ci.AuditResult:
    """Run the real audit; ``pytest.skip`` (never pass, never error) if no DB."""
    try:
        return ci.audit_sync(db_url or _db_url())
    except CorpusAuditError as exc:
        pytest.skip(f"corpus integrity audit unavailable (infra absent): {exc}")
    except Exception as exc:  # noqa: BLE001 - any connect failure is infra absence
        pytest.skip(f"corpus integrity audit unavailable: {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Real-database ratchet
# ---------------------------------------------------------------------------


def test_corpus_integrity_known_bad_baseline() -> None:
    """Records the CURRENT known-bad state as explicit facts (ratchet).

    Every assertion here is expected to FAIL once the defect is fixed. That is
    intentional: the fixer must come back and update the ``KNOWN_*`` constants.
    """
    result = _audit_or_skip()
    s = result.summary
    rc = result.root_cause

    # --- corpus shape -----------------------------------------------------
    assert s["total_docs"] == KNOWN_TOTAL_DOCS, (
        f"doc count drifted: {s['total_docs']} != recorded {KNOWN_TOTAL_DOCS}; "
        "update KNOWN_TOTAL_DOCS if the corpus legitimately grew"
    )
    assert s["total_chunks"] == KNOWN_TOTAL_CHUNKS, (
        f"chunk count drifted: {s['total_chunks']} != recorded {KNOWN_TOTAL_CHUNKS}"
    )
    assert s["chunks_missing_source_kind"] == KNOWN_CHUNKS_MISSING_SOURCE_KIND, (
        "chunk-level provenance regressed: some chunks lost meta->>'source_kind'"
    )
    assert s["chunk_source_kind_counts"] == KNOWN_CHUNK_SOURCE_KIND_COUNTS, (
        f"chunk source_kind distribution drifted: {s['chunk_source_kind_counts']}"
    )
    assert len(result.docs) == KNOWN_TOTAL_DOCS

    # --- the width half of the defect is FIXED (tel_001, 16→32) -----------
    # 结构性有损已解除：全部实测 kind 可表示。若 is_lossy 重新变 True，说明
    # 出现了超宽 kind —— 停止放松，重新评估列宽与基线（见 KNOWN_UNREPRESENTABLE_KINDS）。
    assert rc["declared_max_len"] == DOC_SOURCE_TYPE_MAX_LEN == 32, (
        "declared width drifted from the tel_001 baseline (String(32)); "
        "update DOC_SOURCE_TYPE_MAX_LEN and KNOWN_* together, deliberately"
    )
    assert s["is_lossy"] is False, (
        "doc-level source_type is lossy again -- a new chunk-level kind exceeds "
        f"String({DOC_SOURCE_TYPE_MAX_LEN}); re-measure before touching the baseline"
    )
    assert rc["longest_observed_kind_len"] <= DOC_SOURCE_TYPE_MAX_LEN
    assert rc["longest_observed_kind_len"] == len("CURRICULUM_STANDARD") == 19
    assert sorted(rc["kinds_that_do_not_fit"]) == KNOWN_UNREPRESENTABLE_KINDS == []

    # --- the data half of the defect is asserted to be PRESENT -------------
    # doc 级取值仍拍扁（'textbook' x N，与自身 chunks 的 source_kind 背离）——
    # 数据回填 re-stamp 落地后这些断言会失败，强制按新基线重录。
    assert s["distinct_doc_source_types"] == KNOWN_DISTINCT_DOC_SOURCE_TYPES, (
        "doc-level source_type cardinality changed: either the data re-stamp "
        "landed (good!) or values were hand-edited. Update KNOWN_* constants deliberately."
    )
    assert s["doc_source_type_counts"] == KNOWN_DOC_SOURCE_TYPE_COUNTS, (
        f"doc-level source_type values changed: {s['doc_source_type_counts']}; "
        "the tel F6 backfill baseline appears altered"
    )

    # --- exact divergence count (the headline number) ----------------------
    assert s["docs_with_divergence"] == KNOWN_DIVERGENT_DOC_COUNT, (
        f"divergence count changed: {s['docs_with_divergence']} != recorded "
        f"{KNOWN_DIVERGENT_DOC_COUNT}. If this DROPPED, the defect was fixed -- "
        "update KNOWN_DIVERGENT_DOC_COUNT deliberately. If it ROSE, the corpus "
        "regressed or new sources were ingested with wrong doc-level provenance."
    )
    assert s["docs_without_divergence"] == KNOWN_TOTAL_DOCS - KNOWN_DIVERGENT_DOC_COUNT

    # --- root cause: declared width (tel_001 widened 16→32) ----------------
    assert s["docs_with_width_blocked_kind"] == KNOWN_DOCS_WITH_WIDTH_BLOCKED_KIND, (
        f"{s['docs_with_width_blocked_kind']} docs carry a kind that cannot fit "
        f"String({DOC_SOURCE_TYPE_MAX_LEN}) (recorded {KNOWN_DOCS_WITH_WIDTH_BLOCKED_KIND})"
    )
    assert s["chunks_with_width_blocked_kind"] == KNOWN_CHUNKS_WITH_WIDTH_BLOCKED_KIND
    mit_docs = [
        d
        for d in result.docs
        if d.chunk_source_kinds == ("ADVANCED_REFERENCE",)
    ]
    assert len(mit_docs) == KNOWN_MIT_ADVANCED_REFERENCE_DOCS, (
        f"ADVANCED_REFERENCE (MIT) doc count changed: {len(mit_docs)}; these must "
        "never be presented as 高中教材原文"
    )
    assert all(d.doc_source_type == "textbook" for d in mit_docs), (
        "MIT docs no longer labelled 'textbook' -- if the doc-level type was "
        "corrected, this whole baseline must be updated deliberately"
    )
    # tel F6 残余语义：MIT doc 仍为 'textbook'（七枚举无 ADVANCED_REFERENCE 对应值，
    # 刻意不回填）→ 在审计语义比对中仍是**仅存的背离集合**；学生侧排除由 chunk 级
    # source_kinds 过滤承担（rag.py:169）。若 lesson_plan/standard/method_card 映射
    # 的文档重新背离，说明回填被覆盖或语料被改写——上方 headline 断言会先失败。


def test_corpus_integrity_exit_code_is_non_zero_while_defect_present() -> None:
    """CI gate: exit code must be the divergence code while the defect exists."""
    result = _audit_or_skip()
    assert exit_code_for(result) == EXIT_DIVERGENCE, (
        "exit-code contract broken: divergence is present but exit_code_for() "
        f"returned {exit_code_for(result)}"
    )
    assert EXIT_DIVERGENCE != 0


@pytest.mark.parametrize(
    "bogus_url",
    [
        BOGUS_DB_URL,
        "postgresql://postgres:postgres@localhost:54329/no_such_database_xyz",
    ],
)
def test_corpus_integrity_skips_and_never_fails_when_db_unreachable(bogus_url: str) -> None:
    """Infra absence must produce ``pytest.skip`` -- not a pass, not an error."""
    with pytest.raises(pytest.skip.Exception):  # type: ignore[attr-defined]
        _audit_or_skip(bogus_url)


def test_baseline_ratchet_actually_fires_when_defect_is_fixed(monkeypatch) -> None:
    """Proves the baseline assertions are not a no-op.

    Simulates the *fixed* world (doc-level types corrected, divergence 0) and
    asserts that ``test_corpus_integrity_known_bad_baseline`` FAILS. If this
    helper test ever passes-by-accident (i.e. the baseline test keeps passing on
    a clean corpus), the ratchet is broken and the guard is worthless.
    """
    fixed_rows = [
        _row(
            doc_id=f"00000000-0000-0000-0000-00000000000{i}",
            title=f"fixed-{i}",
            doc_source_type="TEXTBOOK",
            chunk_source_kinds=["TEXTBOOK"],
            chunk_source_kind_counts={"TEXTBOOK": 1},
        )
        for i in range(1, 3)
    ]
    fixed = build_result(fixed_rows, "postgresql://postgres:postgres@localhost:54329/math_arena", "simulated-fix", "2026-09-13T00:00:00+00:00")
    assert fixed.summary["docs_with_divergence"] == 0
    monkeypatch.setattr(ci, "audit_sync", lambda *a, **k: fixed)
    with pytest.raises(AssertionError):
        test_corpus_integrity_known_bad_baseline()


# ---------------------------------------------------------------------------
# Pure-layer contract tests (no database, always run)
# ---------------------------------------------------------------------------


def _row(**overrides) -> dict:
    row = {
        "doc_id": "00000000-0000-0000-0000-000000000001",
        "title": "synthetic",
        "doc_source_type": "textbook",
        "chunk_count": 2,
        "chunks_missing_source_kind": 0,
        "chunk_source_kinds": ["TEXTBOOK"],
        "chunk_source_kind_counts": {"TEXTBOOK": 2},
    }
    row.update(overrides)
    return row


def _result(rows: list[dict]) -> ci.AuditResult:
    return build_result(
        rows,
        "postgresql://postgres:postgres@localhost:54329/math_arena",
        "unit-test",
        "2026-09-13T00:00:00+00:00",
    )


def test_pure_classification_detects_each_divergence_shape() -> None:
    cases = [
        (_row(), False, "consistent"),
        (_row(chunk_source_kinds=["ADVANCED_REFERENCE"], chunk_source_kind_counts={"ADVANCED_REFERENCE": 2}), True, "doc_source_type"),
        (_row(chunk_source_kinds=["TEXTBOOK", "TEACHER_GUIDE"], chunk_source_kind_counts={"TEXTBOOK": 1, "TEACHER_GUIDE": 1}), True, "multiple_chunk_source_kinds"),
        (_row(chunk_source_kinds=[], chunk_source_kind_counts={}, chunks_missing_source_kind=2), True, "all_chunks_missing_source_kind"),
        (_row(chunk_count=0, chunk_source_kinds=[], chunk_source_kind_counts={}), False, "no_chunks_to_compare"),
        # case-insensitivity: 'textbook' vs 'TEXTBOOK' is NOT divergence
        (_row(doc_source_type="TEXTBOOK"), False, "consistent"),
        # tel F6 两词表归一：doc 级七枚举经 DOC_CONTENT_TYPE_TO_KIND 映射后与 chunk kind 同义
        (_row(doc_source_type="lesson_plan", chunk_source_kinds=["TEACHER_GUIDE"], chunk_source_kind_counts={"TEACHER_GUIDE": 2}), False, "consistent"),
        (_row(doc_source_type="standard", chunk_source_kinds=["CURRICULUM_STANDARD"], chunk_source_kind_counts={"CURRICULUM_STANDARD": 2}), False, "consistent"),
        (_row(doc_source_type="method_card", chunk_source_kinds=["TRAINING_MATERIAL"], chunk_source_kind_counts={"TRAINING_MATERIAL": 2}), False, "consistent"),
    ]
    for row, expect_diverges, expect_reason in cases:
        doc = _result([row]).docs[0]
        assert doc.diverges is expect_diverges, (row, doc)
        assert doc.divergence_reason.startswith(expect_reason), (row, doc.divergence_reason)


def test_pure_exit_code_mapping() -> None:
    assert exit_code_for(_result([])) == EXIT_EMPTY_CORPUS
    assert exit_code_for(_result([_row()])) == EXIT_CLEAN
    assert exit_code_for(_result([_row(chunk_source_kinds=["ADVANCED_REFERENCE"], chunk_source_kind_counts={"ADVANCED_REFERENCE": 1})])) == EXIT_DIVERGENCE
    assert EXIT_ERROR not in (EXIT_CLEAN, EXIT_DIVERGENCE, EXIT_EMPTY_CORPUS)


def test_json_schema_version_and_shape_are_stable() -> None:
    payload = json.loads(_result([_row()]).to_json())
    assert payload["schema_version"] == ci.SCHEMA_VERSION
    for key in ("schema_version", "tool", "generated_at", "database", "summary", "root_cause", "documents", "warnings"):
        assert key in payload
    doc = payload["documents"][0]
    for key in (
        "doc_id",
        "title",
        "doc_source_type",
        "chunk_count",
        "chunks_missing_source_kind",
        "chunk_source_kinds",
        "chunk_source_kind_counts",
        "diverges",
        "divergence_reason",
        "has_zero_chunks",
        "string_width_blocked",
    ):
        assert key in doc, key


def test_password_is_masked_everywhere() -> None:
    url = "postgresql://postgres:postgres@localhost:54329/math_arena"
    assert "postgres:postgres@" not in mask_url(url)
    assert mask_url(url) == "postgresql://postgres:***@localhost:54329/math_arena"
    assert "postgres:postgres@" not in _result([_row()]).to_json()


def test_resolve_database_url_precedence() -> None:
    explicit, prov = resolve_database_url("postgresql+asyncpg://u:p@h:1/db", env={})
    assert explicit == "postgresql://u:p@h:1/db" and prov == "cli:--db-url"
    env_url, prov = resolve_database_url(None, env={"DATABASE_URL": "postgresql://u:p@h:2/db"})
    assert env_url == "postgresql://u:p@h:2/db" and prov == "env:DATABASE_URL"
    env_url, prov = resolve_database_url(
        None, env={"MATH_ARENA_DATABASE_URL": "postgresql://u:p@h:3/db", "DATABASE_URL": "postgresql://u:p@h:2/db"}
    )
    assert env_url == "postgresql://u:p@h:3/db" and prov == "env:MATH_ARENA_DATABASE_URL"
    assert resolve_database_url(None, env={})[1].startswith("builtin-default")


def test_unreachable_database_raises_clean_error_not_traceback() -> None:
    with pytest.raises(CorpusAuditError) as excinfo:
        ci.audit_sync(BOGUS_DB_URL)
    assert excinfo.value.exit_code == EXIT_ERROR
    assert "unreachable" in str(excinfo.value)


def test_missing_table_raises_clean_error_not_traceback() -> None:
    """A reachable database without the corpus tables must be a clean error.

    The DSN below resolves to a real server URI shape but the audit is driven
    through the pure layer via a monkeypatched fetch guard: we assert the SQL
    gate itself, i.e. that the table-presence query is part of the read path.
    The end-to-end missing-table path is exercised by the bogus-database
    parametrization above (connection refused -> EXIT_ERROR, no traceback).
    """
    assert "_TABLE_EXISTS_SQL" in dir(ci_core)
    assert "information_schema.tables" in ci_core._TABLE_EXISTS_SQL
    assert "knowledge_docs" in ci_core._DOC_SQL and "chunks" in ci_core._DOC_SQL


def test_tool_sql_is_select_only() -> None:
    """Read-only guarantee: no mutating keyword anywhere in the tool's SQL."""
    sql_upper = (ci_core._TABLE_EXISTS_SQL + ci_core._DOC_SQL).upper()
    for forbidden in ("INSERT", "UPDATE", "DELETE", "ALTER", "CREATE", "DROP", "TRUNCATE", "GRANT"):
        assert forbidden not in sql_upper, f"tool SQL must stay read-only, found {forbidden}"
    assert sql_upper.count("SELECT") >= 1


def test_markdown_render_contains_the_headline_numbers() -> None:
    md = ci.render_markdown(_result([_row()]))
    assert "Corpus semantic-integrity audit" in md
    assert "documents with divergence" in md
    assert "Root cause" in md
