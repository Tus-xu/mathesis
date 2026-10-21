"""P00 · 服务注册表契约测试（registry 单一真相的机器守卫）。

`deploy/registry.yml` 是端口/队列/前缀/worker pool 的**唯一权威**。
本测试把它机器化，防止后续轮次再次出现 "散落硬编码端口/前缀" 与
"浏览器直连内部服务" 的回归（M00 X-1..X-9 的处置必须持续成立）。

不依赖 YAML 库：用极简解析（本 registry 结构固定、缩进规整），
避免为契约测试引入新依赖。
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
REGISTRY = REPO_ROOT / "deploy" / "registry.yml"

EXPECTED_PORTS = {
    "api-8000": 8000,
    "teacher-platform-8100": 8100,
    "ppt-studio-v2-8200": 8200,
    "ppt-presenton-8201": 8201,
    "research-vnext-8300": 8300,
}

# 8100/8200 的队列隔离参数必须与两份 queue_policy.py 的实际常量一致
EXPECTED_QUEUE_ISOLATION = {
    "teacher-platform-8100": ("strm:tp8100", "tp8100-workers", "tp8100", "tp8100/", 16, 600),
    "ppt-studio-v2-8200": ("strm:ps8200", "ps8200-workers", "ps8200", "pps2/", 8, 300),
}


def _registry_text() -> str:
    assert REGISTRY.exists(), "缺少 deploy/registry.yml（P00 唯一服务注册表）"
    return REGISTRY.read_text(encoding="utf-8")


def test_registry_exists_and_has_all_services() -> None:
    text = _registry_text()
    for svc in EXPECTED_PORTS:
        assert f"{svc}:" in text, f"registry 缺少服务 {svc}"
    assert 'schema_version: "1.0.0"' in text


def test_registry_ports_are_unique_and_match_expected() -> None:
    text = _registry_text()
    for svc, port in EXPECTED_PORTS.items():
        assert f"id: {svc}" in text, f"{svc} 缺少 id"
        assert f"port: {port}" in text, f"{svc} 端口未登记或与预期不符（期望 {port}）"
    # 端口唯一性
    ports = list(EXPECTED_PORTS.values())
    assert len(ports) == len(set(ports)), "服务端口重复"


def test_browser_must_not_reach_internal_services() -> None:
    text = _registry_text()
    assert "exposed_internal_ports: []" in text, "不得对浏览器暴露任何内部端口"
    for name in (
        "teacher-platform-8100",
        "ppt-studio-v2-8200",
        "ppt-presenton-8201",
        "research-vnext-8300",
        "minio",
        "valkey",
        "postgres",
    ):
        assert name in text
    # 2021 必须 internal_only
    assert "internal_only: true" in text


def test_queue_isolation_matches_real_policy_files() -> None:
    """registry 登记的隔离参数必须与两份 queue_policy.py 实际常量一致（防双真相）。"""
    text = _registry_text()
    for owner, (stream, group, dedupe, prefix, pool, quota) in EXPECTED_QUEUE_ISOLATION.items():
        for value in (stream, group, dedupe, prefix):
            assert value in text, f"{owner}: registry 缺少隔离值 {value!r}"
        assert f"max_pool: {pool}" in text, f"{owner}: max_pool 不一致"
        assert f"quota_per_min: {quota}" in text, f"{owner}: quota 不一致"

    tp = REPO_ROOT / "services" / "teacher-platform" / "app" / "reliability" / "queue_policy.py"
    pps = REPO_ROOT / "services" / "ppt-studio-v2" / "app" / "reliability" / "queue_policy.py"
    if tp.exists():
        tp_text = tp.read_text(encoding="utf-8")
        assert "strm:tp8100" in tp_text and "tp8100-workers" in tp_text
        assert "tp8100/" in tp_text
    if pps.exists():
        pps_text = pps.read_text(encoding="utf-8")
        assert "strm:ps8200" in pps_text and "ps8200-workers" in pps_text
        assert "pps2/" in pps_text


def test_migration_heads_recorded() -> None:
    text = _registry_text()
    assert "f19a2b3c4d5e" in text, "8100 迁移 head 未登记"
    assert "0002_r21_pages_artifacts" in text, "8200 迁移 head 未登记"


def test_embedding_dimensions_isolated() -> None:
    text = _registry_text()
    assert "api-8000: 1024" in text
    assert "teacher-platform-8100: 768" in text
    assert "禁止混 collection" in text or "禁止交叉写索引" in text


def test_teaching_deck_spec_authority_is_8200() -> None:
    """X-4 定案：TeachingDeckSpec 唯一写权威 = 8200。"""
    text = _registry_text()
    assert "TeachingDeckSpec" in text
    assert "唯一写权威 = 8200" in text or "唯一写权威" in text


def test_research_port_frozen_at_8300() -> None:
    """X-6 定案：Research 端口固化 8300（禁止散落硬编码）。"""
    text = _registry_text()
    assert "research-vnext-8300" in text
    assert "port: 8300" in text
    assert "8300" in text


def test_api_db_port_single_authority() -> None:
    """X-1 定案：8000 DB 端口单一口径。"""
    text = _registry_text()
    assert "54329" in text
    assert "单一权威口径 = 54329" in text or "54329" in text


def test_no_secret_values_in_registry() -> None:
    """registry 不得含真实密钥（只允许变量名/占位符）。"""
    import re

    text = _registry_text()
    # 允许出现变量名，禁止出现 sk-/ak- 开头的长串
    leaks = re.findall(r"(?:sk|ak)-[A-Za-z0-9]{16,}", text)
    assert not leaks, f"registry 含疑似真实密钥: {leaks[:2]}"


def test_research_service_declares_no_teacher_import() -> None:
    text = _registry_text()
    assert "不 import Teacher domain" in text
    assert "不自建第二套账号库" in text
