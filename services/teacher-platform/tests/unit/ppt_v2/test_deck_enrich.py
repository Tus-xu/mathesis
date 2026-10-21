"""课件逐页增稠（deck enrich）单测：真实 LLM 路径 + 确定性兜底（不触真 provider）。

验收锚点：
- 每页 ≥3 个结构化元素、≥2 类 role（密度门）；
- 教师动作 / 教研批注文本（「引导学生…」）不得进课件正文；
- 增稠走 T24 事实链写回新 revision（提案 source=regen → apply），封面页不动；
- 单页失败 → 确定性兜底 / 保留骨架页标 draft，**绝不整单失败**；
- 跨页逐字重复（QA cross_page_duplicate p0 口径）在汇总层首现保留消除；
- 单页 SDK 重试 = 1（R7 误伤修复，R22 恢复）。
"""

from __future__ import annotations

import re

from app.ppt_v2.domain.spec import TeachingDeckSpec
from app.ppt_v2.service import enrich as enrich_service
from app.ppt_v2.service.enrich import _LlmElement, _LlmSlide

from .test_t24_spec_edit import _seed_deck_with_projection

# fake 按 prompt 里的「本页位置：第 N/M 页」出页序化 payload（gather 并发下确定）
_ORDINAL_RE = re.compile(r"本页位置：第 (\d+)/")


class _FakeAdapter:
    """返回固定结构化结果的合规通道 fake（断言必须走 deck_enrich 用例）。

    - payload：所有页同一结果（适合单页/去重场景）；
    - per_ordinal：按页序生成结果（多页场景用不同 payload，模拟真实 LLM 逐页产出）。
    """

    def __init__(
        self,
        payload: object | None = None,
        *,
        raises: Exception | None = None,
        per_ordinal=None,  # noqa: ANN001 - (ordinal: int) -> _LlmSlide
    ) -> None:
        self.payload = payload
        self.raises = raises
        self.per_ordinal = per_ordinal
        self.calls = 0
        self.last_max_retries: int | None = None

    async def structured(self, model, *, system, user, temperature=0.2, use_case="chat", max_retries=2):  # noqa: ANN001
        self.calls += 1
        self.last_max_retries = max_retries
        assert use_case == "deck_enrich"
        if self.raises is not None:
            raise self.raises
        if self.per_ordinal is not None:
            m = _ORDINAL_RE.search(user)
            return self.per_ordinal(int(m.group(1)) if m else 1)
        return self.payload


def _payload(ordinal: int = 1) -> _LlmSlide:
    """按页序产出不同正文（真实 LLM 逐页扩写形态；跨页天然无重复）。"""
    return _LlmSlide(
        elements=[
            _LlmElement(role="definition", text=f"把研究对象统称为元素（第 {ordinal} 页视角），组成的总体叫做集合。"),
            _LlmElement(role="formula", latex=rf"A_{{{ordinal}}}=\{{x \mid x \in \mathbb{{Z}},\ -2\le x<3\}}"),
            _LlmElement(role="bullets", items=[f"第 {ordinal} 页：元素具有确定性", f"第 {ordinal} 页：元素具有互异性",
                                               f"第 {ordinal} 页：元素具有无序性"]),
            # 教师动作：应被丢弃，不得进正文
            _LlmElement(role="note", text="引导学生讨论集合的例子"),
            # 越界 role：应被丢弃
            _LlmElement(role="banner", text="横幅"),
        ]
    )


async def _spec_of(row) -> TeachingDeckSpec:
    from app.ppt_v2.service import spec_edit

    return spec_edit.load_spec(row)


async def test_enrich_pages_reach_density_and_drop_teacher_notes(db) -> None:
    row = await _seed_deck_with_projection(db)
    spec = await _spec_of(row)
    adapter = _FakeAdapter(per_ordinal=_payload)

    outcome = await enrich_service.enrich_deck_spec(spec, adapter=adapter)

    assert adapter.calls == len(spec.slides) - 1  # 封面页不调用 LLM
    assert len(outcome.pages) == len(spec.slides) - 1
    for page in outcome.pages:
        assert page.ok, f"{page.slide_id} 未达密度门: {page.source} {page.error}"
        assert len(page.elements) >= 3
        assert len({e["role"] for e in page.elements}) >= 2
        blob = "\n".join(
            [str(e.get("text") or "") for e in page.elements]
            + [i for e in page.elements for i in (e.get("items") or [])]
        )
        assert "引导学生" not in blob
        assert all(e["role"] != "banner" for e in page.elements)


async def test_enrich_writes_new_revision_via_patch_chain(db) -> None:
    row = await _seed_deck_with_projection(db)
    adapter = _FakeAdapter(per_ordinal=_payload)

    result = await enrich_service.enrich_deck(db, str(row.id), adapter=adapter, actor_id="teacher-a")

    assert result["changed"] is True
    # 3 → 4（元素增稠）+ 可能的自动配图 revision（每张图一次 patch）；至少 +1。
    assert result["revision"] >= 4
    assert row.revision == result["revision"]
    s2 = next(s for s in row.spec["slides"] if s["id"] == "s2")
    assert len(s2["elements"]) >= 3
    roles = {e["role"] for e in s2["elements"]}
    assert {"definition", "formula", "bullets"} <= roles
    # 封面页 elements 不被改写
    s1 = next(s for s in row.spec["slides"] if s["id"] == "s1")
    assert s1.get("elements") in (None, [])
    # 修订快照留下新版本
    from sqlalchemy import select

    from app.ppt_v2.persistence.models import PptV2SpecRevision

    revs = (await db.execute(select(PptV2SpecRevision))).scalars().all()
    assert 4 in {r.revision for r in revs}


