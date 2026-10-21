"""模型配置域路由（domains/model_config/router.py）

用户自定义模型 API 配置的 CRUD。
"""

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.gateway.auth import get_current_user
from app.gateway.schemas import ApiResponse
from app.models.database import get_db
from app.models.user_model_config import UserModelConfig
from app.providers.crypto import decrypt_api_key, encrypt_api_key, mask_api_key
from app.providers.deepseek import DeepSeekProvider
from app.providers.spark import SparkProvider

logger = structlog.get_logger()
router = APIRouter()


# ========== Schemas ==========


class ChannelConfig(BaseModel):
    """单通道配置"""

    api_key: str | None = Field(None, description="API Key（空=保留现有值）")
    base_url: str | None = Field(None, description="Base URL")
    model: str | None = Field(None, description="模型名")
    thinking: bool | None = Field(None, description="是否启用思考（仅 DeepSeek）")


class ModelConfigRequest(BaseModel):
    """保存模型配置请求"""

    primary: ChannelConfig = Field(..., description="主通道配置（星火）")
    secondary: ChannelConfig = Field(..., description="备用通道配置（DeepSeek/mimo）")


class TestConfigRequest(BaseModel):
    """测试连通性请求"""

    primary: ChannelConfig = Field(..., description="主通道配置")
    secondary: ChannelConfig = Field(..., description="备用通道配置")


class ChannelTestResult(BaseModel):
    ok: bool
    latency_ms: int = 0
    error: str = ""
    model: str = ""


class TestResult(BaseModel):
    primary: ChannelTestResult
    secondary: ChannelTestResult


# ========== GET / — 读取配置 ==========


@router.get("")
@router.get("/", include_in_schema=False)
async def get_model_config(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回当前用户模型配置，API Key 脱敏"""
    user_id = current_user["sub"]
    result = await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))
    cfg = result.scalar_one_or_none()

    if cfg is None:
        # 无用户配置，返回 .env 默认值的脱敏
        return ApiResponse(
            code=0,
            message="ok",
            data={
                "configured": False,
                "primary": {
                    "api_key": (
                        mask_api_key(settings.spark_api_password)
                        if settings.spark_api_password
                        else ""
                    ),
                    "base_url": "",  # 星火 URL 是硬编码常量，不暴露
                    "model": settings.spark_model,
                    "source": "env_default",
                },
                "secondary": {
                    "api_key": (
                        mask_api_key(settings.deepseek_api_key) if settings.deepseek_api_key else ""
                    ),
                    "base_url": settings.deepseek_base_url,
                    "model": settings.deepseek_model,
                    "thinking": settings.deepseek_thinking,
                    "source": "env_default",
                },
            },
        )

    return ApiResponse(
        code=0,
        message="ok",
        data={
            "configured": True,
            "primary": {
                "api_key": (
                    mask_api_key(decrypt_api_key(cfg.primary_api_key))
                    if cfg.primary_api_key
                    else (
                        mask_api_key(settings.spark_api_password)
                        if settings.spark_api_password
                        else ""
                    )
                ),
                "base_url": cfg.primary_base_url or "",
                "model": cfg.primary_model or settings.spark_model,
                "source": "user" if cfg.primary_api_key else "env_default",
            },
            "secondary": {
                "api_key": (
                    mask_api_key(decrypt_api_key(cfg.secondary_api_key))
                    if cfg.secondary_api_key
                    else (
                        mask_api_key(settings.deepseek_api_key) if settings.deepseek_api_key else ""
                    )
                ),
                "base_url": cfg.secondary_base_url or settings.deepseek_base_url,
                "model": cfg.secondary_model or settings.deepseek_model,
                "thinking": (
                    cfg.secondary_thinking
                    if cfg.secondary_thinking is not None
                    else settings.deepseek_thinking
                ),
                "source": "user" if cfg.secondary_api_key else "env_default",
            },
        },
    )


# ========== PUT / — 保存配置 ==========


@router.put("")
@router.put("/", include_in_schema=False)
async def save_model_config(
    body: ModelConfigRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """upsert 用户模型配置；空 Key = 保留现有值"""
    user_id = current_user["sub"]
    result = await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))
    cfg = result.scalar_one_or_none()

    if cfg is None:
        cfg = UserModelConfig(user_id=user_id)
        db.add(cfg)

    # 主通道
    if body.primary.api_key is not None and body.primary.api_key != "":
        cfg.primary_api_key = encrypt_api_key(body.primary.api_key)
    # 空 api_key = 保留现有值，不覆盖

    if body.primary.base_url is not None:
        cfg.primary_base_url = body.primary.base_url or None
    if body.primary.model is not None:
        cfg.primary_model = body.primary.model or None

    # 备用通道
    if body.secondary.api_key is not None and body.secondary.api_key != "":
        cfg.secondary_api_key = encrypt_api_key(body.secondary.api_key)

    if body.secondary.base_url is not None:
        cfg.secondary_base_url = body.secondary.base_url or None
    if body.secondary.model is not None:
        cfg.secondary_model = body.secondary.model or None
    if body.secondary.thinking is not None:
        cfg.secondary_thinking = body.secondary.thinking

    await db.flush()
    await db.commit()

    logger.info("model_config.saved", user_id=user_id)
    return ApiResponse(code=0, message="ok", data={"saved": True})


# ========== POST /test — 测试连通性 ==========


@router.post("/test")
async def test_model_config(
    body: TestConfigRequest,
    current_user: dict = Depends(get_current_user),
):
    """用提交的临时配置测试连通性（不入库）"""
    # 构造临时 Provider 测试
    spark = SparkProvider(
        api_password=body.primary.api_key or None,
        model=body.primary.model or None,
        base_url=body.primary.base_url or None,
    )
    deepseek = DeepSeekProvider(
        api_key=body.secondary.api_key or None,
        model=body.secondary.model or None,
        base_url=body.secondary.base_url or None,
        thinking=body.secondary.thinking,
    )

    spark_result = await spark.health_check()
    deepseek_result = await deepseek.health_check()

    return ApiResponse(
        code=0,
        message="ok",
        data={
            "primary": {
                "ok": spark_result["ok"],
                "latency_ms": spark_result.get("latency_ms", 0),
                "error": spark_result.get("error", ""),
                "model": spark_result.get("model", body.primary.model or ""),
            },
            "secondary": {
                "ok": deepseek_result["ok"],
                "latency_ms": deepseek_result.get("latency_ms", 0),
                "error": deepseek_result.get("error", ""),
                "model": deepseek_result.get("model", body.secondary.model or ""),
            },
        },
    )


# ========== DELETE / — 清除配置 ==========


@router.delete("")
@router.delete("/", include_in_schema=False)
async def reset_model_config(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """清除用户配置，恢复 .env 默认"""
    user_id = current_user["sub"]
    result = await db.execute(select(UserModelConfig).where(UserModelConfig.user_id == user_id))
    cfg = result.scalar_one_or_none()
    if cfg is not None:
        await db.delete(cfg)
        await db.commit()
    logger.info("model_config.reset", user_id=user_id)
    return ApiResponse(code=0, message="ok", data={"reset": True})
