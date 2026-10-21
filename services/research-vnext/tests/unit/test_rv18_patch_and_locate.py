"""RV18 修复 · 纯函数单元测试（不联网、不连库）。

覆盖本轮三个真实缺陷的**可回归**断言（宪法 §14/§33/§39/§40）：

1. B8「定位原文」：`locate_bbox` 必须**分级匹配**（60→40→24→16→8），
   让「标题 + 正文」拼串也能落到单块；且 `_source_dict` 的
   `locator.selected_text` 必须带**真实 snippet**（不再是空串）。
2. C10「应用修复」：`build_patch` 必须支持 `control`（未定义控制序列）
   与**无扩展名** `\\input{}` 两类；且**绝不发明命令 / 给假 Diff**。
3. 拿不到真实 before/after 时一律返回 None（诚实下限）。
"""

from __future__ import annotations

import types

from research_vnext.core.research_workspace import reasoning as r

BS = "\\"


class _File:
    def __init__(self, path: str, kind: str, content: str) -> None:
        self.path = path
        self.kind = kind
        self.content = content


def _row(*, blocks: list[dict], page: int = 1) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        title="A Real Paper",
        authors=["A. Author"],
        year=2015,
        pages=[{"page": page, "blocks": blocks}],
    )


# --------------------------------------------------------------------------
# B8 · locate_bbox 分级匹配 + _source_dict.selected_text
# --------------------------------------------------------------------------


def _blocks() -> list[dict]:
    return [
        {"text": "2 Preliminaries", "bbox": [10.0, 20.0, 200.0, 40.0]},
        {"text": "Let V be a Hilbert space with inner product.", "bbox": [10.0, 50.0, 300.0, 120.0]},
    ]


def test_locate_bbox_grading_matches_single_block_when_needle_spans_blocks() -> None:
    """「标题 + 正文」拼串：60 字符整前缀跨块匹配不到，分级到短前缀要能命中标题块。"""
    blocks = _blocks()
    row = _row(blocks=blocks)
    needle = "2 Preliminaries Let V be a Hilbert space with inner product."
    # 反证：整串（含 60 字符前缀）不落在**任一单个 block** 内 —— 旧实现必然返回 None。
    for b in blocks:
        assert needle[:60].lower() not in b["text"].lower()
    bbox = r.locate_bbox(row, page=1, needle=needle)
    assert bbox == [10.0, 20.0, 200.0, 40.0]


def test_locate_bbox_prefers_longest_matching_prefix() -> None:
    """取**能命中的最长前缀**：正文块比标题块更长，拼串里正文片段应命中正文块。"""
    row = _row(blocks=_blocks())
    bbox = r.locate_bbox(row, page=1, needle="Let V be a Hilbert space")
    assert bbox == [10.0, 50.0, 300.0, 120.0]


def test_locate_bbox_returns_none_for_too_short_needle() -> None:
    """短于最小有效长度（8 字符）一律不匹配，避免"几个字"在全页到处命中（假高亮）。"""
    row = _row(blocks=_blocks())
    assert r.locate_bbox(row, page=1, needle="Prelim") is None


def test_locate_bbox_returns_none_when_absent_never_fabricates() -> None:
    """定位不到必须返回 None（前端退化为"只跳页"），**绝不**给个大概 bbox。"""
    row = _row(blocks=_blocks())
    assert r.locate_bbox(row, page=1, needle="nothing of the sort appears here") is None


def test_locate_bbox_returns_none_for_unknown_page() -> None:
    row = _row(blocks=_blocks(), page=1)
    assert r.locate_bbox(row, page=9, needle="Let V be a Hilbert space") is None


def test_source_dict_locator_carries_real_selected_text() -> None:
    """`locator.selected_text` 必须带上真实（sanitize 后）snippet，而不是空串。"""
    row = _row(blocks=_blocks())
    src = r._source_dict(row, page=1, section="2 Preliminaries", snippet="Let V be  a Hilbert space")
    assert src["locator"]["selected_text"] == "Let V be a Hilbert space"
    assert src["snippet"] == src["locator"]["selected_text"]
    assert src["locator"]["selected_text"] != ""


def test_source_dict_empty_snippet_stays_empty() -> None:
    row = _row(blocks=_blocks())
    src = r._source_dict(row, page=1, section="", snippet="")
    assert src["locator"]["selected_text"] == ""


# --------------------------------------------------------------------------
# C10 · build_patch：control（未定义控制序列）
# --------------------------------------------------------------------------


def test_build_patch_control_fixes_transposed_common_command() -> None:
    patch = r.build_patch(
        kind="control",
        raw="! Undefined control sequence.",
        source_line=BS + "tabel{1}",
        file_="main.tex",
        line=42,
        files=[],
    )
    assert patch is not None
    assert patch["before"] == BS + "tabel{1}"
    assert patch["after"] == BS + "table{1}"
    assert patch["line"] == 42
    assert patch["file"] == "main.tex"


def test_build_patch_control_returns_none_when_all_commands_known() -> None:
    """行内命令全在白名单 ⇒ 不是笔误 ⇒ 不给 Diff。"""
    plain = r.build_patch(
        kind="control",
        raw="! Undefined control sequence.",
        source_line=BS + "section{Intro} and " + BS + "label{sec:intro}",
        file_="main.tex",
        line=3,
        files=[],
    )
    assert plain is None


