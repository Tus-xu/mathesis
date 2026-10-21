"""RV-XF · 面向 API 的服务层（元信息 / 健康检查 / 带审计的运行入口）。

本层**不**读 config.py（保持与研究域解耦）：配置一律经 `models.load_settings()`
从环境变量读取；调用引擎在 `client`。审计日志只落 workflow / latency /
成功与否 / 上游 code，**不含**参数明文与任何凭证。
"""

from __future__ import annotations

import logging
from typing import Any

from . import client
from .models import (
    WORKFLOWS,
    XfError,
    XfResult,
    XfSettings,
    load_settings,
    mask_flow_id,
    mask_secret,
)

logger = logging.getLogger(__name__)


def _flow_configured(settings: XfSettings, env_key: str) -> bool:
    return bool(settings.flow_id(env_key))


def available_workflows(settings: XfSettings | None = None) -> list[dict[str, Any]]:
    """返回四个工作流的元信息（供前端功能栏渲染）。

    已配置的 flowId 做掩码展示（如 `7495…7648`）；未配置标 `configured: false`。
    **不回显** key/secret。
    """
    cfg = settings or load_settings()
    items: list[dict[str, Any]] = []
    for spec in WORKFLOWS:
        flow_id = cfg.flow_id(spec.env_key)
        items.append(
            {
                "key": spec.key,
                "title": spec.title,
                "description": spec.description,
                "requiredParameters": list(spec.required_parameters),
                "configured": bool(flow_id),
                "flowId": mask_flow_id(flow_id) if flow_id else None,
            }
        )
    return items


def health(settings: XfSettings | None = None) -> dict[str, Any]:
    """健康检查：开关 / base_url 是否配置 / appId（掩码）/ 各 flow 配置状态。

    **不得**回显 key/secret 原文。
    """
    cfg = settings or load_settings()
    return {
        "enabled": cfg.enabled,
        "baseUrlConfigured": bool(cfg.base_url.strip()),
        "appId": mask_secret(cfg.app_id),
        "credentialConfigured": cfg.has_credentials,
        "flows": {
            spec.key: ("configured" if _flow_configured(cfg, spec.env_key) else "missing")
            for spec in WORKFLOWS
        },
    }


async def run(
    workflow: str,
    parameters: dict[str, Any],
    uid: str | None = None,
) -> XfResult:
    """执行一次工作流调用，并落一条 INFO 级审计日志。

    Raises:
        XfError: 原样上抛（由 API 层映射 HTTP 状态码与稳定错误码）。
    """
    try:
        result = await client.run_workflow(workflow, parameters, uid=uid)
    except XfError as e:
        logger.info(
            "xf_workflow_failed workflow=%s code=%s http_status=%s retryable=%s",
            workflow,
            e.code,
            e.http_status,
            e.retryable,
        )
        raise
    logger.info(
        "xf_workflow_ok workflow=%s latency_ms=%s upstream_code=%s",
        result.workflow,
        result.latency_ms,
        result.upstream_code,
    )
    return result


__all__ = ["available_workflows", "health", "run"]
