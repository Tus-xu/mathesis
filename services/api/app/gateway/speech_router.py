"""语音链路路由（SSOT §5.5-5.6 / ADR-017）

端点：
- POST /api/agent/speech/asr-token — 讯飞 IAT 临时凭证
- POST /api/agent/speech/to-latex — 口语转 LaTeX
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import urlencode

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.gateway.auth import get_current_user
from app.gateway.redis import get_redis
from app.models.database import get_db
from app.models.m2_logs import SpeechLog
from app.providers.xingchen import XingchenConcurrencyError

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/agent/speech", tags=["speech"])

# 热词静态表（ADR-019 v1）
HOTWORDS = ["根号", "积分", "西格玛", "分之", "平方"]


# ==================== Schemas ====================


class ToLatexRequest(BaseModel):
    asr_text: str = Field(..., max_length=500)
    session_id: str
    conversation_id: str | None = None
    context_kp: str | None = None  # ADR-017


# ==================== 端点 ====================


@router.post("/asr-token")
async def get_asr_token(
    user: dict = Depends(get_current_user),
):
    """讯飞 IAT 临时凭证（SSOT §5.5）"""
    user_id = user["sub"]

    # 限流：每用户 20 次/小时
    redis = await get_redis()
    rate_key = f"ratelimit:asr:{user_id}"
    count = await redis.incr(rate_key)
    if count == 1:
        await redis.expire(rate_key, 3600)
    if count > settings.asr_token_rate_limit_per_hour:
        return {"code": 42901, "message": "ASR 凭证请求频率超限（每小时 20 次）"}

    # 检查讯飞配置
    if not settings.xfyun_api_key or not settings.xfyun_api_secret:
        return {"code": 50301, "message": "语音服务未配置"}

    # 生成签名
    session_id = uuid.uuid4().hex
    ws_url = _generate_iat_url(session_id)

    # session 落 Redis（ADR-017：TTL 300s）
    await redis.set(f"speech_session:{session_id}", user_id, ex=300)

    return {
        "code": 0,
        "data": {
            "ws_url": ws_url,
            "expires_in": 300,
            "session_id": session_id,
            "hotwords": HOTWORDS,
        },
    }


@router.post("/to-latex")
async def speech_to_latex(
    req: ToLatexRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """口语转 LaTeX（SSOT §5.6）"""
    user_id = user["sub"]
    redis = await get_redis()
    start_time = time.perf_counter()

    # session_id 校验（ADR-017）
    session_user = await redis.get(f"speech_session:{req.session_id}")
    if not session_user or session_user != user_id:
        return {"code": 40101, "message": "语音会话无效或已过期"}

    # 主通道：星辰 wf_speech_to_latex（三层解析有效配置，管理后台配置即时生效）
    from app.providers.xingchen import resolve_effective_xingchen_config

    xcfg = await resolve_effective_xingchen_config(db, user_id)
    engine = "wf_speech_to_latex"
    latex = None
    normalized_text = req.asr_text
    ambiguous = False
    status = "success"

    try:
        if xcfg.enabled:
            from app.providers.xingchen import run_workflow

            result = await run_workflow(
                "wf_speech_to_latex",
                uid=user_id,
                parameters={
                    "AGENT_USER_INPUT": req.asr_text,
                    "asr_text": req.asr_text,
                    "context_kp": req.context_kp or "",
                },
                config=xcfg,
            )
            latex = result.get("latex")
            normalized_text = result.get("normalized_text", req.asr_text)
            ambiguous = result.get("ambiguous", False)

            # LaTeX 结构校验
            if latex:
                from app.providers.latex_check import validate_latex
                valid, err = validate_latex(latex)
                if not valid:
                    # 带错误信息重试一次
                    logger.info("latex_retry", error=err)
                    result2 = await run_workflow(
                        "wf_speech_to_latex",
                        uid=user_id,
                        parameters={
                            "AGENT_USER_INPUT": req.asr_text,
                            "asr_text": req.asr_text,
                            "context_kp": req.context_kp or "",
                        },
                        config=xcfg,
                    )
                    latex = result2.get("latex")
                    if latex:
                        valid2, _ = validate_latex(latex)
                        if not valid2:
                            latex = None
                            ambiguous = True
                    else:
                        latex = None
                        ambiguous = True
        else:
            # 星辰未启用，走本地星火直调降级
            engine = "spark_direct"
            status = "fallback"
            latex = await _local_spark_to_latex(req.asr_text, req.context_kp)
            if not latex:
                ambiguous = True

    except XingchenConcurrencyError:
        # 20357 → 42902（ADR-004：同账号并发处理中，不走降级，前端 toast 轻提示重试）
        latency_ms_busy = int((time.perf_counter() - start_time) * 1000)
        logger.info("speech_to_latex_concurrency_42902")
        db.add(
            SpeechLog(
                user_id=uuid.UUID(user_id),
                conversation_id=uuid.UUID(req.conversation_id) if req.conversation_id else None,
                session_id=req.session_id,
                asr_text=req.asr_text,
                normalized_text=req.asr_text,
                latex=None,
                ambiguous=False,
                engine="wf_speech_to_latex",
                latency_ms=latency_ms_busy,
                status="busy",
            )
        )
        await db.commit()
        return {"code": 42902, "message": "AI 服务繁忙（同账号并发处理中），请稍后重试"}

    except Exception as e:
        logger.warning("speech_to_latex_fallback", error=str(e))
        # 降级：本地星火 HTTP 同 prompt 直调
        engine = "spark_direct"
        status = "fallback"
        try:
            latex = await _local_spark_to_latex(req.asr_text, req.context_kp)
            if not latex:
                ambiguous = True
        except Exception:
            status = "error"
            latex = None
            ambiguous = True

    latency_ms = int((time.perf_counter() - start_time) * 1000)

    # 落 speech_logs
    log = SpeechLog(
        user_id=uuid.UUID(user_id),
        conversation_id=uuid.UUID(req.conversation_id) if req.conversation_id else None,
        session_id=req.session_id,
        asr_text=req.asr_text,
        normalized_text=normalized_text,
        latex=latex,
        ambiguous=ambiguous,
        engine=engine,
        latency_ms=latency_ms,
        status=status,
    )
    db.add(log)
    await db.commit()

    # 双通道全挂
    if status == "error":
        return {"code": 50301, "message": "语音转 LaTeX 服务暂不可用"}

    return {
        "code": 0,
        "data": {
            "latex": latex,
            "normalized_text": normalized_text,
            "ambiguous": ambiguous,
            "engine": engine,
            "latency_ms": latency_ms,
        },
    }


# ==================== 内部工具 ====================


def _generate_iat_url(session_id: str) -> str:
    """生成讯飞 IAT WSS 签名 URL"""
    host = "iat-api.xfyun.cn"
    date = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    request_line = "GET /v2/iat HTTP/1.1"

    # signature_origin
    signature_origin = f"host: {host}\ndate: {date}\n{request_line}"

    # HMAC-SHA256
    signature_sha = hmac.new(
        settings.xfyun_api_secret.encode(),
        signature_origin.encode(),
        digestmod=hashlib.sha256,
    ).digest()
    signature = base64.b64encode(signature_sha).decode()

    # authorization_origin
    authorization_origin = (
        f'api_key="{settings.xfyun_api_key}", '
        f'algorithm="hmac-sha256", '
        f'headers="host date request-line", '
        f'signature="{signature}"'
    )
    authorization = base64.b64encode(authorization_origin.encode()).decode()

    # 拼接 URL
    params = {
        "authorization": authorization,
        "date": date,
        "host": host,
    }
    base_url = settings.xfyun_iat_base_url
    return f"{base_url}?{urlencode(params)}"


async def _local_spark_to_latex(asr_text: str, context_kp: str | None) -> str | None:
    """本地星火 HTTP 同 prompt 直调（降级通道）"""
    try:
        from app.providers.router import get_model_router

        router = get_model_router()
        prompt = (
            "把以下中文数学口语转成 LaTeX 公式，只输出 LaTeX，不要解释。\n"
            f"口语：{asr_text}\n"
        )
        if context_kp:
            prompt += f"当前知识点：{context_kp}\n"
        prompt += "LaTeX："

        result = await router.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=200,
            request_id="",
            scene="speech_to_latex",
        )
        latex = result.get("content", "").strip()
        # 去除可能的 $ 包裹
        latex = latex.strip("$").strip()
        return latex if latex else None
    except Exception as e:
        logger.error("spark_to_latex_failed", error=str(e))
        return None
