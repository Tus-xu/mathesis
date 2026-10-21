"""RV-XF · 讯飞星辰非流式工作流调用引擎。

协议照抄本仓库既有实现 `services/api/app/providers/xingchen.py`（**只读参考**，
不 import）：

- URL：`{base_url.rstrip('/')}/workflow/v1/chat/completions`
- 鉴权头：`Authorization: Bearer {key}:{secret}`
- 请求体：`{"flow_id", "uid", "stream": false, "parameters"}`
- 错误码：20804=超时；20375/20376/20902/20903=限流；20357=并发超限；
  20373=模型授权/额度；HTTP 401/403 且 body 为 `{"message": "..."}` = 鉴权失败。
- 成功：HTTP 200 且 `code in (0, None)`；业务数据在 `choices[0].messages[*].content`
  （兼容 `choices[0].delta.content` / `choices[0].message.content`），
  content 可能是纯文本 / JSON 字符串 / dict —— 一律容错并保留 raw。

**脱敏铁律**：任何异常的 `str()`、任何日志都不得出现 key/secret。错误消息里
涉及凭证时只允许出现 key 的前 4 位 + `***`；上游若回显了凭证，也会被
`_scrub()` 覆写成掩码。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from .models import (
    WORKFLOW_BY_KEY,
    XfConcurrency,
    XfDisabled,
    XfError,
    XfNotConfigured,
    XfQuotaExceeded,
    XfRateLimited,
    XfResult,
    XfSettings,
    XfTimeout,
    XfTransportError,
    XfUnauthorized,
    XfUpstreamError,
    load_settings,
    mask_secret,
)

logger = logging.getLogger(__name__)

#: 上游「超时」业务码。
_CODE_TIMEOUT = 20804
#: 上游「限流」业务码集合。
_CODE_RATE_LIMIT = frozenset({20375, 20376, 20902, 20903})
#: 上游「并发处理中」业务码。
_CODE_CONCURRENCY = 20357
#: 上游「模型授权 / 额度」业务码。
_CODE_QUOTA = 20373
#: 上游「flow_id 不存在」业务码（真机实测：配置错误时返回 20201）。
_CODE_FLOW_NOT_FOUND = 20201

#: 默认 `Retry-After`（秒）——限流/并发类响应头。
DEFAULT_RETRY_AFTER = 5


# --------------------------------------------------------------------------
# 并发信号量（按 max_concurrency 惰性创建，复用既有实现的缓存策略）
# --------------------------------------------------------------------------

_semaphores: dict[int, asyncio.Semaphore] = {}


def _get_semaphore(limit: int) -> asyncio.Semaphore:
    n = limit if limit and limit > 0 else 1
    sem = _semaphores.get(n)
    if sem is None:
        sem = asyncio.Semaphore(n)
        _semaphores[n] = sem
    return sem


# --------------------------------------------------------------------------
# 脱敏
# --------------------------------------------------------------------------


def _scrub(text: str, settings: XfSettings) -> str:
    """把任意文本里出现的真实凭证替换成掩码，保证不外泄。"""
    if not text:
        return ""
    out = text
    for secret in (settings.api_secret, settings.api_key):
        if secret:
            out = out.replace(secret, mask_secret(secret))
    return out


def _build_headers(settings: XfSettings) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.api_key}:{settings.api_secret}",
        "Content-Type": "application/json",
    }


def _build_url(settings: XfSettings) -> str:
    base = settings.base_url.rstrip("/")
    return f"{base}/workflow/v1/chat/completions"


# --------------------------------------------------------------------------
# 上游码 / HTTP 状态 → 异常
# --------------------------------------------------------------------------


def _map_upstream_code(code: int, message: str) -> XfError:
    msg = message or f"上游业务错误码 {code}"
    if code == _CODE_TIMEOUT:
        return XfTimeout(msg, retry_after=None)
    if code in _CODE_RATE_LIMIT:
        return XfRateLimited(msg, retry_after=DEFAULT_RETRY_AFTER)
    if code == _CODE_CONCURRENCY:
        return XfConcurrency(msg, retry_after=DEFAULT_RETRY_AFTER)
    if code == _CODE_QUOTA:
        return XfQuotaExceeded(msg)
    if code == _CODE_FLOW_NOT_FOUND:
        # 配置的 flow_id 在上游不存在 → 配置问题，按「未正确配置」处理（503）。
        return XfNotConfigured(f"上游找不到该 flow（{code}）: {msg}")
    return XfUpstreamError(msg)


def _map_http_error(status: int, body_text: str, settings: XfSettings) -> XfError:
    """非 200 响应 → 异常。401/403 且携带 `message` 时把上游原文**原样**透传。"""
    upstream_msg = ""
    if body_text:
        try:
            parsed = json.loads(body_text)
            if isinstance(parsed, dict):
                upstream_msg = str(parsed.get("message") or parsed.get("msg") or "")
                code = parsed.get("code")
                if isinstance(code, int) and code not in (0,):
                    # 上游把业务码放在 HTTP 错误体里（少见但存在）时优先按业务码分类。
                    return _map_upstream_code(code, upstream_msg or body_text)
        except (json.JSONDecodeError, TypeError):
            upstream_msg = ""
    if status in (401, 403):
        # 鉴权类：上游实测只回 `{"message": "HMAC secret key does not match"}`。
        # message 必须**原样**透传，便于联调定位（但仍过脱敏）。
        return XfUnauthorized(
            _scrub(upstream_msg or f"上游鉴权失败（HTTP {status}）", settings)
        )
    detail = _scrub(upstream_msg or body_text[:200], settings)
    return XfUpstreamError(f"上游 HTTP {status}: {detail}")


# --------------------------------------------------------------------------
# content 解析（容错：纯文本 / JSON 字符串 / dict）
# --------------------------------------------------------------------------


def _collect_contents(choice: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """从单个 choice 抽取文本与结构化 dict（三级兼容）。

    优先 `messages[*].content`（论文工作流实测形状），回退 `delta.content` /
    `message.content`。返回 `(raw_text, first_structured_dict_or_None)`。
    """
    pieces: list[Any] = []
    messages = choice.get("messages")
    if isinstance(messages, list):
        for m in messages:
            if isinstance(m, dict) and "content" in m:
                pieces.append(m.get("content"))
    if not pieces:
        delta = choice.get("delta")
        if isinstance(delta, dict) and delta.get("content") is not None:
            pieces.append(delta.get("content"))
    if not pieces:
        message = choice.get("message")
        if isinstance(message, dict) and message.get("content") is not None:
            pieces.append(message.get("content"))

    texts: list[str] = []
    structured: dict[str, Any] | None = None
    for piece in pieces:
        if piece is None:
            continue
        if isinstance(piece, dict):
            if structured is None:
                structured = piece
            texts.append(json.dumps(piece, ensure_ascii=False))
        elif isinstance(piece, (list, tuple)):
            texts.append(json.dumps(piece, ensure_ascii=False))
        else:
            text = str(piece)
            texts.append(text)
            if structured is None:
                parsed = _try_json_dict(text)
                if parsed is not None:
                    structured = parsed
    return "".join(texts), structured


def _try_json_dict(text: str) -> dict[str, Any] | None:
    """尝试把字符串解析为 dict（容忍 BOM 与 Python 风格 True/False/None）。"""
    if not text:
        return None
    candidate = text.strip().lstrip("\ufeff")
    if not candidate:
        return None
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        import re as _re

        normalized = _re.sub(r"\bTrue\b", "true", candidate)
        normalized = _re.sub(r"\bFalse\b", "false", normalized)
        normalized = _re.sub(r"\bNone\b", "null", normalized)
        try:
            parsed = json.loads(normalized)
        except (json.JSONDecodeError, TypeError):
            return None
    return parsed if isinstance(parsed, dict) else None


def _extract_result(data: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """从响应体抽取 `(result_dict, raw_text)`。"""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return {"text": ""}, ""
    first = choices[0] if isinstance(choices[0], dict) else {}
    raw_text, structured = _collect_contents(first)
    if structured is not None:
        return structured, raw_text
    return {"text": raw_text}, raw_text


# --------------------------------------------------------------------------
# 公开入口
# --------------------------------------------------------------------------


def _require_ready(workflow: str, settings: XfSettings) -> str:
    """配置就绪校验；返回 flow_id。缺失即抛，**不静默返回空结果**。"""
    if not settings.has_credentials:
        raise XfNotConfigured(
            "讯飞星辰未配置（缺少 RESEARCH_XF_BASE_URL/APP_ID/API_KEY/API_SECRET）"
        )
    if not settings.enabled:
        raise XfDisabled("讯飞星辰已禁用（RESEARCH_XF_ENABLED=false）")
    spec = WORKFLOW_BY_KEY.get(workflow)
    if spec is None:
        raise XfError(
            f"未知工作流: {workflow}",
            code="XF_UNKNOWN_WORKFLOW",
            http_status=400,
        )
    flow_id = settings.flow_id(spec.env_key)
    if not flow_id:
        raise XfNotConfigured(f"工作流 {workflow} 未配置 flow_id（{spec.env_key}）")
    return flow_id


async def run_workflow(
    workflow: str,
    parameters: dict[str, Any],
    *,
    uid: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    settings: XfSettings | None = None,
) -> XfResult:
    """非流式工作流调用。

    Args:
        workflow: 工作流 key（见 `models.WORKFLOWS`）。
        parameters: 工作流输入参数（原样透传给上游 `parameters`）。
        uid: 用户维度隔离 ID（避免 20357 并发）；缺省用 `"anonymous"`。
        transport: 可选 httpx 传输层（单测注入 `httpx.MockTransport`）。
        settings: 可选配置快照（缺省实时读 env）。

    Returns:
        `XfResult`。

    Raises:
        XfError: 其子类按上游错误分类（见 `models` 稳定错误码）。
    """
    cfg = settings or load_settings()
    flow_id = _require_ready(workflow, cfg)

    url = _build_url(cfg)
    headers = _build_headers(cfg)
    payload: dict[str, Any] = {
        "flow_id": flow_id,
        "uid": uid or "anonymous",
        "stream": False,
        "parameters": parameters or {},
    }

    sem = _get_semaphore(cfg.max_concurrency)
    start = time.perf_counter()
    timeout = httpx.Timeout(
        connect=5.0, read=cfg.timeout_seconds, write=10.0, pool=5.0
    )

    async with sem:
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
                resp = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise XfTimeout(
                f"上游读取超时（>{cfg.timeout_seconds:g}s）",
                retry_after=DEFAULT_RETRY_AFTER,
            ) from exc
        except httpx.TransportError as exc:
            # 只保留异常类名，避免底层消息里偶然夹带 URL 凭证（脱敏铁律）。
            raise XfTransportError(
                f"上游网络传输失败（{type(exc).__name__}）"
            ) from exc

    latency_ms = int((time.perf_counter() - start) * 1000)

    if resp.status_code != 200:
        raise _map_http_error(resp.status_code, resp.text or "", cfg)

    try:
        data = resp.json()
    except (json.JSONDecodeError, TypeError) as exc:
        raise XfUpstreamError("上游返回非 JSON 响应") from exc
    if not isinstance(data, dict):
        raise XfUpstreamError("上游返回 JSON 顶层不是对象")

    raw_code = data.get("code")
    code = 0 if raw_code is None else int(raw_code)
    if code != 0:
        raise _map_upstream_code(code, _scrub(str(data.get("message") or ""), cfg))

    result, raw_text = _extract_result(data)
    return XfResult(
        workflow=workflow,
        flow_id=flow_id,
        latency_ms=latency_ms,
        result=result,
        raw_text=raw_text,
        upstream_code=0,
    )


__all__ = ["run_workflow", "DEFAULT_RETRY_AFTER"]
