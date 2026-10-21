"""RV-WB · 对抗性验证（QA 严过关 / Edward · 第 2 轮）。

目标：**证伪** `wb` 自研内核端点（`/wb/capabilities` / `/wb/latex:edit` /
`/wb/method:search` / `/wb/translate`）的纵深防御与「诚实降级」声明，而非盖章。

覆盖攻击面（对应主理人任务书 A–G）：
- A 别名机制 / 类型混淆 / 未知字段 —— 危险值能否绕过守卫到达模型；
- B 守卫本身的绕过（在 `instruction` 字段上打）—— 真机 + 单元双证；
- C `method:search` 的「绝不编造文献」铁律；
- D `translate` 解析健壮性（纯译文 / 公式诚实计数 / 无 blocks 不冒充 / 不可解析诚实 502）；
- E 鉴权矩阵 + 能力原则（`method:search` 调 LLM 却只要 READ？）+ 凭据零泄漏；
- G 跨端点契约（`changes[].old_lines/new_lines` 必须是字符串）。

铁律：本文件**只新建**，不改任何既有文件；对 LLM upstream 用 `FakeBackend` 捕获
真实 prompt（零真实额度消耗），对文献内核用 `FakeProvider`。
"""

from __future__ import annotations

import json
import os
import pathlib
import re

import pytest

from research_vnext.core.literature.provider import (
    LiteratureMetadata,
    ProviderTimeout,
)
from research_vnext.core.llm import gateway
from research_vnext.core.writing import service as wb_service

PREFIX = "/api/research-vnext"

CLEAN_TEX = "\\documentclass{article}\n\\begin{document}\nHello $x^2\\ge 0$\n\\end{document}\n"
EDITED_TEX = "\\documentclass{article}\n\\begin{document}\nHi $x^2\\ge 0$\n\\end{document}\n"

#: 形似真实 DOI 的字符串（用于「绝不编造文献」回归断言）。
_DOI_RE = re.compile(r"10\.\d{4,}/")


class CapturingBackend:
    """记录**实际发给模型**的全部消息，用于证明危险值是否到达 upstream。"""

    name = "capture"

    def __init__(self, text: str = EDITED_TEX, *, error: Exception | None = None) -> None:
        self._text = text
        self._error = error
        self.available = True
        self.calls = 0
        self.sent: list[list[dict]] = []

    @property
    def model(self) -> str:
        return "capture-model"

    async def acompletion(self, *, model=None, messages, stream=False, **kw):  # noqa: ANN001,ANN003
        self.calls += 1
        self.sent.append(messages)
        if self._error is not None:
            raise self._error
        return gateway.Completion(text=self._text, model="capture-model", provider="capture", raw={})

    def all_text(self) -> str:
        return "\n".join(
            str(m.get("content") or "") for msgs in self.sent for m in msgs if isinstance(m, dict)
        )


class FakeProvider:
    """最小文献 provider（记录被调用的 limit，便于断言钳制）。"""

    name = "fake-lit"

    def __init__(self, items=None, *, error: Exception | None = None) -> None:
        self._items = list(items or [])
        self._error = error
        self.seen_limit: int | None = None

    def search(self, query, *, limit=5, request_id=""):  # noqa: ANN001
        self.seen_limit = limit
        if self._error is not None:
            raise self._error
        return self._items[:limit]


def _meta(title: str, doi: str = "") -> LiteratureMetadata:
    return LiteratureMetadata(
        provider="crossref",
        provider_version="1.0.0",
        provider_record_id=doi or title,
        request_id="r",
        query="q",
        doi=doi,
        title=title,
        authors=[{"given": "Ada", "family": "Lovelace", "name": "Ada Lovelace"}],
        venue="J. Test",
        published_date="2024",
        source_url=f"https://doi.org/{doi}" if doi else "",
        retrieved_at="2024-01-01T00:00:00Z",
        raw={"secret_internal": "should_not_leak"},
    )


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from research_vnext.core.api import wb_routes

    app = FastAPI()
    app.include_router(wb_routes.router, prefix=PREFIX)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_backend():
    yield
    gateway.set_backend(None)


def _post_edit(client, hdr_factory, ctx, body):
    return client.post(f"{PREFIX}/wb/latex:edit", headers=hdr_factory(ctx), json=body)


