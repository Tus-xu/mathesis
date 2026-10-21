"""VisionAdapter 单元测试（R11；FakeBackend 不触网）。"""
from __future__ import annotations

import pytest

from app.ppt_v2.adapters.ai import AIPlatformAdapter
from app.ppt_v2.adapters.errors import AdapterFatalError
from app.ppt_v2.adapters.vision import VisionAdapter
from tests.unit.ppt_v2.adapters.test_ai_adapter import _FakeBackend


def _vision(backend: _FakeBackend) -> VisionAdapter:
    ai = AIPlatformAdapter(backend=backend, text_key="k", vision_key="k")
    return VisionAdapter(ai=ai)


async def test_understand_parses_structured() -> None:
    backend = _FakeBackend([
        '{"purpose": "展示直角三角形三边关系", "objects": ["三角形ABC"], "relations": ["AB垂直AC"]}'
    ])
    va = _vision(backend)
    res = await va.understand("data:image/png;base64,ABC")
    assert res.purpose == "展示直角三角形三边关系"
    assert res.objects == ["三角形ABC"]
    assert res.relations == ["AB垂直AC"]
    assert res.model == "xophunyuanocr"


async def test_understand_invalid_json_fails_closed() -> None:
    backend = _FakeBackend(["这不是 JSON"])
    va = _vision(backend)
    with pytest.raises(AdapterFatalError, match="结构化"):
        await va.understand("data:image/png;base64,ABC")


async def test_ocr_opt_in_returns_draft() -> None:
    backend = _FakeBackend(["直角三角形 ABC\n∠A = 90°"])
    va = _vision(backend)
    draft = await va.ocr_opt_in("data:image/png;base64,ABC")
    assert draft.text == "直角三角形 ABC\n∠A = 90°"
    assert draft.is_draft is True
    assert draft.model == "xophunyuanocr"
