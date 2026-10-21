"""R11 真实供应商 capability smoke（integration / 手工 opt-in）。

前置：4 个 key 已撤销/轮换并经打码核验；设置 R11_SMOKE=1 才运行。
输出只含 status/latency/model/dimension/error code，不含任何 key/原文/签名 URL。
embedding 实测维度 != 768 → 失败（fail closed，不写索引；按 NO-GO 处理）。
"""
from __future__ import annotations

import os

import pytest

from app.ppt_v2.adapters.embedding import V2_INDEX_DIMS
from app.ppt_v2.adapters.probe import run_all

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    not os.getenv("R11_SMOKE"),
    reason="真实 API smoke 需 R11_SMOKE=1 手工 opt-in（密钥撤销/轮换并打码核验后）",
)
async def test_capability_smoke_matrix() -> None:
    matrix = await run_all()
    for p in matrix:
        print(
            f"[R11] {p.name}: ok={p.ok} latency={p.latency_ms}ms "
            f"model={p.model} dim={p.dimension} :: {p.detail}"
        )
    assert len(matrix) == 8, "capability matrix 必须覆盖 8 项探测"

    emb = next(p for p in matrix if p.name == "embedding")
    if emb.ok:
        assert emb.dimension == V2_INDEX_DIMS == 768, "embedding 实测维度必须等于 768"
    else:
        pytest.fail(
            f"embedding 探测失败（{emb.error_code}）：真实维度未获实测量级 → "
            f"按 NO-GO 处理，禁止写 V2 索引。详情: {emb.detail}"
        )

    # 零秘密红线：matrix 不含 key 形态
    for p in matrix:
        assert "Bearer " not in p.detail and "sk-" not in p.detail, f"{p.name} 输出泄露秘密"
