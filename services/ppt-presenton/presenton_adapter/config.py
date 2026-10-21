"""8201 adapter 配置：默认强制 loopback 绑定（浏览器/前端不可直连）。"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    # loopback-only：拒绝非回环绑定（DECISIONS §2：8201 只允许 8200 服务端调用）
    host: str = "127.0.0.1"
    port: int = 8201
    # 服务级令牌（8200 出站携带；常量时间比较）
    service_token: str = "presenton-service-token-dev"
    # 仅开发允许注入故障（unit/e2e 用）；生产必须关闭
    simulate_allowed: bool = True
    # R21 引擎：内容寻址产物根目录 + 预算
    engine_data_dir: str = "./data/engine"
    max_artifact_bytes: int = 50 * 1024 * 1024
    page_render_timeout_s: float = 120.0
    assemble_timeout_s: float = 420.0
    # T24：PPTX → PDF（PowerPoint COM SaveAs 32）超时预算
    export_pdf_timeout_s: float = 180.0

    def validate_binding(self) -> None:
        if self.host not in LOOPBACK_HOSTS:
            raise ValueError(f"8201 禁止绑定非回环地址 {self.host!r}；只允许 {LOOPBACK_HOSTS}")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.validate_binding()
    return s
