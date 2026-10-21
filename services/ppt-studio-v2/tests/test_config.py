"""配置隔离：8200 默认端口/独立库/独立 8201 基址；与 8100 无耦合。"""
from __future__ import annotations

from app.config import Settings, get_settings


def test_defaults_isolated() -> None:
    s = Settings(_env_file=None)
    assert s.port == 8200
    assert s.host == "0.0.0.0"
    assert "ppt_studio_v2" in s.database_url
    assert "teacher_platform" not in s.database_url
    assert s.presenton_base_url == "http://127.0.0.1:8201"
    assert s.worker_pool == 8


def test_env_override() -> None:
    s = Settings(_env_file=None, port=9999, worker_pool=4)
    assert s.port == 9999
    assert s.worker_pool == 4


def test_get_settings_singleton() -> None:
    assert get_settings() is get_settings()
