# coding: utf-8
"""B4 管家工具集注册与分发（M2-C · Gate 3 支撑，架构 §8「B4 提供」契约）。

ToolSpec 协议本应 import B1 的 app/agent/tools/protocol.py——B1 未落地前以同构
TypedDict 定义（字段与架构 §8 冻结签名逐字一致：name/label/kind/confirm_required/
description/params_schema），B1 落地后改为 from app.agent.tools.protocol import
ToolSpec（登记 IFC-005，import 面唯一变化）。/butler/tools 聚合端点归 B1，
B4 经 register_tools() 供给。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession


class ToolSpec(TypedDict):
    name: str
    label: str
    kind: Literal['read', 'write', 'frontend']
    confirm_required: bool
    description: str
    params_schema: dict[str, Any]


@dataclass(frozen=True)
class TeacherCtx:
    """工具执行上下文（会话内教师身份 + 注入的 DB 会话）。"""

    session: AsyncSession
    user_id: UUID
    school_id: UUID | None = None


Handler = Callable[[TeacherCtx, dict[str, Any]], Awaitable[dict[str, Any]]]

_REGISTRY: dict[str, tuple[ToolSpec, Handler]] = {}


def register(spec: ToolSpec, handler: Handler) -> None:
    """幂等注册：同名 + 同一份 spec 与同一 handler 视为重复调用（no-op）。

    重复调用必须安全——register_tools() 会被 _build_registry() 与 /butler/tools 反复触发。
    但「同名不同实现」仍是真冲突，必须响亮失败，不得静默覆盖：
    - spec 按值比较（TypedDict → dict 等值，字段/参数 schema 任一变化即不等）；
    - handler 按对象同一性比较（函数对象不可比较语义，is 才能区分「同一实现」与「换了实现」）。
    """
    existing = _REGISTRY.get(spec['name'])
    if existing is not None:
        prev_spec, prev_handler = existing
        if prev_handler is handler and prev_spec == spec:
            return  # 同一实现的重复注册：安全 no-op
        raise ValueError(f'工具重复注册（同名不同实现）：{spec["name"]}')
    _REGISTRY[spec['name']] = (spec, handler)


def register_tools() -> list[ToolSpec]:
    """聚合全部 B4 域工具（/butler/tools 数据面；与 mock 响应同构）。"""
    from app.domains.catalog.tools import TOOLS as catalog_tools  # noqa: PLC0415
    from app.domains.classroom.tools import TOOLS as classroom_tools  # noqa: PLC0415
    from app.domains.contents.tools import TOOLS as contents_tools  # noqa: PLC0415
    from app.domains.figures.tools import TOOLS as figure_tools  # noqa: PLC0415
    from app.domains.grading.tools import TOOLS as grading_tools  # noqa: PLC0415

    for spec, handler in [*catalog_tools, *contents_tools, *figure_tools, *grading_tools, *classroom_tools]:
        register(spec, handler)
    return [spec for spec, _ in _REGISTRY.values()]


def registry_items() -> dict[str, tuple[ToolSpec, Handler]]:
    """注册表只读视图（rescue 整合：butler 路由桥接 B4 工具到 B1 ToolRegistry 用）。"""
    return dict(_REGISTRY)


def specs() -> list[ToolSpec]:
    if not _REGISTRY:
        register_tools()
    return [spec for spec, _ in _REGISTRY.values()]


async def invoke_tool(name: str, params: dict[str, Any], ctx: TeacherCtx) -> dict[str, Any]:
    """工具执行入口（写操作由 B1 interrupt/resume 确认流先行；此处假设已获教师确认）。"""
    if not _REGISTRY:
        register_tools()
    if name not in _REGISTRY:
        raise KeyError(f'未知工具：{name}')
    _, handler = _REGISTRY[name]
    return await handler(ctx, params or {})
