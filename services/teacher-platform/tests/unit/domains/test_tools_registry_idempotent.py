# coding: utf-8
"""WP-E / E-1 回归：工具注册幂等（重复调用安全）+ 真冲突仍响亮失败。

修复前：register_tools() 第二次调用抛 ValueError('工具重复注册：quiz.search')，
导致 GET /butler/tools 在首次调用之后再无一次成功（P0）。
"""
from __future__ import annotations

import pytest

from app.domains import tools as t


@pytest.fixture(autouse=True)
def _isolate_registry():
    """_REGISTRY 是模块级全局：用例前后快照/还原，避免污染同进程其它用例。"""
    snapshot = dict(t._REGISTRY)
    yield
    t._REGISTRY.clear()
    t._REGISTRY.update(snapshot)


def test_register_tools_is_idempotent():
    first = t.register_tools()
    assert len(first) == 15

    # 修复前此处抛 ValueError: 工具重复注册：quiz.search
    second = t.register_tools()
    third = t.register_tools()

    assert [s["name"] for s in first] == [s["name"] for s in second] == [s["name"] for s in third]
    assert len(t.registry_items()) == 15
    assert len(t.specs()) == 15


def test_specs_and_registry_items_semantics_unchanged():
    """specs()/registry_items() 语义不变：空表时惰性注册，返回完整 15 项。"""
    t._REGISTRY.clear()
    assert t.registry_items() == {}
    assert len(t.specs()) == 15
    items = t.registry_items()
    assert set(items) == {s["name"] for s in t.specs()}
    assert all(callable(handler) for _, handler in items.values())
    # 已注册后 specs() 不再重复注册（仍 15 项，不抛）
    assert len(t.specs()) == 15


def test_reregister_identical_spec_and_handler_is_noop():
    """同一实现（同一 handler 对象 + 等值 spec）重复注册 → no-op，不抛。"""
    t.register_tools()
    spec, handler = t.registry_items()["quiz.search"]
    t.register(spec, handler)  # 不得抛
    assert t.registry_items()["quiz.search"] == (spec, handler)


def test_reregister_same_name_different_handler_still_raises():
    """真冲突：同名换实现 → 必须响亮失败，不得静默覆盖。"""
    t.register_tools()
    spec, _ = t.registry_items()["quiz.search"]

    async def _other_handler(ctx, params):  # noqa: ANN001, ANN202
        return {}

    with pytest.raises(ValueError, match="工具重复注册"):
        t.register(spec, _other_handler)
    # 原实现未被覆盖
    assert t.registry_items()["quiz.search"][1] is not _other_handler


def test_reregister_same_name_different_spec_still_raises():
    """真冲突：同名改 spec（如参数 schema / confirm_required 变化）→ 必须失败。"""
    t.register_tools()
    spec, handler = t.registry_items()["quiz.search"]
    tampered = dict(spec)
    tampered["confirm_required"] = not spec["confirm_required"]

    with pytest.raises(ValueError, match="工具重复注册"):
        t.register(tampered, handler)
    assert t.registry_items()["quiz.search"][0] == spec
