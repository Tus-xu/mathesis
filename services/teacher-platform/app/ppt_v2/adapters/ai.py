"""AIPlatformAdapter —— V2 AI 侧契约（R11）。

包装 app.llm.gateway.LlmGateway（全平台唯一模型调用入口；dirty 现场不改写）。
模型意图（用户给定，总手册 G4）：text=spark-x2.5-4b；vision=xophunyuanocr；
OpenAI-compatible 指向讯飞 v2（LlmSettings.base_url_spark 缺省值），不重复拼接 /v2 或
/chat/completions。

密钥约定：文本走 MODEL_API_KEY，视觉走 VISION_API_KEY（独立 MaaS 授权）；只从进程环境读取。
model_longform 显式钉空：V2 用例 pin 4B，防 .env 的 LLM_MODEL_LONGFORM 覆盖路由。
thinking 默认关（R04 契约）；供应商是否接受 thinking enabled 由 capability probe 实测，
不接受则在 adapter capability 中禁用，不魔改全局 gateway。

structured()：网关返回文本 → JSON 解析 → Pydantic 校验，bounded retry ≤2。
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.llm.ai_runs import AiRunsSink, MemoryAiRunsSink
from app.llm.config import LlmSettings, get_llm_settings
from app.llm.gateway import AiRunMeta, ChatChunk, LlmGateway
from app.ppt_v2.adapters.dto import ChatReply, extract_json_object
from app.ppt_v2.adapters.errors import AdapterFatalError, AdapterRetryableError

MODEL_TEXT = "spark-x2.5-4b"
MODEL_VISION = "xophunyuanocr"
_XFYUN_V2_BASE = "https://maas-api.cn-huabei-1.xf-yun.com/v2"

T = TypeVar("T", bound=BaseModel)

# 上游异常类名 → 可重试/不可恢复（不 import litellm，避免 ppt_v2 vendor 守卫触发）
_RETRYABLE_EXC = {
    "TimeoutError",
    "ConnectError",
    "APIConnectionError",
    "InternalServerError",
    "ServiceUnavailableError",
    "APITimeoutError",
}


def classify_error(exc: Exception) -> type[AdapterRetryableError] | type[AdapterFatalError]:
    if isinstance(exc, TimeoutError) or type(exc).__name__ in _RETRYABLE_EXC:
        return AdapterRetryableError
    return AdapterFatalError


class AIPlatformAdapter:
    """V2 AI 适配：chat / stream / structured / vision（包装 LlmGateway，spark profile）。"""

    def __init__(
        self,
        *,
        backend: Any = None,
        text_key: str | None = None,
        vision_key: str | None = None,
        spark_thinking: bool = False,
        runs_sink: AiRunsSink | None = None,
    ) -> None:
        self._backend = backend
        # 「调用方是否显式传 key」与「环境是否存在 MODEL_API_KEY」是两回事：前者为假时应
        # 优先走平台 spark 通道（见 _env_text_channel），否则本机若存在过期的 MODEL_API_KEY
        # 会把全部 ppt_v2 文本用例钉到失效的讯飞 pin 通道（实测 no valid authorization）。
        self._explicit_text_key = text_key is not None
        self._text_key = text_key if text_key is not None else os.getenv("MODEL_API_KEY")
        self._vision_key = vision_key if vision_key is not None else os.getenv("VISION_API_KEY")
        self._spark_thinking = spark_thinking
        self._sink = runs_sink or MemoryAiRunsSink()
        self._text_gw: LlmGateway | None = None
        self._vision_gw: LlmGateway | None = None

    # ── capability 标志（probe 实测后可能翻转）──
    supports_stream: bool = True
    supports_structured: bool = True
    thinking_default_disabled: bool = True
    thinking_enabled_accepted: bool | None = None  # None=未实测

    def _settings(
        self, *, api_key: str, model: str, base_url: str, model_longform: str = ""
    ) -> LlmSettings:
        return LlmSettings(
            active_profile="spark",
            api_key_spark=api_key,
            base_url_spark=base_url,
            model_spark=model,
            # 默认显式 pin 空（防 LLM_MODEL_LONGFORM 覆盖讯飞 4B pin 通道路由）；
            # 平台 spark 通道下由调用处传入真实 longform 模型（80B/lite）。
            model_longform=model_longform,
            model_vision=MODEL_VISION,
            spark_thinking=self._spark_thinking,
            real_router_enabled=self._backend is None,
        )

    @staticmethod
    def _env_text_channel() -> tuple[str, str, str] | None:
        """环境 spark 文本通道（R4 · subagent D）。

        默认 pin 总手册 G4 的讯飞 4B（MODEL_TEXT + _XFYUN_V2_BASE + MODEL_API_KEY）。
        当进程环境给出**完整**平台 spark 通道（LLM_MODEL_SPARK / LLM_BASE_URL_SPARK /
        LLM_API_KEY_SPARK，与平台 .env 同源）时返回该通道——实测原 pin 通道额度失效
        （"no valid authorization: the account lacks an active order"）会导致 ppt_v2
        全部 AI 链路（局部重生成/快捷操作/添加页）不可用。密钥只经进程环境注入。
        """
        model = (os.getenv("LLM_MODEL_SPARK") or "").strip()
        base = (os.getenv("LLM_BASE_URL_SPARK") or "").strip()
        key = (os.getenv("LLM_API_KEY_SPARK") or "").strip()
        if model and base and key:
            return model, base, key
        # 进程环境未注入 LLM_*（taskiq worker 入口不执行 main.load_dotenv）→ 回落读 .env
        # （LlmSettings 的 env_file），否则 worker 侧仍会落回失效的 MODEL_API_KEY pin 通道。
        settings = get_llm_settings()
        model = model or (settings.model_spark or "").strip()
        base = base or (settings.base_url_spark or "").strip()
        key = key or (settings.api_key_spark or "").strip()
        if model and base and key:
            return model, base, key
        return None

    @staticmethod
    def _platform_longform_model() -> str:
        """平台长文模型（.env 的 LLM_MODEL_LONGFORM / LLM_MODEL_SPARK 同源通道）。

        平台 spark 通道下，longform 用例（含 ppt_v2 逐页增稠 deck_enrich）应走该模型；
        实测 model_spark（doubao-seed-2.0-mini）在订阅额度耗尽时会整体不可用，
        而 longform（doubao-seed-2.0-lite）仍可用。
        """
        m = (os.getenv("LLM_MODEL_LONGFORM") or "").strip()
        return m or (get_llm_settings().model_longform or "").strip()

    def _text_gateway(self) -> LlmGateway:
        if self._text_gw is None:
            env_channel = self._env_text_channel() if not self._explicit_text_key else None
            if env_channel is not None:
                # 生产（调用方未显式传 key）+ 环境通道完整 → 按环境路由
                model, base, key = env_channel
                self._text_gw = LlmGateway(
                    self._sink,
                    settings=self._settings(
                        api_key=key, model=model, base_url=base,
                        model_longform=self._platform_longform_model(),
                    ),
                    backend=self._backend,
                )
                return self._text_gw
            key = self._resolve_key("MODEL_API_KEY", self._text_key)
            self._text_gw = LlmGateway(
                self._sink,
                settings=self._settings(api_key=key, model=MODEL_TEXT, base_url=_XFYUN_V2_BASE),
                backend=self._backend,
            )
        return self._text_gw

    def _vision_gateway(self) -> LlmGateway:
        if self._vision_gw is None:
            key = self._resolve_key("VISION_API_KEY", self._vision_key)
            self._vision_gw = LlmGateway(
                self._sink,
                settings=self._settings(api_key=key, model=MODEL_TEXT, base_url=_XFYUN_V2_BASE),
                backend=self._backend,
            )
        return self._vision_gw

    @staticmethod
    def _resolve_key(env_name: str, explicit: str | None) -> str:
        """密钥解析（R11.5-C fail-closed 语义）：

        - explicit=None → 走环境变量；环境缺失 → 抛 AdapterFatalError；
        - explicit 为「显式空字符串」→ 视为调用方显式声明未配置，**不回落环境变量**，
          直接 fail-closed（防止静默借用易变的环境凭据——显式空 ≠ 让我找别的 key）。
        """
        if explicit is None:
            return AIPlatformAdapter._env_key(env_name)
        if not explicit.strip():
            raise AdapterFatalError(f"{env_name} 未配置（显式空 key 被拒绝；key 只经进程环境注入）")
        return explicit

    @staticmethod
    def _env_key(name: str) -> str:
        val = os.getenv(name)
        if not val:
            raise AdapterFatalError(f"{name} 未配置（key 只经进程环境注入，B2 红线 3）")
        return val

    @staticmethod
    def _classify(exc: Exception) -> AdapterRetryableError | AdapterFatalError:
        cls = classify_error(exc)
        return cls(f"{type(exc).__name__}: {exc}", status_code=getattr(exc, "status_code", None))

    # ── 文本用例 ────────────────────────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        use_case: str = "chat",
        temperature: float | None = None,
        model: str | None = None,
    ) -> ChatReply:
        """非流式文本补全（短回答/结构化前处理共用入口）。"""
        try:
            result = await self._text_gateway().chat(
                messages,
                use_case=use_case,
                temperature=temperature,
                model=model,
                metadata=AiRunMeta(feature="ppt_v2", prompt_name="chat"),
            )
        except Exception as exc:
            raise self._classify(exc) from exc
        return ChatReply(
            text=result.content,
            model=result.model,
            finish_reason=result.finish_reason,
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        use_case: str = "chat",
        temperature: float | None = None,
        model: str | None = None,
    ) -> AsyncIterator[ChatChunk]:
        """流式文本补全（R12 大纲 SSE 候选；thinking/token 分通道由网关归一）。"""
        gen = await self._text_gateway().chat(
            messages,
            stream=True,
            use_case=use_case,
            temperature=temperature,
            model=model,
            metadata=AiRunMeta(feature="ppt_v2", prompt_name="stream"),
        )
        async for chunk in gen:
            yield chunk

    async def structured(
        self,
        model: type[T],
        *,
        system: str,
        user: str,
        temperature: float = 0.2,
        use_case: str = "chat",
        max_retries: int = 2,
    ) -> T:
        """结构化输出：JSON 解析 + Pydantic 校验，非法输出带纠错提示 bounded retry。"""
        schema_text = json.dumps(model.model_json_schema(), ensure_ascii=False)
        last_exc: Exception | None = None
        current_user = user
        for attempt in range(max_retries + 1):
            messages = [
                {
                    "role": "system",
                    "content": (
                        f"{system}\n\n你必须只输出合法 JSON（不要 Markdown 代码块），"
                        f"且必须符合以下 JSON Schema：\n{schema_text}"
                    ),
                },
                {"role": "user", "content": current_user},
            ]
            try:
                reply = await self.chat(messages, use_case=use_case, temperature=temperature)
                payload = extract_json_object(reply.text)
                return model.model_validate(payload)
            except (json.JSONDecodeError, ValueError, ValidationError) as exc:
                last_exc = exc
                if attempt < max_retries:
                    current_user = (
                        f"{current_user}\n\n（上一次输出不是合法 JSON 或不符合 schema，"
                        f"请只重新输出 JSON 本身）"
                    )
        raise AdapterFatalError(
            f"结构化输出经 {max_retries + 1} 次尝试仍非法: {type(last_exc).__name__}"
        )

    # ── 视觉用例（VisionAdapter 的上层通道）──────────────────────────────────────

    async def vision(self, image: str, prompt: str, *, model: str | None = None) -> str:
        """图像理解原文返回（image 为 data URL 或 http(s) URL；原图原样传递，不替换）。"""
        try:
            return await self._vision_gateway().vision(
                image,
                prompt,
                model=model,
                metadata=AiRunMeta(feature="ppt_v2", prompt_name="vision"),
            )
        except Exception as exc:
            raise self._classify(exc) from exc