# ==========================================================================
# A. 别名机制能否绕过守卫
# ==========================================================================


def test_a1_danger_only_in_alias_source_is_rejected_without_calling_model(
    client, ctx_factory, hdr_factory
) -> None:
    """危险命令**只**在别名 `source` 键里 → 必须 400 且**不调用模型**。"""
    backend = CapturingBackend()
    gateway.set_backend(backend)
    r = _post_edit(client, hdr_factory, ctx_factory(), {"source": "\\write18{rm -rf /}", "instruction": "x"})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["success"] is False and body["security_msg"]
    assert body["error"]["code"] == "WB_TEX_SOURCE_INVALID"
    assert backend.calls == 0, "危险别名被解析成源码后必须被守卫拦下，绝不能达到模型"


def test_a1b_danger_only_in_alias_filename_is_rejected_without_calling_model(
    client, ctx_factory, hdr_factory
) -> None:
    """危险命令**只**在别名 `filename` 键里 → 必须 400 且不调用模型。"""
    backend = CapturingBackend()
    gateway.set_backend(backend)
    r = _post_edit(
        client,
        hdr_factory,
        ctx_factory(),
        {"file_content": CLEAN_TEX, "instruction": "x", "filename": "\\write18{id}"},
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "WB_TEX_SOURCE_INVALID"
    assert backend.calls == 0


def test_a1c_target_file_clean_filename_dangerous_actual_behaviour(
    client, ctx_factory, hdr_factory
) -> None:
    """`target_file`(干净) 与 `filename`(危险) 同时给 → 规范名优先，别名被忽略。

    安全属性（真正的断言）：被忽略的危险别名**绝不**出现在发往模型的任何消息里。
    注：实现对本例返回 200（别名 inert），与模块 docstring「守卫解析后的三项值」一致，
    但严于/宽于主理人「应 400」的预期 —— 见 QA 报告 A.2 漏过项(信息级)。
    """
    backend = CapturingBackend(text=EDITED_TEX)
    gateway.set_backend(backend)
    r = _post_edit(
        client,
        hdr_factory,
        ctx_factory(),
        {"file_content": CLEAN_TEX, "instruction": "tidy", "target_file": "main.tex", "filename": "\\write18{id}"},
    )
    # 无论 400 还是 200，危险别名都绝不能进 upstream —— 这是不可妥协的安全属性。
    assert "write18" not in backend.all_text(), "被忽略的别名竟到达了模型"
    if r.status_code == 200:
        assert r.json()["target_file"] == "main.tex", "规范名 target_file 应优先"
    else:
        assert r.status_code == 400


def test_a2_canonical_and_alias_both_present_dangerous_alias_inert(
    client, ctx_factory, hdr_factory
) -> None:
    """`file_content`(干净) + `source`(危险) 同时给 → 规范名生效；危险别名 inert。"""
    backend = CapturingBackend(text=EDITED_TEX)
    gateway.set_backend(backend)
    r = _post_edit(
        client,
        hdr_factory,
        ctx_factory(),
        {"file_content": CLEAN_TEX, "instruction": "tidy", "source": "\\write18{rm -rf /}"},
    )
    assert "write18" not in backend.all_text(), "被忽略的 source 别名竟到达了模型"
    if r.status_code == 400:
        assert backend.calls == 0
    else:
        assert r.status_code == 200 and backend.calls == 1


def test_a3_unknown_extra_fields_are_ignored_not_passed_to_model(
    client, ctx_factory, hdr_factory
) -> None:
    """未知字段（extra_payload/note/context/prompt）带负载 → 必须**被忽略**（非到达模型）。"""
    backend = CapturingBackend(text=EDITED_TEX)
    gateway.set_backend(backend)
    r = _post_edit(
        client,
        hdr_factory,
        ctx_factory(),
        {
            "file_content": CLEAN_TEX,
            "instruction": "tidy",
            "extra_payload": "\\write18{id}",
            "note": "\\write18{note}",
            "context": "\\write18{ctx}",
            "prompt": "\\write18{prompt}",
        },
    )
    assert r.status_code == 200, r.text
    text = backend.all_text()
    for marker in ("extra_payload", "\\write18{note}", "\\write18{ctx}", "\\write18{prompt}"):
        assert marker not in text, f"未知字段 {marker!r} 竟被拼进了 prompt"
    # 证明「被忽略」而不是「被使用但未守卫」：模型确实被调用了一次（说明请求被处理）。
    assert backend.calls == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("instruction", ["a", "b"]),
        ("instruction", {"a": 1}),
        ("instruction", 123),
        ("instruction", None),
        ("instruction", True),
        ("file_content", ["\\write18{id}"]),
        ("file_content", {"tex": "x"}),
        ("file_content", 42),
    ],
)
def test_a4_type_confusion_is_deterministically_rejected(
    client, ctx_factory, hdr_factory, field, value
) -> None:
    """类型混淆必须**确定性 4xx**（不是 500，也不是 `str(list)` 静默通过）。"""
    backend = CapturingBackend()
    gateway.set_backend(backend)
    body = {"file_content": CLEAN_TEX, "instruction": "tidy"}
    body[field] = value
    r = _post_edit(client, hdr_factory, ctx_factory(), body)
    assert 400 <= r.status_code < 500, f"非 4xx: {r.status_code} {r.text[:200]}"
    assert r.status_code != 500
    assert backend.calls == 0, f"类型混淆 {field}={value!r} 竟调用了模型"
    # 尤其：绝不允许 str(list) 把 \write18 洗成 "['\\write18{id}']" 后放行
    assert "write18" not in backend.all_text()


