"""Spark Provider（providers/spark.py）

主通道 —— 星火大模型 HTTP 协议（禁止 WebSocket SDK，ADR-001-8）。
使用 OpenAI 兼容接口: https://spark-api-open.xf-yun.com/v1/chat/completions
"""

import json
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

SPARK_API_URL = "https://spark-api-open.xf-yun.com/v1/chat/completions"


class SparkProvider:
    """星火大模型主通道（HTTP 协议）"""

    name: str = "spark"

    def __init__(
        self,
        *,
        api_password: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        thinking: bool | None = None,
    ) -> None:
        self._api_password = api_password or settings.spark_api_password
        self._model = model or settings.spark_model
        # 端点优先级：调用方显式传入（三层回退的用户/全局配置）> env > 默认 spark-api-open
        self._api_url = base_url or settings.spark_base_url or SPARK_API_URL
        self._thinking = thinking if thinking is not None else settings.spark_thinking

    @property
    def available(self) -> bool:
        return bool(self._api_password)

    def _build_headers(self) -> dict[str, str]:
        # 星火 OpenAI 兼容接口使用 Bearer token 鉴权
        return {
            "Authorization": f"Bearer {self._api_password}",
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
        # 思考模式：per-call 覆盖 > 实例/全局配置（默认关 —— mimo 等模型
        # 默认开启 thinking 会干扰 Function Calling 解析）
        thinking_on = thinking if thinking is not None else self._thinking
        if not settings.llm_omit_thinking_field:
            payload["thinking"] = {"type": "enabled" if thinking_on else "disabled"}
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
            raise RuntimeError("Spark API password not configured")

        log = logger.bind(request_id=request_id, scene=scene, provider="spark")
        log.info("spark.chat.start", model=self._model, msg_count=len(messages))
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
            # 显式总超时（挂单次 120s）：共享 client 的 read=180 会被
            # 网关缓慢滴水字节重置而失效，课堂链路必须硬性有界。
            resp = await client.post(
                self._api_url,
                headers=self._build_headers(),
                json=payload,
                timeout=httpx.Timeout(120.0, connect=5.0),
            )
            resp.raise_for_status()
            data = resp.json()

            message = data["choices"][0]["message"] or {}
            content = message.get("content") or ""
            if not content:
                # 2026-09-08 实测：讯飞 MaaS spark-x2.5-4b 无视 thinking:disabled 参数，
                # 正文全部落在 reasoning_content（content 恒空）——不回退会让管家全线静默降级模板
                content = message.get("reasoning_content") or ""
            # 防御性过滤：移除可能的 <think> 块
            content = strip_thinking(content)
            tool_calls = parse_tool_calls(message)
            usage = data.get("usage", {})
            latency = int((time.monotonic() - t0) * 1000)

            log.info(
                "spark.chat.ok",
                latency_ms=latency,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                has_tool_calls=bool(tool_calls),
            )

            return ChatResult(
                content=content,
                provider="spark",
                model=self._model,
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                latency_ms=latency,
                tool_calls=tool_calls,
            )
        except httpx.HTTPStatusError as e:
            latency = int((time.monotonic() - t0) * 1000)
            log.error("spark.chat.http_error", status=e.response.status_code, latency_ms=latency)
            raise
        except Exception:
            latency = int((time.monotonic() - t0) * 1000)
            log.exception("spark.chat.error", latency_ms=latency)
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

        emit_thinking=True 时，额外 yield {"thinking": str} 事件承载 <think> 思考片段，
        供上层把模型思考过程下发前端「思考过程」面板（M2 重构）。
        """
        if not self.available:
            raise RuntimeError("Spark API password not configured")

        log = logger.bind(request_id=request_id, scene=scene, provider="spark")
        log.info("spark.stream.start", model=self._model, msg_count=len(messages))

        think_filter = ThinkingFilter(emit_thinking=emit_thinking)
        nl_compressor = NewlineCompressor()
        last_finish: str | None = (
            None  # v1.3：记录流末 finish_reason，末尾以 _finish 事件透出（供截断续写判定）
        )
        # 2026-09-09：讯飞 MaaS x2.5-4b 即使 thinking:disabled，长推理题正文也全部落在
        # reasoning_content（content 恒空，与 chat() 的 2026-09-08 补丁同源问题）。
        # 流式缓冲 reasoning：一旦出现正文即按"真思考"处理（丢弃缓冲）；整流无正文则把
        # 缓冲的 reasoning 作为正文下发，避免难题全预算被思考吃光后"零输出=服务不可用"误降级。
        reason_buf: list[str] = []
        content_seen = False
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
                    # ① delta.reasoning_content 独立字段（星火/DeepSeek 官方协议）
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
                        # 防御性过滤 <think> 块 + 压缩多余空行
                        filtered = think_filter.process(text)
                        # M2 重构：思考片段旁路下发（不受 NewlineCompressor 影响，保持原始思考流）
                        if emit_thinking and think_filter.last_thinking:
                            yield {"thinking": think_filter.last_thinking}
                        if filtered:
                            filtered = nl_compressor.process(filtered)
                        if filtered:
                            yield {"token": filtered}
                    if finish_reason:
                        log.info("spark.stream.finish", finish_reason=finish_reason)
                        last_finish = finish_reason
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

        # 流结束：刷出过滤器残留；整流无正文但有 reasoning → MaaS 把正文写进了
        # reasoning_content（见上方注释），此时把缓冲的 reasoning 作为正文下发
        remaining = think_filter.flush()
        if not content_seen and reason_buf:
            remaining = "".join(reason_buf).strip()
        if remaining:
            filtered_rem = nl_compressor.process(remaining)
            if filtered_rem:
                yield {"token": filtered_rem}

        # 流末真实用量（OpenAI 兼容：stream_options.include_usage）
        if stream_usage is not None:
            yield {"_usage": stream_usage}

        # v1.3：透出 finish_reason（length=截断），技能层据此断点续写而非整段重想
        yield {"_finish": last_finish or "stop"}
        log.info("spark.stream.done")

    async def health_check(self) -> dict:
        """探测星火通道可用性"""
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
                },
            )
            resp.raise_for_status()
            latency = int((time.monotonic() - t0) * 1000)
            return {"ok": True, "latency_ms": latency}
        except Exception as e:
            latency = int((time.monotonic() - t0) * 1000)
            return {"ok": False, "latency_ms": latency, "error": str(e)[:200]}