async def test_page_failure_falls_back_without_failing_job(db) -> None:
    row = await _seed_deck_with_projection(db)
    adapter = _FakeAdapter(raises=RuntimeError("provider boom"))

    result = await enrich_service.enrich_deck(db, str(row.id), adapter=adapter, actor_id="teacher-a")

    # 逐页失败走确定性兜底：作业不失败、仍给出结果计数
    assert result["enriched_pages"] == 0
    assert result["pages"] == 7
    # 达标页用确定性结果，未达标页保留骨架并标 draft
    for page in result["sources"]:
        assert result["sources"][page] in ("deterministic", "skeleton")
    assert "s2" in result["errors"] or result["errors"] == {}
    draft_ids = {s["id"] for s in row.spec["slides"] if s.get("status") == "draft"}
    assert draft_ids  # 至少一页被标记 draft（诚实交付）


async def test_enrich_single_page_sdk_retry_restored(db) -> None:
    """单页 SDK 重试 = 1（R7 提速误伤修复，R22 恢复）：失败一次仍可由 adapter 内重试兜住。"""
    row = await _seed_deck_with_projection(db)
    spec = await _spec_of(row)
    adapter = _FakeAdapter(per_ordinal=_payload)

    await enrich_service.enrich_deck_spec(spec, adapter=adapter)

    assert adapter.last_max_retries == 1, "单页增稠必须恢复 1 次 SDK 层重试（max_retries=1）"


async def test_cross_page_duplicate_stripped_keep_first_page(db) -> None:
    """跨页横幅级同文（QA cross_page_duplicate p0 口径）：汇总层首现保留，后继页消除。"""
    row = await _seed_deck_with_projection(db)
    dup = "把研究对象统称为元素，把一些元素组成的总体叫做集合。"

    def _same_def_payload(ordinal: int) -> _LlmSlide:
        return _LlmSlide(
            elements=[
                _LlmElement(role="definition", text=dup),  # 每页同文（横幅级同文）
                _LlmElement(role="bullets", items=[f"第 {ordinal} 页要点甲", f"第 {ordinal} 页要点乙",
                                                   f"第 {ordinal} 页要点丙"]),
                _LlmElement(role="note", text=f"本页（第 {ordinal} 页）聚焦集合与判读"),
            ]
        )

    result = await enrich_service.enrich_deck(
        db, str(row.id), adapter=_FakeAdapter(per_ordinal=_same_def_payload), actor_id="teacher-a"
    )

    assert result["changed"] is True
    assert result["cross_page_dedup"]["removed_lines"] >= 1, "跨页同文必须被消除"
    dup_pages = [
        s["id"] for s in row.spec["slides"]
        if any(e.get("text") == dup for e in (s.get("elements") or []))
    ]
    assert len(dup_pages) == 1, f"同文定义只允许首现页保留，实际: {dup_pages}"
    # 密度地板不被去重打穿（QA content_thin p1 防线）：≥2 元素且 ≥2 role
    for s in row.spec["slides"]:
        if s["id"] == "s1":
            continue  # 封面页
        els = s.get("elements") or []
        assert len(els) >= 2 and len({e["role"] for e in els}) >= 2, f"{s['id']} 去重后跌破密度地板"


async def test_dedupe_deck_elements_writes_proposal_and_idempotent(db) -> None:
    """导出卡门收敛动作：落库去重走 T24 提案链（revision+1）；无重复时诚实 no-op。"""
    row = await _seed_deck_with_projection(db)
    raw = dict(row.spec)
    dup = "二次函数 y=ax²+bx+c (a≠0) 的图象是抛物线"
    per_slide_note = {"s2": "结合图象理解开口方向", "s5": "结合顶点式理解最值"}
    per_slide_items = {
        "s2": ["开口由 a 决定", "对称轴 x=-b/2a"],
        "s5": ["顶点纵坐标定最值", "增减性分段看"],
    }
    for sid in ("s2", "s5"):
        slide = next(s for s in raw["slides"] if s["id"] == sid)
        slide["elements"] = [
            {"role": "definition", "text": dup},  # 唯一注入的跨页同文
            {"role": "bullets", "items": per_slide_items[sid]},
            {"role": "note", "text": per_slide_note[sid]},
        ]
    row.spec = raw
    await db.flush()

    result = await enrich_service.dedupe_deck_elements(db, str(row.id), actor_id="qa-gate")

    assert result["changed"] is True
    assert result["removed_lines"] == 1
    assert result["revision"] == 4  # 3 → 4（与 enrich 同一条提案事实链）
    s2 = next(s for s in row.spec["slides"] if s["id"] == "s2")
    s5 = next(s for s in row.spec["slides"] if s["id"] == "s5")
    assert any(e.get("text") == dup for e in s2["elements"]), "首现页保留"
    assert all(e.get("text") != dup for e in s5["elements"]), "后继页消除"
    assert len(s5["elements"]) >= 2 and len({e["role"] for e in s5["elements"]}) >= 2, "密度地板"

    again = await enrich_service.dedupe_deck_elements(db, str(row.id), actor_id="qa-gate")
    assert again["changed"] is False
    assert again["revision"] == 4  # 幂等 no-op，不再产生 revision
