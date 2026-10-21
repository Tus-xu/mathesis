"""集成配置路由（/api/integrations）

用户级运行时配置：对象存储（storage）与星辰工作流（xingchen）。
范式对齐 /api/model-config：
- 字段级回退 .env（用户值优先，缺字段用 env）
- 敏感字段 Fernet 加密落库，GET 脱敏回显
- 空字符串 = 保持原值；显式 null = 清除该字段回退 env
- /test 用合并后有效配置测连通
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog
from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.gateway.auth import get_current_user
from app.gateway.schemas import ApiResponse
from app.models.database import get_db
from app.models.user_integration_config import (
    INTEGRATION_KINDS,
    UserIntegrationConfig,
    get_user_integration_row,
)
from app.providers.crypto import encrypt_api_key, mask_api_key
from app.providers.http import get_http
from app.providers.storage import (
    STORAGE_FIELDS,
    STORAGE_PROVIDERS,
    STORAGE_SENSITIVE_FIELDS,
    StorageConfig,
    StorageProvider,
    resolve_storage_config,
)
from app.providers.xingchen import (
    FLOW_REGISTRY,
    XINGCHEN_FIELDS,
    XINGCHEN_SENSITIVE_FIELDS,
    XingchenConfig,
    resolve_xingchen_config,
    run_workflow,
)

logger = structlog.get_logger()
router = APIRouter()

_INT_FIELDS: dict[str, frozenset[str]] = {
    "storage": frozenset({"port", "presign_expires"}),
    "xingchen": frozenset({"max_concurrency", "queue_max"}),
}
_DICT_FIELDS: dict[str, frozenset[str]] = {
    "storage": frozenset(),
    "xingchen": frozenset({"flow_ids", "timeouts"}),
}
_FIELDS_BY_KIND: dict[str, tuple[str, ...]] = {
    "storage": STORAGE_FIELDS,
    "xingchen": XINGCHEN_FIELDS,
}
_SENSITIVE_BY_KIND: dict[str, frozenset[str]] = {
    "storage": STORAGE_SENSITIVE_FIELDS,
    "xingchen": XINGCHEN_SENSITIVE_FIELDS,
}


# ========== 视图（脱敏回显） ==========


def _mask(value: str) -> str:
    """脱敏：空值返回空串，对齐 model_config 回显风格"""
    return mask_api_key(value) if value else ""


def _storage_view(cfg: StorageConfig, configured: bool) -> dict[str, Any]:
    return {
        "provider": cfg.provider,
        "scheme": cfg.scheme,
        "host": cfg.host,
        "port": cfg.port,
        "endpoint_url": cfg.endpoint_url,
        "region": cfg.region,
        "bucket": cfg.bucket,
        "access_key": _mask(cfg.access_key),
        "secret_key": _mask(cfg.secret_key),
        "session_token": _mask(cfg.session_token),
        "presign_expires": cfg.presign_expires,
        "endpoint": cfg.endpoint,  # 推导结果，便于前端确认
        "configured": configured,
        "source": "user" if configured else "env",
    }


def _xingchen_view(cfg: XingchenConfig, configured: bool) -> dict[str, Any]:
    return {
        "enabled": cfg.enabled,
        "base_url": cfg.base_url,
        "api_key": _mask(cfg.api_key),
        "api_secret": _mask(cfg.api_secret),
        "flow_ids": cfg.flow_ids,
        "timeouts": cfg.timeouts,
        "max_concurrency": cfg.max_concurrency,
        "queue_max": cfg.queue_max,
        "registered_flows": sorted(FLOW_REGISTRY.keys()),
        "configured": configured,
        "source": "user" if configured else "env",
    }


# ========== GET / — 读取全部集成配置 ==========


@router.get("")
@router.get("/", include_in_schema=False)
async def get_integrations(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回当前用户集成配置（脱敏），用户值优先、缺字段回显 env 有效值"""
    user_id = current_user["sub"]
    storage_cfg = await resolve_storage_config(user_id, db)
    xingchen_cfg = await resolve_xingchen_config(user_id, db)
    storage_row = await get_user_integration_row(user_id, "storage", db)
    xingchen_row = await get_user_integration_row(user_id, "xingchen", db)

    return ApiResponse(
        code=0,
        message="ok",
        data={
            "storage": _storage_view(storage_cfg, storage_row is not None),
            "xingchen": _xingchen_view(xingchen_cfg, xingchen_row is not None),
        },
    )


# ========== PUT /{kind} — 保存（upsert 部分字段） ==========