def test_build_patch_control_does_not_fabricate_for_custom_macro() -> None:
    """自定义宏（长度差 > 1）不得被当成手误 —— 否则就是假 Diff（§14/§33）。"""
    patch = r.build_patch(
        kind="control",
        raw="! Undefined control sequence.",
        source_line=BS + "myvec{x}",
        file_="main.tex",
        line=7,
        files=[],
    )
    assert patch is None


def test_build_patch_control_returns_none_for_unrecognizable_command() -> None:
    patch = r.build_patch(
        kind="control",
        raw="! Undefined control sequence.",
        source_line=BS + "zzzqqq{1}",
        file_="main.tex",
        line=7,
        files=[],
    )
    assert patch is None


def test_build_patch_control_absent_without_source_line() -> None:
    assert (
        r.build_patch(
            kind="control",
            raw="! Undefined control sequence.",
            source_line="",
            file_="main.tex",
            line=1,
            files=[],
        )
        is None
    )


# --------------------------------------------------------------------------
# C10 · build_patch：file（含**无扩展名** \\input{}）
# --------------------------------------------------------------------------


def _file_files() -> list[_File]:
    return [
        _File("main.tex", "tex", BS + "input{intro}\n"),
        _File("sections/introduction.tex", "tex", "\\section{Introduction}\n"),
    ]


def test_build_patch_file_without_extension_matches_unique_real_file() -> None:
    """无扩展名 `\\input{intro}` 应唯一匹配到 `sections/introduction.tex` 并给出补丁。"""
    patch = r.build_patch(
        kind="file",
        raw="! I can't find file `intro'.",
        source_line=BS + "input{intro}",
        file_="main.tex",
        line=1,
        files=_file_files(),
    )
    assert patch is not None
    assert patch["before"] == BS + "input{intro}"
    assert patch["after"] == BS + "input{sections/introduction}"
    assert patch["after"] != patch["before"]


def test_build_patch_file_without_extension_ambiguous_returns_none() -> None:
    """同名多候选（歧义）⇒ 宁可不给补丁，也不改错文件（§14/§33）。"""
    files = [
        _File("main.tex", "tex", BS + "input{intro}\n"),
        _File("sections/introduction.tex", "tex", "a\n"),
        _File("appendix/introduction.tex", "tex", "b\n"),
    ]
    patch = r.build_patch(
        kind="file",
        raw="! I can't find file `intro'.",
        source_line=BS + "input{intro}",
        file_="main.tex",
        line=1,
        files=files,
    )
    assert patch is None


def test_build_patch_file_with_extension_keeps_directory() -> None:
    """带扩展名且在子目录：只替换文件名词干，保留原目录。"""
    files = [
        _File("main.tex", "tex", BS + "input{sections/old.tex}\n"),
        _File("sections/new.tex", "tex", "x\n"),
    ]
    patch = r.build_patch(
        kind="file",
        raw="! I can't find file `sections/old.tex'.",
        source_line=BS + "input{sections/old.tex}",
        file_="main.tex",
        line=1,
        files=files,
    )
    assert patch is not None
    assert patch["after"] == BS + "input{sections/new.tex}"


def test_build_patch_file_target_exists_returns_none() -> None:
    """目标文件确实存在（说明报错不是"找不到文件"）⇒ 不改。"""
    files = [
        _File("main.tex", "tex", BS + "input{sections/introduction.tex}\n"),
        _File("sections/introduction.tex", "tex", "x\n"),
    ]
    patch = r.build_patch(
        kind="file",
        raw="! I can't find file `sections/introduction.tex'.",
        source_line=BS + "input{sections/introduction.tex}",
        file_="main.tex",
        line=1,
        files=files,
    )
    assert patch is None


def test_build_patch_file_absent_without_source_line() -> None:
    patch = r.build_patch(
        kind="file",
        raw="! I can't find file `intro'.",
        source_line="",
        file_="main.tex",
        line=1,
        files=_file_files(),
    )
    assert patch is None


# --------------------------------------------------------------------------
# 并发编译 · 幂等/排队契约（纯函数部分）
# --------------------------------------------------------------------------


def test_compile_in_progress_error_contract() -> None:
    """并发编译被拒：机器可读码 + 409 + 携带既有 run（前端据此提示并复用）。"""
    from research_vnext.core.research_workspace.compile import CompileInProgress

    err = CompileInProgress("已有编译在进行", run_id="run-1", status="running")
    assert err.code == "COMPILE_IN_PROGRESS"
    assert err.http_status == 409
    payload = err.to_dict()
    assert payload["code"] == "COMPILE_IN_PROGRESS"
    assert payload["run_id"] == "run-1"
    assert payload["status"] == "running"


def test_compile_advisory_lock_key_is_stable_and_scoped() -> None:
    """同一 (tenant, project, entry) 恒得同一键；任一维度变化键即变（串行化同一入口）。"""
    import uuid

    from research_vnext.core.research_workspace.compile import _compile_lock_key

    t = uuid.UUID("11111111-1111-1111-1111-111111111111")
    p = uuid.UUID("22222222-2222-2222-2222-222222222222")
    p2 = uuid.UUID("33333333-3333-3333-3333-333333333333")
    key = _compile_lock_key(t, p, "main.tex")
    assert key == _compile_lock_key(t, p, "main.tex")
    assert key != _compile_lock_key(t, p, "chapter.tex")
    assert key != _compile_lock_key(t, p2, "main.tex")
    # pg_advisory_xact_lock 收 64 位有符号整数
    assert -(2**63) <= key < 2**63

