"""T24 导出 QA 卡门（R22）单测：p0/p1 拦截 + 跨页重复消除收敛 + skipped 显式放行。

真实组件：SQLite deck spec + T24 提案链（收敛写回 revision+1）、Tasks 表（result 回写）。
Fake 组件：8200 引擎客户端、QA 检测编排（run_full_qa）、对象存储、事件发布器、COM 渲染。

验收锚点（宪法 §12/§18：缺陷件不出厂、失败如实、放行显式）：
- QA p0/p1 → 不登记 artifact，先走确定性收敛（enrich 汇总层同规则去重落库）；
- spec 变更 → 新 revision → 重新 ingest（8200 内容寻址产生新 deck → 重渲染）；
- spec 无变化（无重复可消除）→ 不烧假轮次，如实失败；
- 页图不成套 / 本地渲染不可用 → 放行 + qa_status=skipped（绝不静默冒充通过）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.db.models  # noqa: F401 —— 注册全部 ORM 模型到 Base.metadata
import app.tasks.jobs.export_artifact_v2 as job_mod
from app.db.base import Base
from app.db.models.platform import Tasks
from app.ppt_v2.adapters.deck_engine import EngineError
from app.ppt_v2.persistence.models import PptV2DeckSpecRecord

_FIXTURES = Path(__file__).resolve().parents[1] / "ppt_v2" / "fixtures"
_DUP_DEF = "二次函数 y=ax²+bx+c (a≠0) 的图象是抛物线"


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
def session_factory_of(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


class _FakePublisher:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish_durable(self, *, scope_kind, scope_id, event_type, payload):  # noqa: ANN001
        self.events.append({"event_type": event_type, "payload": payload})

    def kinds(self) -> list[str]:
        return [e["event_type"] for e in self.events]

    def stages(self) -> list[str]:
        return [e["payload"].get("stage") for e in self.events if e["event_type"] == "stage"]


class _FakeStore:
    def __init__(self) -> None:
        self.put_calls: list[tuple[str, str]] = []

    def put_bytes(self, bucket: str, key: str, data: bytes, media_type: str) -> None:
        assert data
        self.put_calls.append((bucket, key))

    def get_bytes(self, bucket: str, key: str) -> bytes:
        raise RuntimeError("unit test：无资产字节可取（引擎侧闸门应显式失败）")


class _FakeEngine:
    """8200 引擎客户端 fake：ingest 内容由测试经 ingest_calls 断言。"""

    def __init__(self, *, n_pages: int, with_page_pngs: bool = True) -> None:
        self.n_pages = n_pages
        self.with_page_pngs = with_page_pngs
        self.ingest_calls: list[dict] = []
        self.pptx = b"PK\x03\x04fake-deck-pptx-bytes"

    async def ingest_deck(self, spec_dict: dict, assets_inline: dict) -> dict:
        self.ingest_calls.append(spec_dict)
        return {"deck_id": "eng-deck-1"}

    async def submit_generate(self, deck_id: str) -> dict:
        return {"id": "job-1"}

    async def get_job(self, job_id: str) -> dict:
        return {"status": "COMPLETED"}

    async def list_job_events(self, job_id: str) -> list:
        return []

    async def list_artifacts(self, deck_id: str) -> list:
        arts = [{
            "id": "art-pptx", "kind": "DECK_PPTX",
            "created_at": "2026-10-01T00:00:00+00:00",
            "sha256": hashlib.sha256(self.pptx).hexdigest(),
        }]
        if self.with_page_pngs:
            for i in range(1, self.n_pages + 1):
                arts.append({
                    "id": f"art-png-{i}", "kind": "PAGE_PNG", "page_no": i,
                    "created_at": "2026-10-01T00:00:00+00:00",
                })
        return arts

    async def artifact_bytes(self, artifact_id: str) -> bytes:
        if artifact_id == "art-pptx":
            return self.pptx
        if str(artifact_id).startswith("art-png-"):
            return b"\x89PNG fake-page"
        raise EngineError(f"unknown artifact {artifact_id}")


def _install_qa(monkeypatch, summaries: list[dict]) -> list[dict]:
    """按脚本出 QA summary（最后一项重复）；记录每次调用的关键入参。"""
    calls: list[dict] = []

    async def fake_run_full_qa(session, *, deck_id, spec_revision, pptx_bytes, pages_png, plan=None, artifact_id=None):  # noqa: ANN001
        calls.append({
            "spec_revision": spec_revision,
            "artifact_id": artifact_id,
            "png_pages": sorted(pages_png),
        })
        summary = summaries[min(len(calls) - 1, len(summaries) - 1)]
        return {
            "deck_id": str(deck_id), "spec_revision": spec_revision,
            "summary": summary, "pages": [],
        }

    monkeypatch.setattr(job_mod.qa_service, "run_full_qa", fake_run_full_qa)
    return calls


def _fail_summary() -> dict:
    return {
        "p0_count": 2, "p1_count": 0, "failed_pages": [2, 5],
        "by_kind": {"cross_page_duplicate": 2},
    }


def _pass_summary() -> dict:
    return {"p0_count": 0, "p1_count": 0, "failed_pages": [], "by_kind": {}}


async def _seed_deck(session_factory_of, *, with_dup: bool):
    raw = json.loads((_FIXTURES / "deck_golden_a.json").read_text(encoding="utf-8"))
    raw["revision"] = 3
    if with_dup:
        for sid in ("s2", "s5"):
            slide = next(s for s in raw["slides"] if s["id"] == sid)
            slide["elements"] = [
                {"role": "definition", "text": _DUP_DEF},  # 唯一跨页同文（p2/p5 实测形态）
                {"role": "bullets", "items": (["开口由 a 决定", "对称轴 x=-b/2a"] if sid == "s2"
                                              else ["顶点纵坐标定最值", "增减性分段看"])},
                {"role": "note", "text": ("结合图象理解开口方向" if sid == "s2"
                                          else "结合顶点式理解最值")},
            ]
    async with session_factory_of() as s:
        row = PptV2DeckSpecRecord(
            school_id=None, schema_version=raw["schema_version"], revision=3, spec=raw
        )
        s.add(row)
        await s.flush()  # 先取 id 再构造 payload
        task_id = uuid.uuid4()
        payload = {"deck_id": str(row.id), "format": "pptx", "owner_id": str(uuid.uuid4())}
        s.add(Tasks(id=task_id, user_id=uuid.uuid4(), type="export_deck_v2",
                    status="running", payload=payload))
        await s.commit()
        return str(row.id), task_id, payload


def _install_common(monkeypatch, session_factory_of, fake_engine: _FakeEngine) -> _FakePublisher:
    # job 模块内 session_factory 是「工厂的工厂」：session_factory() → maker → session；
    # 这里以零参工厂返回同一个无状态 maker 等价替换。
    monkeypatch.setattr(job_mod, "session_factory", lambda: session_factory_of)
    monkeypatch.setattr(job_mod, "DeckEngineClient", lambda: fake_engine)
    monkeypatch.setattr(job_mod, "get_storage", lambda: _FakeStore())
    return _FakePublisher()


async def test_qa_gate_p0_blocks_artifact_then_converges_via_dedupe(
    monkeypatch, session_factory_of
) -> None:
    """p0 → 拦截产物；去重提案落库（revision+1）→ 新 spec 重新 ingest → 通过后出产物。"""
    deck_id, task_id, payload = await _seed_deck(session_factory_of, with_dup=True)
    fake_engine = _FakeEngine(n_pages=8)
    publisher = _install_common(monkeypatch, session_factory_of, fake_engine)
    qa_calls = _install_qa(monkeypatch, [_fail_summary(), _pass_summary(), _pass_summary()])

    await job_mod._run_deck_export(str(task_id), publisher, payload)

    # QA 三次：轮1 fail → 轮2 pass → 产物绑定（artifact_id）
    assert len(qa_calls) == 3
    assert qa_calls[0]["artifact_id"] is None
    assert qa_calls[-1]["artifact_id"] is not None
    # 收敛：去重提案落库 → revision 3→4；第二轮 ingest 的 spec 已无跨页同文
    assert "qa_remediation" in publisher.stages()
    async with session_factory_of() as s:
        row = (await s.execute(select(PptV2DeckSpecRecord))).scalars().one()
        assert row.revision == 4
        s2 = next(sl for sl in row.spec["slides"] if sl["id"] == "s2")
        s5 = next(sl for sl in row.spec["slides"] if sl["id"] == "s5")
        assert any(e.get("text") == _DUP_DEF for e in (s2.get("elements") or [])), "首现页保留"
        assert all(e.get("text") != _DUP_DEF for e in (s5.get("elements") or [])), "后继页消除"
    assert len(fake_engine.ingest_calls) == 2
    assert fake_engine.ingest_calls[0]["slides"] != fake_engine.ingest_calls[1]["slides"]
    # 产物在 QA 通过后才登记
    assert "artifact" in publisher.kinds()
    async with session_factory_of() as s:
        task_row = (await s.execute(select(Tasks).where(Tasks.id == task_id))).scalar_one()
        assert task_row.result["qa_status"]["status"] == "pass"
        assert task_row.result["qa_status"]["rounds"] == 2
        assert task_row.result["pptx_artifact_id"]


async def test_qa_gate_unfixable_fails_honestly_without_artifact(
    monkeypatch, session_factory_of
) -> None:
    """无重复可消除（spec 未变 → 重渲染必得同产物）：不烧假轮次，如实失败且零产物。"""
    deck_id, task_id, payload = await _seed_deck(session_factory_of, with_dup=False)
    fake_engine = _FakeEngine(n_pages=8)
    publisher = _install_common(monkeypatch, session_factory_of, fake_engine)
    qa_calls = _install_qa(monkeypatch, [_fail_summary()])

    with pytest.raises(RuntimeError, match="QA 未通过"):
        await job_mod._run_deck_export(str(task_id), publisher, payload)

    assert len(qa_calls) == 1, "spec 无变化时不得重复无效轮次"
    assert "artifact" not in publisher.kinds(), "QA 未通过必须拦截产物"
    async with session_factory_of() as s:
        row = (await s.execute(select(PptV2DeckSpecRecord))).scalars().one()
        assert row.revision == 3, "失败路径不得改动落库 spec"
        task_row = (await s.execute(select(Tasks).where(Tasks.id == task_id))).scalar_one()
        assert task_row.status == "running"  # 终态由 run_task 置 failed（此处未到收尾）


async def test_qa_gate_render_unavailable_passes_with_explicit_skipped(
    monkeypatch, session_factory_of
) -> None:
    """页图不成套且本地渲染不可用 → 显式 skipped 放行（qa_status=skipped，不静默）。"""
    deck_id, task_id, payload = await _seed_deck(session_factory_of, with_dup=False)

    from app.ppt_v2.qa import render as qa_render

    async def _render_boom(pptx_bytes):
        raise RuntimeError("PowerPoint COM 不可用")

    monkeypatch.setattr(qa_render, "render_pages", _render_boom)
    fake_engine = _FakeEngine(n_pages=8, with_page_pngs=False)
    publisher = _install_common(monkeypatch, session_factory_of, fake_engine)
    qa_calls = _install_qa(monkeypatch, [_pass_summary()])

    await job_mod._run_deck_export(str(task_id), publisher, payload)

    assert len(qa_calls) == 0, "QA 渲染不可用时不得产出 QA 结论"
    assert "qa_skipped" in publisher.stages()
    assert "artifact" in publisher.kinds(), "显式放行仍出产物"
    async with session_factory_of() as s:
        task_row = (await s.execute(select(Tasks).where(Tasks.id == task_id))).scalar_one()
        assert task_row.result["qa_status"]["status"] == "skipped"
        assert "COM" in task_row.result["qa_status"]["reason"] or task_row.result["qa_status"]["reason"]


async def test_qa_gate_partial_page_pngs_passes_with_explicit_skipped(
    monkeypatch, session_factory_of
) -> None:
    """页图半套（引擎 3/8 + 本地渲染也只出 3）→ 不得静默部分通过，显式 skipped 放行。"""
    deck_id, task_id, payload = await _seed_deck(session_factory_of, with_dup=False)

    from app.ppt_v2.qa import render as qa_render

    async def _render_partial(pptx_bytes):
        return {1: b"\x89PNG a", 2: b"\x89PNG b", 3: b"\x89PNG c"}

    monkeypatch.setattr(qa_render, "render_pages", _render_partial)
    fake_engine = _FakeEngine(n_pages=3)  # 引擎只登记 3 张页图（deck 共 8 页）
    publisher = _install_common(monkeypatch, session_factory_of, fake_engine)
    qa_calls = _install_qa(monkeypatch, [_pass_summary()])

    await job_mod._run_deck_export(str(task_id), publisher, payload)

    assert len(qa_calls) == 0, "页图不成套时不得产出 QA 结论（部分通过 = 静默放行）"
    assert "qa_skipped" in publisher.stages()
    async with session_factory_of() as s:
        task_row = (await s.execute(select(Tasks).where(Tasks.id == task_id))).scalar_one()
        assert task_row.result["qa_status"]["status"] == "skipped"
        assert "不成套" in task_row.result["qa_status"]["reason"]


def test_qa_gate_blocked_on_p0_or_p1() -> None:
    assert job_mod._qa_gate_blocked({"p0_count": 0, "p1_count": 0}) is False
    assert job_mod._qa_gate_blocked({"p0_count": 1, "p1_count": 0}) is True
    assert job_mod._qa_gate_blocked({"p0_count": 0, "p1_count": 2}) is True
    assert job_mod._qa_gate_blocked({}) is False
