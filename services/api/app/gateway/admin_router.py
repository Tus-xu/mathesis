"""管理后台配置路由（/api/admin）

全部端点 require_role("admin")（admin 角色由 ADMIN_PHONES 白名单登录引导产生）。
配置载体为 system_configs KV 表，敏感字段 Fernet 加密落库、GET 脱敏回显：
- model.global    全局默认模型通道（三层回退：用户配置 > model.global > env）
- xingchen.global 星辰全局凭证（env ← xingchen.global ← 用户覆盖）
- cloud_kb        云知识库（env ← cloud_kb）
- workflows       工作流 flow_id/timeout 覆盖（env map < workflows < 用户覆盖）

PUT 语义：字段缺省 = 保持原值；空串/null = 清除该字段回退下一层。
test 端点做真实连通性探测（短超时），失败如实返回，不造假。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Body, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.gateway.auth import require_role
from app.gateway.redis import get_redis
from app.gateway.schemas import ApiResponse
from app.models.ai_call import AICall
from app.models.class_ import Class
from app.models.coursework import Submission
from app.models.database import get_db
from app.models.system_config import get_system_config, upsert_system_config
from app.models.user import User
from app.providers.crypto import decrypt_api_key, encrypt_api_key, mask_api_key
from app.providers.embedding import EmbeddingProvider
from app.providers.http import get_http
from app.providers.reranker import RerankProvider
from app.providers.router import clear_global_model_config_cache, get_model_router_global
from app.providers.spark import SPARK_API_URL
from app.providers.xingchen import (
    _DEFAULT_TIMEOUTS,
    FLOW_REGISTRY,
    XingchenConfig,
    _resolve_timeout,
    resolve_effective_xingchen_config,
    resolve_xingchen_config,
    run_workflow,
)

logger = structlog.get_logger()
router = APIRouter()

# 字段缺省哨兵（区分"未传"与显式 null）
_MISSING: Any = object()

_MODEL_CHANNELS = ("primary", "secondary")
_MODEL_FIELDS = ("base_url", "model", "api_key", "thinking")
_XINGCHEN_GLOBAL_FIELDS = ("enabled", "base_url", "api_key", "api_secret")

# M2 feature profile：科研工作流在 M2 管理面不可见（阶段 1 契约护栏；
# M4 科研端置 M2_ENABLE_RESEARCH=true 后自动恢复，代码不物理删除）。
_M2_EXCLUDED_WORKFLOWS = frozenset({"wf_verify_derivation"})

_TEACHER_WORKFLOW_CAPABILITIES: dict[str, str] = {
    "wf_lesson_plan": "adapt_lesson",
    "wf_ai_ppt": "create_slides",
    "wf_explainer_script": "explain_problem",
    "wf_smart_quiz": "create_quiz",
    "wf_solution_pregrade": "suggest_grade",
    "wf_course_preprocess": "preprocess_course",
    "wf_doc_understand": "understand_document",
}


def _visible_flow_names() -> list[str]:
    """当前 profile 下管理面可见的工作流名（科研模式全量；M2 排除 F14）"""
    names = [
        n
        for n in FLOW_REGISTRY
        if settings.m2_enable_research or n not in _M2_EXCLUDED_WORKFLOWS
    ]
    if settings.m3_enable_teacher:
        names.extend(n for n in _TEACHER_WORKFLOW_CAPABILITIES if n not in names)
    return names
_XINGCHEN_SENSITIVE = frozenset({"api_key", "api_secret"})
_CLOUD_KB_PROVIDERS = ("tencent_lkeap", "aliyun_bailian")
_CLOUD_KB_FIELDS = (
    "enabled",
    "provider",
    "credentials",
    "knowledge_base_id",
    "top_k",
    "score_threshold",
)

# 工作流用途说明（按 FLOW_REGISTRY 注释与各调用点推断）
_FLOW_PURPOSES: dict[str, str] = {
    "wf_doc_understand": "题目图片理解（拍照上传 → 题干/LaTeX 提取）",
    "wf_speech_to_latex": "语音口述转 LaTeX（ASR 文本 → 公式）",
    "wf_web_search": "联网搜索补充（答案 + 来源标注）",
    "wf_intent_router": "消息意图路由（chat/quiz/solve 分流）",
    "wf_verify_derivation": "推导验证（科研端逐步校验）",
    "wf_socratic_chat": "苏格拉底式引导对话（流式）",
    "wf_smart_quiz": "智能出题（知识点 + 难度 → 题目）",
    "wf_solution_pregrade": "解答预批改（评分 + 错因预判）",
    "wf_error_analysis": "错因分析（错因类型回填）",
    "wf_course_preprocess": "课程转写预处理（章节/知识点/知识卡片）",
    "wf_lesson_plan": "教案生成与改编",
    "wf_ai_ppt": "课堂演示文稿生成",
    "wf_explainer_script": "题目讲解脚本生成",
}

# 各工作流最小探测入参（按 FLOW_REGISTRY 注释与调用点推断，保证非流式真实调用可跑通）
_SAMPLE_PARAMS: dict[str, dict] = {
    "wf_doc_understand": {
        "image_url": "https://example.com/sample.png",
        "task": "extract_question",
    },
    "wf_speech_to_latex": {"asr_text": "x 的平方加二 x 加一等于零", "context_kp": ""},
    "wf_web_search": {"query": "函数单调性的定义", "max_results": 3},
    "wf_intent_router": {
        "utterance": "帮我解这道题",
        "workspace": "student",
        "history_brief": "",
    },
    "wf_verify_derivation": {
        "derivation_text": "若 a=b 则 a+1=b+1",
        "domain_hint": "general",
        "expected_result": "a+1=b+1",
    },
    "wf_socratic_chat": {"question": "如何理解函数单调性？", "workspace": "student"},
    "wf_smart_quiz": {
        "kp_name": "函数单调性",
        "kp_code": "",
        "difficulty": "easy",
        "q_type": "choice",
    },
    "wf_solution_pregrade": {
        "question_text": "求函数 f(x)=x^2 的导数",
        "student_answer": "f'(x)=2x",
        "full_score": 10,
    },
    "wf_error_analysis": {"question_text": "解方程 x^2-1=0", "answer_text": "x=1"},
    "wf_course_preprocess": {
        "course_id": "admin-probe",
        "transcript": "同学们好，今天我们学习函数的单调性。",
    },
    "wf_lesson_plan": {"topic": "函数单调性", "grade": "高一", "duration_minutes": 45},
    "wf_ai_ppt": {"topic": "函数单调性", "grade": "高一", "slide_count": 8},
    "wf_explainer_script": {
        "question_text": "求函数 f(x)=x^2 的导数",
        "grade": "高一",
    },
}


def _mask(value: str) -> str:
    """脱敏：空值返回空串，对齐 integration_router 回显风格"""
    return mask_api_key(value) if value else ""


def _is_num(value: Any) -> bool:
    """数字校验（bool 是 int 子类，需排除）"""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ========== GET /overview — 管理驾驶舱 ==========


async def _probe_xingchen(cfg: XingchenConfig) -> dict:
    """星辰通道探测：开关/凭证完整性 + base_url 短超时可达性"""
    if not cfg.enabled:
        return {"ok": False, "latency_ms": 0, "error": "总开关关闭（XINGCHEN_ENABLED=false）"}
    if not (cfg.api_key and cfg.api_secret):
        return {"ok": False, "latency_ms": 0, "error": "未配置凭证（api_key/api_secret）"}
    start = time.perf_counter()
    try:
        resp = await get_http().get(cfg.base_url, timeout=3.0)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {"ok": True, "latency_ms": latency_ms, "detail": f"HTTP {resp.status_code}"}
    except Exception as e:
        latency_ms = int((time.perf_counter() - start) * 1000)
        return {"ok": False, "latency_ms": latency_ms, "error": str(e)[:200]}


@router.get("/overview")
async def admin_overview(
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """管理驾驶舱：组件存活 / 通道健康 / 今日调用 / 核心计数"""
    user_id = current_user["sub"]

    # 全局有效通道（model.global 已并入）；星辰解析链含 xingchen.global 与 workflows 覆盖
    model_router = await get_model_router_global(db)
    xingchen_cfg = await resolve_xingchen_config(user_id, db)
    cloud_kb_view = await _cloud_kb_view(db)

    # 通道健康并行探测（均为纯网络探测，不共享 db 会话，无并发安全问题）
    spark_res, deepseek_res, embedding_res, reranker_res, xingchen_res = await asyncio.gather(
        model_router._spark.health_check(),
        model_router._deepseek.health_check(),
        EmbeddingProvider().health_check(),
        RerankProvider().health_check(),
        _probe_xingchen(xingchen_cfg),
    )

    # db / redis 存活
    db_ok = True
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    try:
        redis_ok = bool(await get_redis().ping())
    except Exception:
        redis_ok = False

    # 今日 ai_calls 按 provider 聚合（口径对齐 ops_ext_router：服务器本地零点）
    today_start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    ai_rows = (
        await db.execute(
            select(AICall.provider, func.count(AICall.id))
            .where(AICall.created_at >= today_start)
            .group_by(AICall.provider)
        )
    ).all()
    by_provider = dict(ai_rows)

    users = (
        await db.execute(select(func.count(User.id)).where(User.deleted_at.is_(None)))
    ).scalar() or 0
    classes = (
        await db.execute(select(func.count(Class.id)).where(Class.deleted_at.is_(None)))
    ).scalar() or 0
    submissions_today = (
        await db.execute(
            select(func.count(Submission.id)).where(Submission.created_at >= today_start)
        )
    ).scalar() or 0

    return ApiResponse(
        code=0,
        message="ok",
        data={
            "db": {"ok": db_ok},
            "redis": {"ok": redis_ok},
            "channels": {
                "spark": spark_res,
                "deepseek": deepseek_res,
                "embedding": embedding_res,
                "reranker": reranker_res,
                "xingchen": xingchen_res,
                # 真实连通性由 POST /system/cloud-kb/test 探测，此处只报配置状态
                "cloud_kb": {
                    "ok": bool(cloud_kb_view["enabled"] and cloud_kb_view["provider"]),
                    "detail": cloud_kb_view["provider"] or "未配置",
                },
            },
            "ai_calls_today": {
                "total": sum(by_provider.values()),
                "by_provider": by_provider,
            },
            "counts": {
                "users": users,
                "classes": classes,
                "submissions_today": submissions_today,
            },
        },
    )


# ========== GET/PUT /system/model + POST /system/model/test ==========


def _model_env_channel(channel: str) -> dict:
    """单通道 env 有效值（primary=星火，secondary=DeepSeek）"""
    if channel == "primary":
        return {
            "base_url": SPARK_API_URL,
            "model": settings.spark_model,
            "api_key": settings.spark_api_password,
            "thinking": settings.spark_thinking,
        }
    return {
        "base_url": settings.deepseek_base_url,
        "model": settings.deepseek_model,
        "api_key": settings.deepseek_api_key,
        "thinking": settings.deepseek_thinking,
    }


def _model_channel_view(stored: dict, env: dict) -> dict:
    """单通道脱敏视图：stored 覆盖优先，缺省回显 env 有效值"""
    api_key_plain = env["api_key"]
    if stored.get("api_key"):
        api_key_plain = decrypt_api_key(stored["api_key"])
    thinking = stored["thinking"] if isinstance(stored.get("thinking"), bool) else env["thinking"]
    return {
        "base_url": stored.get("base_url") or env["base_url"],
        "model": stored.get("model") or env["model"],
        "api_key": _mask(api_key_plain),
        "thinking": thinking,
        "source": "global" if stored else "env",
    }


@router.get("/system/model")
async def get_system_model(
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """读取全局默认模型通道（model.global，脱敏回显）"""
    stored = await get_system_config(db, "model.global", default={})
    if not isinstance(stored, dict):
        stored = {}
    primary = stored.get("primary") if isinstance(stored.get("primary"), dict) else {}
    secondary = stored.get("secondary") if isinstance(stored.get("secondary"), dict) else {}
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "configured": bool(stored),
            "primary": _model_channel_view(primary, _model_env_channel("primary")),
            "secondary": _model_channel_view(secondary, _model_env_channel("secondary")),
        },
    )


@router.put("/system/model")
async def put_system_model(
    body: dict[str, Any] = Body(default_factory=dict),
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """upsert model.global（部分更新：缺省=保持，空串/null=清除回退 env）"""
    unknown = sorted(set(body) - set(_MODEL_CHANNELS))
    if unknown:
        return ApiResponse(
            code=40001, message=f"未知字段: {unknown}（支持 {list(_MODEL_CHANNELS)}）"
        )

    stored = await get_system_config(db, "model.global", default={})
    if not isinstance(stored, dict):
        stored = {}
    new_value = dict(stored)

    for channel in _MODEL_CHANNELS:
        patch = body.get(channel)
        if patch is None:
            continue
        if not isinstance(patch, dict):
            return ApiResponse(code=40001, message=f"{channel} 必须为对象")
        bad = sorted(set(patch) - set(_MODEL_FIELDS))
        if bad:
            return ApiResponse(
                code=40001, message=f"未知字段: {bad}（{channel} 支持 {list(_MODEL_FIELDS)}）"
            )

        existing = new_value.get(channel)
        chan = dict(existing) if isinstance(existing, dict) else {}
        for field_name, value in patch.items():
            if field_name == "thinking":
                if value is None or value == "":
                    chan.pop("thinking", None)  # 清除 → 回退 env
                elif not isinstance(value, bool):
                    return ApiResponse(code=40001, message="thinking 必须为布尔值")
                else:
                    chan["thinking"] = value
                continue
            if value is None or (isinstance(value, str) and value == ""):
                chan.pop(field_name, None)  # 清除 → 回退 env
                continue
            if not isinstance(value, str):
                return ApiResponse(code=40001, message=f"{field_name} 必须为字符串")
            chan[field_name] = encrypt_api_key(value) if field_name == "api_key" else value
        if chan:
            new_value[channel] = chan
        else:
            new_value.pop(channel, None)

    await upsert_system_config(
        db,
        "model.global",
        new_value,
        description="全局默认模型通道（primary=星火/secondary=DeepSeek）",
    )
    clear_global_model_config_cache()  # 立即失效缓存，下一次解析即吃到新值
    await db.commit()

    logger.info("admin.model_global_saved", user_id=current_user["sub"])
    return ApiResponse(code=0, message="ok", data={"saved": True, "key": "model.global"})


@router.post("/system/model/test")
async def test_system_model(
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """用全局有效配置（env ← model.global）真实探测双通道"""
    model_router = await get_model_router_global(db)
    spark_res, deepseek_res = await asyncio.gather(
        model_router._spark.health_check(),
        model_router._deepseek.health_check(),
    )
    ok = spark_res["ok"] or deepseek_res["ok"]
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "ok": ok,
            "latency_ms": max(spark_res.get("latency_ms", 0), deepseek_res.get("latency_ms", 0)),
            "detail": "至少一条通道可用" if ok else "双通道均不可用",
            "error": "" if ok else "双通道均不可用",
            "primary": spark_res,
            "secondary": deepseek_res,
        },
    )


# ========== GET/PUT /system/xingchen ==========


@router.get("/system/xingchen")
async def get_system_xingchen(
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """读取星辰全局凭证（xingchen.global，脱敏回显；总开关仅 env 可控）"""
    stored = await get_system_config(db, "xingchen.global", default={})
    if not isinstance(stored, dict):
        stored = {}
    api_key = (
        decrypt_api_key(stored["api_key"]) if stored.get("api_key") else settings.xingchen_api_key
    )
    api_secret = (
        decrypt_api_key(stored["api_secret"])
        if stored.get("api_secret")
        else settings.xingchen_api_secret
    )
    return ApiResponse(
        code=0,
        message="ok",
        data={
            "configured": bool(stored),
            "enabled": (
                stored["enabled"]
                if isinstance(stored.get("enabled"), bool)
                else settings.xingchen_enabled
            ),  # 总开关：env ← xingchen.global.enabled
            "base_url": stored.get("base_url") or settings.xingchen_base_url,
            "api_key": _mask(api_key),
            "api_secret": _mask(api_secret),
        },
    )


@router.put("/system/xingchen")
async def put_system_xingchen(
    body: dict[str, Any] = Body(default_factory=dict),
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """upsert xingchen.global（部分更新；enabled 布尔校验；敏感字段加密落库）"""
    unknown = sorted(set(body) - set(_XINGCHEN_GLOBAL_FIELDS))
    if unknown:
        return ApiResponse(
            code=40001, message=f"未知字段: {unknown}（支持 {list(_XINGCHEN_GLOBAL_FIELDS)}）"
        )

    stored = await get_system_config(db, "xingchen.global", default={})
    if not isinstance(stored, dict):
        stored = {}
    new_value = dict(stored)
    enabled = body.get("enabled", _MISSING)
    if enabled is not _MISSING:
        if enabled is None or enabled == "":
            new_value.pop("enabled", None)  # 清除 → 回退 env 总开关
        elif not isinstance(enabled, bool):
            return ApiResponse(code=40001, message="enabled 必须为布尔值")
        else:
            new_value["enabled"] = enabled
    for field_name, value in body.items():
        if field_name == "enabled":
            continue
        if value is None or (isinstance(value, str) and value == ""):
            new_value.pop(field_name, None)  # 清除 → 回退 env
            continue
        if not isinstance(value, str):
            return ApiResponse(code=40001, message=f"{field_name} 必须为字符串")
        new_value[field_name] = (
            encrypt_api_key(value) if field_name in _XINGCHEN_SENSITIVE else value
        )

    await upsert_system_config(db, "xingchen.global", new_value, description="星辰全局凭证")
    await db.commit()

    logger.info("admin.xingchen_global_saved", user_id=current_user["sub"])
    return ApiResponse(code=0, message="ok", data={"saved": True, "key": "xingchen.global"})


# ========== GET/PUT /system/cloud-kb + POST /system/cloud-kb/test ==========


def _cloud_kb_env_base() -> dict:
    """env 兜底层：CLOUD_KB_CONFIG JSON + 显式开关/阈值"""
    base = dict(settings.cloud_kb_config_map)
    creds = base.get("credentials")
    base["credentials"] = dict(creds) if isinstance(creds, dict) else {}
    base["enabled"] = settings.cloud_kb_enabled
    base["provider"] = settings.cloud_kb_provider
    base["knowledge_base_id"] = str(base.get("knowledge_base_id") or "")
    base["top_k"] = settings.cloud_kb_top_k
    base["score_threshold"] = settings.cloud_kb_score_threshold
    return base


async def _cloud_kb_view(db: AsyncSession) -> dict:
    """cloud_kb 脱敏视图：env ← system_configs["cloud_kb"]（credentials 值解密后脱敏）"""
    stored = await get_system_config(db, "cloud_kb", default={})
    if not isinstance(stored, dict):
        stored = {}
    env = _cloud_kb_env_base()

    creds: dict[str, str] = {}
    for k, v in env["credentials"].items():
        creds[str(k)] = _mask(str(v))
    stored_creds = stored.get("credentials") if isinstance(stored.get("credentials"), dict) else {}
    for k, v in stored_creds.items():
        plain = decrypt_api_key(str(v))
        creds[str(k)] = _mask(plain)

    return {
        "configured": bool(stored),
        "enabled": stored["enabled"] if isinstance(stored.get("enabled"), bool) else env["enabled"],
        "provider": stored.get("provider") or env["provider"],
        "credentials": creds,
        "knowledge_base_id": stored.get("knowledge_base_id") or env["knowledge_base_id"],
        "top_k": stored["top_k"] if _is_num(stored.get("top_k")) else env["top_k"],
        "score_threshold": (
            stored["score_threshold"]
            if _is_num(stored.get("score_threshold"))
            else env["score_threshold"]
        ),
    }


@router.get("/system/cloud-kb")
async def get_system_cloud_kb(
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """读取云知识库配置（cloud_kb，credentials 脱敏回显）"""
    return ApiResponse(code=0, message="ok", data=await _cloud_kb_view(db))


@router.put("/system/cloud-kb")
async def put_system_cloud_kb(
    body: dict[str, Any] = Body(default_factory=dict),
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """upsert cloud_kb（部分更新；credentials 值加密落库，空串清除单键）"""
    unknown = sorted(set(body) - set(_CLOUD_KB_FIELDS))
    if unknown:
        return ApiResponse(
            code=40001, message=f"未知字段: {unknown}（支持 {list(_CLOUD_KB_FIELDS)}）"
        )

    # 先校验后落库
    enabled = body.get("enabled", _MISSING)
    if enabled is not _MISSING and not isinstance(enabled, bool):
        return ApiResponse(code=40001, message="enabled 必须为布尔值")
    provider = body.get("provider", _MISSING)
    if provider is not _MISSING and provider not in (None, "") + _CLOUD_KB_PROVIDERS:
        return ApiResponse(code=40001, message=f"provider 必须为 {_CLOUD_KB_PROVIDERS} 之一或空串")
    credentials = body.get("credentials", _MISSING)
    if credentials is not _MISSING:
        if not isinstance(credentials, dict):
            return ApiResponse(code=40001, message="credentials 必须为对象")
        if any(not isinstance(v, (str, type(None))) for v in credentials.values()):
            return ApiResponse(code=40001, message="credentials 的值必须为字符串")
    knowledge_base_id = body.get("knowledge_base_id", _MISSING)
    if (
        knowledge_base_id is not _MISSING
        and knowledge_base_id is not None
        and not isinstance(knowledge_base_id, str)
    ):
        return ApiResponse(code=40001, message="knowledge_base_id 必须为字符串")
    top_k = body.get("top_k", _MISSING)
    if (
        top_k is not _MISSING
        and top_k is not None
        and (not _is_num(top_k) or not 1 <= int(top_k) <= 50)
    ):
        return ApiResponse(code=40001, message="top_k 必须为 1-50 的整数")
    score_threshold = body.get("score_threshold", _MISSING)
    if (
        score_threshold is not _MISSING
        and score_threshold is not None
        and (not _is_num(score_threshold) or not 0.0 <= float(score_threshold) <= 1.0)
    ):
        return ApiResponse(code=40001, message="score_threshold 必须为 0-1 的数字")

    stored = await get_system_config(db, "cloud_kb", default={})
    if not isinstance(stored, dict):
        stored = {}
    new_value = dict(stored)

    if enabled is not _MISSING:
        new_value["enabled"] = enabled
    if provider is not _MISSING:
        if provider in (None, ""):
            new_value.pop("provider", None)  # 清除 → 回退 env
        else:
            new_value["provider"] = provider
    if credentials is not _MISSING:
        existing = new_value.get("credentials")
        creds = dict(existing) if isinstance(existing, dict) else {}
        for k, v in credentials.items():
            if v is None or v == "":
                creds.pop(str(k), None)  # 清除单键 → 回退 env/下层
            else:
                creds[str(k)] = encrypt_api_key(str(v))
        if creds:
            new_value["credentials"] = creds
        else:
            new_value.pop("credentials", None)
    if knowledge_base_id is not _MISSING:
        if knowledge_base_id in (None, ""):
            new_value.pop("knowledge_base_id", None)
        else:
            new_value["knowledge_base_id"] = knowledge_base_id
    if top_k is not _MISSING:
        if top_k is None:
            new_value.pop("top_k", None)
        else:
            new_value["top_k"] = int(top_k)
    if score_threshold is not _MISSING:
        if score_threshold is None:
            new_value.pop("score_threshold", None)
        else:
            new_value["score_threshold"] = float(score_threshold)

    await upsert_system_config(db, "cloud_kb", new_value, description="云知识库全局配置")
    await db.commit()

    logger.info("admin.cloud_kb_saved", user_id=current_user["sub"])
    return ApiResponse(code=0, message="ok", data={"saved": True, "key": "cloud_kb"})


@router.post("/system/cloud-kb/test")
async def test_system_cloud_kb(
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """云知识库真实连通性探测。

    cloud_kb 模块由并行任务开发：延迟 import，缺失时 50301 而非崩溃。
    """
    try:
        from app.providers.cloud_kb import resolve_cloud_kb_config, test_cloud_kb
    except ImportError:
        return ApiResponse(code=50301, message="cloud_kb 模块未就绪（并行开发中）")

    try:
        cfg = await resolve_cloud_kb_config(db)
        result = await test_cloud_kb(cfg)
    except Exception as e:
        logger.warning("admin.cloud_kb_test_failed", error=str(e)[:200])
        return ApiResponse(
            code=0,
            message="ok",
            data={
                "ok": False,
                "provider": "",
                "latency_ms": 0,
                "records": [],
                "error": str(e)[:200],
            },
        )
    return ApiResponse(code=0, message="ok", data=result)


# ========== GET/PUT /system/embedding + POST /system/embedding/test ==========

_EMBEDDING_PROVIDERS = ("local", "aliyun", "tencent")
_EMBEDDING_FIELDS = ("provider", "base_url", "api_key", "model", "dimension")
_EMBEDDING_DEFAULT_MODELS = {"local": "bge-m3", "aliyun": "text-embedding-v4", "tencent": "bge-m3"}


def _embedding_env_base() -> dict:
    """env 兜底层（与 providers/embedding._config_from_env 对齐）"""
    provider = str(getattr(settings, "embedding_provider", "local") or "local")
    if provider not in _EMBEDDING_PROVIDERS:
        provider = "local"
    return {
        "provider": provider,
        "base_url": str(getattr(settings, "embedding_base_url", "") or ""),
        "api_key": str(getattr(settings, "embedding_api_key", "") or ""),
        "model": str(getattr(settings, "embedding_model", "") or ""),
        "dimension": 1024,  # pgvector Vector(1024) 红线
    }


async def _embedding_view(db: AsyncSession) -> dict:
    """embedding 脱敏视图：env ← system_configs["embedding"]（api_key 解密后脱敏）"""
    stored = await get_system_config(db, "embedding", default={})
    if not isinstance(stored, dict):
        stored = {}
    env = _embedding_env_base()

    api_key = _mask(env["api_key"]) if env["api_key"] else ""
    if stored.get("api_key"):
        api_key = _mask(decrypt_api_key(str(stored["api_key"])))
    provider = str(stored.get("provider") or env["provider"])
    model = str(stored.get("model") or env["model"] or _EMBEDDING_DEFAULT_MODELS.get(provider, ""))
    return {
        "configured": bool(stored),
        "provider": provider,
        "base_url": str(stored.get("base_url") or env["base_url"]),
        "api_key": api_key,
        "model": model,
        "dimension": stored["dimension"] if _is_num(stored.get("dimension")) else env["dimension"],
    }


@router.get("/system/embedding")
async def get_system_embedding(
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """读取 Embedding 配置（api_key 脱敏回显）"""
    return ApiResponse(code=0, message="ok", data=await _embedding_view(db))


@router.put("/system/embedding")
async def put_system_embedding(
    body: dict[str, Any] = Body(default_factory=dict),
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """upsert embedding（部分更新；api_key 加密落库，空串/null 清除回退 env）"""
    unknown = sorted(set(body) - set(_EMBEDDING_FIELDS))
    if unknown:
        return ApiResponse(
            code=40001, message=f"未知字段: {unknown}（支持 {list(_EMBEDDING_FIELDS)}）"
        )

    provider = body.get("provider", _MISSING)
    if provider is not _MISSING and provider not in (None, "") + _EMBEDDING_PROVIDERS:
        return ApiResponse(code=40001, message=f"provider 必须为 {_EMBEDDING_PROVIDERS} 之一或空串")
    for fname in ("base_url", "api_key", "model"):
        v = body.get(fname, _MISSING)
        if v is not _MISSING and v is not None and not isinstance(v, str):
            return ApiResponse(code=40001, message=f"{fname} 必须为字符串")
    dimension = body.get("dimension", _MISSING)
    if (
        dimension is not _MISSING
        and dimension is not None
        and (not _is_num(dimension) or not 128 <= int(dimension) <= 4096)
    ):
        return ApiResponse(code=40001, message="dimension 必须为 128-4096 的整数（库表红线 1024）")

    stored = await get_system_config(db, "embedding", default={})
    if not isinstance(stored, dict):
        stored = {}
    new_value = dict(stored)

    if provider is not _MISSING:
        if provider in (None, ""):
            new_value.pop("provider", None)
        else:
            new_value["provider"] = provider
    for fname in ("base_url", "model"):
        v = body.get(fname, _MISSING)
        if v is _MISSING:
            continue
        if v in (None, ""):
            new_value.pop(fname, None)
        else:
            new_value[fname] = v
    api_key = body.get("api_key", _MISSING)
    if api_key is not _MISSING:
        if api_key in (None, ""):
            new_value.pop("api_key", None)
        else:
            new_value["api_key"] = encrypt_api_key(str(api_key))
    if dimension is not _MISSING:
        if dimension is None:
            new_value.pop("dimension", None)
        else:
            new_value["dimension"] = int(dimension)

    await upsert_system_config(db, "embedding", new_value, description="Embedding 向量服务全局配置")
    await db.commit()

    logger.info("admin.embedding_saved", user_id=current_user["sub"])
    return ApiResponse(code=0, message="ok", data={"saved": True, "key": "embedding"})


@router.post("/system/embedding/test")
async def test_system_embedding(
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Embedding 真实连通性探测（含维度一致性告警）"""
    try:
        from app.providers.embedding import resolve_embedding_config

        cfg = await resolve_embedding_config(db)
        result = await EmbeddingProvider(cfg).health_check()
    except Exception as e:
        logger.warning("admin.embedding_test_failed", error=str(e)[:200])
        return ApiResponse(
            code=0,
            message="ok",
            data={"ok": False, "latency_ms": 0, "error": str(e)[:200]},
        )
    result["provider"] = cfg.provider
    result["model"] = cfg.model
    result["source"] = cfg.source
    return ApiResponse(code=0, message="ok", data=result)


