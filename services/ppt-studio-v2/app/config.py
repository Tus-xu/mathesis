"""应用配置（pydantic-settings 读 .env）。

与 8100 teacher-platform 完全独立：字段名自带 ppt_studio 前缀语义，
密钥只进 .env（.env.example 全占位符）。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 服务
    environment: str = "development"
    log_level: str = "INFO"

    # 监听（8200 是面向网关注入的独立服务；开发直连 127.0.0.1）
    host: str = "0.0.0.0"
    port: int = 8200

    # 独立 Postgres：ppt_studio_v2 库（与 teacher_platform 库物理隔离）
    database_url: str = "postgresql+asyncpg://teacher:teacher_dev_pw@127.0.0.1:5433/ppt_studio_v2"

    # 独立 Valkey（Streams）：8200 owner 命名空间
    valkey_url: str = "redis://127.0.0.1:6380/0"

    # 仪表鉴权：教师 JWT（HS256；密钥仅环境变量）
    jwt_secret: str = "change-me-in-env"
    jwt_algorithm: str = "HS256"
    jwt_ttl_seconds: int = 7 * 24 * 3600
    # 内部服务令牌（局点/测试调用；公网环境应关闭 internal_token 模式）
    internal_token: str = ""

    # 8201 Presenton adapter（私有引擎，仅 8200 服务端调用）
    presenton_base_url: str = "http://127.0.0.1:8201"
    presenton_service_token: str = "presenton-service-token-dev"

    # 8300？ 无。

    # 队列/并发预算（owner ppt_studio_v2，R11.5-C 注册值，见 app/reliability/queue_policy.py）
    worker_pool: int = 8
    outbox_batch: int = 20
    dispatch_interval_s: float = 1.0
    lease_seconds: int = 60
    backoff_base_s: float = 1.0
    backoff_max_s: float = 30.0

    # R21 生成预算
    min_deck_pages: int = 8
    max_deck_pages: int = 12
    max_artifact_bytes: int = 50 * 1024 * 1024
    page_render_timeout_s: float = 150.0
    assemble_timeout_s: float = 480.0
    # 本地 artifact 存储（对象存储/MinIO 接线留后续轮；object_prefix 仍为 pps2/）
    artifact_root: str = "./data/artifacts"

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
