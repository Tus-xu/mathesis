"""LLM 网关参数契约（单元）—— **逐字锁定 LEAD 给出的确切参数**。

这些用例的存在理由：网关参数错误（尤其是端点重复拼接）历史上已经造成过一次
真实的 404。参数是**外部事实**，必须被测试钉住，而不是靠注释提醒。
"""

from __future__ import annotations

import sys
import types

import pytest

from research_vnext.core.llm import gateway as gw


# --------------------------------------------------------------------------
# 端点（**最容易被拼错的一处**）
# --------------------------------------------------------------------------


def test_default_base_url_is_the_full_endpoint() -> None:
    """默认值必须**包含** `/chat/completions`（逐字来自 MODEL-INVENTORY.md §2）。"""
    assert gw.DEFAULT_BASE_URL == (
        "https://maas-api.cn-huabei-1.xf-yun.com/v2/chat/completions"
    )


def test_base_url_is_not_double_appended(monkeypatch) -> None:
    """**核心回归**：端点已含路径，任何二次拼接都会打出 404。

    这条正是历史 embedding 404 的同一错误，因此它必须是可被打破的测试。
    """
    monkeypatch.setenv("RESEARCH_LLM_BASE_URL", gw.DEFAULT_BASE_URL)
    for _ in range(3):
        url = gw.llm_base_url()
    assert url.count("/chat/completions") == 1, f"路径被重复拼接: {url}"
    assert url.endswith("/chat/completions")


def test_historical_double_appended_value_is_repaired(monkeypatch) -> None:
    """即便环境变量里已经写成了重复形式，也不得把重复带出去。"""
    monkeypatch.setenv(
        "RESEARCH_LLM_BASE_URL",
        "https://maas-api.cn-huabei-1.xf-yun.com/v2/chat/completions/chat/completions",
    )
    url = gw.llm_base_url()
    assert url.count("/chat/completions") == 1, url


def test_litellm_api_base_has_no_endpoint_suffix(monkeypatch) -> None:
    """**核心回归（真实 404 的那个）**：交给 LiteLLM Router 的 `api_base` 必须**不含**
    `/chat/completions`。

    为什么必须断言**构造 Router 时的入参**，而不是 `backend.api_base` 属性：
    第一版这条测试只断言了属性，结果在"把缺陷注入回去"时**依然全绿** —— 因为真正
    被 LiteLLM 使用的是 `_get_router()` 里构造 `model_list` 时写的那个值，属性只是
    并行的副本。断言属性等于**测了个影子**，缺陷可以从旁边溜过去。

    实测背景（2026-09-13）：完整端点当 api_base →
    `litellm.NotFoundError: NotFoundError: OpenAIException - 404 page not found`；
    去掉后缀后同一调用 200。故此处直接捕获构造入参。
    """
    monkeypatch.setenv("RESEARCH_LLM_BASE_URL", gw.DEFAULT_BASE_URL)

    api_base = gw.llm_api_base()
    assert "/chat/completions" not in api_base, f"api_base 仍带端点后缀: {api_base}"
    assert api_base.endswith("/v2"), api_base

    # 捕获真正传给 LiteLLM 的值
    captured: dict = {}

    def fake_router(*, model_list, **kw):  # noqa: ANN001, ANN202, ARG001
        captured["model_list"] = model_list
        return object()

    fake_litellm = types.SimpleNamespace(Router=fake_router)
    monkeypatch.setitem(sys.modules, "litellm", fake_litellm)

    backend = gw.LiteLlmRouterBackend(api_key="dummy", base_url=gw.DEFAULT_BASE_URL)
    backend._get_router()

    passed_base = captured["model_list"][0]["litellm_params"]["api_base"]
    assert "/chat/completions" not in passed_base, (
        f"传给 LiteLLM 的 api_base 仍带端点后缀（会打成 .../chat/completions/chat/completions "
        f"→ 404）: {passed_base}"
    )
    assert passed_base.endswith("/v2"), passed_base
    # 对外口径保持不变（自检 / 错误信息仍报完整端点）
    assert backend.base_url.endswith("/chat/completions")


def test_default_max_tokens_budget_survives_reasoning_model() -> None:
    """推理模型会把输出预算先花在 reasoning 上，预算过紧会导致**正文恒为空**。

    实测：`max_tokens=200` → reasoning=199 / content 长度 0（表现为"模型没回答"，
    极易被误判成网关故障）；`max_tokens=3000` → reasoning=859 / content 54。

    RV08 起默认 8192：科研端目标是建模/竞赛级**长推导**（`Proposal.text` 上限 8000 字），
    4096 扣掉 reasoning 后写不完整。断言下界仍守住"显著高于 2048"这条原始意图。
    """
    assert gw.llm_max_tokens() >= 8192


def test_litellm_route_name_mirrors_platform_shape() -> None:
    """平台把 Spark 注册为自定义 OpenAI 兼容部署 ⇒ 路由名 `openai/<model>`。"""
    b = gw.LiteLlmRouterBackend(api_key="dummy")
    assert b.litellm_route_name() == "openai/spark-x2.5-4b"
    assert b.litellm_route_name("xop3qwen80bnext") == "openai/xop3qwen80bnext"