def _coerce_field(kind: str, field_name: str, value: Any) -> tuple[bool, Any]:
    """校验/转换单个字段。返回 (ok, 值或错误消息)"""
    if field_name in _SENSITIVE_BY_KIND[kind]:
        if not isinstance(value, str):
            return False, f"{field_name} 必须为字符串"
        return True, value

    if field_name in _INT_FIELDS[kind]:
        try:
            iv = int(value)
        except (TypeError, ValueError):
            return False, f"{field_name} 必须为整数"
        if iv <= 0:
            return False, f"{field_name} 必须为正整数"
        if field_name == "port" and iv > 65535:
            return False, "port 超出范围（1-65535）"
        return True, iv

    if field_name in _DICT_FIELDS[kind]:
        if not isinstance(value, dict):
            return False, f"{field_name} 必须为对象（dict）"
        if field_name == "flow_ids":
            return True, {str(k): str(v) for k, v in value.items()}
        # timeouts：值必须可转 float
        try:
            return True, {str(k): float(v) for k, v in value.items()}
        except (TypeError, ValueError):
            return False, "timeouts 的值必须为数字（秒）"

    if kind == "xingchen" and field_name == "enabled":
        if not isinstance(value, bool):
            return False, "enabled 必须为布尔值"
        return True, value

    if kind == "storage" and field_name == "provider":
        if value not in STORAGE_PROVIDERS:
            return False, f"provider 必须为 {sorted(STORAGE_PROVIDERS)} 之一"
        return True, value

    if kind == "storage" and field_name == "scheme":
        if value not in ("http", "https"):
            return False, "scheme 必须为 http 或 https"
        return True, value

    # 其余字符串字段
    if not isinstance(value, str):
        return False, f"{field_name} 必须为字符串"
    return True, value


