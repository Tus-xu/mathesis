"""ToolSpec 工具协议（B1 独占；B4 只在 app/domains/*/tools.py 写实现——§8/§9）。

定稿给 B4 的契约：
- ToolSpec：name/label/kind('read'|'write'|'frontend')/confirm_required/description/
  params_schema（Pydantic v2 模型）。
- write 工具 confirm_required 恒为 True 且不可关闭（红线 1：AI 直写零旁路）；
  真正执行只发生在「教师批准 → 图节点 resume → invoke_tool()」链路上。
- frontend 工具只产前端动作（Action Registry 执行），后端不代劳。
- B4 注册方式：在各自域模块内 `register_tool(spec, handler)`；协议/注册表归 B1，
  实现归 B4，禁止复制本文件。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, Protocol

from pydantic import BaseModel, ValidationError

from app.llm.ai_runs import digest_payload, redact_error

ToolKind = Literal["read", "write", "frontend"]

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class TeacherCtx(dict):
    """工具执行上下文（来自会话现场；selection 存在时传给工具层）。"""

    @property
    def teacher_id(self) -> str | None:
        return self.get("teacher_id")

    @property
    def session_id(self) -> str | None:
        return self.get("session_id")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    kind: ToolKind
    description: str
    params_schema: type[BaseModel]
    confirm_required: bool = False
    domain: str = ""  # B4 的域目录名，如 "quiz"

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ValueError(f"工具名 {self.name!r} 不合法（^[a-z][a-z0-9_]{{2,63}}$）")
        if not self.description.strip():
            raise ValueError(f"工具 {self.name} 缺 description（要进 FC schema）")
        if not (isinstance(self.params_schema, type) and issubclass(self.params_schema, BaseModel)):
            raise ValueError(f"工具 {self.name} 的 params_schema 必须是 Pydantic v2 模型类")
        if self.kind == "write" and not self.confirm_required:
            raise ValueError(f"write 工具 {self.name} 的 confirm_required 不可为 False（红线 1）")


ToolHandler = Callable[[BaseModel, TeacherCtx], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


class ToolRegistry:
    """进程内注册表。协议归 B1；B4 在启动时（app/domains/*/tools.py）注册实现。"""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具 {spec.name} 重复注册（协议独占，B4 勿复制 protocol）")
        self._tools[spec.name] = RegisteredTool(spec=spec, handler=handler)

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def require(self, name: str) -> RegisteredTool:
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"工具 {name!r} 未注册")
        return tool

    def list_specs(self, *, kind: ToolKind | None = None) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values() if kind is None or t.spec.kind == kind]

    def fc_schema(self) -> list[dict[str, Any]]:
        """Function-Calling 用 schema（只暴露 read/write；frontend 工具不进 FC）。"""
        out = []
        for spec in self.list_specs(kind="read") + self.list_specs(kind="write"):
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.params_schema.model_json_schema(),
                    },
                }
            )
        return out


class ToolDenied(RuntimeError):
    """write 工具未经确认流直接调用——红线 1 触发。"""


_default_registry = ToolRegistry()
# R11.5-B：工具调用审计 sink（None 表示未注入 → 不记录，normal 运行由但ler 域注入）
_tool_call_sink: Any = None


def set_tool_call_sink(sink: Any) -> None:
    global _tool_call_sink
    _tool_call_sink = sink


def get_tool_call_sink() -> Any:
    return _tool_call_sink


def register_tool(spec: ToolSpec, handler: ToolHandler, *, registry: ToolRegistry | None = None) -> None:
    (registry or _default_registry).register(spec, handler)


async def invoke_tool(
    name: str,
    params: dict[str, Any],
    ctx: TeacherCtx,
    *,
    confirmed: bool = False,
    registry: ToolRegistry | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """工具执行唯一入口。

    confirmed=True 表示本调用来自确认流的 resume 分支（图节点在 interrupt 恢复
    后调用）；write 工具 confirmed=False 到达此处即红线 1 违规，直接拒绝。
    R11.5-B：注入 tool_call_sink 后每次调用落 ToolCallRecord（参数 hash / 脱敏 /
    状态 / 延迟 / 错误）。
    """
    tool = (registry or _default_registry).require(name)
    started = time.monotonic()
    status = "ok"
    error: str | None = None
    try:
        if tool.spec.kind == "write":
            if not confirmed:
                raise ToolDenied(f"write 工具 {name} 必须经确认卡批准后执行（红线 1：AI 直写零旁路）")
        try:
            parsed = tool.spec.params_schema.model_validate(params)
        except ValidationError as exc:
            status = "param_invalid"
            error = f"参数校验失败：{exc.errors()[:3]}"
            return {"ok": False, "error": error}
        return await tool.handler(parsed, ctx)
    except ToolDenied:
        status = "denied"
        error = "write 工具未经确认流直接调用"
        raise
    except Exception as exc:
        status = "error"
        error = repr(exc)
        raise
    finally:
        latency = int((time.monotonic() - started) * 1000)
        sink = _tool_call_sink
        if sink is not None:
            try:
                await sink.write(
                    _tool_call_record_for(name, tool.spec, params, ctx, status=status, error=error, latency_ms=latency, run_id=run_id)
                )
            except Exception:  # noqa: BLE001 —— 审计失败不拖垮工具执行（但大声留痕）
                import logging

                logging.getLogger(__name__).exception("tool_call_records 写入失败（tool=%s）", name)


def _tool_call_record_for(name: str, spec: "ToolSpec", params: dict[str, Any], ctx: TeacherCtx, *, status: str, error: str | None, latency_ms: int, run_id: str | None) -> Any:
    from app.agent.tool_calls import ToolCallRecord

    ctx_teacher = ctx.get("teacher_id") if isinstance(ctx, dict) else None
    ctx_session = ctx.get("session_id") if isinstance(ctx, dict) else None
    return ToolCallRecord(
        tool_name=name,
        tool_kind=spec.kind,
        params_hash=digest_payload(params),
        params_redacted=_redact_params(params),
        status=status,
        error=redact_error(error),
        latency_ms=latency_ms,
        teacher_id=str(ctx_teacher) if ctx_teacher else None,
        session_id=str(ctx_session) if ctx_session else None,
        run_id=run_id,
    )


_SENSITIVE_KEYS = ("password", "token", "api_key", "secret", "authorization", "key", "appkey")


def _redact_params(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in (params or {}).items():
        if any(s in k.lower() for s in _SENSITIVE_KEYS):
            out[k] = "<redacted>"
        elif isinstance(v, dict):
            out[k] = _redact_params(v)
        elif isinstance(v, list):
            out[k] = [_redact_params(i) if isinstance(i, dict) else i for i in v]
        else:
            out[k] = v
    return out
