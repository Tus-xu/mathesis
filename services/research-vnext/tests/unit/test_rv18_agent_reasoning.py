"""RV18 · Research Agent 真实推理路径的**纯函数**单元测试（不联网、不连库）。

覆盖本轮改动里最容易回归的几条红线（宪法 §33/§34/§35/§37/§56/§85/§90）：
- 模型 JSON 里的 LaTeX 反斜杠不能被 JSON 转义吃掉（`\\nabla` 不能变成换行+abla）；
- 没有真实来源时不许自称 verified/supported；
- 条件缺失必须点名缺什么（而不是"需要更多信息"）；
- 面向用户的文案不得出现实现术语；
- LaTeX 错误解释的 Diff 必须来自**真实源码行**，拿不到就不给 Diff；
- section 启发式不得再把正文句子/公式碎片当章节标题。
"""

from __future__ import annotations

import uuid

import pytest

from research_vnext.core.research_workspace import reasoning as r
from research_vnext.core.research_workspace.pdfs import _looks_like_heading

BS = "\\"


# --------------------------------------------------------------------------
# JSON 提取（LaTeX 反斜杠 / 裸换行 / 围栏 / 截断）
# --------------------------------------------------------------------------


def test_extract_json_keeps_latex_commands() -> None:
    """`\\nabla`/`\\times`/`\\beta`/`\\frac`/`\\rho` 必须原样保留。

    这几个是 JSON 合法转义（`\\n`/`\\t`/`\\b`/`\\f`/`\\r`）与 LaTeX 命令名的
    真实冲突点；丢了它们公式就坏了（宪法 §76）。
    """
    text = (
        '{"summary": "由 ' + BS + 'nabla u 与 ' + BS + 'times ' + BS + 'beta 得",'
        ' "math": "' + BS + 'frac{1}{2}' + BS + 'rho"}'
    )
    got = r._extract_json(text)
    assert got is not None
    assert BS + "nabla" in got["summary"]
    assert BS + "times" in got["summary"]
    assert BS + "beta" in got["summary"]
    assert got["math"] == BS + "frac{1}{2}" + BS + "rho"


def test_extract_json_keeps_real_newline() -> None:
    got = r._extract_json('{"s": "line1' + BS + 'nline2"}')
    assert got == {"s": "line1\nline2"}


def test_extract_json_strips_code_fence() -> None:
    got = r._extract_json('```json\n{"status": "likely"}\n```')
    assert got == {"status": "likely"}


def test_extract_json_returns_none_for_garbage() -> None:
    assert r._extract_json("模型今天不想输出 JSON") is None


# --------------------------------------------------------------------------
# 推导校验结果组装
# --------------------------------------------------------------------------

_SOURCE = {
    "id": "11111111-1111-1111-1111-111111111111",
    "paper_id": "11111111-1111-1111-1111-111111111111",
    "title": "A Real Paper",
    "authors": "A. Author",
    "year": 2015,
    "page": 7,
    "section": "2 Preliminaries",
    "locator": {"paper_id": "11111111-1111-1111-1111-111111111111", "page": 7, "bbox": None},
    "snippet": "",
}


def test_verified_without_cited_source_downgrades_to_likely() -> None:
    """§90：没有真实来源 ⇒ 不许自称"已验证"。"""
    res = r.assemble_math_result(
        kind="derivation_check",
        parsed={"status": "verified", "summary": "看起来对", "source_ids": []},
        sources=[_SOURCE],
        selected="a = a",
    )
    assert res["status"] == "likely"


def test_verified_never_returned_by_llm_path() -> None:
    """§35/§90：模型路径**不冒充确定性**（只有符号校验才配 verified）。"""
    res = r.assemble_math_result(
        kind="derivation_check",
        parsed={"status": "verified", "summary": "对", "source_ids": [_SOURCE["id"]]},
        sources=[_SOURCE],
        selected="a = a",
    )
    assert res["status"] == "likely"