def test_default_model_and_role_models_match_inventory() -> None:
    assert gw.DEFAULT_MODEL == "spark-x2.5-4b"
    assert gw.DEFAULT_MODEL_LONG == "xop3qwen80bnext"
    assert gw.DEFAULT_MODEL_VISION == "xophunyuanocr"


# --------------------------------------------------------------------------
# 账号 ↔ 模型配对（实测：授权**按账号隔离**）
# --------------------------------------------------------------------------


def test_account_model_map_matches_measured_facts() -> None:
    """实测事实：A 服务 chat+长文本，B 服务视觉。"""
    assert gw.ACCOUNT_MODEL_MAP == {
        "A": ("spark-x2.5-4b", "xop3qwen80bnext"),
        "B": ("xophunyuanocr",),
    }


def test_pairing_ok_for_research_default() -> None:
    ok, hint = gw.check_pairing(account="A", model="spark-x2.5-4b")
    assert ok and hint == ""


def test_pairing_rejected_for_vision_model_on_account_a() -> None:
    """实测：`xophunyuanocr` 在 Account A 的 key 下 403。"""
    ok, hint = gw.check_pairing(account="A", model="xophunyuanocr")
    assert ok is False
    assert "xophunyuanocr" in hint and "B" in hint


def test_pairing_rejected_for_long_model_on_account_b() -> None:
    """实测：`xop3qwen80bnext` 在 Account B 的 key 下 403。"""
    ok, hint = gw.check_pairing(account="B", model="xop3qwen80bnext")
    assert ok is False
    assert "A" in hint


def test_unknown_account_is_rejected_with_actionable_hint() -> None:
    ok, hint = gw.check_pairing(account="C", model="spark-x2.5-4b")
    assert ok is False
    assert "RESEARCH_LLM_ACCOUNT" in hint


def test_account_model_map_is_configurable(monkeypatch) -> None:
    """账号↔模型映射必须是**数据且可配置**，不是硬编码的 if/else。"""
    monkeypatch.setenv("RESEARCH_LLM_ACCOUNT_MODELS", "A:spark-x2.5-4b;B:everything-else")
    assert gw.account_model_map() == {"A": ("spark-x2.5-4b",), "B": ("everything-else",)}
    assert gw.check_pairing(account="B", model="everything-else")[0] is True


def test_key_fingerprint_is_stable_and_not_the_key() -> None:
    fp = gw.key_fingerprint("some-secret-value")
    assert len(fp) == 12
    assert fp == gw.key_fingerprint("some-secret-value")
    assert "some-secret-value" not in fp
    assert gw.key_fingerprint("") == ""


def test_validate_pairing_degrades_cleanly_without_credentials(monkeypatch) -> None:
    """无凭据是**合法状态**（诚实降级），默认不抛错。"""
    monkeypatch.delenv("RESEARCH_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SPARK_API_PASSWORD", raising=False)
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    report = gw.validate_pairing()
    assert report.configured is False
    assert report.ok is False
    assert report.uses_full_endpoint is True
    assert "RESEARCH_LLM_API_KEY" in report.hint


def test_validate_pairing_requires_credentials_when_asked(monkeypatch) -> None:
    """生产启动要求凭据时，缺失必须**响亮失败**。"""
    monkeypatch.delenv("RESEARCH_LLM_API_KEY", raising=False)
    monkeypatch.delenv("SPARK_API_PASSWORD", raising=False)
    monkeypatch.delenv("SPARK_API_KEY", raising=False)
    with pytest.raises(gw.LlmGateError) as ei:
        gw.validate_pairing(require_credentials=True)
    assert ei.value.code == "LLM_NOT_CONFIGURED"


def test_validate_pairing_raises_model_account_mismatch(monkeypatch) -> None:
    """配错账号必须抛**专用**异常（可在启动时 fail loud），而不是裸 403。"""
    monkeypatch.setenv("RESEARCH_LLM_API_KEY", "dummy-key-value")
    monkeypatch.setenv("RESEARCH_LLM_ACCOUNT", "A")
    with pytest.raises(gw.ModelAccountMismatch) as ei:
        gw.validate_pairing(model="xophunyuanocr")
    assert ei.value.code == "LLM_MODEL_ACCOUNT_MISMATCH"
    assert ei.value.model == "xophunyuanocr"
    assert ei.value.account == "A"
    assert "B" in ei.value.hint


def test_validate_pairing_ok_with_correct_account(monkeypatch) -> None:
    monkeypatch.setenv("RESEARCH_LLM_API_KEY", "dummy-key-value")
    monkeypatch.setenv("RESEARCH_LLM_ACCOUNT", "A")
    monkeypatch.setenv("RESEARCH_LLM_BASE_URL", gw.DEFAULT_BASE_URL)
    report = gw.validate_pairing(model="spark-x2.5-4b")
    assert report.ok and report.configured
    assert report.account == "A"
    assert report.uses_full_endpoint is True
    assert report.key_fingerprint == gw.key_fingerprint("dummy-key-value")


