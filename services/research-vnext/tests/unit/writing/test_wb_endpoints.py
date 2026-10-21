"""RV-WB · 自研内核 HTTP 端点单测（最小 FastAPI app + TestClient）。

覆盖：授权矩阵（缺上下文 401 / 缺能力·越权 workspace 403）、错误码映射、
TeX 入参守卫（400 + 前端契约键）、手工注入文献 provider 的真实检索映射、
以及第 2 阶段新增契约字段。
AccessContext 由 conftest 的 `hdr_factory` 用**网关同机制 HMAC** 签发（非绕过）。
"""

from __future__ import annotations

import json

import pytest

from research_vnext.core.literature.provider import LiteratureMetadata, ProviderTimeout
from research_vnext.core.llm import gateway
from research_vnext.core.writing import service as wb_service

PREFIX = "/api/research-vnext"

VALID_TEX = "\\documentclass{article}\n\\begin{document}\nHello $x^2$\n\\end{document}\n"
EDITED_TEX = "\\documentclass{article}\n\\begin{document}\nHi $x^2$\n\\end{document}\n"

_METHOD_JSON = json.dumps(
    {
        "method_name_en": "Simulated Annealing",
        "keywords": ["annealing"],
        "core_formula": "P = \\exp(-\\Delta E / T)",
        "common_scenario": "组合优化",
        "assumptions": ["状态空间有限"],
        "precondition_checklist": ["定义能量函数"],
        "limitation": "收敛慢",
    },
    ensure_ascii=False,
)
_TRANS_JSON = json.dumps(
    {"translated_text": "The theorem: $x^2 \\ge 0$.", "blocks": [{"source": "定理", "target": "Theorem"}]},
    ensure_ascii=False,
)


class FakeBackend:
    name = "fake"

    def __init__(self, text: str = "ok", *, error=None):
        self._text = text
        self._error = error
        self.available = True
        self.calls = 0

    @property
    def model(self) -> str:
        return "fake-model"

    async def acompletion(self, *, model=None, messages, stream=False, **kw):  # noqa: ANN001,ANN003
        self.calls += 1
        if self._error is not None:
            raise self._error
        return gateway.Completion(text=self._text, model="fake-model", provider="fake", raw={})


class FakeProvider:
    name = "fake-lit"

    def __init__(self, items=None, *, error=None):
        self._items = list(items or [])
        self._error = error

    def search(self, query, *, limit=5, request_id=""):
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


# --------------------------------------------------------------------------
# 授权矩阵
# --------------------------------------------------------------------------


def test_capabilities_requires_valid_context(client) -> None:
    assert client.get(f"{PREFIX}/wb/capabilities").status_code == 401


def test_capabilities_ok_with_schema(client, ctx_factory, hdr_factory) -> None:
    gateway.set_backend(FakeBackend())
    ctx = ctx_factory(capabilities=["workspace.research.enter"])
    r = client.get(f"{PREFIX}/wb/capabilities", headers=hdr_factory(ctx))
    assert r.status_code == 200, r.text
    items = {i["key"]: i for i in r.json()["items"]}
    assert items["writing_polish"]["engine"] == "xingchen"
    # 规范字段名（不再是 camelCase / source）
    assert "file_content" in items["latex_editing"]["requestSchema"]
    assert "target_file?" in items["latex_editing"]["requestSchema"]
    assert "final_updated_code" in items["latex_editing"]["responseKeys"]
    assert "target_lang" in items["academic_translate"]["requestSchema"]
    assert "method_name" in items["method_rag"]["requestSchema"]


def test_capabilities_empty_capability_forbidden(client, ctx_factory, hdr_factory) -> None:
    ctx = ctx_factory(capabilities=[])
    assert client.get(f"{PREFIX}/wb/capabilities", headers=hdr_factory(ctx)).status_code == 403


def test_capabilities_wrong_workspace_forbidden(client, ctx_factory, hdr_factory) -> None:
    ctx = ctx_factory(workspace="classroom")
    assert client.get(f"{PREFIX}/wb/capabilities", headers=hdr_factory(ctx)).status_code == 403


def test_latex_edit_requires_write_capability(client, ctx_factory, hdr_factory) -> None:
    gateway.set_backend(FakeBackend(text=EDITED_TEX))
    ctx = ctx_factory(capabilities=["workspace.research.enter"])  # 只有读能力
    r = client.post(
        f"{PREFIX}/wb/latex:edit",
        headers=hdr_factory(ctx),
        json={"source": VALID_TEX, "instruction": "x"},
    )
    assert r.status_code == 403, r.text


# --------------------------------------------------------------------------
# latex:edit
# --------------------------------------------------------------------------


