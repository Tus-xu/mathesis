"""RV-WB · 自研内核服务层单测。

注入 fake LLM backend（`gateway.set_backend`）与 fake 文献 provider，
覆盖：错误码映射、TeX 双层校验（**入参命中守卫绝不调用模型**）、
真实检索映射、诚实降级（不伪造）、以及第 2 阶段新增契约字段
（success/changes/final_updated_code/security_msg、bilingual_blocks/formulas、
method 生成字段 + ai_generated_fields）。
纯单测，不触网 / 不编译 / 不连库。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from research_vnext.core.literature.provider import (
    LiteratureMetadata,
    ProviderRateLimited,
    ProviderTimeout,
)
from research_vnext.core.llm import gateway
from research_vnext.core.writing import service as wb
from research_vnext.core.writing.models import (
    WB_LLM_BAD_OUTPUT,
    WB_LLM_NOT_CONFIGURED,
    WB_LLM_TIMEOUT,
    WB_LITERATURE_UNAVAILABLE,
    WB_TEX_SOURCE_INVALID,
    WB_UNKNOWN,
    WbBadRequest,
    WbError,
    WbLlmBadOutput,
    WbLlmNotConfigured,
    WbLlmTimeout,
    WbLiteratureUnavailable,
    WbTexSourceInvalid,
)

VALID_TEX = "\\documentclass{article}\n\\begin{document}\nHello $x^2$\n\\end{document}\n"
EDITED_TEX = "\\documentclass{article}\n\\begin{document}\nHi $x^2$\n\\end{document}\n"
DANGEROUS_TEX = "\\lstinputlisting{/etc/passwd}"


class RecordingBackend:
    """最小 LLM 后端：记录**调用次数**（用于断言守卫未调用模型），可注入正文/延迟/异常。"""

    name = "recording"

    def __init__(
        self, text: str = "ok", *, model: str = "fake-model", delay: float = 0.0, error=None
    ):
        self._text = text
        self._model = model
        self._delay = delay
        self._error = error
        self.available = True
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    async def acompletion(self, *, model=None, messages, stream=False, **kw):  # noqa: ANN001,ANN003
        self.calls += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return gateway.Completion(
            text=self._text,
            model=model or self._model,
            provider="fake",
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
            raw={},
        )


class FakeProvider:
    """最小文献 provider（`name` + `search`）。"""

    name = "fake-lit"

    def __init__(self, items=None, *, error=None):
        self._items = list(items or [])
        self._error = error
        self.calls: list[dict] = []

    def search(self, query: str, *, limit: int = 5, request_id: str = ""):
        self.calls.append({"query": query, "limit": limit, "request_id": request_id})
        if self._error is not None:
            raise self._error
        return self._items[:limit]


def _meta(title: str, doi: str = "") -> LiteratureMetadata:
    return LiteratureMetadata(
        provider="crossref",
        provider_version="1.0.0",
        provider_record_id=doi or title,
        request_id="rid",
        query="q",
        doi=doi,
        title=title,
        authors=[{"given": "Ada", "family": "Lovelace", "name": "Ada Lovelace"}],
        venue="J. Test",
        published_date="2024",
        source_url=f"https://doi.org/{doi}" if doi else "",
        retrieved_at="2024-01-01T00:00:00Z",
        raw_response_hash="deadbeef",
        raw={"secret_internal": "should_not_leak"},
    )


_METHOD_JSON = json.dumps(
    {
        "method_name_en": "Simulated Annealing",
        "keywords": ["annealing", "optimization"],
        "core_formula": "P = \\exp(-\\Delta E / T)",
        "common_scenario": "组合优化",
        "assumptions": ["状态空间有限"],
        "precondition_checklist": ["定义能量函数"],
        "limitation": "收敛慢",
    },
    ensure_ascii=False,
)


@pytest.fixture(autouse=True)
def _reset_backend():
    yield
    gateway.set_backend(None)


# --------------------------------------------------------------------------
# capabilities
# --------------------------------------------------------------------------


def test_capabilities_engine_ownership_and_schema() -> None:
    gateway.set_backend(RecordingBackend())
    caps = wb.capabilities()
    items = {c["key"]: c for c in caps["items"]}
    assert items["writing_polish"]["engine"] == "xingchen"  # 仅此一项保留讯飞
    assert items["latex_editing"]["engine"] == "self"
    assert items["method_rag"]["engine"] == "self"
    assert items["academic_translate"]["engine"] == "self"
    # 新增：数据驱动 schema（**规范字段名**，别再手抄 camelCase）
    assert "file_content" in items["latex_editing"]["requestSchema"]
    assert "target_file?" in items["latex_editing"]["requestSchema"]
    assert "final_updated_code" in items["latex_editing"]["responseKeys"]
    assert "target_lang" in items["academic_translate"]["requestSchema"]
    assert "source_lang?" in items["academic_translate"]["requestSchema"]
    assert "bilingual_blocks" in items["academic_translate"]["responseKeys"]
    assert "method_name" in items["method_rag"]["requestSchema"]
    assert caps["llm"]["configured"] is True


def test_capabilities_never_leaks_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_LLM_API_KEY", "SENTINEL_SECRET_DO_NOT_LEAK")
    gateway.set_backend(RecordingBackend())
    payload = json.dumps(wb.capabilities(), ensure_ascii=False)
    assert "SENTINEL_SECRET_DO_NOT_LEAK" not in payload


# --------------------------------------------------------------------------
# edit_latex
# --------------------------------------------------------------------------


def test_edit_latex_happy_path_contract() -> None:
    gateway.set_backend(RecordingBackend(text="```latex\n" + EDITED_TEX + "```"))
    out = asyncio.run(wb.edit_latex(VALID_TEX, "把 Hello 改成 Hi"))
    # 契约键
    assert out["success"] is True
    assert out["security_msg"] == ""
    assert out["final_updated_code"] == EDITED_TEX.strip()
    assert out["source"] == EDITED_TEX.strip()
    # 真实行级 diff（Hello→Hi 至少一条）
    assert isinstance(out["changes"], list) and out["changes"]
    ch0 = out["changes"][0]
    assert ch0["file"] == "main.tex"
    # 契约：old_lines/new_lines 是**字符串**（多行用 \n 连接）
    assert isinstance(ch0["old_lines"], str) and isinstance(ch0["new_lines"], str)
    assert ch0["old_lines"] == "Hello $x^2$"
    assert ch0["new_lines"] == "Hi $x^2$"
    # 行数组放在额外的 _list 键里（不改主键类型）
    assert ch0["old_lines_list"] == ["Hello $x^2$"]
    assert ch0["new_lines_list"] == ["Hi $x^2$"]
    assert out["model"] == "fake-model"


def test_edit_latex_changes_multiline_joined_with_newline() -> None:
    """多行改动块：`old_lines`/`new_lines` 用 `\\n` 连接（仍是字符串）。"""
    old = "a\nb\nc\n"
    new = "a\nb\nX\nY\n"
    gateway.set_backend(RecordingBackend(text=new))
    out = asyncio.run(wb.edit_latex(old, "replace c with X,Y"))
    assert out["success"] is True
    ch = out["changes"][0]
    assert isinstance(ch["old_lines"], str) and ch["old_lines"] == "c"
    assert isinstance(ch["new_lines"], str) and ch["new_lines"] == "X\nY"
    assert ch["new_lines_list"] == ["X", "Y"]


def test_edit_latex_no_change_yields_empty_changes() -> None:
    gateway.set_backend(RecordingBackend(text=VALID_TEX))
    out = asyncio.run(wb.edit_latex(VALID_TEX, "keep"))
    assert out["success"] is True
    assert out["changes"] == []


def test_edit_latex_dangerous_input_does_not_call_model() -> None:
    """P0 回归：入参命中守卫 → 抛错 **且模型调用次数为 0**。"""
    backend = RecordingBackend(text=VALID_TEX)
    gateway.set_backend(backend)
    with pytest.raises(WbTexSourceInvalid) as ei:
        asyncio.run(wb.edit_latex("\\write18{rm -rf /}", "clean"))
    assert ei.value.code == WB_TEX_SOURCE_INVALID
    assert ei.value.http_status == 400
    assert backend.calls == 0, "命中入参守卫时**绝不能**调用模型"


@pytest.mark.parametrize(
    "evil",
    [
        "\\write18{rm -rf /}",
        "\\immediate\\write18{rm -rf /}",
        "\\openout1=x.tex",
        "\\input{/etc/passwd}",
        "\\lstinputlisting{/etc/passwd}",
        "\\VerbatimInput{/etc/passwd}",
        "\\usepackage{shellesc}\\ShellEscape{rm -rf /}",
        "^^5cwrite18{rm -rf /}",
    ],
)
def test_edit_latex_dangerous_variants_never_call_model(evil: str) -> None:
    backend = RecordingBackend(text=VALID_TEX)
    gateway.set_backend(backend)
    with pytest.raises(WbTexSourceInvalid):
        asyncio.run(wb.edit_latex(evil, "x"))
    assert backend.calls == 0


def test_edit_latex_dangerous_instruction_does_not_call_model() -> None:
    """P0 回归（历史漏洞）：危险命令出现在 **instruction** 里时，源码是干净的 ——
    也必须**在任何 LLM 调用之前**被拦截，模型调用次数为 0。

    真机曾复现：只校验源码 → 指令里的 `\\write18` 放行 → 模型被调用（33.86s/60s）。
    """
    backend = RecordingBackend(text=VALID_TEX)
    gateway.set_backend(backend)
    with pytest.raises(WbTexSourceInvalid) as ei:
        asyncio.run(wb.edit_latex(VALID_TEX, "请在导言区加入 \\write18{rm -rf /}"))
    assert ei.value.code == WB_TEX_SOURCE_INVALID
    assert ei.value.http_status == 400
    assert backend.calls == 0, "指令命中守卫时**绝不能**调用模型"


@pytest.mark.parametrize(
    "evil_instruction",
    [
        "\\write18{rm -rf /}",
        "\\immediate\\write18{id}",
        "请把 \\input{/etc/passwd} 展开",
        "\\csname write\\endcsname18{rm -rf /}",
        "\\usepackage{shellesc}\\ShellEscape{id}",
        "^^5cwrite18{rm -rf /}",
    ],
)
def test_edit_latex_instruction_variants_never_call_model(evil_instruction: str) -> None:
    backend = RecordingBackend(text=VALID_TEX)
    gateway.set_backend(backend)
    with pytest.raises(WbTexSourceInvalid):
        asyncio.run(wb.edit_latex(VALID_TEX, evil_instruction))
    assert backend.calls == 0


def test_edit_latex_dangerous_target_file_does_not_call_model() -> None:
    """target_file（路径语义）同样纳入前置守卫。"""
    backend = RecordingBackend(text=VALID_TEX)
    gateway.set_backend(backend)
    with pytest.raises(WbTexSourceInvalid):
        asyncio.run(wb.edit_latex(VALID_TEX, "tidy", filename="\\write18{id}.tex"))
    assert backend.calls == 0


def test_edit_latex_benign_instruction_still_passes() -> None:
    """正常指令（含普通 LaTeX 控制序列，如 \\cite / \\textbf）不得被误伤。"""
    backend = RecordingBackend(text=EDITED_TEX)
    gateway.set_backend(backend)
    out = asyncio.run(wb.edit_latex(VALID_TEX, "把 \\textbf{Hello} 改成 \\cite{ref1}"))
    assert out["success"] is True
    assert backend.calls == 1


def test_edit_latex_rejects_dangerous_model_output() -> None:
    gateway.set_backend(RecordingBackend(text=DANGEROUS_TEX))
    with pytest.raises(WbLlmBadOutput) as ei:
        asyncio.run(wb.edit_latex(VALID_TEX, "read a file"))
    assert ei.value.code == WB_LLM_BAD_OUTPUT
    assert ei.value.http_status == 502
    # 输出不合格是确定性失败 → 不可重试（重试只会白烧额度）
    assert ei.value.retryable is False


def test_edit_latex_empty_source() -> None:
    gateway.set_backend(RecordingBackend())
    with pytest.raises(WbTexSourceInvalid):
        asyncio.run(wb.edit_latex("   ", "x"))


def test_edit_latex_missing_instruction() -> None:
    gateway.set_backend(RecordingBackend())
    with pytest.raises(WbBadRequest) as ei:
        asyncio.run(wb.edit_latex(VALID_TEX, "  "))
    assert ei.value.code == WB_UNKNOWN


# --------------------------------------------------------------------------
# translate
# --------------------------------------------------------------------------

_TRANS_JSON = json.dumps(
    {
        "translated_text": "The theorem states that $x^2 \\ge 0$.",
        "blocks": [
            {"source": "定理：", "target": "Theorem: "},
            {"source": "$x^2 \\ge 0$。", "target": "$x^2 \\ge 0$."},
        ],
    },
    ensure_ascii=False,
)


def test_translate_happy_path_contract() -> None:
    gateway.set_backend(RecordingBackend(text=_TRANS_JSON))
    out = asyncio.run(wb.translate("定理：$x^2\\ge 0$。", source_lang="zh", target_lang="en"))
    assert out["translated_text"] == "The theorem states that $x^2 \\ge 0$."
    assert out["text"] == out["translated_text"]
    assert len(out["bilingual_blocks"]) == 2
    assert out["formulas_total"] == 1
    assert out["formulas_preserved"] == 1
    assert out["warning"] is None


def test_translate_non_json_output_degrades_honestly() -> None:
    # 模型没给严格 JSON → 整段译文 + 空 blocks + warning（绝不朴素配对）
    gateway.set_backend(RecordingBackend(text="Plain translation with $x^2 \\ge 0$."))
    out = asyncio.run(wb.translate("定理：$x^2\\ge 0$。", source_lang="zh", target_lang="en"))
    assert out["bilingual_blocks"] == []
    assert out["warning"] == wb.BILINGUAL_UNAVAILABLE_WARNING
    assert out["formulas_preserved"] == 1


def test_translate_parses_json_with_latex_backslashes() -> None:
    """真机根因回归：模型给了严格 JSON，但字符串含**未转义 LaTeX**（`\\ge`）。

    `\\g` 不是合法 JSON 转义，朴素 `json.loads` 会抛错；历史缺陷据此走了"非 JSON"
    分支，把整串 JSON 当译文吐给用户。修复后必须正确解出译文与段级对照。
    """
    raw = (
        '{"translated_text": "定理：$x \\ge 0$。", '
        '"blocks": [{"source": "Theorem: $x \\ge 0$.", "target": "定理：$x \\ge 0$。"}]}'
    )
    gateway.set_backend(RecordingBackend(text=raw))
    out = asyncio.run(wb.translate("Theorem: $x \\ge 0$.", source_lang="en", target_lang="zh"))
    assert out["translated_text"] == "定理：$x \\ge 0$。"
    assert not out["translated_text"].lstrip().startswith("{")
    assert "translated_text" not in out["translated_text"]
    assert out["bilingual_blocks"] == [
        {"source": "Theorem: $x \\ge 0$.", "target": "定理：$x \\ge 0$。"}
    ]
    assert out["warning"] is None


def test_translate_parses_direct_json() -> None:
    payload = json.dumps(
        {"translated_text": "译文", "blocks": [{"source": "src", "target": "译文"}]},
        ensure_ascii=False,
    )
    gateway.set_backend(RecordingBackend(text=payload))
    out = asyncio.run(wb.translate("src", source_lang="en", target_lang="zh"))
    assert out["translated_text"] == "译文"
    assert out["bilingual_blocks"] == [{"source": "src", "target": "译文"}]
    assert out["warning"] is None


def test_translate_parses_fenced_json() -> None:
    payload = json.dumps(
        {"translated_text": "译文", "blocks": [{"source": "src", "target": "译文"}]},
        ensure_ascii=False,
    )
    gateway.set_backend(RecordingBackend(text="```json\n" + payload + "\n```"))
    out = asyncio.run(wb.translate("src", source_lang="en", target_lang="zh"))
    assert out["translated_text"] == "译文"
    assert len(out["bilingual_blocks"]) == 1


def test_translate_parses_json_with_surrounding_noise() -> None:
    payload = json.dumps(
        {"translated_text": "译文", "blocks": [{"source": "src", "target": "译文"}]},
        ensure_ascii=False,
    )
    gateway.set_backend(RecordingBackend(text="好的，以下是译文：\n" + payload + "\n（完）"))
    out = asyncio.run(wb.translate("src", source_lang="en", target_lang="zh"))
    assert out["translated_text"] == "译文"
    assert len(out["bilingual_blocks"]) == 1


def test_translate_json_without_blocks_degrades() -> None:
    """模型确实没给 blocks → 用 translated_text，blocks=[] + warning。"""
    gateway.set_backend(RecordingBackend(text='{"translated_text": "纯译文", "blocks": []}'))
    out = asyncio.run(wb.translate("src", source_lang="en", target_lang="zh"))
    assert out["translated_text"] == "纯译文"
    assert out["bilingual_blocks"] == []
    assert out["warning"] == wb.BILINGUAL_UNAVAILABLE_WARNING


def test_translate_salvages_text_from_truncated_json() -> None:
    """截断的 JSON：仍能抢救出 `translated_text`，**绝不**把 JSON 串当译文。"""
    gateway.set_backend(RecordingBackend(text='{"translated_text": "只有译文的片段", "blocks": ['))
    out = asyncio.run(wb.translate("src", source_lang="en", target_lang="zh"))
    assert out["translated_text"] == "只有译文的片段"
    assert out["bilingual_blocks"] == []
    assert out["warning"] == wb.BILINGUAL_UNAVAILABLE_WARNING


def test_translate_unusable_json_raises_not_leaks() -> None:
    """JSON 里根本没有可用译文 → 诚实 502，**绝不**把 JSON 串当译文吐出。"""
    gateway.set_backend(RecordingBackend(text='{"some": "json", "without": "translated_text"}'))
    with pytest.raises(WbLlmBadOutput):
        asyncio.run(wb.translate("src", source_lang="en", target_lang="zh"))


def test_translate_missing_text() -> None:
    gateway.set_backend(RecordingBackend())
    with pytest.raises(WbBadRequest):
        asyncio.run(wb.translate("", source_lang="zh", target_lang="en"))


def test_translate_missing_target_lang() -> None:
    gateway.set_backend(RecordingBackend())
    with pytest.raises(WbBadRequest):
        asyncio.run(wb.translate("hi", source_lang="zh", target_lang=""))


# --------------------------------------------------------------------------
# LLM 配置 / 超时
# --------------------------------------------------------------------------


def test_llm_not_configured() -> None:
    gateway.set_backend(gateway.UnavailableBackend())
    with pytest.raises(WbLlmNotConfigured) as ei:
        asyncio.run(wb.translate("hi", source_lang="zh", target_lang="en"))
    assert ei.value.code == WB_LLM_NOT_CONFIGURED
    assert ei.value.http_status == 503


def test_llm_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESEARCH_WB_LLM_TIMEOUT_SECONDS", "0.05")
    gateway.set_backend(RecordingBackend(text="late", delay=1.0))
    with pytest.raises(WbLlmTimeout) as ei:
        asyncio.run(wb.translate("hi", source_lang="zh", target_lang="en"))
    assert ei.value.code == WB_LLM_TIMEOUT
    assert ei.value.http_status == 504
    # 超时是瞬时负载类 → 可重试
    assert ei.value.retryable is True


def test_llm_default_timeout_is_bounded() -> None:
    """默认硬性上限必须 ≤60s（防 99.8s 级失控）。"""
    assert wb.wb_llm_timeout_s() <= 60.0


def test_llm_gate_error_mapped_to_wb_error() -> None:
    gateway.set_backend(RecordingBackend(error=gateway.LlmGateError("LLM_CALL_FAILED", "boom")))
    with pytest.raises(WbError) as ei:
        asyncio.run(wb.translate("hi", source_lang="zh", target_lang="en"))
    assert ei.value.http_status == 502


# --------------------------------------------------------------------------
# search_methods / method_search（文献内核 + 可选 LLM）
# --------------------------------------------------------------------------


def test_search_methods_maps_real_metadata() -> None:
    prov = FakeProvider([_meta("A Method", doi="10.1/x"), _meta("B Method")])
    out = wb.search_methods("method", limit=5, provider=prov)
    assert out["count"] == 2
    assert out["provider"] == "fake-lit"
    assert prov.calls[0]["query"] == "method"
    first = out["items"][0]
    assert "raw" not in first
    assert first["record_kind"] == "METADATA_ONLY"
    assert "should_not_leak" not in json.dumps(out)


def test_search_methods_rejects_empty_query() -> None:
    with pytest.raises(WbBadRequest):
        wb.search_methods("  ", provider=FakeProvider())


def test_search_methods_honest_degrade_on_timeout() -> None:
    with pytest.raises(WbLiteratureUnavailable) as ei:
        wb.search_methods("method", provider=FakeProvider(error=ProviderTimeout("t")))
    assert ei.value.code == WB_LITERATURE_UNAVAILABLE
    assert ei.value.http_status == 503


def test_search_methods_honest_degrade_on_rate_limit() -> None:
    with pytest.raises(WbLiteratureUnavailable) as ei:
        wb.search_methods("method", provider=FakeProvider(error=ProviderRateLimited("r")))
    assert ei.value.code == WB_LITERATURE_UNAVAILABLE


def test_search_methods_clamps_limit() -> None:
    prov = FakeProvider([_meta(f"m{i}") for i in range(30)])
    out = wb.search_methods("m", limit=999, provider=prov)
    assert out["count"] == wb.WB_METHOD_MAX_RESULTS
    assert prov.calls[0]["limit"] == wb.WB_METHOD_MAX_RESULTS


def test_method_search_full_contract_with_enrichment() -> None:
    gateway.set_backend(RecordingBackend(text=_METHOD_JSON))
    out = asyncio.run(
        wb.method_search("simulated annealing", method_name="模拟退火", provider=FakeProvider([_meta("SA Paper", "10.1/sa")]))
    )
    assert out["method_name"] == "模拟退火"  # 回显入参
    assert out["method_name_en"] == "Simulated Annealing"
    assert out["keywords"] == ["annealing", "optimization"]
    assert out["sources"] and out["sources"][0]["title"] == "SA Paper"
    assert out["sources"][0]["year"] == "2024"
    assert out["sources"][0]["authors"] == ["Ada Lovelace"]
    assert out["evidence_records"][0]["record_kind"] == "METADATA_ONLY"
    assert "method_name_en" in out["ai_generated_fields"]
    assert "reference_note" in out and "模型生成" in out["reference_note"]
    assert out["warning"] is None


def test_method_search_degrades_when_llm_unavailable() -> None:
    """LLM 不可用 → 方法说明字段留空 + warning，但**整体成功且返回真实 sources**。"""
    gateway.set_backend(gateway.UnavailableBackend())
    out = asyncio.run(
        wb.method_search("svm", method_name="支持向量机", provider=FakeProvider([_meta("SVM Paper", "10.1/svm")]))
    )
    assert out["sources"] and out["sources"][0]["doi"] == "10.1/svm"
    assert out["keywords"] == []
    assert out["core_formula"] == ""
    assert out["ai_generated_fields"] == []
    assert out["warning"]


def test_method_search_empty_sources_warns() -> None:
    gateway.set_backend(RecordingBackend(text=_METHOD_JSON))
    out = asyncio.run(wb.method_search("nothing", provider=FakeProvider([])))
    assert out["sources"] == []
    assert out["warning"] and "未检索到" in out["warning"]


def test_method_search_literature_failure_is_503() -> None:
    gateway.set_backend(RecordingBackend(text=_METHOD_JSON))
    with pytest.raises(WbLiteratureUnavailable):
        asyncio.run(wb.method_search("svm", provider=FakeProvider(error=ProviderTimeout("t"))))
