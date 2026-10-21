"""TeacherMemory 单测：三层召回、MD5 去重、KNR source_refs 红线、L2 晋升审计。"""
from __future__ import annotations

import pytest

from app.agent.memory.teacher_memory import InMemoryMemoryStore, TeacherMemoryImpl


@pytest.fixture()
def memory() -> TeacherMemoryImpl:
    return TeacherMemoryImpl(InMemoryMemoryStore())


async def test_l1_requires_source_refs(memory: TeacherMemoryImpl):
    """KNR 红线 9：无来源不入库。"""
    with pytest.raises(ValueError, match="source_refs"):
        await memory.record_fact("t1", "fact", "偏好几何直观导入", [])


async def test_l1_md5_dedup(memory: TeacherMemoryImpl):
    first = await memory.record_fact("t1", "fact", "偏好几何直观导入", ["doc-1"], domain="lesson")
    dup = await memory.record_fact("t1", "fact", "偏好几何直观导入", ["doc-1"], domain="lesson")
    assert first is not None and dup is None


async def test_recall_orders_l3_l2_l1(memory: TeacherMemoryImpl):
    await memory.record_preference("t1", "45 分钟五环节结构", ["ai-run-1"])
    await memory.record_fact("t1", "summary", "二次函数单元正确率偏低", ["doc-2"], domain="classroom")
    await memory.promote("t1", "classroom")
    rows = await memory.recall("t1", top_k=3)
    assert [r["layer"] for r in rows] == ["L3", "L2", "L1"]


async def test_promote_requires_l1_and_carries_source_refs(memory: TeacherMemoryImpl):
    assert await memory.promote("t1", "classroom") is None  # 空域不晋升
    await memory.record_fact("t1", "fact", "二次函数单元正确率偏低", ["doc-2"], domain="classroom")
    await memory.record_fact("t1", "fact", "学生怕参数讨论", ["doc-3"], domain="classroom")
    mid = await memory.promote("t1", "classroom")
    assert mid is not None
    store = memory._store
    l2 = next(r for r in store._rows if r["id"] == mid)
    assert l2["layer"] == "L2" and l2["domain"] == "classroom"
    # 红线：L2 必须指回 L1 来源
    assert set(l2["source_refs"]) >= {"doc-2", "doc-3"}
    # 审计行存在
    assert any(h["action"] == "PROMOTE" for h in store._history)


async def test_promote_duplicate_summary_skipped(memory: TeacherMemoryImpl):
    await memory.record_fact("t1", "fact", "二次函数单元正确率偏低", ["doc-2"], domain="classroom")
    first = await memory.promote("t1", "classroom")
    again = await memory.promote("t1", "classroom")
    assert first is not None and again is None


async def test_promote_with_injected_summarizer(memory: TeacherMemoryImpl):
    async def summarizer(domain, facts):
        return f"{domain} 域共 {len(facts)} 条要点，建议强化数形结合"

    memory = TeacherMemoryImpl(InMemoryMemoryStore(), summarizer=summarizer)
    await memory.record_fact("t1", "fact", "二次函数单元正确率偏低", ["doc-2"], domain="classroom")
    mid = await memory.promote("t1", "classroom")
    store = memory._store
    l2 = next(r for r in store._rows if r["id"] == mid)
    assert "数形结合" in l2["content"]


async def test_teacher_isolation(memory: TeacherMemoryImpl):
    await memory.record_fact("t1", "fact", "偏好几何直观导入", ["doc-1"], domain="lesson")
    rows = await memory.recall("t2")
    assert all(r["teacher_id"] == "t2" for r in rows) if rows else True
    assert not rows


async def test_domain_filtered_recall(memory: TeacherMemoryImpl):
    await memory.record_fact("t1", "fact", "课堂答题率高但质量一般", ["doc-1"], domain="classroom")
    await memory.record_fact("t1", "fact", "偏好板书推导", ["doc-2"], domain="lesson")
    rows = await memory.recall("t1", domain="classroom", top_k=5)
    assert all(r.get("domain") in ("classroom", None) for r in rows)