# ========== GET/PUT /system/butler ==========

# 仅管理员能力开关可持久化；web_search_local_refused 是运行事实，禁止写入配置
_BUTLER_AUTHORIZATION_FIELDS = ("external_allowed", "web_search_enabled")


async def _butler_authorization_view(db: AsyncSession) -> dict:
    """butler.authorization 视图：env ← system_configs（布尔开关，无敏感字段）"""
    from app.butler.config import resolve_butler_authorization

    stored = await get_system_config(db, "butler.authorization", default={})
    if not isinstance(stored, dict):
        stored = {}
    effective = await resolve_butler_authorization(db)
    return {
        "configured": bool(stored),
        "external_allowed": effective["external_allowed"],
        "web_search_enabled": effective["web_search_enabled"],
    }


@router.get("/system/butler")
async def get_system_butler(
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """读取 Butler 授权开关（butler.authorization，env ← 全局覆盖）"""
    return ApiResponse(code=0, message="ok", data=await _butler_authorization_view(db))


@router.put("/system/butler")
async def put_system_butler(
    body: dict[str, Any] = Body(default_factory=dict),
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """upsert butler.authorization（部分更新；布尔校验；null/空清除回退默认）"""
    unknown = sorted(set(body) - set(_BUTLER_AUTHORIZATION_FIELDS))
    if unknown:
        return ApiResponse(
            code=40001, message=f"未知字段: {unknown}（支持 {list(_BUTLER_AUTHORIZATION_FIELDS)}）"
        )

    stored = await get_system_config(db, "butler.authorization", default={})
    if not isinstance(stored, dict):
        stored = {}
    new_value = dict(stored)
    for field_name, value in body.items():
        if value is None or value == "":
            new_value.pop(field_name, None)  # 清除 → 回退默认
            continue
        if not isinstance(value, bool):
            return ApiResponse(code=40001, message=f"{field_name} 必须为布尔值")
        new_value[field_name] = value

    await upsert_system_config(
        db, "butler.authorization", new_value, description="Butler 授权开关（外部工具/联网搜索）"
    )
    await db.commit()

    logger.info("admin.butler_authorization_saved", user_id=current_user["sub"])
    return ApiResponse(code=0, message="ok", data={"saved": True, "key": "butler.authorization"})


# ========== GET /workflows + PUT /workflows/{name} + POST /workflows/{name}/test ==========


async def _read_flow_switch(name: str, default: str = "on") -> str:
    """读取 Redis 运行时开关（故障时按 on 处理，对齐 xingchen._check_runtime_switch）"""
    try:
        val = await get_redis().get(f"switch:xingchen:{name}")
        return val or default
    except Exception:
        return default


async def _cache_flow_switch(name: str, enabled: bool | None) -> None:
    """尽力同步 Redis 运行时缓存；数据库配置始终是权威真相源。"""
    try:
        redis = get_redis()
        if enabled is None:
            await redis.delete(f"switch:xingchen:{name}")
        else:
            await redis.set(f"switch:xingchen:{name}", "on" if enabled else "off")
    except Exception:
        logger.warning("admin.workflow_switch_cache_unavailable", flow=name)


def _today_start() -> datetime:
    """当日口径 = 服务器本地日期的零点（对齐 ops_ext_router）"""
    return datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)


async def _workflow_item(
    name: str,
    wf_overrides: dict,
    calls_by_scene: dict,
    xingchen_cfg: XingchenConfig | None = None,
) -> dict:
    """单个工作流的脱敏管理视图（数据库配置优先、全局/env 回退）。"""
    ov = wf_overrides.get(name)
    ov = ov if isinstance(ov, dict) else {}
    cfg = xingchen_cfg or XingchenConfig(
        base_url=settings.xingchen_base_url,
        api_key=settings.xingchen_api_key,
        api_secret=settings.xingchen_api_secret,
        flow_ids=settings.xingchen_flow_id_map,
        timeouts=settings.xingchen_timeout_map,
    )
    cfg_flow = cfg.flow_ids.get(name)
    inherited_flow_id = (
        cfg_flow.get("flow_id", "") if isinstance(cfg_flow, dict) else cfg_flow or ""
    )
    flow_id = str(ov.get("workflow_id") or ov.get("flow_id") or inherited_flow_id)
    timeout = ov.get("timeout_seconds", ov.get("timeout"))
    if timeout is None:
        timeout = cfg.timeouts.get(name, _DEFAULT_TIMEOUTS.get(name, 30.0))
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = _DEFAULT_TIMEOUTS.get(name, 30.0)
    api_key = decrypt_api_key(str(ov.get("api_key") or "")) or cfg.api_key
    api_secret = decrypt_api_key(str(ov.get("api_secret") or "")) or cfg.api_secret
    base_url = str(ov.get("base_url") or cfg.workflow_base_urls.get(name) or cfg.base_url or "")
    default_switch = "on" if ov.get("enabled", True) else "off"
    switch = await _read_flow_switch(name, default_switch)
    enabled = switch not in ("off", "force_fallback")
    last_test_status = str(ov.get("last_test_status") or "never")
    configured = bool(flow_id and base_url and api_key and api_secret)
    verified = last_test_status == "success"
    retry_count = ov.get("retry_count", 0)
    if isinstance(retry_count, bool) or not isinstance(retry_count, int):
        retry_count = 0
    return {
        "name": name,
        "workflow_name": name,
        "capability": ov.get("capability") or _TEACHER_WORKFLOW_CAPABILITIES.get(name, ""),
        "purpose": _FLOW_PURPOSES.get(name, ""),
        "flow_id": flow_id,
        "workflow_id": flow_id,
        "enabled": enabled,
        "timeout": timeout,
        "timeout_seconds": timeout,
        "retry_count": retry_count,
        "base_url": base_url,
        "api_key_masked": _mask(api_key) if api_key else "",
        "api_key_configured": bool(api_key),
        "api_secret_configured": bool(api_secret),
        "input_mapping": ov.get("input_mapping") if isinstance(ov.get("input_mapping"), dict) else {},
        "output_mapping": ov.get("output_mapping") if isinstance(ov.get("output_mapping"), dict) else {},
        "health_check_url": str(ov.get("health_check_url") or ""),
        "last_tested_at": ov.get("last_tested_at"),
        "last_test_status": last_test_status,
        "last_error_code": ov.get("last_error_code"),
        "today_calls": calls_by_scene.get(name, 0),
        "configured": configured,
        "verified": verified,
        "available": bool(enabled and configured and verified),
    }


@router.get("/workflows")
async def list_workflows(
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """列出 FLOW_REGISTRY 全部工作流（有效 flow_id/开关/超时/今日调用量）"""
    workflows = await get_system_config(db, "workflows", default={})
    if not isinstance(workflows, dict):
        workflows = {}

    # 今日 ai_calls 按 scene 聚合（provider=xingchen）
    rows = (
        await db.execute(
            select(AICall.scene, func.count(AICall.id))
            .where(AICall.provider == "xingchen", AICall.created_at >= _today_start())
            .group_by(AICall.scene)
        )
    ).all()
    calls_by_scene = dict(rows)

    # master_enabled 读星辰有效配置（env ← system_configs["xingchen.global"] ← 用户覆盖），
    # 而非只读 settings.xingchen_enabled：PUT /system/xingchen enabled 后立即一致。
    xingchen_cfg = await resolve_effective_xingchen_config(db)
    items = [
        await _workflow_item(name, workflows, calls_by_scene, xingchen_cfg)
        for name in _visible_flow_names()
    ]
    return ApiResponse(
        code=0,
        message="ok",
        data={"master_enabled": xingchen_cfg.enabled, "workflows": items},
    )


_WORKFLOW_MUTABLE_FIELDS = frozenset(
    {
        "workflow_name",
        "capability",
        "enabled",
        "base_url",
        "api_key",
        "api_secret",
        "workflow_id",
        "flow_id",
        "input_mapping",
        "output_mapping",
        "timeout_seconds",
        "timeout",
        "retry_count",
        "health_check_url",
    }
)


def _normalize_workflow_entry(
    name: str, body: dict[str, Any], existing: dict[str, Any] | None = None
) -> tuple[dict[str, Any] | None, str | None]:
    """校验并归一化管理输入；敏感字段在此加密，绝不进入响应。"""
    unknown = sorted(set(body) - _WORKFLOW_MUTABLE_FIELDS)
    if unknown:
        return None, f"未知字段: {unknown}"
    body_name = body.get("workflow_name")
    if body_name not in (None, "", name):
        return None, "workflow_name 与路径不一致"
    expected_capability = _TEACHER_WORKFLOW_CAPABILITIES.get(name)
    capability = body.get("capability", _MISSING)
    if expected_capability and capability not in (_MISSING, None, "", expected_capability):
        return None, f"{name} 只能绑定能力 {expected_capability}"

    entry = dict(existing or {})
    if expected_capability:
        entry["capability"] = expected_capability
    elif capability is not _MISSING:
        if capability in (None, ""):
            entry.pop("capability", None)
        elif not isinstance(capability, str):
            return None, "capability 必须为字符串"
        else:
            entry["capability"] = capability.strip()

    enabled = body.get("enabled", _MISSING)
    if enabled is not _MISSING:
        if not isinstance(enabled, bool):
            return None, "enabled 必须为布尔值"
        entry["enabled"] = enabled

    workflow_id = body.get("workflow_id", body.get("flow_id", _MISSING))
    if workflow_id is not _MISSING:
        if workflow_id in (None, ""):
            entry.pop("workflow_id", None)
            entry.pop("flow_id", None)
        elif not isinstance(workflow_id, str):
            return None, "workflow_id 必须为字符串"
        else:
            entry["workflow_id"] = workflow_id.strip()
            entry["flow_id"] = workflow_id.strip()

    for field_name in ("base_url", "health_check_url"):
        value = body.get(field_name, _MISSING)
        if value is _MISSING:
            continue
        if value in (None, ""):
            entry.pop(field_name, None)
        elif not isinstance(value, str) or not value.startswith(("http://", "https://")):
            return None, f"{field_name} 必须为 http(s) URL"
        else:
            entry[field_name] = value.rstrip("/")

    for field_name in ("api_key", "api_secret"):
        value = body.get(field_name, _MISSING)
        if value is _MISSING:
            continue
        if value in (None, ""):
            entry.pop(field_name, None)
        elif not isinstance(value, str):
            return None, f"{field_name} 必须为字符串"
        else:
            entry[field_name] = encrypt_api_key(value)

    for field_name in ("input_mapping", "output_mapping"):
        value = body.get(field_name, _MISSING)
        if value is _MISSING:
            continue
        if value is None:
            entry.pop(field_name, None)
        elif not isinstance(value, dict):
            return None, f"{field_name} 必须为对象"
        else:
            entry[field_name] = value

    timeout = body.get("timeout_seconds", body.get("timeout", _MISSING))
    if timeout is not _MISSING:
        if timeout in (None, ""):
            entry.pop("timeout_seconds", None)
            entry.pop("timeout", None)
        elif not _is_num(timeout) or not 0 < float(timeout) <= 300:
            return None, "timeout_seconds 需为 (0, 300] 秒的数字"
        else:
            entry["timeout_seconds"] = float(timeout)
            entry["timeout"] = float(timeout)

    retry_count = body.get("retry_count", _MISSING)
    if retry_count is not _MISSING:
        if retry_count is None:
            entry.pop("retry_count", None)
        elif isinstance(retry_count, bool) or not isinstance(retry_count, int):
            return None, "retry_count 必须为整数"
        elif not 0 <= retry_count <= 5:
            return None, "retry_count 需在 [0, 5]"
        else:
            entry["retry_count"] = retry_count

    verification_sensitive = {
        "base_url",
        "api_key",
        "api_secret",
        "workflow_id",
        "flow_id",
        "input_mapping",
        "output_mapping",
        "timeout_seconds",
        "timeout",
        "retry_count",
    }
    if existing is not None and set(body) & verification_sensitive:
        entry["last_tested_at"] = None
        entry["last_test_status"] = "never"
        entry["last_error_code"] = None
    return entry, None


async def _workflow_view(
    name: str, workflows: dict[str, Any], db: AsyncSession
) -> dict[str, Any]:
    today_calls = (
        await db.execute(
            select(func.count(AICall.id)).where(
                AICall.provider == "xingchen",
                AICall.scene == name,
                AICall.created_at >= _today_start(),
            )
        )
    ).scalar() or 0
    cfg = await resolve_effective_xingchen_config(db)
    return await _workflow_item(name, workflows, {name: today_calls}, cfg)


async def _save_workflow_entry(
    name: str, body: dict[str, Any], db: AsyncSession
) -> tuple[dict[str, Any] | None, str | None, dict[str, Any]]:
    workflows = await get_system_config(db, "workflows", default={})
    workflows = dict(workflows) if isinstance(workflows, dict) else {}
    existing = workflows.get(name)
    entry, error = _normalize_workflow_entry(
        name, body, existing if isinstance(existing, dict) else None
    )
    if error or entry is None:
        return None, error, workflows
    workflows[name] = entry
    await upsert_system_config(
        db, "workflows", workflows, description="工作流连接、映射、重试与验证状态"
    )
    await db.commit()
    if "enabled" in body:
        await _cache_flow_switch(name, body["enabled"])
    return entry, None, workflows


@router.get("/workflows/{name}")
async def get_workflow(
    name: str,
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    if name not in _visible_flow_names():
        return ApiResponse(code=40400, message=f"未知工作流: {name}")
    workflows = await get_system_config(db, "workflows", default={})
    if not isinstance(workflows, dict) or not isinstance(workflows.get(name), dict):
        return ApiResponse(code=40400, message=f"工作流尚未配置: {name}")
    return ApiResponse(code=0, message="ok", data=await _workflow_view(name, workflows, db))


@router.post("/workflows")
async def create_workflow(
    body: dict[str, Any] = Body(default_factory=dict),
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    name = body.get("workflow_name")
    if not isinstance(name, str) or name not in _visible_flow_names():
        return ApiResponse(code=40001, message="workflow_name 非法或当前 profile 不可用")
    workflows = await get_system_config(db, "workflows", default={})
    if isinstance(workflows, dict) and isinstance(workflows.get(name), dict):
        return ApiResponse(code=40900, message=f"工作流配置已存在: {name}")
    _, error, workflows = await _save_workflow_entry(name, body, db)
    if error:
        return ApiResponse(code=40001, message=error)
    logger.info("admin.workflow_created", user_id=current_user["sub"], flow=name)
    return ApiResponse(code=0, message="ok", data=await _workflow_view(name, workflows, db))


@router.put("/workflows/{name}")
async def put_workflow(
    name: str,
    body: dict[str, Any] = Body(default_factory=dict),
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    if name not in _visible_flow_names():
        return ApiResponse(code=40400, message=f"未知工作流: {name}")
    _, error, workflows = await _save_workflow_entry(name, body, db)
    if error:
        return ApiResponse(code=40001, message=error)
    logger.info("admin.workflow_saved", user_id=current_user["sub"], flow=name)
    return ApiResponse(code=0, message="ok", data=await _workflow_view(name, workflows, db))


@router.delete("/workflows/{name}")
async def delete_workflow(
    name: str,
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    if name not in _visible_flow_names():
        return ApiResponse(code=40400, message=f"未知工作流: {name}")
    workflows = await get_system_config(db, "workflows", default={})
    if not isinstance(workflows, dict) or not isinstance(workflows.get(name), dict):
        return ApiResponse(code=40400, message=f"工作流配置不存在: {name}")
    workflows = dict(workflows)
    workflows.pop(name, None)
    await upsert_system_config(db, "workflows", workflows)
    await db.commit()
    await _cache_flow_switch(name, None)
    logger.info("admin.workflow_deleted", user_id=current_user["sub"], flow=name)
    return ApiResponse(code=0, message="ok", data={"workflow_name": name, "deleted": True})


async def _set_workflow_enabled(
    name: str, enabled: bool, current_user: dict, db: AsyncSession
) -> ApiResponse:
    if name not in _visible_flow_names():
        return ApiResponse(code=40400, message=f"未知工作流: {name}")
    workflows = await get_system_config(db, "workflows", default={})
    if not isinstance(workflows, dict) or not isinstance(workflows.get(name), dict):
        return ApiResponse(code=40400, message=f"工作流配置不存在: {name}")
    workflows = dict(workflows)
    entry = dict(workflows[name])
    entry["enabled"] = enabled
    workflows[name] = entry
    await upsert_system_config(db, "workflows", workflows)
    await db.commit()
    await _cache_flow_switch(name, enabled)
    logger.info(
        "admin.workflow_switch_changed",
        user_id=current_user["sub"],
        flow=name,
        enabled=enabled,
    )
    return ApiResponse(code=0, message="ok", data=await _workflow_view(name, workflows, db))


@router.post("/workflows/{name}/enable")
async def enable_workflow(
    name: str,
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await _set_workflow_enabled(name, True, current_user, db)


@router.post("/workflows/{name}/disable")
async def disable_workflow(
    name: str,
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    return await _set_workflow_enabled(name, False, current_user, db)


@router.post("/workflows/{name}/test")
async def test_workflow(
    name: str,
    current_user: dict = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """单个工作流真实探测（内置最小入参，非流式，30s 上限）

    总开关关闭 / 运行时开关关闭 / 未配置 flow_id → 明确提示，不报错不触网。
    """
    if name not in _visible_flow_names():
        return ApiResponse(code=40400, message=f"未知工作流: {name}")
    user_id = current_user["sub"]
    # env ← xingchen.global ← workflows ← 用户覆盖 的全链有效配置
    cfg = await resolve_xingchen_config(user_id, db)

    async def _result(
        *,
        ok: bool,
        latency_ms: int,
        error: str = "",
        error_code: str | None = None,
        output_snippet: str = "",
    ) -> ApiResponse:
        workflows = await get_system_config(db, "workflows", default={})
        workflows = dict(workflows) if isinstance(workflows, dict) else {}
        entry = workflows.get(name)
        if isinstance(entry, dict):
            entry = dict(entry)
            entry["last_tested_at"] = datetime.now().astimezone().isoformat()
            entry["last_test_status"] = "success" if ok else "failed"
            entry["last_error_code"] = None if ok else error_code
            workflows[name] = entry
            await upsert_system_config(db, "workflows", workflows)
            await db.commit()
        view = await _workflow_view(name, workflows, db)
        data = {
            "ok": ok,
            "latency_ms": latency_ms,
            "output_snippet": output_snippet,
            "error": error,
            "last_tested_at": view["last_tested_at"],
            "last_test_status": view["last_test_status"],
            "last_error_code": view["last_error_code"],
            "verified": view["verified"],
            "available": view["available"],
        }
        return ApiResponse(
            code=0,
            message="ok",
            data=data,
        )

    if not cfg.enabled:
        return await _result(
            ok=False,
            latency_ms=0,
            error="星辰总开关关闭，未发起调用",
            error_code="WORKFLOW_MASTER_DISABLED",
        )
    stored_workflows = await get_system_config(db, "workflows", default={})
    stored_entry = (
        stored_workflows.get(name) if isinstance(stored_workflows, dict) else None
    )
    default_switch = (
        "off" if isinstance(stored_entry, dict) and stored_entry.get("enabled") is False else "on"
    )
    switch = await _read_flow_switch(name, default_switch)
    if switch in ("off", "force_fallback"):
        return await _result(
            ok=False,
            latency_ms=0,
            error="工作流运行时开关关闭，未发起调用",
            error_code="WORKFLOW_DISABLED",
        )
    if not cfg.flow_ids.get(name):
        return await _result(
            ok=False,
            latency_ms=0,
            error="未配置 workflow_id，未发起调用",
            error_code="WORKFLOW_NOT_CONFIGURED",
        )

    timeout = min(30.0, _resolve_timeout(name, cfg))
    start = time.perf_counter()
    try:
        result = await run_workflow(
            name,
            uid=user_id,
            parameters=_SAMPLE_PARAMS[name],
            read_timeout=timeout,
            config=cfg,
        )
        latency_ms = int((time.perf_counter() - start) * 1000)
        snippet = json.dumps(result, ensure_ascii=False)[:300]
        return await _result(
            ok=True,
            latency_ms=latency_ms,
            output_snippet=snippet,
        )
    except Exception:
        latency_ms = int((time.perf_counter() - start) * 1000)
        logger.info("admin.workflow_test_failed", flow=name, error_code="WORKFLOW_TEST_FAILED")
        return await _result(
            ok=False,
            latency_ms=latency_ms,
            error="工作流连通性测试失败",
            error_code="WORKFLOW_TEST_FAILED",
        )
