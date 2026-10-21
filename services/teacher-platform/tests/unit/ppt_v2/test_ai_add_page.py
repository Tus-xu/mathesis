"""R4 AI 添加页服务测试：真实 AI 产出内容 → add_slide + move_slide 提案（不触真 provider）。

验收锚点（ACCEPTANCE-MATRIX 4.6 的服务层）：
- 新页内容来自 provider 真实返回（fake adapter），禁止空白假页（空内容 → 503）；
- 新页插在锚点页之后（move_slide 组合提案），只新增一页；
- 提案阶段不改事实（INV-4）：row.revision 与 slides 不变，需显式 apply。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app.core.errors import ApiError
from app.ppt_v2.persistence.models import PptV2EditProposal
from app.ppt_v2.service import pages as pages_service
from app.ppt_v2.service import spec_edit

from .test_t24_spec_edit import _regen_refs, _seed_deck_with_projection


class _Fake:
    async def chat(self, messages, *, use_case="chat", temperature=None, model=None):  # noqa: ANN001
        from app.ppt_v2.adapters.dto import ChatReply

        assert use_case == "ppt_regen"
        return ChatReply(
            text=json.dumps(
                {"title": "椭圆的离心率", "content": "离心率 $e=\\frac{c}{a}$ 刻画椭圆的扁平程度\\n当 e 越接近 1，椭圆越扁",
                 "teaching_goal": "理解离心率对椭圆形状的刻画"},
                ensure_ascii=False,
            ),
            model="fake-model",
        )


async def test_add_page_proposes_ai_content_after_anchor(db) -> None:
    row = await _seed_deck_with_projection(db)
    before_ids = [s["id"] for s in row.spec["slides"]]

    out = await pages_service.add_generated_page(
        db, str(row.id), expected_revision=3, after_slide_id="s2",
        instruction="围绕本环节补一页离心率", refs=_regen_refs(), actor_id="teacher-a", adapter=_Fake(),
    )
    assert out["status"] == "proposed"
    new_id = out["added_slide_id"]
    assert new_id not in before_ids
    ops = out["operations"]
    assert ops[0]["op"] == "add_slide" and ops[0]["target"] == new_id
    assert ops[1] == {"op": "move_slide", "target": new_id, "value": {"index": 2}}
    value = ops[0]["value"]
    assert value["title"] == "椭圆的离心率"
    assert "离心率" in value["content"] and value["content"].strip() != ""
    # 提案阶段不改事实
    assert row.revision == 3
    assert [s["id"] for s in row.spec["slides"]] == before_ids

    # apply 后新页落在锚点后一位
    applied = await spec_edit.apply_proposal_by_id(db, str(row.id), out["patch_id"], expected_revision=3)
    assert applied["revision"] == 4
    after_ids = [s["id"] for s in row.spec["slides"]]
    assert len(after_ids) == len(before_ids) + 1
    assert after_ids[2] == new_id
    assert after_ids[1] == "s2"
    assert len((await db.execute(select(PptV2EditProposal))).all()) == 1


async def test_add_page_rejects_empty_provider_output(db) -> None:
    row = await _seed_deck_with_projection(db)
    before_count = len(row.spec["slides"])

    class _Empty:
        async def chat(self, messages, *, use_case="chat", temperature=None, model=None):  # noqa: ANN001
            from app.ppt_v2.adapters.dto import ChatReply

            return ChatReply(text=json.dumps({"title": "x", "content": ""}), model="fake-model")

    with pytest.raises(ApiError) as exc:
        await pages_service.add_generated_page(
            db, str(row.id), expected_revision=3, after_slide_id="s2",
            instruction="x", refs=_regen_refs(), adapter=_Empty(),
        )
    assert exc.value.status_code == 503
    assert row.revision == 3
    assert len(row.spec["slides"]) == before_count  # 禁止空白假页：什么都没落库
