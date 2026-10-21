"""LlmGateway：全平台唯一模型调用入口（B1 红线 3：任何人不得绕过）。

- chat/vision 两方法；**没有 embed 方法**——embedding 唯一入口是 B2 的
  Embedder（G2 定案，消除 B1↔B2 循环依赖），此处以属性缺失为契约（有单测断言）。
- 每次调用写 ai_runs（G6）：feature/model/prompt_version/tokens/latency/cost。
- 后端可注入：真实 LiteLLM Router（real_router_enabled 且有 key）或测试 FakeBackend。
  LiteLLM 用法参照 repos/litellm 调研（notes/B1-notes.md §2）：Router + RetryPolicy
  只对 5xx/超时重试 ≤2 + fallbacks 主备。
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from ..reliability.trace import get_current, provider_headers
from .ai_runs import AiRunRecord, AiRunsSink, digest_payload
from .config import RETRY_POLICY, USE_CASE_ROUTES, LlmSettings, get_llm_settings


class GatewayNotConfigured(RuntimeError):
    """未配置任何供应商 key 且未注入 Backend 时抛出。"""


@dataclass
class ChatChunk:
    """流式归一化块：thinking = 推理模型的 reasoning 通道，token = 可见正文。"""

    kind: Literal["token", "thinking"]
    text: str


@dataclass
class ChatResult:
    content: str
    reasoning: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model: str | None = None
    finish_reason: str | None = None


@dataclass
class AiRunMeta:
    """业务侧随调用透传的台账字段（写入 ai_runs）。"""

    feature: str
    user_id: str | None = None
    task_id: str | None = None
    prompt_name: str | None = None
    prompt_version: str | None = None
    rag_collections: list[str] = field(default_factory=list)
    retrieved_doc_ids: list[str] = field(default_factory=list)
    retrieval_query: str | None = None
    evidence_manifest: list[dict[str, Any]] | None = None
    evidence_source_ids: list[str] | None = None
    # R11.5-B：透明 lineage（完整链路，脱敏后落 ai_runs.lineage）
    lineage: dict[str, Any] | None = None


class Backend(Protocol):
    """可注入的补全后端（生产 = litellm.Router，测试 = Fake）。"""

    async def acompletion(self, *, model: str, messages: list[dict], stream: bool = False, **kw: Any) -> Any: ...


def build_router(settings: LlmSettings):
    """构造 LiteLLM Router（SDK 模式）。部署级超时给保守上限，精确超时随请求传。

    Spark（讯飞 MaaS，OpenAI 兼容）按 openai/ 自定义部署接入；矩阵部署在其 key
    存在时注册。fallbacks 仅在 matrix profile 下生效（spark profile 单部署靠重试/冷却）。
    """
    from litellm import Router

    providers = {
        "deepseek": settings.api_key_deepseek,
        "qwen": settings.api_key_qwen,
        "zhipu": settings.api_key_zhipu,
    }
    base_urls = {"deepseek": settings.base_url_deepseek or None, "qwen": settings.base_url_qwen or None}

    model_list: list[dict[str, Any]] = []
    seen: set[str] = set()
    for route in USE_CASE_ROUTES.values():
        for name in (route.primary, route.fallback):
            if not name or name in seen:
                continue
            seen.add(name)
            provider = name.split("/", 1)[0]
            if not providers.get(provider):
                continue  # 无 key 的供应商不注册（LiteLLM 要求可识别 provider）
            params: dict[str, Any] = {
                "model": name,
                "timeout": 120,
                "max_retries": 0,  # 重试交给 Router RetryPolicy，禁 provider 内部重试叠加
            }
            if base_urls.get(provider):
                params["api_base"] = base_urls[provider]
            model_list.append({"model_name": name, "litellm_params": params})

    # Spark 部署（openai 兼容自定义端点；文本用例的 spark-profile 主力）
    if settings.spark_available:
        model_list.append(
            {
                "model_name": settings.model_spark,
                "litellm_params": {
                    "model": f"openai/{settings.model_spark}",
                    "api_base": settings.base_url_spark,
                    "api_key": settings.api_key_spark,
                    "timeout": 120,
                    "max_retries": 0,
                },
            }
        )
        # 讯飞图像理解模型（同端点/key；vision 用例在 spark profile 下解析到它）
        _vision = (settings.model_vision or "").strip()
        if _vision and _vision != settings.model_spark:
            model_list.append(
                {
                    "model_name": _vision,
                    "litellm_params": {
                        "model": f"openai/{_vision}",
                        "api_base": settings.base_url_spark,
                        "api_key": settings.api_key_spark,
                        "timeout": 120,
                        "max_retries": 0,
                    },
                }
            )
        # 长文生成大模型（80B：课件/教案/讲评；独立授权 key，可缺省回落 spark key）
        _longform = (settings.model_longform or "").strip()
        if _longform and _longform not in {m["model_name"] for m in model_list}:
            model_list.append(
                {
                    "model_name": _longform,
                    "litellm_params": {
                        "model": f"openai/{_longform}",
                        "api_base": settings.base_url_spark,
                        "api_key": settings.api_key_longform or settings.api_key_spark,
                        "timeout": 180,
                        "max_retries": 0,
                    },
                }
            )

    registered = {m["model_name"] for m in model_list}
    # 救援部署（openai 兼容自定义端点）：主通道欠费/额度耗尽/持续故障时的兜底（2026-09-23）。
    if settings.rescue_available and settings.model_rescue not in registered:
        model_list.append(
            {
                "model_name": settings.model_rescue,
                "litellm_params": {
                    "model": f"openai/{settings.model_rescue}",
                    "api_base": settings.base_url_rescue,
                    "api_key": settings.api_key_rescue,
                    "timeout": 180,
                    "max_retries": 0,
                },
            }
        )
        registered.add(settings.model_rescue)
    fallbacks: list[dict[str, list[str]]] = []
    if settings.active_profile != "spark":
        for route in USE_CASE_ROUTES.values():
            if route.fallback and route.primary in registered and route.fallback in registered:
                fallbacks.append({route.primary: [route.fallback]})
    # R3 修复：spark profile 下 80B（model_longform）额度/授权间歇性不可用 →
    # 不能再直接降级到 basic_mode，必须有可用回落。4B（model_spark）实测稳定 200，
    # 作为 80B 的 fallback（慢一些但能跑通；超时已由用例路由抬到 180/300s）。
    if (
        settings.active_profile == "spark"
        and settings.model_longform
        and settings.model_spark
        and settings.model_longform != settings.model_spark
        and settings.model_longform in registered
        and settings.model_spark in registered
    ):
        fallbacks.append({settings.model_longform: [settings.model_spark]})

    # 救援通道兜底（2026-09-23）：spark profile 下的一切主部署（短任务/长文）都挂 rescue，
    # 覆盖「方舟欠费 403 / 额度耗尽 429 / 端点持续 5xx」这类整通道故障。
    if settings.rescue_available and settings.model_rescue in registered:
        for primary in (settings.model_spark, settings.model_longform, settings.model_vision):
            name = (primary or "").strip()
            if name and name in registered and name != settings.model_rescue:
                fallbacks.append({name: [settings.model_rescue]})

    return Router(
        model_list=model_list,
        fallbacks=fallbacks,
        retry_policy=RETRY_POLICY,
        num_retries=2,
        allowed_fails=3,
        cooldown_time=5,
    )


class LlmGateway:
    def __init__(
        self,
        runs_sink: AiRunsSink,
        *,
        settings: LlmSettings | None = None,
        backend: Backend | None = None,
    ) -> None:
        self._settings = settings or get_llm_settings()
        self._sink = runs_sink
        self._backend: Backend | None = backend
        if self._backend is None and self._settings.real_router_enabled and self._settings.has_any_key:
            self._backend = build_router(self._settings)

    # ── 契约断言：网关无 embed（B1 红线 / G2）──
    def __getattr__(self, name: str) -> None:  # 仅拦截不存在属性，给出清晰错误
        if name == "embed":
            raise AttributeError(
                "LlmGateway 无 embed 方法（G2 定案）：embedding 唯一入口是 B2 的 app.rag.embedder.Embedder"
            )
        raise AttributeError(name)

    def _require_backend(self) -> Backend:
        if self._backend is None:
            raise GatewayNotConfigured("未配置 LLM key（LLM_API_KEY_*）且未注入 Backend；测试请注入 FakeBackend")
        return self._backend

    def _resolve_model(self, use_case: str, model: str | None) -> str:
        if model is not None:
            # R11.5-C fail-closed：显式传入但为空白 → 配置缺陷，拒绝，绝不悄悄改用默认路由
            resolved = str(model).strip()
            if not resolved:
                raise GatewayNotConfigured(
                    f"用例 {use_case!r} 显式模型名为空 → fail-closed，拒绝调用供应商"
                )
            return resolved
        resolved = self._settings.resolve_route(use_case).primary
        if not resolved:
            # R11.5-C fail-closed：显式空模型 = 配置缺陷，拒绝向供应商发送空模型名
            raise GatewayNotConfigured(
                f"用例 {use_case!r} 解析出空模型名（显式空/未配置）→ fail-closed，拒绝调用供应商"
            )
        return resolved

    def _call_kwargs(self, resolved: str, use_case: str, temperature: float, tools: list | None = None) -> dict[str, Any]:
        route = self._settings.resolve_route(use_case)
        kwargs: dict[str, Any] = {
            "model": resolved,
            "temperature": temperature,
            "timeout": route.timeout_s,  # 每用例超时随请求精确覆盖（30/180/60）
        }
        if tools:
            kwargs["tools"] = tools
        # R11.5-C：trace 贯穿 → provider 出站透传 W3C 头（request_trace 上下文续链）
        headers = provider_headers()
        if headers:
            kwargs["extra_headers"] = headers
        if self._settings.thinking_extra_body is not None:
            # 通用 thinking 开关（方舟语法）：适用全部主通道模型；救援通道在下游
            # 剥掉 extra_body，不受影响。优先级高于 spark 顶层 enable_thinking。
            kwargs["extra_body"] = dict(self._settings.thinking_extra_body)
        elif (
            self._settings.spark_available
            and resolved == self._settings.model_spark
            and not self._settings.omit_thinking_extra_body
        ):
            # Spark 专有：thinking 默认关（FC/结构化解析干扰，学生端 ADR 同款）。
            # 该 MaaS 只认顶层 enable_thinking；旧写法 {"thinking":{"type":"disabled"}}
            # 实测不生效——同一提示下 reasoning token 反而翻倍（1726→8687）、
            # 延迟 25.4s→108.4s，而 enable_thinking:false 为 16.3s / 964 token。
            kwargs["extra_body"] = {"enable_thinking": self._settings.spark_thinking}
        return kwargs

    async def _record(self, meta: AiRunMeta, model: str, started: float, response: Any = None, error: str | None = None, temperature: float | None = None, *, input_payload: Any = None, output_payload: Any = None) -> None:
        usage = getattr(response, "usage", None) if response is not None else None
        cost = None
        try:  # cost 表缺模型价格时不应影响主链路
            import litellm

            if response is not None:
                cost = litellm.completion_cost(completion_response=response)
        except Exception:
            cost = None
        record = AiRunRecord(
            feature=meta.feature,
            model=model,
            prompt_name=meta.prompt_name,
            prompt_version=meta.prompt_version,
            temperature=temperature,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            cost=cost,
            latency_ms=int((time.monotonic() - started) * 1000),
            user_id=meta.user_id,
            task_id=meta.task_id,
            rag_collections=list(meta.rag_collections),
            retrieved_doc_ids=list(meta.retrieved_doc_ids),
            retrieval_query=meta.retrieval_query,
            evidence_manifest=meta.evidence_manifest,
            evidence_source_ids=meta.evidence_source_ids,
            error=error,
            # R11.5-B：输入输出只记 hash（脱敏）+ 完整 lineage
            lineage=meta.lineage,
            input_hash=digest_payload(input_payload),
            output_hash=digest_payload(output_payload),
            # R11.5-C：trace 贯穿落库（contextvar 当前链路）
            trace_id=get_current().trace_id if get_current() else None,
        )
        try:
            await self._sink.write(record)
        except Exception:  # 台账后端故障不拖垮对话主链路，但必须大声留痕（ai_runs 是唯一账本）
            import logging

            logging.getLogger(__name__).exception("ai_runs 写入失败（feature=%s）", record.feature)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        use_case: str = "chat",
        temperature: float | None = None,
        metadata: AiRunMeta | None = None,
    ) -> ChatResult | AsyncIterator[ChatChunk]:
        """对话/补全。stream=True 时返回 ChatChunk 异步流（thinking 与 token 分通道）。"""
        meta = metadata or AiRunMeta(feature=use_case)
        resolved = self._resolve_model(use_case, model)
        route = self._settings.resolve_route(use_case)
        temp = route.temperature if temperature is None else temperature
        backend = self._require_backend()
        kwargs = self._call_kwargs(resolved, use_case, temp, tools)
        kwargs["messages"] = messages

        if not stream:
            started = time.monotonic()
            try:
                response = await backend.acompletion(**kwargs)
            except Exception as exc:
                # 救援通道（2026-09-23）：主通道**整通道故障**（如方舟欠费 403
                # AccountOverdueError / 额度耗尽 429）时，LiteLLM Router 的 fallbacks
                # 对 auth/forbidden 类错误不保证触发（实测：403 直接上抛 → 用户端
                # 只剩 basic_mode）。故在网关层做一次**显式**兜底：换救援模型重试一次；
                # 救援也失败则原样抛出（fail-closed，不吞错）。
                rescue_model = (self._settings.model_rescue or "").strip()
                if self._settings.rescue_available and rescue_model and resolved != rescue_model:
                    await self._record(meta, resolved, started, error=repr(exc), temperature=temp, input_payload=messages)
                    # extra_body（主通道专有，如 spark 的 enable_thinking）不得带给救援模型
                    rescue_kwargs = {k: v for k, v in kwargs.items() if k not in ("extra_body",)}
                    rescue_kwargs["model"] = rescue_model
                    started = time.monotonic()
                    response = await backend.acompletion(**rescue_kwargs)
                    result = self._to_chat_result(response)
                    await self._record(meta, f"{rescue_model}(rescue)", started, response=response, temperature=temp,
                                       input_payload=messages, output_payload=result.content)
                    return result
                await self._record(meta, resolved, started, error=repr(exc), temperature=temp, input_payload=messages)
                raise
            result = self._to_chat_result(response)
            await self._record(meta, resolved, started, response=response, temperature=temp,
                               input_payload=messages, output_payload=result.content)
            return result

        return self._stream_chat(backend, kwargs, meta, resolved, temp, input_payload=messages)

    async def _stream_chat(self, backend: Backend, kwargs: dict, meta: AiRunMeta, model: str, temp: float, *, input_payload: Any = None) -> AsyncIterator[ChatChunk]:
        started = time.monotonic()
        usage = None
        streamed_text: list[str] = []
        try:
            stream = await backend.acompletion(**kwargs, stream=True, stream_options={"include_usage": True})
            async for chunk in stream:
                usage = getattr(chunk, "usage", None) or usage
                delta = _delta_of(chunk)
                if delta is None:
                    continue
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield ChatChunk(kind="thinking", text=reasoning)
                content = getattr(delta, "content", None)
                if content:
                    streamed_text.append(content)
                    yield ChatChunk(kind="token", text=content)
        except Exception as exc:
            await self._record(meta, model, started, error=repr(exc), temperature=temp, input_payload=input_payload)
            raise
        # 流式 usage 记录（include_usage 尾 chunk）；输出 hash 用聚合后的可见正文（脱敏）
        record_usage = _UsageShim(usage)
        await self._record(meta, model, started, response=record_usage, temperature=temp,
                           input_payload=input_payload, output_payload="".join(streamed_text))

    async def vision(
        self,
        image: str,
        prompt: str,
        *,
        model: str | None = None,
        use_case: str = "vision",
        metadata: AiRunMeta | None = None,
    ) -> str:
        """拍照理解/读题重建。image 为 data URL 或 http(s) URL。"""
        meta = metadata or AiRunMeta(feature=use_case)
        resolved = self._resolve_model(use_case, model)
        backend = self._require_backend()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image}},
                ],
            }
        ]
        started = time.monotonic()
        try:
            response = await backend.acompletion(model=resolved, messages=messages, temperature=0.2)
        except Exception as exc:
            await self._record(meta, resolved, started, error=repr(exc), input_payload={"prompt": prompt})
            raise
        content = self._to_chat_result(response).content
        await self._record(meta, resolved, started, response=response,
                           input_payload={"prompt": prompt}, output_payload=content)
        return content

    @staticmethod
    def _to_chat_result(response: Any) -> ChatResult:
        choice = response.choices[0]
        message = choice.message
        tool_calls = [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
            for tc in (getattr(message, "tool_calls", None) or [])
        ]
        return ChatResult(
            content=message.content or "",
            reasoning=getattr(message, "reasoning_content", None),
            tool_calls=tool_calls,
            model=getattr(response, "model", None),
            finish_reason=getattr(choice, "finish_reason", None),
        )


class _UsageShim:
    """把流式尾 chunk 的 usage 包装成 _record 可读的形状。"""

    def __init__(self, usage: Any) -> None:
        self.usage = usage


def _delta_of(chunk: Any) -> Any:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    return getattr(choices[0], "delta", None)