def test_a4_json_array_body_is_not_500(client, ctx_factory, hdr_factory) -> None:
    """顶层 JSON 数组（而非对象）→ 4xx（FastAPI 校验），绝不是 500。"""
    r = client.post(
        f"{PREFIX}/wb/latex:edit",
        headers=hdr_factory(ctx_factory()),
        json=[1, 2, 3],
    )
    assert 400 <= r.status_code < 500, r.text
    assert r.status_code != 500


def test_a5_oversize_source_rejected(client, ctx_factory, hdr_factory) -> None:
    """源码 > 2MB 上限 → 400（防滥用），不调用模型。"""
    backend = CapturingBackend()
    gateway.set_backend(backend)
    big = "a" * (2 * 1024 * 1024 + 16)
    r = _post_edit(client, hdr_factory, ctx_factory(), {"file_content": big, "instruction": "x"})
    assert r.status_code == 400, r.status_code
    assert "上限" in r.json()["error"]["message"]
    assert backend.calls == 0


def test_a5_instruction_has_no_length_cap_FINDING(client, ctx_factory, hdr_factory) -> None:
    """[漏过项·P3] `instruction` 无任何长度上限（与 source 的 2MB 上限不对称）。

    证明 1MB 指令被原样接收并送往模型 —— 记录当前行为，供主理人定级。
    """
    backend = CapturingBackend(text=EDITED_TEX)
    gateway.set_backend(backend)
    r = _post_edit(
        client,
        hdr_factory,
        ctx_factory(),
        {"file_content": CLEAN_TEX, "instruction": "x" * (1024 * 1024)},
    )
    assert r.status_code == 200, r.text
    assert backend.calls == 1
    assert len(backend.all_text()) >= 1024 * 1024, "1MB 指令确实被完整送往模型（无上限）"


# ==========================================================================
# B. 守卫本身的绕过（在 instruction 字段上打）
# ==========================================================================

_DANGEROUS_INSTRUCTIONS = [
    r"\write18{rm -rf /}",
    r"\immediate\write18{id}",
    "\\write\n18{id}",  # \write 后接换行
    r"\input{/etc/passwd}",
    r"\include{/etc/passwd}",
    r"\lstinputlisting{/etc/passwd}",
    r"\VerbatimInput{/etc/passwd}",
    r"\inputminted{python}{/etc/passwd}",
    r"\@@input /etc/passwd",
    r"\openin1 /etc/passwd",
    r"\openout1 /tmp/x",
    r"\catcode`\@=0",
    r"\csname write\endcsname18",
    r"\string\write18",
    r"\directlua{os.execute('id')}",
    r"\special{ps: /etc/passwd}",
    r"\read1 to \x",
    "^^5cwrite18",
    r"\usepackage{shellesc}",
    "\\write\u200b18{id}",  # 零宽字符插入
    r"\def\x{\write}\x18{id}",
]


