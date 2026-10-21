"""DeepSeek Provider（providers/deepseek.py）

备用通道，OpenAI 兼容协议。
model: deepseek-v4-flash（deepseek-chat 于 2026-07-24 弃用，ADR-001-8）
"""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator

import httpx
import structlog

from app.config import settings
from app.providers.base import (
    ChatMessage,
    ChatResult,
    NewlineCompressor,
    ThinkingFilter,
    parse_tool_calls,
    strip_thinking,
)
from app.providers.http import get_http

logger = structlog.get_logger()


class DeepSeekProvider:
    """DeepSeek-v4-flash 备用通道"""

    name: str = "deepseek"

    # 已知模型的单次输出上限（token）：超限会被服务商以参数错误拒绝。
    _MODEL_OUTPUT_CAPS = {
        "glm-4v-flash": 1024,
    }

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        thinking: bool | None = None,
        stream_model: str | None = None,
    ) -> None:
        self._api_key = api_key or settings.deepseek_api_key
        self._model = model or settings.deepseek_model
        self._thinking = thinking if thinking is not None else settings.deepseek_thinking
        self._api_url = base_url or settings.deepseek_base_url
        # 流式专用模型（可选）：主模型流式通道故障时由 .env 切换 SSE 对话走它
        self._stream_model = stream_model or settings.deepseek_stream_model or ""

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        stream: bool = False,
        functions: list[dict] | None = None,
        thinking: bool | None = None,
    ) -> dict:
        payload: dict = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        # ADR-001-8: 聊天场景默认关闭深度思考（thinking 为顶层字段，
        # extra_body 是 OpenAI SDK 客户端概念，直接放进 HTTP body 无效）
        # per-call thinking 覆盖 > 实例/全局配置
        thinking_on = thinking if thinking is not None else self._thinking
        if not thinking_on and not settings.llm_omit_thinking_field:
            payload["thinking"] = {"type": "disabled"}
            # Qwen3+ 混合推理模型（硅基流动）：思考流会吃掉 max_tokens 预算并拖慢调用，
            # 用硅基流动的原生参数显式关闭（对智谱等其他服务商无副作用时可忽略）。
            if "qwen" in self._model.lower():
                payload["enable_thinking"] = False
        if stream:
            # 让流末返回真实 usage（OpenAI 兼容）
            payload["stream_options"] = {"include_usage": True}
        if functions:
            payload["tools"] = [{"type": "function", "function": f} for f in functions]
        return payload

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        functions: list[dict] | None = None,
        thinking: bool | None = None,
        request_id: str,
        scene: str,
    ) -> ChatResult:
        if not self.available:
            raise RuntimeError("DeepSeek API key not configured")

        log = logger.bind(request_id=request_id, scene=scene, provider="deepseek")
        log.info("deepseek.chat.start", model=self._model, msg_count=len(messages))
        t0 = time.monotonic()

        client = get_http()
        payload = self._build_payload(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            functions=functions,
            thinking=thinking,
        )

        try:
            # 并发生成时免费档偶发 1305/429：仅对 429 做短退避重试（正常路径零开销）
            resp: httpx.Response | None = None
            for attempt in range(3):
                # 显式总超时（挂单次 120s）：网关滴水字节会让共享 client 的
                # read 超时失效，对话链路必须硬性有界。
                resp = await client.post(
                    self._api_url,
                    headers=self._build_headers(),
                    json=payload,
                    timeout=httpx.Timeout(120.0, connect=5.0),
                )
                if resp.status_code == 429 and attempt < 2:
                    backoff = 3.0 * (attempt + 1)
                    log.warning("deepseek.chat.rate_limited_retry", attempt=attempt + 1, backoff_s=backoff)
                    await asyncio.sleep(backoff)
                    continue
                break
            resp.raise_for_status()
            data = resp.json()

            message = data["choices"][0]["message"] or {}
            content = message.get("content") or ""
            # 过滤 thinking 内容（防御 mimo 等模型返回 <think> 块）
            content = strip_thinking(content)
            tool_calls = parse_tool_calls(message)
            usage = data.get("usage", {})
            latency = int((time.monotonic() - t0) * 1000)

            log.info(
                "deepseek.chat.ok",
                latency_ms=latency,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                has_tool_calls=bool(tool_calls),
            )

            return ChatResult(
                content=content,
                provider="deepseek",
                model=self._model,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                latency_ms=latency,
                tool_calls=tool_calls,
            )
        except httpx.HTTPStatusError as e:
            latency = int((time.monotonic() - t0) * 1000)
            log.error("deepseek.chat.http_error", status=e.response.status_code, latency_ms=latency)
            raise
        except Exception:
            latency = int((time.monotonic() - t0) * 1000)
            log.exception("deepseek.chat.error", latency_ms=latency)
            raise

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.3,
        max_tokens: int = 8192,
        thinking: bool | None = None,
        request_id: str,
        scene: str,
        emit_thinking: bool = False,
    ) -> AsyncIterator[dict]:
        """流式生成。yield {"token": str} 或 {"_usage": dict}（流末真实用量）。

        emit_thinking=True 时，额外 yield {"thinking": str} 承载思考片段（M2 重构）。
        """
        if not self.available:
            raise RuntimeError("DeepSeek API key not configured")

        log = logger.bind(request_id=request_id, scene=scene, provider="deepseek")
        log.info("deepseek.stream.start", model=self._stream_model or self._model, msg_count=len(messages))

        think_filter = ThinkingFilter(emit_thinking=emit_thinking)
        nl_compressor = NewlineCompressor()
        last_finish: str | None = None  # v1.3：记录流末 finish_reason，末尾以 _finish 事件透出（供截断续写判定）
        # 2026-09-09：讯飞 MaaS x2.5-4b 即使 thinking:disabled，长推理题正文也全部落在
        # reasoning_content（content 恒空）。流式缓冲 reasoning：出现正文则视为普通思考丢弃；
        # 整流无正文则把缓冲 reasoning 作为正文下发，避免难题"零输出=服务不可用"误降级。
        reason_buf: list[str] = []
        content_seen = False

        # 迭代18：末尾免责声明缓冲。小米 MiMo 会在回复末尾追加
        # "|部分内容可能由AI生成"（平台侧注入，跨多 token）。逐 token 已无法收回，
        # 故缓冲末尾 _TAIL_HOLD_CHARS 字符，流结束时清洗尾巴后发出；首 token 零延迟。
        _TAIL_HOLD_CHARS = 48
        _AI_GEN_TAIL_RE = re.compile(r"[\s｜|]*部分内容可能由AI生成[。.!！]*\s*$")
        tail_buf = ""
        # 流末 usage（choices 可能非空的实现，见循环内注释）；缓存在此处，
        # 待尾部 token 全部下发后、_finish 之前统一透出，保证事件顺序契约。
        stream_usage: dict | None = None

        client = get_http()
        payload = self._build_payload(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            thinking=thinking,
        )
        # 流式专用模型（DEEPSEEK_STREAM_MODEL）：主模型流式通道故障/限流时由
        # env 把 SSE 对话切到备用模型；输出上限按模型钳制（超限会被拒绝）。
        if self._stream_model:
            payload["model"] = self._stream_model
            cap = self._MODEL_OUTPUT_CAPS.get(self._stream_model)
            if cap:
                payload["max_tokens"] = min(int(payload.get("max_tokens") or max_tokens), cap)

        async with client.stream(
            "POST",
            self._api_url,
            headers=self._build_headers(),
            json=payload,
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    # 流末 usage chunk。注意：OpenAI 协议下该 chunk 的 choices 既可能是
                    # 空数组，也可能是 [{"index":0,"delta":{},"finish_reason":null}]
                    # （讯飞 MaaS / scnet 等实现），因此不能以 choices 为空作为判定条件，
                    # 否则 usage 被静默丢弃 → 流式 token 计量退化为启发式估算。
                    if chunk.get("usage"):
                        stream_usage = chunk["usage"]
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    delta = choice.get("delta", {})
                    finish_reason = choice.get("finish_reason")
                    # M2 重构：思考内容两种承载方式都兼容——
                    # ① delta.reasoning_content 独立字段（DeepSeek/星火官方协议）
                    # ② delta.content 内嵌 <think> 标签（mimo 等模型，走 ThinkingFilter）
                    reasoning = delta.get("reasoning_content", "")
                    if reasoning:
                        reason_buf.append(reasoning)
                        if emit_thinking:
                            yield {"thinking": reasoning}
                    text = delta.get("content", "")
                    if text:
                        if not content_seen:
                            content_seen = True
                            reason_buf.clear()  # 有真正文：缓冲的 reasoning 是普通思考，弃
                        # 过滤 <think> 块 + 压缩多余空行
                        filtered = think_filter.process(text)
                        # M2 重构：思考片段旁路下发
                        if emit_thinking and think_filter.last_thinking:
                            yield {"thinking": think_filter.last_thinking}
                        if filtered:
                            filtered = nl_compressor.process(filtered)
                        if filtered:
                            # 迭代18：进尾部缓冲，超出的部分实时下发
                            tail_buf += filtered
                            if len(tail_buf) > _TAIL_HOLD_CHARS:
                                yield {"token": tail_buf[:-_TAIL_HOLD_CHARS]}
                                tail_buf = tail_buf[-_TAIL_HOLD_CHARS:]
                    if finish_reason:
                        log.info("deepseek.stream.finish", finish_reason=finish_reason)
                        last_finish = finish_reason
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

        # 流结束：刷出过滤器残留；整流无正文但有 reasoning → 把缓冲 reasoning 作为正文
        remaining = think_filter.flush()
        if not content_seen and reason_buf:
            remaining = "".join(reason_buf).strip()
        if remaining:
            remaining = nl_compressor.process(remaining)
        if remaining:
            tail_buf += remaining
        # 迭代18：清洗免责尾巴后发出尾部缓冲（去掉"|部分内容可能由AI生成"）
        if tail_buf:
            cleaned = _AI_GEN_TAIL_RE.sub("", tail_buf).rstrip()
            if cleaned:
                yield {"token": cleaned}

        # 流末真实用量（OpenAI 兼容：stream_options.include_usage）
        if stream_usage is not None:
            yield {"_usage": stream_usage}

        # v1.3：透出 finish_reason（length=截断），技能层据此断点续写而非整段重想
        yield {"_finish": last_finish or "stop"}
        log.info("deepseek.stream.done")

    async def health_check(self) -> dict:
        """探测 DeepSeek 通道可用性"""
        if not self.available:
            return {"ok": False, "latency_ms": 0, "error": "API key not configured"}

        t0 = time.monotonic()
        client = get_http()
        try:
            resp = await client.post(
                self._api_url,
                headers=self._build_headers(),
                json={
                    "model": self._model,
                    "messages": [{"role": "user", "content": "hi"}],
                    "max_tokens": 1,
                    # glm-5.3-flash 拒绝 thinking.type=disabled（400）→ 按全局开关省略
                    **({} if settings.llm_omit_thinking_field else {"thinking": {"type": "disabled"}}),
                },
            )
            resp.raise_for_status()
            latency = int((time.monotonic() - t0) * 1000)
            return {"ok": True, "latency_ms": latency, "model": self._model}
        except Exception as e:
            latency = int((time.monotonic() - t0) * 1000)
            return {"ok": False, "latency_ms": latency, "error": str(e)[:200]}
