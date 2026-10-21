"""RV18 · 术语记忆集成测试（宪法 §42 Terminology Memory）。

**为什么在 integration 而不是 unit**：
术语记忆是**持久化**能力（`research_terminology` 表），必须对着**真实 PG + Alembic 迁移**
验证；`tests/unit/` 的约定是纯函数测试（不连库）。这里用 `app_env` fixture
（session 级，跑 `alembic upgrade head` 建独立 schema）。

覆盖：
  1. 规范化：大小写 / 多余空格 → 同一键（否则"看起来有记忆却查不到"）
  2. 空记忆不污染 prompt（`prompt_block()` 必须返回空串）
  3. 学习新术语 vs 刷新已知术语的计数语义（`remember_many` 只数"新学"）
  4. 按 (tenant, project) 隔离：A 项目的术语不得泄漏到 B 项目
  5. **唯一约束 = §42 的"一致"**：同项目同术语重复学习后**只有一行**，译名更新为最新
  6. 跨"进程重启"仍可读回（新实例 + 空缓存 → 从库读到全部术语）
  7. `clear()` 清空
  8. 畸形输入不抛错
  9. 反向校验 `conflicts()`
"""

from __future__ import annotations

import uuid

from research_vnext.core.db.engine import session_scope
from research_vnext.core.research_workspace import terminology as T
from research_vnext.core.research_workspace.models import ResearchTerminologyRow


def _ids() -> tuple[uuid.UUID, uuid.UUID]:
    return uuid.uuid4(), uuid.uuid4()


def _mem(tenant: uuid.UUID, project: uuid.UUID) -> T.TerminologyMemory:
    return T.for_project(tenant, project)


def test_normalization_makes_lookup_case_and_space_insensitive(app_env) -> None:
    t, p = _ids()
    with session_scope() as db:
        m = _mem(t, p)
        m.remember(db, "a posteriori error estimate", "后验误差估计")
        assert m.lookup(db, "A Posteriori  Error Estimate") == "后验误差估计"
        assert len(m.snapshot(db)) == 1, "不同写法不应产生两条记忆"


def test_empty_memory_does_not_pollute_prompt(app_env) -> None:
    t, p = _ids()
    with session_scope() as db:
        m = _mem(t, p)
        assert m.prompt_block(db) == "", "无记忆时必须返回空串，不能往 prompt 里塞空标题"
        m.remember(db, "finite element method", "有限元方法")
        block = m.prompt_block(db)
        assert "有限元方法" in block
        assert "必须沿用" in block, "术语块必须明确要求模型沿用既有译法"


def test_remember_many_counts_only_new_terms(app_env) -> None:
    t, p = _ids()
    with session_scope() as db:
        m = _mem(t, p)
        assert m.remember_many(db, [("finite element method", "有限元方法")]) == 1
        assert m.remember_many(db, [("finite element method", "有限元方法")]) == 0
        assert (
            m.remember_many(
                db, [("finite element method", "有限元方法"), ("a priori", "先验")]
            )
            == 1
        )
        assert len(m.snapshot(db)) == 2


def test_isolated_between_projects(app_env) -> None:
    tenant = uuid.uuid4()
    pa, pb = uuid.uuid4(), uuid.uuid4()
    with session_scope() as db:
        ma, mb = _mem(tenant, pa), _mem(tenant, pb)
        ma.remember(db, "finite element method", "有限元方法")
        assert ma.lookup(db, "finite element method") == "有限元方法"
        assert mb.lookup(db, "finite element method") is None, "术语记忆必须按项目隔离"


def test_same_term_yields_single_row_and_latest_target(app_env) -> None:
    """§42 的「一致」由唯一约束保证：同项目同术语**永远只有一行**。"""
    t, p = _ids()
    with session_scope() as db:
        m = _mem(t, p)
        m.remember(db, "a posteriori error estimate", "后验误差估计")
        m.remember(db, "a posteriori error estimate", "事后误差估计")  # 改译名
        rows = (
            db.query(ResearchTerminologyRow)
            .filter(
                ResearchTerminologyRow.tenant_id == t,
                ResearchTerminologyRow.project_id == p,
            )
            .all()
        )
        assert len(rows) == 1, "同一项目同一术语绝不能出现两行"
        assert rows[0].target_term == "事后误差估计", "重复学习应更新为最新译名"
        assert rows[0].hits >= 2, "hits 应累加（排序依据）"


def test_survives_process_restart_new_instance_reads_from_db(app_env) -> None:
    """v2 的核心收益：**8300 重启后术语仍在**（新实例 + 空缓存 → 从库读回）。"""
    t, p = _ids()
    with session_scope() as db:
        _mem(t, p).remember(db, "finite element method", "有限元方法")
    # 模拟"新进程"：全新实例（不在实例注册表里），缓存必然为空
    fresh = T.TerminologyMemory(t, p)
    fresh.forget_cache()
    with session_scope() as db:
        assert (
            fresh.lookup(db, "finite element method") == "有限元方法"
        ), "持久化版必须能跨进程读回术语（这正是 v1 进程内方案的缺陷）"


def test_clear(app_env) -> None:
    t, p = _ids()
    with session_scope() as db:
        m = _mem(t, p)
        m.remember_many(db, [(f"term number {i} x", f"术语{i}") for i in range(5)])
        assert len(m.snapshot(db)) == 5
        assert m.clear(db) == 5
        assert m.snapshot(db) == {}


def test_bad_input_is_ignored_not_raised(app_env) -> None:
    t, p = _ids()
    with session_scope() as db:
        m = _mem(t, p)
        m.remember(db, "", "空源")
        m.remember(db, "源", "")
        m.remember(db, "源", "x" * 200)  # 目标过长 → 不是术语，忽略
        assert m.snapshot(db) == {}
        # 畸形项不得抛错
        assert m.remember_many(db, [("onlyone",), None, ("ok term", "好术语")]) == 1  # type: ignore[list-item]


def test_conflicts_detects_missing_established_target(app_env) -> None:
    """反向校验素材：源文出现某术语、译文没沿用已确立译名 → 报一条提醒。"""
    t, p = _ids()
    with session_scope() as db:
        m = _mem(t, p)
        m.remember(db, "a posteriori error estimate", "后验误差估计")
        hit = m.conflicts(
            db, "We use the a posteriori error estimate.", "我们使用后验误差估计。"
        )
        assert hit == [], "沿用了已确立译名 → 不应报冲突"
        miss = m.conflicts(
            db, "We use the a posteriori error estimate.", "我们使用事后误差估计。"
        )
        assert len(miss) == 1, "未沿用已确立译名 → 必须报一条"
        assert "后验误差估计" in miss[0]
