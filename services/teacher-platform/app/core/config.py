"""应用配置（pydantic-settings 读 .env）。

密钥只进 .env（gitignore）；.env.example 永远占位符（02-ARCHITECTURE §15）。
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 服务
    teacher_api_host: str = "0.0.0.0"
    teacher_api_port: int = 8100
    environment: str = "development"
    log_level: str = "INFO"

    # Postgres（teacher_platform 库）
    database_url: str = "postgresql+asyncpg://teacher:teacher_dev_pw@teacher-postgres:5433/teacher_platform"

    # Valkey（Streams + PubSub + TaskIQ broker）
    valkey_url: str = "redis://teacher-valkey:6380/0"

    # MinIO
    minio_endpoint: str = "teacher-minio:9002"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin_dev_pw"
    minio_secure: bool = False
    minio_public_base_url: str = "http://127.0.0.1:9002"

    # JWT（HS256）
    jwt_secret: str = "change-me-in-env"
    jwt_algorithm: str = "HS256"
    jwt_ttl_days: int = 7
    # 统一身份平台（services/api :8000）签发 JWT 的密钥，用于跨端登录 token 兼容
    legacy_jwt_secret: str = ""

    # 8000 通用 API 基址（R14 起：成员关系/任务下发经 Api8000Adapter 转发；env 可注入）
    api8000_base_url: str = "http://127.0.0.1:8000"

    # 8200 PPT Studio V2（T24：deck 生成/内容取回；内网 only，token 仅从 env 注入）
    ppt_studio_base_url: str = "http://127.0.0.1:8200"
    ppt_studio_internal_token: str = ""
    # 8201 Presenton adapter（T24：逐页渲染 / PPTX→PDF）
    presenton_base_url: str = "http://127.0.0.1:8201"
    presenton_service_token: str = ""
    # T24 下载票据默认有效期（秒）与导出作业预算
    artifact_ticket_ttl_seconds: int = 300
    export_job_timeout_seconds: int = 900

    # 推理 sidecar（B4/B1 提供；未就绪不阻塞骨架）
    pix2text_url: str = "http://teacher-pix2text:8103"
    funasr_url: str = "http://teacher-funasr:8104"
    katex_url: str = "http://teacher-katex:8105"

    # LLM（B1 维护，占位）
    llm_default_model: str = "deepseek/deepseek-chat"
    llm_vlm_model: str = "qwen/qwen-vl-max"

    # 幂等窗口（秒）
    idempotency_window_seconds: int = 900

    # SSE
    sse_heartbeat_seconds: int = 15
    sse_stream_maxlen: int = 2000

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
