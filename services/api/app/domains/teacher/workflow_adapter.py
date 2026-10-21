"""M3 教师端星辰工作流适配器。

管理配置来自 ``system_configs['workflows']``；只有启用、配置完整且已验证的
工作流才可远程执行。输入/输出映射在此隔离，原始上游错误与响应不直传前端。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_config import get_system_config
from app.providers import xingchen

WORKFLOWS = {
    "adapt_lesson": "wf_lesson_plan",
    "create_slides": "wf_ai_ppt",
    "create_quiz": "wf_smart_quiz",
    "suggest_grade": "wf_solution_pregrade",
    "explain_problem": "wf_explainer_script",
    "preprocess_course": "wf_course_preprocess",
    "understand_document": "wf_doc_understand",
}

ERR_DISABLED = "workflow_disabled"
ERR_UNCONFIGURED = "workflow_unconfigured"
ERR_UNVERIFIED = "workflow_unverified"
ERR_TIMEOUT = "workflow_timeout"
ERR_RATE_LIMITED = "workflow_rate_limited"
ERR_SCHEMA_INVALID = "workflow_schema_invalid"
ERR_UPSTREAM = "workflow_upstream_error"

_SAFE_RETRY_CAPABILITIES = frozenset(
    {"adapt_lesson", "create_quiz", "suggest_grade", "explain_problem", "understand_document"}
)


class WorkflowResult(dict):
    """规范化返回：status/content/workflow/warnings/error_code/trace。"""


@dataclass(frozen=True)
class _Runtime:
    flow_name: str
    config: xingchen.XingchenConfig
    entry: dict[str, Any]


def _degraded(capability: str, error_code: str) -> WorkflowResult:
    return WorkflowResult(
        status="degraded",
        workflow=WORKFLOWS.get(capability),
        content={},
        warnings=[f"workflow unavailable: {error_code}"],
        error_code=error_code,
    )


async def _resolve_runtime(
    capability: str,
    db: AsyncSession,
    teacher_id: str | None,
) -> tuple[_Runtime | None, str | None]:
    flow_name = WORKFLOWS.get(capability)
    if flow_name is None:
        return None, ERR_UNCONFIGURED
    cfg = await xingchen.resolve_effective_xingchen_config(db, teacher_id)
    if not cfg.enabled:
        return None, ERR_DISABLED

    workflows = await get_system_config(db, "workflows", default={})
    entry = workflows.get(flow_name) if isinstance(workflows, dict) else None
    if not isinstance(entry, dict):
        return None, ERR_UNCONFIGURED
    if entry.get("enabled", True) is not True:
        return None, ERR_DISABLED
    if entry.get("last_test_status") != "success":
        return None, ERR_UNVERIFIED

    flow_config = cfg.flow_ids.get(flow_name)
    flow_id = flow_config.get("flow_id") if isinstance(flow_config, dict) else flow_config
    flow_key = flow_config.get("api_key") if isinstance(flow_config, dict) else cfg.api_key
    flow_secret = flow_config.get("api_secret") if isinstance(flow_config, dict) else cfg.api_secret
    if not (flow_id and flow_key and flow_secret):
        return None, ERR_UNCONFIGURED
    return _Runtime(flow_name=flow_name, config=cfg, entry=entry), None


async def workflow_available(
    capability: str,
    db: AsyncSession,
    teacher_id: str | None = None,
) -> tuple[bool, str | None]:
    """返回教师工作流是否达到 available=true 的完整条件。"""
    runtime, error = await _resolve_runtime(capability, db, teacher_id)
    return runtime is not None, error


def map_input(capability: str, internal: dict[str, Any]) -> dict[str, Any]:
    """默认内部输入映射；数据库映射在执行时覆盖字段名。"""
    return dict(internal.get("payload") or {})


def _rename_fields(value: dict[str, Any], mapping: Any) -> dict[str, Any]:
    if not isinstance(mapping, dict):
        return dict(value)
    return {str(mapping.get(key) or key): item for key, item in value.items()}


async def run(
    capability: str,
    internal: dict[str, Any],
    *,
    db: AsyncSession,
) -> WorkflowResult:
    """按数据库有效配置执行工作流，失败统一降级且不泄漏上游细节。"""
    teacher_id = str(internal.get("teacher_id") or "") or None
    runtime, error = await _resolve_runtime(capability, db, teacher_id)
    if runtime is None:
        return _degraded(capability, error or ERR_UNCONFIGURED)

    parameters = _rename_fields(
        map_input(capability, internal), runtime.entry.get("input_mapping")
    )
    timeout = runtime.entry.get("timeout_seconds", runtime.entry.get("timeout"))
    try:
        read_timeout = float(timeout) if timeout is not None else xingchen._resolve_timeout(
            runtime.flow_name, runtime.config
        )
    except (TypeError, ValueError):
        read_timeout = xingchen._resolve_timeout(runtime.flow_name, runtime.config)

    retry_count = runtime.entry.get("retry_count", 0)
    retry_count = retry_count if isinstance(retry_count, int) and not isinstance(retry_count, bool) else 0
    retries = min(max(retry_count, 0), 1) if capability in _SAFE_RETRY_CAPABILITIES else 0

    last_error = ERR_UPSTREAM
    for attempt in range(retries + 1):
        try:
            output = await xingchen.run_workflow(
                runtime.flow_name,
                uid=teacher_id or "teacher",
                parameters=parameters,
                read_timeout=read_timeout,
                config=runtime.config,
            )
            content = output.get("data", output) if isinstance(output, dict) else None
            if not isinstance(content, dict):
                return _degraded(capability, ERR_SCHEMA_INVALID)
            content = _rename_fields(content, runtime.entry.get("output_mapping"))
            return WorkflowResult(
                status="succeeded",
                workflow=runtime.flow_name,
                content=content,
                warnings=[],
                provider_trace_id=output.get("trace_id"),
                engine="xingchen",
                attempts=attempt + 1,
            )
        except Exception as exc:  # noqa: BLE001 - 只输出稳定错误码
            last_error = _classify_error(exc)
            if attempt >= retries:
                break
    return _degraded(capability, last_error)


def _classify_error(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "timeout" in name or "timeout" in message or "timed out" in message:
        return ERR_TIMEOUT
    if "rate" in name or "rate" in message or "限流" in message:
        return ERR_RATE_LIMITED
    if "schema" in name or "validation" in name:
        return ERR_SCHEMA_INVALID
    return ERR_UPSTREAM


__all__ = ["WORKFLOWS", "WorkflowResult", "run", "workflow_available", "map_input"]