@pytest.mark.parametrize("evil", _DANGEROUS_INSTRUCTIONS)
def test_b_dangerous_instruction_always_400_and_never_reaches_model(
    client, ctx_factory, hdr_factory, evil
) -> None:
    """危险控制序列出现在 `instruction`（源码干净）→ 400 且不调用模型。"""
    backend = CapturingBackend(text=EDITED_TEX)
    gateway.set_backend(backend)
    r = _post_edit(client, hdr_factory, ctx_factory(), {"file_content": CLEAN_TEX, "instruction": evil})
    assert r.status_code == 400, f"未拦下 {evil!r}: {r.status_code} {r.text[:160]}"
    body = r.json()
    assert body["success"] is False and body["changes"] == [] and body["final_updated_code"] == ""
    assert body["error"]["code"] == "WB_TEX_SOURCE_INVALID"
    assert backend.calls == 0, f"指令 {evil!r} 竟到达模型"
    assert "write18" not in backend.all_text()


@pytest.mark.parametrize(
    "inert",
    [
        "$x \\ge 0$ 且 $y \\le 1$",  # 正常数学，不得误伤
        r"\includegraphics{fig.png}",  # 不得因 include 前缀误伤
        r"把标题居中并加粗",
    ],
)
def test_b_negative_control_benign_instruction_passes(
    client, ctx_factory, hdr_factory, inert
) -> None:
    """反向控制：良性指令**不得**被守卫误杀（否则是过宽拒绝的可用性缺陷）。"""
    backend = CapturingBackend(text=EDITED_TEX)
    gateway.set_backend(backend)
    r = _post_edit(client, hdr_factory, ctx_factory(), {"file_content": CLEAN_TEX, "instruction": inert})
    assert r.status_code == 200, f"良性指令被误拒: {inert!r} → {r.status_code} {r.text[:160]}"


@pytest.mark.parametrize(
    "confusable",
    [
        "＼write18{id}",  # 全角反斜杠（非 TeX 转义符）
        "\\ W R I T E 1 8",  # \ 后接空格 → 控制空格，非 \write
        r"\WRITE18{id}",  # 大写：TeX 控制序列区分大小写 → 非 \write
        r"\readline{x}",  # \read 后接字母 → 不同控制序列
    ],
)
def test_b_lenient_confusables_pass_but_are_tex_inert(
    client, ctx_factory, hdr_factory, confusable
) -> None:
    """[信息级] 这些串**不**命中逐字正则，但在 TeX 词法下**不构成**危险控制序列。

    记录实际行为（放行 → 200）。安全性依赖：(1) 它们不是 \\write；(2) 模型输出侧
    仍有第二层守卫。若后续认为应一并拒绝，需扩展正则（属设计取舍，非当前 P0）。
    """
    backend = CapturingBackend(text=EDITED_TEX)
    gateway.set_backend(backend)
    r = _post_edit(client, hdr_factory, ctx_factory(), {"file_content": CLEAN_TEX, "instruction": confusable})
    # 只断言"不是 5xx"，不强制 200/400 —— 记录型用例。
    assert r.status_code != 500, r.text


# ==========================================================================
# C. method:search 的「不编造文献」铁律
# ==========================================================================


def test_c_provider_failure_is_503_and_never_fabricates(
    client, ctx_factory, hdr_factory, monkeypatch
) -> None:
    """检索失败 → 503，响应体**不含任何 DOI 形状字符串**、无 sources 条目。"""
    backend = CapturingBackend(text='{"translated_text":"x"}')
    gateway.set_backend(backend)
    monkeypatch.setattr(
        wb_service, "_default_literature_provider", lambda: FakeProvider(error=ProviderTimeout("t"))
    )
    r = client.post(
        f"{PREFIX}/wb/method:search",
        headers=hdr_factory(ctx_factory()),
        json={"method_name": "模拟退火"},
    )
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "WB_LITERATURE_UNAVAILABLE"
    assert not _DOI_RE.search(r.text), "检索失败时响应体出现了 DOI 形状字符串（疑似编造）"
    assert backend.calls == 0, "检索失败不得触发 LLM 编造顶替"