@router.put("/{kind}")
async def put_integration(
    kind: str,
    body: dict[str, Any] = Body(default_factory=dict),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """upsert 用户集成配置（部分字段）。

    语义：字段缺省 = 不变；空字符串 = 保持原值；显式 null = 清除该字段回退 env。
    """
    if kind not in INTEGRATION_KINDS:
        return ApiResponse(code=40001, message=f"未知集成类型: {kind}（支持 {list(INTEGRATION_KINDS)}）")

    allowed = set(_FIELDS_BY_KIND[kind])
    unknown = sorted(set(body) - allowed)
    if unknown:
        return ApiResponse(code=40001, message=f"未知字段: {unknown}（{kind} 支持 {sorted(allowed)}）")

    user_id = current_user["sub"]
    row = await get_user_integration_row(user_id, kind, db)
    if row is None:
        row = UserIntegrationConfig(user_id=user_id, kind=kind, config={})
        db.add(row)

    config = dict(row.config or {})
    sensitive = _SENSITIVE_BY_KIND[kind]
    for field_name, value in body.items():
        if value is None:
            config.pop(field_name, None)  # 显式 null → 清除回退 env
            continue
        if isinstance(value, str) and value == "":
            continue  # 空字符串 = 保持原值
        ok, converted = _coerce_field(kind, field_name, value)
        if not ok:
            return ApiResponse(code=40001, message=str(converted))
        config[field_name] = encrypt_api_key(converted) if field_name in sensitive else converted

    row.config = config  # 重新赋值以触发 JSONB 变更检测
    await db.flush()
    await db.commit()

    logger.info("integration_config.saved", user_id=user_id, kind=kind)
    return ApiResponse(code=0, message="ok", data={"saved": True, "kind": kind})


# ========== DELETE /{kind} — 清除用户覆盖 ==========


@router.delete("/{kind}")
async def delete_integration(
    kind: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """删除用户覆盖，回退 env 全局配置"""
    if kind not in INTEGRATION_KINDS:
        return ApiResponse(code=40001, message=f"未知集成类型: {kind}（支持 {list(INTEGRATION_KINDS)}）")

    user_id = current_user["sub"]
    row = await get_user_integration_row(user_id, kind, db)
    if row is not None:
        await db.delete(row)
        await db.commit()
    logger.info("integration_config.reset", user_id=user_id, kind=kind)
    return ApiResponse(code=0, message="ok", data={"reset": True, "kind": kind})


# ========== POST /{kind}/test — 连通性测试 ==========


def _check_item(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _classify_storage_error(exc: Exception) -> tuple[str, str]:
    """存储错误分类 → (category, 用户可读消息)"""
    from botocore.exceptions import ClientError, ConnectTimeoutError, EndpointConnectionError

    if isinstance(exc, (EndpointConnectionError, ConnectTimeoutError)):
        return "endpoint_unreachable", f"端点不可达: {str(exc)[:200]}"
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchBucket", "404"):
            return "bucket_not_found", "bucket 不存在"
        if code in ("SignatureDoesNotMatch", "InvalidAccessKeyId", "AccessDenied", "403"):
            return "auth_failed", f"认证失败或无权限: {code}"
        return "storage_error", f"存储错误: {code or str(exc)[:200]}"
    return "unknown", f"未知错误: {str(exc)[:200]}"


def _bucket_probe(provider: StorageProvider) -> str:
    """同步 bucket 探测（to_thread 中执行）：head_bucket，403 时回退 list_objects_v2"""
    from botocore.exceptions import ClientError

    client = provider._get_client()
    try:
        client.head_bucket(Bucket=provider.bucket)
        return "head_bucket 成功"
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("403", "AccessDenied"):
            # 部分只读密钥无 HeadBucket 权限，回退 list 验证
            client.list_objects_v2(Bucket=provider.bucket, MaxKeys=1)
            return "head_bucket 无权限，list_objects_v2 成功"
        raise


async def _test_storage(user_id: str, db: AsyncSession) -> dict[str, Any]:
    cfg = await resolve_storage_config(user_id, db)
    checks: list[dict[str, Any]] = []

    # 1. 配置完整性
    missing = []
    if not cfg.endpoint:
        missing.append("endpoint（endpoint_url 或 host/region）")
    if not cfg.bucket:
        missing.append("bucket")
    if not cfg.access_key:
        missing.append("access_key")
    if not cfg.secret_key:
        missing.append("secret_key")
    if missing:
        checks.append(_check_item("config", False, f"缺少配置: {', '.join(missing)}"))
        return {"ok": False, "latency_ms": 0, "detail": checks[0]["detail"], "checks": checks}
    checks.append(
        _check_item("config", True, f"provider={cfg.provider} endpoint={cfg.endpoint} bucket={cfg.bucket}")
    )

    provider = StorageProvider(config=cfg)

    # 2. bucket 可达性（boto3 为同步调用，放线程执行）
    start = time.perf_counter()
    try:
        probe_detail = await asyncio.to_thread(_bucket_probe, provider)
        latency_ms = int((time.perf_counter() - start) * 1000)
        checks.append(_check_item("bucket", True, probe_detail))
    except Exception as e:
        latency_ms = int((time.perf_counter() - start) * 1000)
        category, message = _classify_storage_error(e)
        checks.append(_check_item("bucket", False, message))
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "detail": message,
            "error_category": category,
            "checks": checks,
        }

    # 3. 预签名链路
    try:
        provider.presign_get("__integration_probe__", expires=60)
        checks.append(_check_item("presign", True, "预签名 URL 生成成功"))
    except Exception as e:
        checks.append(_check_item("presign", False, f"预签名失败: {str(e)[:200]}"))
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "detail": checks[-1]["detail"],
            "error_category": "presign_failed",
            "checks": checks,
        }

    return {"ok": True, "latency_ms": latency_ms, "detail": "存储连通正常", "checks": checks}


async def _test_xingchen(user_id: str, db: AsyncSession) -> dict[str, Any]:
    cfg = await resolve_xingchen_config(user_id, db)
    checks: list[dict[str, Any]] = []

    # 1. 配置完整性
    if not cfg.enabled:
        checks.append(_check_item("config", False, "enabled=false（星辰总开关关闭）"))
        return {"ok": False, "latency_ms": 0, "detail": checks[0]["detail"], "checks": checks}
    missing = [f for f in ("base_url", "api_key", "api_secret") if not getattr(cfg, f)]
    if missing:
        checks.append(_check_item("config", False, f"缺少配置: {', '.join(missing)}"))
        return {"ok": False, "latency_ms": 0, "detail": checks[0]["detail"], "checks": checks}
    checks.append(_check_item("config", True, f"base_url={cfg.base_url}"))

    # 2. base_url 可达性（短超时 HTTP 探测，任意响应即视为可达）
    start = time.perf_counter()
    reachable = True
    try:
        resp = await get_http().get(cfg.base_url, timeout=3.0)
        latency_ms = int((time.perf_counter() - start) * 1000)
        checks.append(_check_item("reachability", True, f"HTTP {resp.status_code}"))
    except Exception as e:
        latency_ms = int((time.perf_counter() - start) * 1000)
        reachable = False
        checks.append(_check_item("reachability", False, f"端点不可达: {str(e)[:200]}"))

    # 3. flow 配置状态
    flows = [
        {
            "flow": name,
            "flow_id": fid,
            "type": "registered" if name in FLOW_REGISTRY else "generic",
        }
        for name, fid in cfg.flow_ids.items()
    ]
    checks.append(
        _check_item(
            "flows",
            bool(flows),
            f"已配置 {len(flows)} 个 flow" if flows else "未配置任何 flow_id",
        )
    )

    # 4. 最小调用探测（有已配置 flow 时尝试一次，失败不致命只标注）
    if reachable and flows:
        first = flows[0]["flow"]
        try:
            await run_workflow(first, uid=user_id, parameters={}, read_timeout=5.0, config=cfg)
            checks.append(_check_item("probe", True, f"{first} 最小调用成功"))
        except Exception as e:
            checks.append(_check_item("probe", False, f"{first} 最小调用失败（非致命）: {str(e)[:200]}"))

    ok = reachable and checks[0]["ok"]
    return {
        "ok": ok,
        "latency_ms": latency_ms,
        "detail": "星辰服务可达" if ok else checks[1]["detail"] if len(checks) > 1 else "配置不完整",
        "flows": flows,
        "checks": checks,
    }


@router.post("/{kind}/test")
async def test_integration(
    kind: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用合并后有效配置测连通，返回 {ok, latency_ms, detail, checks}"""
    if kind not in INTEGRATION_KINDS:
        return ApiResponse(code=40001, message=f"未知集成类型: {kind}（支持 {list(INTEGRATION_KINDS)}）")

    user_id = current_user["sub"]
    if kind == "storage":
        result = await _test_storage(user_id, db)
    else:
        result = await _test_xingchen(user_id, db)
    return ApiResponse(code=0, message="ok", data=result)