def test_missing_conditions_are_named_not_vague() -> None:
    """§34：必须说清"缺哪一个假设"，而不是"需要更多信息"。"""
    res = r.assemble_math_result(
        kind="derivation_check",
        parsed={
            "status": "likely",
            "summary": "该不等式是 Céa 引理的推论。",
            "missing_conditions": ["缺少 α>0 的 V-椭圆条件声明"],
            "source_ids": [],
        },
        sources=[],
        selected="",
    )
    assert res["status"] == "needs_conditions"
    assert "α>0" in res["summary"]
    assert any("缺少的假设" in f["label"] for f in res["findings"])


def test_anchor_source_always_in_evidence() -> None:
    """用户选中的文字来自哪篇哪页是**确定事实**，必须能「定位原文」。"""
    res = r.assemble_math_result(
        kind="derivation_check",
        parsed={"status": "likely", "summary": "结论", "source_ids": []},
        sources=[_SOURCE],
        selected="x",
        anchor=_SOURCE,
    )
    assert res["evidence"][0]["paper_id"] == _SOURCE["id"]
    assert res["evidence"][0]["page"] == 7


@pytest.mark.parametrize(
    "bad", ["用 RAG 检索", "top-k 命中", "embedding 相似度", "tool call 结果", "doubao 生成"]
)
def test_user_facing_text_has_no_implementation_terms(bad: str) -> None:
    """§27/§85：科研用户面前不得出现实现术语与模型名。"""
    out = r.sanitize_user_text(bad)
    lowered = out.lower()
    for term in ("rag", "top-k", "embedding", "tool call", "doubao"):
        assert term not in lowered


def test_degraded_result_is_human_readable() -> None:
    """§56/§90：降级必须是人话，且不得出现错误码/traceback/裸 JSON。"""
    res = r.degraded_math_result("derivation_check", "timeout", "x")
    assert res["status"] == "insufficient"
    assert res["evidence"] == []
    visible = " ".join(
        [res["summary"]] + [f"{f['label']} {f['detail']}" for f in res["findings"]]
    )
    for leak in ("WS_", "Traceback", "LLM_", "timeout", "{", "}"):
        assert leak not in visible


# --------------------------------------------------------------------------
# LaTeX 错误解释
# --------------------------------------------------------------------------


class _File:
    def __init__(self, path: str, kind: str, content: str) -> None:
        self.path = path
        self.kind = kind
        self.content = content


def test_classify_error_maps_to_chinese() -> None:
    assert r.classify_error("LaTeX Warning: Citation `Cea1971' undefined")[0] == "未定义的引用标识符"
    assert r.classify_error("! Undefined control sequence.")[0] == "未定义的控制序列"
    assert r.classify_error("! I can't find file `foo.tex'.")[0] == "找不到文件"


def test_classify_error_handles_real_log_shape() -> None:
    """真实日志里标识符与 `undefined` 之间夹着 "on page N"。"""
    raw = "LaTeX Warning: Citation `Cea1971' on page 1 undefined on input line 20."
    assert r.classify_error(raw)[0] == "未定义的引用标识符"
    assert r.undefined_key(raw) == "Cea1971"
    # 汇总行没有具体标识符 ⇒ 不该被当成可定位的缺陷
    assert r.undefined_key("LaTeX Warning: There were undefined references.") == ""


def test_build_error_item_uses_real_source_line() -> None:
    files = [_File("main.tex", "tex", "a\nb\n\\cite{Cea1971} here\n"), _File("refs.bib", "bib", "@book{Ciarlet2002,\n}\n")]
    item = r.build_error_item(
        log_line={"text": "LaTeX Warning: Citation `Cea1971' undefined", "file": "main.tex", "line": 3},
        entry="main.tex",
        files=files,
    )
    assert item["file"] == "main.tex"
    assert item["line"] == 3
    assert item["source_line"] == "\\cite{Cea1971} here"
    assert item["kind"] == "citation"
    assert item["key"] == "Cea1971"