def test_c_empty_results_with_llm_configured_has_no_fabricated_sources(
    client, ctx_factory, hdr_factory, monkeypatch
) -> None:
    """真实检索 0 条 + LLM 可用 → 200，但 `sources==[]`、`warning` 非空、无 DOI。"""
    backend = CapturingBackend(
        text=json.dumps(
            {
                "method_name_en": "Simulated Annealing",
                "keywords": ["annealing"],
                "core_formula": "P = exp(-dE/T)",
                "common_scenario": "",
                "assumptions": [],
                "precondition_checklist": [],
                "limitation": "",
            },
            ensure_ascii=False,
        )
    )
    gateway.set_backend(backend)
    monkeypatch.setattr(wb_service, "_default_literature_provider", lambda: FakeProvider([]))
    r = client.post(
        f"{PREFIX}/wb/method:search",
        headers=hdr_factory(ctx_factory()),
        json={"method_name": "模拟退火"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sources"] == [], "0 检索结果时不得凭空造文献"
    assert body["count"] == 0
    assert body["warning"], "空结果必须有 warning"
    assert not _DOI_RE.search(json.dumps(body, ensure_ascii=False)), "出现 DOI 形状字符串"


def test_c_real_sources_have_valid_doi_shape(client, ctx_factory, hdr_factory, monkeypatch) -> None:
    """有检索结果时，`sources[].doi` 必须是真实 DOI 形状（可溯源）。"""
    backend = CapturingBackend(text=json.dumps({"method_name_en": "SVM"}, ensure_ascii=False))
    gateway.set_backend(backend)
    monkeypatch.setattr(
        wb_service, "_default_literature_provider", lambda: FakeProvider([_meta("M", "10.1234/abc.def")])
    )
    r = client.post(
        f"{PREFIX}/wb/method:search",
        headers=hdr_factory(ctx_factory()),
        json={"method_name": "svm"},
    )
    assert r.status_code == 200, r.text
    src = r.json()["sources"][0]
    assert re.fullmatch(r"10\.\d{4,}/\S+", src["doi"]), src
    assert src["title"] == "M"


@pytest.mark.parametrize("limit,expected", [(1000, 20), (0, 1), (-5, 1), ("abc", 5)])
def test_c_limit_is_clamped(client, ctx_factory, hdr_factory, monkeypatch, limit, expected) -> None:
    """`limit` 必须被钳制到 [1, 20]（防滥用），非法值退回默认 5。"""
    prov = FakeProvider([_meta("M", "10.1/x")])
    gateway.set_backend(gateway.UnavailableBackend())
    monkeypatch.setattr(wb_service, "_default_literature_provider", lambda: prov)
    r = client.post(
        f"{PREFIX}/wb/method:search",
        headers=hdr_factory(ctx_factory(capabilities=["workspace.research.enter"])),
        json={"method_name": "svm", "limit": limit},
    )
    assert r.status_code == 200, r.text
    assert prov.seen_limit == expected, f"limit={limit!r} 未被钳制到 {expected}，实得 {prov.seen_limit}"


# ==========================================================================
# D. translate 解析健壮性
# ==========================================================================


def test_d1_success_returns_pure_translation_no_json_residue(
    client, ctx_factory, hdr_factory
) -> None:
    raw = json.dumps(
        {"translated_text": "定理：$x \\ge 0$。", "blocks": [{"source": "Theorem", "target": "定理"}]},
        ensure_ascii=False,
    )
    gateway.set_backend(CapturingBackend(text=raw))
    r = client.post(
        f"{PREFIX}/wb/translate",
        headers=hdr_factory(ctx_factory()),
        json={"text": "Theorem: $x \\ge 0$.", "target_lang": "zh"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    tt = body["translated_text"]
    assert not tt.lstrip().startswith("{")
    assert "translated_text" not in tt and '"blocks"' not in tt and '\\"' not in tt
    assert len(body["bilingual_blocks"]) >= 1, "模型给了 blocks 就必须用上"


def test_d2_formula_counts_are_honest_when_output_loses_one(
    client, ctx_factory, hdr_factory
) -> None:
    """输入 2 个公式，输出只剩 1 个 → 计数必须诚实体现丢失。"""
    raw = json.dumps({"translated_text": "Only $x^2$ survived."}, ensure_ascii=False)
    gateway.set_backend(CapturingBackend(text=raw))
    r = client.post(
        f"{PREFIX}/wb/translate",
        headers=hdr_factory(ctx_factory()),
        json={"text": "Both $x^2$ and $y^2$ here.", "target_lang": "zh"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["formulas_total"] == 2, body
    assert body["formulas_preserved"] == 1, "丢失公式未被诚实计入"


def test_d3_no_blocks_and_uneven_paragraphs_no_naive_pairing(
    client, ctx_factory, hdr_factory
) -> None:
    """原文 3 段、模型只给译文 2 段且无 blocks → 必须 `bilingual_blocks:[]` + warning。

    绝不允许用「朴素按段配对」冒充对齐（那会把 3 段原文和 2 段译文错位绑在一起）。
    """
    raw = json.dumps({"translated_text": "Para A.\nPara B."}, ensure_ascii=False)
    gateway.set_backend(CapturingBackend(text=raw))
    r = client.post(
        f"{PREFIX}/wb/translate",
        headers=hdr_factory(ctx_factory()),
        json={"text": "P1.\n\nP2.\n\nP3.", "target_lang": "en"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bilingual_blocks"] == [], "无 blocks 时不得凭空配对"
    assert body["warning"], "无段级对照必须有 warning"


def test_d3b_model_blocks_returned_verbatim_no_count_validation_FINDING(
    client, ctx_factory, hdr_factory
) -> None:
    """[漏过项·P2] 模型返回的 blocks **未与原文段数做任何校验**，错位也照单全收、且无 warning。

    构造：原文 3 段，模型只回 1 个 block（且 source/target 明显只覆盖第 1 段）→
    响应原样返回 1 个 block、`warning` 为空。记录当前行为供主理人定级。
    """
    raw = json.dumps(
        {"translated_text": "Only the first paragraph.", "blocks": [{"source": "P1.", "target": "Only the first paragraph."}]},
        ensure_ascii=False,
    )
    gateway.set_backend(CapturingBackend(text=raw))
    r = client.post(
        f"{PREFIX}/wb/translate",
        headers=hdr_factory(ctx_factory()),
        json={"text": "P1.\n\nP2.\n\nP3.", "target_lang": "en"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["bilingual_blocks"]) == 1
    assert body["warning"] is None, "当前实现不会因段数不匹配而 warning"


@pytest.mark.parametrize(
    "bad",
    [
        '{"foo": "bar", "baz": 1}',  # 是 JSON，但没有 translated_text
        '{"translated_text": ""}',  # 空译文
    ],
)
def test_d4_unparseable_output_is_502_not_json_leaked(
    client, ctx_factory, hdr_factory, bad
) -> None:
    """模型输出不可用 → 诚实 502；**绝不**把 JSON 串当译文返回。"""
    gateway.set_backend(CapturingBackend(text=bad))
    r = client.post(
        f"{PREFIX}/wb/translate",
        headers=hdr_factory(ctx_factory()),
        json={"text": "hello world", "target_lang": "en"},
    )
    assert r.status_code == 502, f"{bad!r} → {r.status_code} {r.text[:160]}"
    assert r.json()["error"]["code"] == "WB_LLM_BAD_OUTPUT"
    assert "translated_text" not in r.text, "错误体里泄露了模型原始 JSON"


@pytest.mark.xfail(strict=True, reason="已知缺陷 P3：_looks_like_json_blob 过严，'{\"开头但无引号/冒号'的截断 JSON 被当译文 200 返回")
def test_d4b_truncated_brace_blob_must_be_502_not_leaked_as_translation(
    client, ctx_factory, hdr_factory
) -> None:
    """[漏过项·P3] 截断的 JSON 片段（以 `{` 开头，但无引号/冒号）竟被当译文 200 返回。

    最小复现：模型输出 `{not valid json at all`（典型的截断 JSON 前半段）。
    `_looks_like_json_blob` 要求同时含 `"` 与 `:` 才算 JSON blob，故该串漏网 →
    `_salvage_translation_text` 原样返回 → **200 + translated_text="{not valid json at all"**。
    这正是「看起来成功、实则把模型原始残片当译文」的诚实降级缺口。
    期望：502（符合任务书 D.4「不可解析必须 502，不得把 JSON 串当译文返回」）。
    """
    gateway.set_backend(CapturingBackend(text="{not valid json at all"))
    r = client.post(
        f"{PREFIX}/wb/translate",
        headers=hdr_factory(ctx_factory()),
        json={"text": "hello world", "target_lang": "en"},
    )
    assert r.status_code == 502, f"截断 JSON 片段被当译文返回: {r.status_code} {r.text[:160]}"
    assert r.json()["error"]["code"] == "WB_LLM_BAD_OUTPUT"


def test_d5_plain_text_output_accepted_with_warning(client, ctx_factory, hdr_factory) -> None:
    """模型返回纯文本（非 JSON）→ 可用则 200 + warning（无段级对照）。"""
    gateway.set_backend(CapturingBackend(text="This is a plain translation."))
    r = client.post(
        f"{PREFIX}/wb/translate",
        headers=hdr_factory(ctx_factory()),
        json={"text": "这是一段译文。", "target_lang": "en"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["translated_text"] == "This is a plain translation."
    assert body["bilingual_blocks"] == []
    assert body["warning"]


def test_d_latex_escape_in_json_still_parses(client, ctx_factory, hdr_factory) -> None:
    """模型在 JSON 字符串里写未转义 LaTeX 反斜杠 → 修复后仍应解析（不得误判为不可解析）。"""
    raw = '{"translated_text": "定理：$x \\ge 0$.", "blocks": []}'
    gateway.set_backend(CapturingBackend(text=raw))
    r = client.post(
        f"{PREFIX}/wb/translate",
        headers=hdr_factory(ctx_factory()),
        json={"text": "Theorem: $x \\ge 0$.", "target_lang": "zh"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["translated_text"] == "定理：$x \\ge 0$."


# ==========================================================================
# E. 鉴权矩阵 + 能力原则 + 凭据零泄漏
# ==========================================================================

_ENDPOINTS = [
    ("GET", "/wb/capabilities", None),
    ("POST", "/wb/latex:edit", {"file_content": CLEAN_TEX, "instruction": "x"}),
    ("POST", "/wb/method:search", {"method_name": "svm"}),
    ("POST", "/wb/translate", {"text": "hi", "target_lang": "en"}),
]


@pytest.mark.parametrize("method,path,body", _ENDPOINTS)
def test_e_missing_signature_401(client, method, path, body) -> None:
    r = client.request(method, f"{PREFIX}{path}", json=body)
    assert r.status_code == 401, f"{path} 无签名应 401，实得 {r.status_code}"


@pytest.mark.parametrize("method,path,body", _ENDPOINTS)
def test_e_tampered_signature_401(client, ctx_factory, hdr_factory, method, path, body) -> None:
    r = client.request(
        method, f"{PREFIX}{path}", headers=hdr_factory(ctx_factory(), tamper=True), json=body
    )
    assert r.status_code == 401, f"{path} 篡改签名应 401，实得 {r.status_code}"


@pytest.mark.parametrize("method,path,body", _ENDPOINTS)
def test_e_empty_capability_403(client, ctx_factory, hdr_factory, method, path, body) -> None:
    r = client.request(
        method, f"{PREFIX}{path}", headers=hdr_factory(ctx_factory(capabilities=[])), json=body
    )
    assert r.status_code == 403, f"{path} 空能力应 403，实得 {r.status_code}"


@pytest.mark.parametrize("method,path,body", _ENDPOINTS)
def test_e_wrong_workspace_403(client, ctx_factory, hdr_factory, method, path, body) -> None:
    r = client.request(
        method, f"{PREFIX}{path}", headers=hdr_factory(ctx_factory(workspace="classroom")), json=body
    )
    assert r.status_code == 403, f"{path} 越权 workspace 应 403，实得 {r.status_code}"


def test_e_write_endpoints_reject_read_only_capability(client, ctx_factory, hdr_factory) -> None:
    """`latex:edit` / `translate` 需要 WRITE；只给 READ 能力必须 403，且不调用模型。"""
    backend = CapturingBackend(text=EDITED_TEX)
    gateway.set_backend(backend)
    ctx = ctx_factory(capabilities=["workspace.research.enter"])
    for path, body in (
        ("/wb/latex:edit", {"file_content": CLEAN_TEX, "instruction": "x"}),
        ("/wb/translate", {"text": "hi", "target_lang": "en"}),
    ):
        r = client.post(f"{PREFIX}{path}", headers=hdr_factory(ctx), json=body)
        assert r.status_code == 403, f"{path} 只读能力应 403，实得 {r.status_code}"
    assert backend.calls == 0


@pytest.mark.xfail(strict=True, reason="已知缺陷 P2：method:search 声明 READ 却触发付费 LLM")
def test_e_defect_method_search_read_only_still_triggers_paid_llm(
    client, ctx_factory, hdr_factory, monkeypatch
) -> None:
    """[缺陷·P2] `method:search` 声明 READ 能力，却会触发**付费 LLM** 调用。

    原则（主理人任务书 E.1）：`method:search` 若调 LLM 则需 WRITE。
    当前实现：只读能力 + LLM 配置 → 200 且 `backend.calls == 1`。
    以 xfail(strict) 记录：断言「期望零调用」→ 实际 1 次 → 稳定 xfailed。
    """
    backend = CapturingBackend(text=json.dumps({"method_name_en": "SVM"}, ensure_ascii=False))
    gateway.set_backend(backend)
    monkeypatch.setattr(
        wb_service, "_default_literature_provider", lambda: FakeProvider([_meta("M", "10.1/x")])
    )
    ctx = ctx_factory(capabilities=["workspace.research.enter"])  # 只读
    r = client.post(
        f"{PREFIX}/wb/method:search", headers=hdr_factory(ctx), json={"method_name": "svm"}
    )
    assert r.status_code == 200
    assert backend.calls == 0, "只读能力竟触发了付费 LLM 调用（越权/计费升级面）"


def test_e_capabilities_response_leaks_no_credentials(client, ctx_factory, hdr_factory) -> None:
    """`/wb/capabilities` 不得回显任何密钥（读取真实 .env 值做子串扫描）。"""
    gateway.set_backend(CapturingBackend())
    r = client.get(f"{PREFIX}/wb/capabilities", headers=hdr_factory(ctx_factory()))
    assert r.status_code == 200, r.text
    text = r.text
    secrets: list[str] = []
    for env_path in (
        pathlib.Path(r"D:\math-arena\services\research-vnext\.env"),
        pathlib.Path(r"D:\math-arena\.env"),
    ):
        try:
            lines = env_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.strip().strip('"').strip("'")
            if any(tok in k.upper() for tok in ("KEY", "SECRET", "PASSWORD", "TOKEN")) and len(v) >= 6:
                secrets.append(v)
    for s in secrets:
        assert s not in text, "capabilities 响应体泄露了凭据"
    # 环境变量里的真实值同样不得出现
    for k in ("SPARK_API_PASSWORD", "RESEARCH_LLM_API_KEY", "RESEARCH_XF_API_SECRET", "RESEARCH_GATEWAY_HMAC_SECRET"):
        v = os.environ.get(k)
        if v and len(v) >= 6:
            assert v not in text, f"capabilities 泄露了 {k}"


# ==========================================================================
# G. 跨端点契约一致性（结构面）
# ==========================================================================

_CONTRACT_KEYS = {"success", "changes", "final_updated_code", "security_msg"}


def test_g_latex_edit_success_contract_keys_and_line_shape(client, ctx_factory, hdr_factory) -> None:
    """成功体必须含 `{success,changes,final_updated_code,security_msg}`；
    `changes[].old_lines/new_lines` 必须是**字符串**（数组只允许在 `*_list`）。"""
    gateway.set_backend(CapturingBackend(text=EDITED_TEX))
    r = _post_edit(
        client,
        hdr_factory,
        ctx_factory(),
        {"file_content": CLEAN_TEX, "instruction": "tidy", "target_file": "main.tex"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert _CONTRACT_KEYS <= set(body.keys()), f"缺契约键: {_CONTRACT_KEYS - set(body)}"
    assert body["changes"], "有改动时 changes 不应为空"
    for ch in body["changes"]:
        assert isinstance(ch["old_lines"], str), f"old_lines 应为 str，实为 {type(ch['old_lines']).__name__}"
        assert isinstance(ch["new_lines"], str)
        assert isinstance(ch.get("old_lines_list"), list)
        assert isinstance(ch.get("new_lines_list"), list)


def test_g_latex_edit_error_contract_keys(client, ctx_factory, hdr_factory) -> None:
    """错误体（加法式）也必须带契约键，前端才能统一渲染。"""
    r = _post_edit(
        client,
        hdr_factory,
        ctx_factory(),
        {"file_content": "\\write18{x}", "instruction": "x"},
    )
    assert r.status_code == 400
    body = r.json()
    assert _CONTRACT_KEYS <= set(body.keys())
    assert body["success"] is False and body["changes"] == [] and body["final_updated_code"] == ""