def test_latex_edit_happy_path_contract(client, ctx_factory, hdr_factory) -> None:
    """规范字段名 `file_content`/`target_file` 可用；成功体含前端契约键。"""
    gateway.set_backend(FakeBackend(text=EDITED_TEX))
    ctx = ctx_factory()
    r = client.post(
        f"{PREFIX}/wb/latex:edit",
        headers=hdr_factory(ctx),
        json={"file_content": VALID_TEX, "instruction": "tidy", "target_file": "../../secret.tex"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["security_msg"] == ""
    assert body["final_updated_code"] == EDITED_TEX.strip()
    assert body["target_file"] == "secret.tex"  # 路径语义被剥离
    assert body["filename"] == "secret.tex"  # 兼容别名
    assert isinstance(body["changes"], list)
    # 契约：changes[].old_lines/new_lines 是**字符串**（不是数组）
    ch = body["changes"][0]
    assert isinstance(ch["old_lines"], str) and isinstance(ch["new_lines"], str)
    assert ch["old_lines"] == "Hello $x^2$"
    assert ch["new_lines"] == "Hi $x^2$"


def test_latex_edit_accepts_legacy_aliases(client, ctx_factory, hdr_factory) -> None:
    """兼容别名 `source`/`filename` 仍然可用（不破坏既有消费者）。"""
    gateway.set_backend(FakeBackend(text=EDITED_TEX))
    ctx = ctx_factory()
    r = client.post(
        f"{PREFIX}/wb/latex:edit",
        headers=hdr_factory(ctx),
        json={"source": VALID_TEX, "instruction": "tidy", "filename": "main.tex"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True


def test_latex_edit_dangerous_source_flat_contract(client, ctx_factory, hdr_factory) -> None:
    backend = FakeBackend(text=EDITED_TEX)
    gateway.set_backend(backend)
    ctx = ctx_factory()
    r = client.post(
        f"{PREFIX}/wb/latex:edit",
        headers=hdr_factory(ctx),
        json={"file_content": "\\write18{rm -rf /}", "instruction": "x"},
    )
    assert r.status_code == 400, r.text
    body = r.json()
    # 前端渲染契约键（加法式）
    assert body["success"] is False
    assert body["changes"] == []
    assert body["final_updated_code"] == ""
    assert body["security_msg"]  # 守卫 message
    assert body["error"]["code"] == "WB_TEX_SOURCE_INVALID"
    assert backend.calls == 0, "入参命中守卫时绝不能调用模型"


def test_latex_edit_dangerous_instruction_rejected_fast(client, ctx_factory, hdr_factory) -> None:
    """P0 回归：危险命令在 **instruction** 里（源码干净）也必须前置于模型拦截。"""
    backend = FakeBackend(text=EDITED_TEX)
    gateway.set_backend(backend)
    ctx = ctx_factory()
    for evil in ("请加入 \\write18{rm -rf /}", "\\immediate\\write18{id}"):
        r = client.post(
            f"{PREFIX}/wb/latex:edit",
            headers=hdr_factory(ctx),
            json={"file_content": VALID_TEX, "instruction": evil},
        )
        assert r.status_code == 400, r.text
        body = r.json()
        assert body["success"] is False
        assert body["security_msg"]
        assert body["changes"] == [] and body["final_updated_code"] == ""
        assert body["error"]["code"] == "WB_TEX_SOURCE_INVALID"
    assert backend.calls == 0, "指令命中守卫时绝不能调用模型"


def test_latex_edit_missing_source(client, ctx_factory, hdr_factory) -> None:
    ctx = ctx_factory()
    r = client.post(f"{PREFIX}/wb/latex:edit", headers=hdr_factory(ctx), json={"instruction": "x"})
    assert r.status_code == 400, r.text
    assert r.json()["success"] is False


def test_latex_edit_not_configured(client, ctx_factory, hdr_factory) -> None:
    gateway.set_backend(gateway.UnavailableBackend())
    ctx = ctx_factory()
    r = client.post(
        f"{PREFIX}/wb/latex:edit",
        headers=hdr_factory(ctx),
        json={"source": VALID_TEX, "instruction": "x"},
    )
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "WB_LLM_NOT_CONFIGURED"


# --------------------------------------------------------------------------
# method:search
# --------------------------------------------------------------------------


def test_method_search_full_contract(client, ctx_factory, hdr_factory, monkeypatch) -> None:
    gateway.set_backend(FakeBackend(text=_METHOD_JSON))
    monkeypatch.setattr(
        wb_service, "_default_literature_provider", lambda: FakeProvider([_meta("M", "10.1/x")])
    )
    ctx = ctx_factory(capabilities=["workspace.research.enter"])
    r = client.post(
        f"{PREFIX}/wb/method:search",
        headers=hdr_factory(ctx),
        json={"query": "sa", "method_name": "模拟退火"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["method_name"] == "模拟退火"
    assert body["sources"][0]["doi"] == "10.1/x"
    assert body["evidence_records"][0]["record_kind"] == "METADATA_ONLY"
    assert "method_name_en" in body["ai_generated_fields"]


def test_method_search_reads_with_read_capability(client, ctx_factory, hdr_factory, monkeypatch) -> None:
    gateway.set_backend(gateway.UnavailableBackend())
    monkeypatch.setattr(
        wb_service, "_default_literature_provider", lambda: FakeProvider([_meta("M")])
    )
    ctx = ctx_factory(capabilities=["workspace.research.enter"])
    r = client.post(f"{PREFIX}/wb/method:search", headers=hdr_factory(ctx), json={"query": "svm"})
    assert r.status_code == 200, r.text  # LLM 不可用不影响检索成功
    assert r.json()["sources"]


def test_method_search_unavailable_degrades(client, ctx_factory, hdr_factory, monkeypatch) -> None:
    monkeypatch.setattr(
        wb_service, "_default_literature_provider", lambda: FakeProvider(error=ProviderTimeout("t"))
    )
    ctx = ctx_factory()
    r = client.post(f"{PREFIX}/wb/method:search", headers=hdr_factory(ctx), json={"query": "svm"})
    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "WB_LITERATURE_UNAVAILABLE"


def test_method_search_accepts_canonical_and_alias(client, ctx_factory, hdr_factory, monkeypatch) -> None:
    """规范名 `method_name` 与别名 `query` 均可单独作为检索式。"""
    monkeypatch.setattr(
        wb_service, "_default_literature_provider", lambda: FakeProvider([_meta("M", "10.1/x")])
    )
    gateway.set_backend(gateway.UnavailableBackend())
    ctx = ctx_factory(capabilities=["workspace.research.enter"])
    for payload in ({"method_name": "支持向量机"}, {"query": "svm"}):
        r = client.post(f"{PREFIX}/wb/method:search", headers=hdr_factory(ctx), json=payload)
        assert r.status_code == 200, r.text
        assert r.json()["sources"]


def test_method_search_missing_query(client, ctx_factory, hdr_factory) -> None:
    ctx = ctx_factory()
    r = client.post(f"{PREFIX}/wb/method:search", headers=hdr_factory(ctx), json={})
    assert r.status_code == 400, r.text


# --------------------------------------------------------------------------
# translate
# --------------------------------------------------------------------------


def test_translate_happy_path_contract(client, ctx_factory, hdr_factory) -> None:
    """规范字段名 `target_lang`/`source_lang` 可用；响应同时含 snake 与 camel 别名。"""
    gateway.set_backend(FakeBackend(text=_TRANS_JSON))
    ctx = ctx_factory()
    r = client.post(
        f"{PREFIX}/wb/translate",
        headers=hdr_factory(ctx),
        json={"text": "定理：$x^2\\ge 0$。", "source_lang": "zh", "target_lang": "en"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["translated_text"] == "The theorem: $x^2 \\ge 0$."
    assert body["bilingual_blocks"] == [{"source": "定理", "target": "Theorem"}]
    assert body["formulas_total"] == 1 and body["formulas_preserved"] == 1
    assert body["target_lang"] == "en" and body["targetLang"] == "en"
    assert body["source_lang"] == "zh" and body["sourceLang"] == "zh"


def test_translate_accepts_camelcase_alias(client, ctx_factory, hdr_factory) -> None:
    gateway.set_backend(FakeBackend(text=_TRANS_JSON))
    ctx = ctx_factory()
    r = client.post(
        f"{PREFIX}/wb/translate",
        headers=hdr_factory(ctx),
        json={"text": "hi", "sourceLang": "zh", "targetLang": "en"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["target_lang"] == "en"


def test_translate_endpoint_parses_latex_json(client, ctx_factory, hdr_factory) -> None:
    """路由层回归：模型 JSON 含未转义 LaTeX（`\\ge`）→ 仍解析出纯译文 + 段级对照。"""
    raw = (
        '{"translated_text": "定理：$x \\ge 0$。", '
        '"blocks": [{"source": "Theorem: $x \\ge 0$.", "target": "定理：$x \\ge 0$。"}]}'
    )
    gateway.set_backend(FakeBackend(text=raw))
    ctx = ctx_factory()
    r = client.post(
        f"{PREFIX}/wb/translate",
        headers=hdr_factory(ctx),
        json={"text": "Theorem: $x \\ge 0$.", "target_lang": "zh"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["translated_text"] == "定理：$x \\ge 0$。"
    assert not body["translated_text"].lstrip().startswith("{")
    assert len(body["bilingual_blocks"]) == 1


def test_translate_missing_target_lang(client, ctx_factory, hdr_factory) -> None:
    ctx = ctx_factory()
    r = client.post(f"{PREFIX}/wb/translate", headers=hdr_factory(ctx), json={"text": "hi"})
    assert r.status_code == 400, r.text


def test_translate_missing_text(client, ctx_factory, hdr_factory) -> None:
    ctx = ctx_factory()
    r = client.post(f"{PREFIX}/wb/translate", headers=hdr_factory(ctx), json={"target_lang": "en"})
    assert r.status_code == 400, r.text