def test_403_is_mapped_to_model_account_mismatch_not_generic_error() -> None:
    """403 必须被解释成"账号/模型不配对"，而不是"密钥无效"。

    报错了会把运维引向错误的方向（去查密钥有效期，而该做的是换 key）。
    """
    backend = gw.LiteLlmRouterBackend(api_key="dummy", model="xophunyuanocr")

    class _Fake403(Exception):
        status_code = 403

    err = backend._map_exception(_Fake403("Forbidden"), model="xophunyuanocr")
    assert isinstance(err, gw.ModelAccountMismatch)
    assert err.code == "LLM_MODEL_ACCOUNT_MISMATCH"
    assert "403" in err.hint


def test_429_is_mapped_to_rate_limited_with_single_provider_note() -> None:
    """单账号单点：429 必须可识别，且说明**没有**第二供应商可降级。"""
    backend = gw.LiteLlmRouterBackend(api_key="dummy")

    class _Fake429(Exception):
        status_code = 429

    err = backend._map_exception(_Fake429("Too Many Requests"), model="spark-x2.5-4b")
    assert err.code == "LLM_RATE_LIMITED"
    assert "没有第二供应商" in err.message or "无第二供应商" in err.message


def test_401_is_mapped_to_unauthorized() -> None:
    backend = gw.LiteLlmRouterBackend(api_key="dummy")

    class _Fake401(Exception):
        status_code = 401

    assert backend._map_exception(_Fake401("nope"), model="spark-x2.5-4b").code == "LLM_UNAUTHORIZED"


def test_unavailable_backend_never_fabricates() -> None:
    import asyncio

    b = gw.UnavailableBackend()
    assert b.available is False
    with pytest.raises(gw.LlmGateError) as ei:
        asyncio.run(b.acompletion(messages=[{"role": "user", "content": "hi"}]))
    assert ei.value.code == "LLM_NOT_CONFIGURED"


def test_no_second_provider_is_invented() -> None:
    """**设计不变量**：代码里不得出现'第二个供应商'的兜底路径。"""
    import inspect
    import re

    src = inspect.getsource(gw)
    # 不得出现指向其它厂商的端点
    for foreign in ("api.openai.com", "api.deepseek.com", "siliconflow", "dashscope"):
        assert foreign not in src, f"出现了未授权的第二供应商端点: {foreign}"
    # 不得存在"回退到另一个 provider"的字样（注释除外）
    code_lines = [
        ln for ln in src.splitlines() if ln.strip() and not ln.strip().startswith("#")
    ]
    for ln in code_lines:
        assert not re.search(r"fallback_provider|secondary_provider|backup_provider", ln), ln


def test_proposal_model_falls_back_to_long_model(monkeypatch) -> None:
    """RV09：结构化提案默认走"长结构化输出"模型（同一账号，非第二供应商）。"""
    monkeypatch.delenv("RESEARCH_LLM_MODEL_PROPOSE", raising=False)
    assert gw.llm_proposal_model() == gw.llm_model_long()
    monkeypatch.setenv("RESEARCH_LLM_MODEL_PROPOSE", "custom-model")
    assert gw.llm_proposal_model() == "custom-model"


def test_router_registers_every_configured_model(monkeypatch) -> None:
    """RV09 修复回归：Router **必须**登记全部已配置模型。

    只登记默认模型时，`send_message` 请求长输出模型会被 LiteLLM 以
    "model not in model_list" 拒绝，映射成 `LLM_CALL_FAILED`、`input_tokens=0`
    —— 表面上像"模型挂了"，实际是路由表没登记（E2E 实测踩过）。
    """
    monkeypatch.setenv("RESEARCH_LLM_MODEL", "primary-model")
    monkeypatch.setenv("RESEARCH_LLM_MODEL_LONG", "long-model")
    monkeypatch.setenv("RESEARCH_LLM_MODEL_VISION", "vision-model")
    monkeypatch.setenv("RESEARCH_LLM_API_KEY", "unit-test-key")
    backend = gw.LiteLlmRouterBackend()
    try:
        raw = backend._get_router().model_list
        # LiteLLM 版本间形态不一：有的是 dict[name→entry]，有的是 list[{model_name:…}]。
        if hasattr(raw, "keys"):
            registered = set(raw.keys())
        else:
            registered = {entry.get("model_name") for entry in raw}
        assert {"primary-model", "long-model", "vision-model"} <= registered
    finally:
        # `Router` 惰性缓存里存着 env 快照，必须清掉，否则污染同进程的其它用例
        backend._router = None


def test_llm_max_tokens_default_raises_for_rv09(monkeypatch) -> None:
    """v3 契约要求一条回复装下八段/多视角 JSON，8192 实测不够（见 gateway docstring）。"""
    monkeypatch.delenv("RESEARCH_LLM_MAX_TOKENS", raising=False)
    assert gw.llm_max_tokens() >= 16384