def test_build_patch_uses_real_before_and_existing_bib_key() -> None:
    files = [_File("main.tex", "tex", "\\cite{Cea1971}\n"), _File("refs.bib", "bib", "@book{Ciarlet2002,\n}\n")]
    patch = r.build_patch(
        kind="citation",
        raw="LaTeX Warning: Citation `Cea1971' undefined",
        source_line="\\cite{Cea1971}",
        file_="main.tex",
        line=1,
        files=files,
    )
    assert patch is not None
    assert patch["before"] == "\\cite{Cea1971}"
    assert patch["after"] == "\\cite{Ciarlet2002}"
    assert patch["line"] == 1


def test_build_patch_refuses_when_line_does_not_match() -> None:
    """行号对不上就**不给** Diff —— 宁可没有，也不给一个假修复（§39/§40）。"""
    files = [_File("main.tex", "tex", "no citation here\n"), _File("refs.bib", "bib", "@book{Ciarlet2002,\n}\n")]
    patch = r.build_patch(
        kind="citation",
        raw="LaTeX Warning: Citation `Cea1971' undefined",
        source_line="no citation here",
        file_="main.tex",
        line=1,
        files=files,
    )
    assert patch is None


def test_build_patch_absent_without_source_line() -> None:
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


def test_rule_explanation_is_human_readable() -> None:
    text = r._rule_explanation("未定义的引用标识符", "citation", "", "Cea1971", 28)
    assert "Cea1971" in text and "第 28 行" in text
    for leak in ("RAG", "embedding", "top-k", "chunk"):
        assert leak not in text


# --------------------------------------------------------------------------
# section 启发式（PDF 章节识别）
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "methods. Analysis for the mixed elements is rather straightforward. However, derivation of",
        "2 sin (πθ)",
        "F F ∈ ED",
        "2 projection of f onto Dk for k ≥ 0. Define local and global weighted",
        "2 Ghesmati, Bangerth, Turcksin",
        "1 3.2629e−01 − 2.1724e−01 1.0050e−01",
    ],
)
def test_false_positive_headings_are_rejected(line: str) -> None:
    assert _looks_like_heading(line) is False


@pytest.mark.parametrize(
    "line",
    [
        "1 Introduction",
        "1.1 Quasi-Monotonicity Assumption",
        "2 Sobolev Space and Preliminaries",
        "3.1 mixed finite element method",
        "5.3 Error bound for the Dirichlet–Neumann map",
        "References",
        "Abstract",
        "FINITE ELEMENT METHODS",
        # 中文标题天然短（1 个"词"、2 个字）——收紧启发式时**不能**把它们误杀。
        "1 引言",
        "2 附录说明",
        "3 方法",
        "参考文献",
    ],
)
def test_real_headings_are_kept(line: str) -> None:
    assert _looks_like_heading(line) is True


# --------------------------------------------------------------------------
# P0-4 · 幂等键（(tenant_id, actor_id, client_request_id)）
# --------------------------------------------------------------------------


def test_idempotency_lock_key_is_stable_and_tenant_scoped() -> None:
    """同一 `(tenant, actor, cid)` 必须得到同一个键；换任一维度都必须变。"""
    from research_vnext.core.research_workspace.agent import _idempotency_lock_key

    t = uuid.UUID("11111111-1111-1111-1111-111111111111")
    a = uuid.UUID("22222222-2222-2222-2222-222222222222")
    b = uuid.UUID("33333333-3333-3333-3333-333333333333")

    k = _idempotency_lock_key(t, a, "req-1")
    assert k == _idempotency_lock_key(t, a, "req-1")
    assert k != _idempotency_lock_key(t, a, "req-2")
    assert k != _idempotency_lock_key(t, b, "req-1")
    assert k != _idempotency_lock_key(b, a, "req-1")
    # pg_advisory_xact_lock 收 64 位有符号整数
    assert -(2**63) <= k < 2**63


def test_latex_error_source_locations_never_fabricate_line_one() -> None:
    """§90：日志行没有行号时，不得把位置顶替成"第 1 行"。"""
    import inspect

    from research_vnext.core.research_workspace import agent as a

    src = inspect.getsource(a._run_latex_error)
    assert "line < 1" in src and "continue" in src, "必须跳过没有真实行号的位置"
    assert 'or 1)' not in src, "不得再用 `int(it[\"line\"] or 1)` 顶替行号"
