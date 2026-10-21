"""星辰工作流引擎（ADR-M2B-004：全配置化可移植）

统一调用约定（SSOT §4）：
- POST {base_url}/workflow/v1/chat/completions
- Authorization: Bearer {API_KEY}:{API_SECRET}
- uid 按用户维度隔离（避免 20357）
- 流式帧含 workflow_step/choices.delta/usage
- 错误码映射：20357→42902, 20804→timeout, 20375/20376/20902/20903→不重试降级

可移植设计：
- flow 注册表驱动；flow_ids 中出现但注册表未注册的名字 → 通用处理器
  （非流式，输出 {"raw": 文本, "data": 解析后的 dict}），新增工作流 = 加配置即可用
- XINGCHEN_BASE_URL 可指向私有化部署（含端口）
- XINGCHEN_FLOW_IDS JSON 配置 flow_id
- 运行时 Redis 开关 switch:xingchen:{flow}
- 用户级运行时配置：resolve_xingchen_config(user_id, db) 三层字段级回退
  （env ← system_configs["xingchen.global"]/["workflows"] ← 用户覆盖）；
  run_workflow/stream_workflow/upload_file 均接受可选 config 参数，缺省走 env 全局
- 业务调用点统一入口 resolve_effective_xingchen_config(db, user_id)：
  内部走三层解析，任何异常（含库表缺失）回退 env，永不抛错
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import Any

import httpx
import structlog
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.providers.crypto import decrypt_api_key
from app.providers.http import get_http

logger = structlog.get_logger(__name__)


# ==================== 错误定义 ====================


class XingchenError(Exception):
    """星辰工作流错误"""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"XingchenError({code}): {message}")


class XingchenTimeoutError(XingchenError):
    """流式输出超时（20804）"""
    pass


class XingchenRateLimitError(XingchenError):
    """流控/并发限制（20375/20376/20902/20903）"""
    pass


class XingchenConcurrencyError(XingchenError):
    """同账号并发处理中（20357）→ 本地 42902"""
    pass


# ==================== 输出模型 ====================


class _XingchenOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")


class DocUnderstandOut(_XingchenOutput):
    """wf_doc_understand 输出"""
    question_text: str = ""
    latex_fragments: list[str] = field(default_factory=list)
    has_figure: bool = False
    question_type: str = "unknown"
    confidence: float = 0.0



class SpeechToLatexOut(_XingchenOutput):
    """wf_speech_to_latex 输出"""
    latex: str | None = None
    normalized_text: str = ""
    ambiguous: bool = False



class WebSearchOut(_XingchenOutput):
    """wf_web_search 输出（非流式 JSON）"""
    answer: str | None = None
    sources: list[dict] = field(default_factory=list)
    badge: str = "web_supplement"



class IntentRouterOut(_XingchenOutput):
    """wf_intent_router 输出"""
    intent: str = "chat"
    confidence: float = 0.5
    reason: str = ""



class VerifyDerivationOut(_XingchenOutput):
    """wf_verify_derivation 输出"""
    verdict: str = "unverifiable"
    steps: list[dict] = field(default_factory=list)
    generated_code: str = ""

# ---- 迭代05 新增四个工作流输出模型（基线字段与 SSOT §4.7~4.10 逐字段一致） ----


class SmartQuizOut(_XingchenOutput):
    """wf_smart_quiz 输出（SSOT §4.7 基线六字段；扩展字段 extra ignore）"""
    question_text: str = ""
    options: list[str] | None = None
    answer: str = ""
    explanation: str = ""
    kp_code: str = ""
    difficulty: str = "medium"



class SolutionPregradeOut(_XingchenOutput):
    """wf_solution_pregrade 输出（SSOT §4.8 基线四字段）"""
    score: float | None = None
    error_type: str | None = None
    step_comments: list[dict] = field(default_factory=list)
    summary: str = ""



class ErrorAnalysisOut(_XingchenOutput):
    """wf_error_analysis 输出（SSOT §4.9 基线三字段）"""
    error_type: str = ""
    kp_code: str | None = None
    confidence: float = 0.0



class CoursePreprocessOut(_XingchenOutput):
    """wf_course_preprocess 输出（SSOT §4.10 基线三字段）"""
    chapters: list[dict] = field(default_factory=list)
    kp_codes: list[str] = field(default_factory=list)
    knowledge_cards: list[dict] = field(default_factory=list)

# ==================== Flow 注册表 ====================

# 默认 read_timeout（秒），可被 XINGCHEN_TIMEOUTS 配置覆盖（迭代05：键名统一文档定名 wf_course_preprocess）
_DEFAULT_TIMEOUTS: dict[str, float] = {
    "wf_speech_to_latex": 10.0,
    "wf_intent_router": 10.0,
    # 迭代19：doc_understand 大图（data URI base64）实测处理需 >30s，放宽到 90s（曾 30s 超时降级）
    "wf_doc_understand": 90.0,
    "wf_web_search": 30.0,
    "wf_socratic_chat": 30.0,
    "wf_smart_quiz": 30.0,
    "wf_solution_pregrade": 10.0,
    "wf_error_analysis": 5.0,
    "wf_course_preprocess": 60.0,
    "wf_verify_derivation": 90.0,
}

# flow 注册表：name → {output_model, scene, fallback_handler}
FLOW_REGISTRY: dict[str, dict[str, Any]] = {
    "wf_doc_understand": {
        "output_model": DocUnderstandOut,
        "scene": "wf_doc_understand",
        "stream": False,
    },
    "wf_speech_to_latex": {
        "output_model": SpeechToLatexOut,
        "scene": "wf_speech_to_latex",
        "stream": False,
    },
    "wf_web_search": {
        "output_model": WebSearchOut,
        "scene": "wf_web_search",
        "stream": False,
    },
    "wf_intent_router": {
        "output_model": IntentRouterOut,
        "scene": "wf_intent_router",
        "stream": False,
    },
    "wf_verify_derivation": {
        "output_model": VerifyDerivationOut,
        "scene": "wf_verify_derivation",
        "stream": False,
    },
    "wf_socratic_chat": {
        "output_model": None,  # 流式文本，无结构化输出
        "scene": "wf_socratic_chat",
        "stream": True,
    },
    # 迭代05 正式注册（SSOT §4.7~4.10；调用点：smart_quiz 出题 / _ai_pregrade_solution / 错因回填 / F9 课程预处理）
    "wf_smart_quiz": {
        "output_model": SmartQuizOut,
        "scene": "wf_smart_quiz",
        "stream": False,
    },
    "wf_solution_pregrade": {
        "output_model": SolutionPregradeOut,
        "scene": "wf_solution_pregrade",
        "stream": False,
    },
    "wf_error_analysis": {
        "output_model": ErrorAnalysisOut,
        "scene": "wf_error_analysis",
        "stream": False,
    },
    "wf_course_preprocess": {
        "output_model": CoursePreprocessOut,
        "scene": "wf_course_preprocess",
        "stream": False,
    },
}

# 通用 flow 处理器模板：flow_ids 中配置但注册表未注册的名字
_GENERIC_FLOW_SPEC: dict[str, Any] = {
    "output_model": None,
    "stream": False,
    "generic": True,
}


def resolve_flow_spec(flow: str, flow_ids: dict[str, str] | None = None) -> dict[str, Any] | None:
    """解析 flow 处理器规格。

    - 注册表内 flow → 原样返回注册项（行为不变）
    - flow_ids 中配置的未注册名 → 通用处理器（非流式，原始输出）
    - 否则 → None（不可用）
    """
    if flow in FLOW_REGISTRY:
        return FLOW_REGISTRY[flow]
    ids = flow_ids if flow_ids is not None else settings.xingchen_flow_id_map
    if flow in ids:
        return {**_GENERIC_FLOW_SPEC, "scene": flow}
    return None


# ==================== 用户级配置 ====================

XINGCHEN_FIELDS: tuple[str, ...] = (
    "enabled",
    "base_url",
    "api_key",
    "api_secret",
    "flow_ids",
    "timeouts",
    "max_concurrency",
    "queue_max",
)
XINGCHEN_SENSITIVE_FIELDS: frozenset[str] = frozenset({"api_key", "api_secret"})


@dataclass(frozen=True)
class XingchenConfig:
    """星辰有效配置（用户覆盖与 env 合并后的不可变结果）"""

    enabled: bool = False
    base_url: str = "https://xingchen-api.xf-yun.com"
    api_key: str = ""
    api_secret: str = ""
    flow_ids: dict[str, Any] = field(default_factory=dict)
    workflow_base_urls: dict[str, str] = field(default_factory=dict)
    timeouts: dict[str, float] = field(default_factory=dict)
    max_concurrency: int = 3
    queue_max: int = 10
    source: str = "env"  # env | global | user


def xingchen_config_from_settings() -> XingchenConfig:
    """从 .env 全局配置构造"""
    return XingchenConfig(
        enabled=settings.xingchen_enabled,
        base_url=settings.xingchen_base_url,
        api_key=settings.xingchen_api_key,
        api_secret=settings.xingchen_api_secret,
        flow_ids=settings.xingchen_flow_id_map,
        timeouts=settings.xingchen_timeout_map,
        max_concurrency=settings.xingchen_max_concurrency,
        queue_max=settings.xingchen_queue_max,
        source="env",
    )


def _merge_xingchen_dict(overrides: dict, base: XingchenConfig, source: str) -> XingchenConfig:
    """字段级合并到 base（敏感字段此处解密；None 覆盖/损坏密文回退 base）"""
    values: dict[str, Any] = {}
    for f in XINGCHEN_FIELDS:
        v = overrides.get(f)
        if v is None:
            continue
        if f in XINGCHEN_SENSITIVE_FIELDS:
            v = decrypt_api_key(v)
            if not v:
                continue  # 密文损坏 → 视为未覆盖，保留下层回退
        elif f == "flow_ids":
            v = {str(k): str(vv) for k, vv in v.items()} if isinstance(v, dict) else {}
        elif f == "timeouts":
            try:
                v = {str(k): float(vv) for k, vv in v.items()} if isinstance(v, dict) else {}
            except (ValueError, TypeError):
                v = {}
        values[f] = v
    return replace(base, **values, source=source)


def xingchen_config_from_global(global_cfg: dict | None) -> XingchenConfig:
    """env ← system_configs["xingchen.global"] 二层合并（管理后台全局凭证）"""
    base = xingchen_config_from_settings()
    if not global_cfg:
        return base
    return _merge_xingchen_dict(global_cfg, base, "global")


def merge_xingchen_overrides(
    overrides: dict | None, base: XingchenConfig | None = None
) -> XingchenConfig:
    """用户覆盖 + 字段级回退（纯函数）。

    overrides 中敏感字段为密文，此处解密；None 覆盖（无行/缺字段）回退 base。
    base 缺省为 env 配置；三层链时传入 xingchen_config_from_global 的结果。
    """
    base = base or xingchen_config_from_settings()
    if not overrides:
        return base
    return _merge_xingchen_dict(overrides, base, "user")


def _merge_workflow_overrides(base: XingchenConfig, workflows: dict) -> XingchenConfig:
    """合并 system_configs["workflows"] 的按流连接配置（管理后台维护）。

    优先级：env map < workflows 覆盖 < 用户覆盖（调用方在其后再合用户层）。
    """
    flow_ids = dict(base.flow_ids)
    timeouts = dict(base.timeouts)
    workflow_base_urls = dict(base.workflow_base_urls)
    for name, item in workflows.items():
        if not isinstance(item, dict):
            continue
        fid = item.get("workflow_id") or item.get("flow_id")
        if fid:
            flow_config: dict[str, str] = {"flow_id": str(fid)}
            api_key = decrypt_api_key(str(item.get("api_key") or ""))
            api_secret = decrypt_api_key(str(item.get("api_secret") or ""))
            if api_key:
                flow_config["api_key"] = api_key
            if api_secret:
                flow_config["api_secret"] = api_secret
            flow_ids[str(name)] = flow_config if len(flow_config) > 1 else str(fid)
        base_url = item.get("base_url")
        if isinstance(base_url, str) and base_url.strip():
            workflow_base_urls[str(name)] = base_url.strip()
        timeout = item.get("timeout_seconds", item.get("timeout"))
        if timeout is not None:
            try:
                timeouts[str(name)] = float(timeout)
            except (TypeError, ValueError):
                continue  # 非法 timeout 跳过，保留下层值
    return replace(
        base,
        flow_ids=flow_ids,
        timeouts=timeouts,
        workflow_base_urls=workflow_base_urls,
    )


async def resolve_xingchen_config(user_id: str, db: AsyncSession) -> XingchenConfig:
    """解析用户有效星辰配置（三层字段级回退）

    env ← system_configs["xingchen.global"]（凭证）+ ["workflows"]（flow_id/timeout）
    ← 用户覆盖；任一层缺字段自动回退下一层。
    """
    from app.models.system_config import get_system_config
    from app.models.user_integration_config import get_user_integration_overrides

    global_cfg = await get_system_config(db, "xingchen.global", default={})
    if not isinstance(global_cfg, dict):  # 防御脏数据
        global_cfg = {}
    base = xingchen_config_from_global(global_cfg)

    workflows = await get_system_config(db, "workflows", default={})
    if isinstance(workflows, dict) and workflows:
        base = _merge_workflow_overrides(base, workflows)

    overrides = await get_user_integration_overrides(user_id, "xingchen", db)
    return merge_xingchen_overrides(overrides, base)


async def resolve_effective_xingchen_config(
    db: AsyncSession | None, user_id: str | None = None
) -> XingchenConfig:
    """业务侧统一入口：三层解析（env ← 管理后台 ← 用户覆盖），永不抛错。

    任何异常（含测试库无 system_configs 表）回退 xingchen_config_from_settings()；
    SAVEPOINT 隔离（对齐 cloud_kb.resolve_cloud_kb_config）使查询失败只回滚到
    保存点，不污染调用方共享事务（InFailedSQLTransactionError 教训）。
    db 为 None（无会话上下文）时直接走 env；user_id 为 None 时跳过用户覆盖层。
    """
    if db is None:
        return xingchen_config_from_settings()
    try:
        async with db.begin_nested():
            if user_id:
                return await resolve_xingchen_config(user_id, db)
            from app.models.system_config import get_system_config

            global_cfg = await get_system_config(db, "xingchen.global", default={})
            if not isinstance(global_cfg, dict):  # 防御脏数据
                global_cfg = {}
            base = xingchen_config_from_global(global_cfg)

            workflows = await get_system_config(db, "workflows", default={})
            if isinstance(workflows, dict) and workflows:
                base = _merge_workflow_overrides(base, workflows)
            return base
    except Exception as e:
        logger.warning("xingchen_resolve_effective_fallback_env", error=str(e)[:200])
        return xingchen_config_from_settings()


# ==================== 核心引擎 ====================

# 并发信号量（按 max_concurrency 值缓存，延迟初始化）
_semaphores: dict[int, asyncio.Semaphore] = {}


def _get_semaphore(limit: int | None = None) -> asyncio.Semaphore:
    n = limit or settings.xingchen_max_concurrency
    sem = _semaphores.get(n)
    if sem is None:
        sem = asyncio.Semaphore(n)
        _semaphores[n] = sem
    return sem


def _resolve_timeout(flow: str, cfg: XingchenConfig) -> float:
    """解析 read_timeout：配置 > 注册表默认 > 30s（通用 flow 兜底）

    迭代18 防御：配置值 None/非法类型会被 httpx.Timeout 拒绝（
    "must either include a default..."），此处统一转 float 并回退。
    """
    v = cfg.timeouts.get(flow)
    if v:
        try:
            return float(v)
        except (TypeError, ValueError):
            pass
    return _DEFAULT_TIMEOUTS.get(flow, 30.0)


def _flow_id_of(cfg: XingchenConfig, flow: str) -> str | None:
    """flow_ids 取值（支持 str / {"flow_id":...} 对象两种形态）"""
    v = cfg.flow_ids.get(flow)
    if isinstance(v, dict):
        return str(v.get("flow_id") or "") or None
    return v or None


def _flow_credentials(cfg: XingchenConfig, flow: str) -> tuple[str, str]:
    """按流凭证覆盖（多应用绑定场景）；无覆盖回落全局 key/secret"""
    v = cfg.flow_ids.get(flow)
    if isinstance(v, dict) and v.get("api_key") and v.get("api_secret"):
        return str(v["api_key"]), str(v["api_secret"])
    return cfg.api_key, cfg.api_secret


def _build_headers(cfg: XingchenConfig, flow: str | None = None) -> dict[str, str]:
    """构造鉴权头（flow 指定时应用按流凭证覆盖）"""
    key, secret = _flow_credentials(cfg, flow) if flow else (cfg.api_key, cfg.api_secret)
    return {
        "Authorization": f"Bearer {key}:{secret}",
        "Content-Type": "application/json",
    }


def _build_url(cfg: XingchenConfig, flow: str | None = None) -> str:
    """构造工作流 API URL；按流地址优先、全局地址兜底。"""
    base = (cfg.workflow_base_urls.get(flow, "") if flow else "") or cfg.base_url
    base = base.rstrip("/")
    return f"{base}/workflow/v1/chat/completions"


async def _check_runtime_switch(flow: str) -> str:
    """检查 Redis 运行时开关。返回 on/off/force_fallback"""
    try:
        from app.gateway.redis import get_redis
        r = await get_redis()
        val = await r.get(f"switch:xingchen:{flow}")
        return val or "on"
    except Exception:
        return "on"


def _map_error(code: int, message: str) -> XingchenError:
    """星辰错误码 → 本地异常类型映射"""
    if code == 20804:
        return XingchenTimeoutError(code, message)
    if code in (20375, 20376, 20902, 20903):
        return XingchenRateLimitError(code, message)
    if code == 20357:
        return XingchenConcurrencyError(code, message)
    return XingchenError(code, message)


async def _audit_log(
    flow: str,
    user_id: str,
    status: str,
    latency_ms: int,
    tokens_in: int = 0,
    tokens_out: int = 0,
    error: str | None = None,
    prompt_hash: str = "",
) -> None:
    """落 ai_calls 审计（fire-and-forget）"""
    try:
        from app.providers.audit import log_ai_call
        # log_ai_call 为同步 fire-and-forget 接口，参数名须与其签名一致
        log_ai_call(
            request_id=f"xingchen:{flow}:{user_id}",
            scene=f"wf_{flow}" if not flow.startswith("wf_") else flow,
            provider="xingchen",
            model=flow,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            latency_ms=latency_ms,
            status=status,
            error=error,
        )
    except Exception as e:
        logger.warning("xingchen_audit_failed", error=str(e))


async def run_workflow(
    flow: str,
    *,
    uid: str,
    parameters: dict,
    read_timeout: float | None = None,
    config: XingchenConfig | None = None,
) -> dict:
    """非流式工作流调用（统一入口）

    Args:
        flow: 工作流注册名（如 wf_speech_to_latex）；flow_ids 中配置的未注册名
              走通用处理器，输出 {"raw": 文本, "data": dict}
        uid: 用户维度隔离 ID（str(user_id)）
        parameters: 工作流输入参数（照抄 SSOT §4 各 schema）
        read_timeout: 覆盖默认超时
        config: 用户级有效配置（resolve_xingchen_config 结果），缺省走 env 全局

    Returns:
        解析后的输出 dict（注册表 flow 经 Pydantic 校验；通用 flow 原始输出）

    Raises:
        XingchenError: 星辰侧错误
        RuntimeError: 配置缺失/总开关关闭
    """
    cfg = config or xingchen_config_from_settings()

    if not cfg.enabled:
        raise RuntimeError(f"星辰工作流总开关关闭（XINGCHEN_ENABLED=false），flow={flow}")

    # 运行时开关检查
    switch = await _check_runtime_switch(flow)
    if switch in ("off", "force_fallback"):
        raise XingchenError(0, f"运行时开关关闭: {flow}")

    flow_id = _flow_id_of(cfg, flow)
    if not flow_id:
        raise RuntimeError(f"工作流 {flow} 未配置 flow_id（XINGCHEN_FLOW_IDS）")

    timeout = read_timeout or _resolve_timeout(flow, cfg)
    url = _build_url(cfg, flow)
    headers = _build_headers(cfg, flow)
    prompt_hash = hashlib.md5(json.dumps(parameters, ensure_ascii=False).encode()).hexdigest()[:8]

    payload = {
        "flow_id": flow_id,
        "uid": uid,
        "stream": False,
        "parameters": parameters,
    }

    start = time.perf_counter()
    sem = _get_semaphore(cfg.max_concurrency)

    async with sem:
        try:
            resp = await get_http().post(
                url,
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(connect=5.0, read=timeout, write=10.0, pool=5.0),
            )

            latency_ms = int((time.perf_counter() - start) * 1000)

            if resp.status_code != 200:
                await _audit_log(flow, uid, "error", latency_ms, error=f"HTTP {resp.status_code}")
                raise XingchenError(resp.status_code, f"HTTP {resp.status_code}: {resp.text[:200]}")

            data = resp.json()

            # 星辰业务错误码
            code = data.get("code", 0)
            if code != 0:
                msg = data.get("message", "unknown")
                await _audit_log(flow, uid, "error", latency_ms, error=f"{code}:{msg}")
                raise _map_error(code, msg)

            # 提取输出（星辰非流式响应的内容在 choices[0].delta.content，迭代18 实测）
            content = ""
            choices = data.get("choices", [])
            if choices:
                delta = choices[0].get("delta", {})
                content = delta.get("content", "") or choices[0].get("message", {}).get("content", "")

            # usage
            usage = data.get("usage", {})
            tokens_in = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)

            # JSON 解析 + Pydantic 校验（通用 flow 原始输出）
            result = _parse_output(flow, content)

            await _audit_log(flow, uid, "success", latency_ms, tokens_in, tokens_out, prompt_hash=prompt_hash)
            return result

        except httpx.TimeoutException as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            await _audit_log(flow, uid, "timeout", latency_ms, error="read_timeout")
            raise XingchenTimeoutError(20804, f"read_timeout after {timeout}s") from exc

        except (XingchenError, RuntimeError):
            raise

        except Exception as e:
            latency_ms = int((time.perf_counter() - start) * 1000)
            await _audit_log(flow, uid, "error", latency_ms, error=str(e)[:200])
            raise XingchenError(-1, str(e)) from e


async def stream_workflow(
    flow: str,
    *,
    uid: str,
    parameters: dict,
    chat_id: str | None = None,
    history: list[dict] | None = None,
    config: XingchenConfig | None = None,
) -> AsyncIterator[dict]:
    """流式工作流调用（SSE 逐帧 yield）

    仅注册表内标记 stream=True 的 flow 可用；通用 flow 请用 run_workflow。

    Yields:
        {"type": "delta", "content": str} | {"type": "usage", ...} | {"type": "step", ...}
    """
    cfg = config or xingchen_config_from_settings()

    if not cfg.enabled:
        raise RuntimeError(f"星辰工作流总开关关闭，flow={flow}")

    flow_id = _flow_id_of(cfg, flow)
    if not flow_id:
        raise RuntimeError(f"工作流 {flow} 未配置 flow_id")

    if flow not in FLOW_REGISTRY:
        raise RuntimeError(f"通用工作流 {flow} 仅支持非流式调用（run_workflow）")

    timeout = _resolve_timeout(flow, cfg)
    url = _build_url(cfg, flow)
    headers = _build_headers(cfg, flow)

    payload: dict[str, Any] = {
        "flow_id": flow_id,
        "uid": uid,
        "stream": True,
        "parameters": parameters,
    }
    if chat_id:
        payload["chat_id"] = chat_id
    if history:
        payload["history"] = history

    start = time.perf_counter()
    sem = _get_semaphore(cfg.max_concurrency)

    async with sem:
        try:
            async with get_http().stream(
                "POST",
                url,
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(connect=5.0, read=timeout, write=10.0, pool=5.0),
            ) as resp:
                if resp.status_code != 200:
                    raise XingchenError(resp.status_code, f"HTTP {resp.status_code}")

                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    json_str = line[5:].strip()
                    if not json_str or json_str == "[DONE]":
                        continue
                    try:
                        frame = json.loads(json_str)
                    except json.JSONDecodeError:
                        continue

                    # 错误帧
                    code = frame.get("code", 0)
                    if code != 0:
                        raise _map_error(code, frame.get("message", ""))

                    # workflow_step（节点进度）——同一帧可能同时携带 choices delta，
                    # 不能 continue 跳过内容（迭代18 实测：帧结构为 step+delta 并存）
                    if "workflow_step" in frame:
                        yield {"type": "step", "step": frame["workflow_step"]}

                    # choices delta
                    choices = frame.get("choices", [])
                    if choices:
                        delta = choices[0].get("delta", {})
                        content = delta.get("content", "")
                        finish = choices[0].get("finish_reason")
                        if content:
                            yield {"type": "delta", "content": content}
                        if finish == "stop":
                            break

                    # usage
                    usage = frame.get("usage")
                    if usage:
                        yield {"type": "usage", **usage}

            latency_ms = int((time.perf_counter() - start) * 1000)
            await _audit_log(flow, uid, "success", latency_ms)

        except httpx.TimeoutException as exc:
            latency_ms = int((time.perf_counter() - start) * 1000)
            await _audit_log(flow, uid, "timeout", latency_ms)
            raise XingchenTimeoutError(20804, f"stream timeout after {timeout}s") from exc


def _parse_output(flow: str, content: str) -> dict:
    """解析工作流输出（JSON + Pydantic 校验）

    非法输出处理（硬纪律）：JSON 解析失败/schema 校验不过 → 抛 XingchenError
    枚举越界时仅 wf_intent_router 允许钳到默认值 chat。
    通用 flow（注册表外）：尽量 JSON 解析为 dict，失败返回原始文本，不抛错。
    """
    registry = FLOW_REGISTRY.get(flow)
    if not registry:
        # 通用 flow：原始 dict/文本输出
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return {"raw": content, "data": parsed}
        except (json.JSONDecodeError, TypeError):
            pass
        return {"raw": content}

    output_model = registry.get("output_model")
    if output_model is None:
        # 流式文本类（wf_socratic_chat），返回原始
        return {"raw": content}

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        # 迭代19 容错：星辰部分流输出 Python 风格布尔/空值（True/False/None）与 BOM，
        # 实测 wf_doc_understand 返回 {"has_figure": False} 直接 json.loads 失败——
        # 先做保守归一化再解析（仅替换裸词，不触碰字符串内部由正则词边界保证）。
        import re as _re

        sanitized = content
        sanitized = sanitized.lstrip("\ufeff")
        sanitized = _re.sub(r"\bTrue\b", "true", sanitized)
        sanitized = _re.sub(r"\bFalse\b", "false", sanitized)
        sanitized = _re.sub(r"\bNone\b", "null", sanitized)
        try:
            parsed = json.loads(sanitized)
            logger.info("wf_output_python_json_fixed", flow=flow)
        except (json.JSONDecodeError, TypeError) as e:
            logger.error("wf_output_invalid", flow=flow, error=str(e), content=content[:200])
            raise XingchenError(-2, f"工作流 {flow} 输出非法 JSON: {str(e)[:100]}") from e

    try:
        validated = output_model(**parsed)
        return validated.model_dump()
    except Exception as e:
        # wf_intent_router 特殊处理：非法输出兜底 chat
        if flow == "wf_intent_router":
            logger.warning("wf_intent_router_fallback", error=str(e))
            return {"intent": "chat", "confidence": 0.3, "reason": "非法输出兜底"}
        logger.error("wf_output_invalid", flow=flow, error=str(e))
        raise XingchenError(-2, f"工作流 {flow} 输出 schema 校验失败: {str(e)[:100]}") from e


# ==================== 文件上传（星辰文件服务） ====================


async def upload_file(
    file_data: bytes,
    filename: str,
    config: XingchenConfig | None = None,
) -> str:
    """上传文件到星辰文件服务，返回 URL

    用于 wf_doc_understand 的 image_url 参数。
    """
    cfg = config or xingchen_config_from_settings()

    if not cfg.enabled:
        raise RuntimeError("星辰工作流总开关关闭")

    base = cfg.base_url.rstrip("/")
    url = f"{base}/workflow/v1/files"
    headers = {
        "Authorization": f"Bearer {cfg.api_key}:{cfg.api_secret}",
    }

    resp = await get_http().post(
        url,
        headers=headers,
        files={"file": (filename, file_data)},
        timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
    )

    if resp.status_code != 200:
        raise XingchenError(resp.status_code, f"文件上传失败: {resp.text[:200]}")

    data = resp.json()
    file_url = data.get("data", {}).get("url", "")
    if not file_url:
        raise XingchenError(-1, "星辰文件上传未返回 URL")
    return file_url
