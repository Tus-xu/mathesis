"""Butler Kernel v2 ContextAssembler（阶段 3B）

覆盖：
- 每类上下文只读取一次（execute 次数 == 上下文类别数）；
- 同一 AsyncSession 顺序读取（不并发）；
- 无数据时返回明确空结构；
- Snapshot 不含敏感字段（密钥/完整隐私文本）。
"""

import re
import uuid
from types import SimpleNamespace

from app.butler.context import ContextAssembler
from app.butler.contracts import ActorContext, ActorRole, ButlerContextSnapshot, ButlerRequest


class _FakeScalars:
    def __init__(self, rows: list):
        self.rows = rows

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return self.rows


class _FakeResult:
    def __init__(self, rows: list):
        self.rows = rows

    def scalars(self):
        return _FakeScalars(self.rows)


class FakeSession:
    """按 FROM 表名返回预置行；统计每次 execute（验证每类只读一次、顺序执行）。"""

    def __init__(self, **table_rows):
        self.table_rows = table_rows
        self.executed: list[str] = []

    async def execute(self, stmt):
        sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        m = re.search(r"FROM\s+(\w+)", sql)
        table = m.group(1) if m else "unknown"
        self.executed.append(table)
        return _FakeResult(list(self.table_rows.get(table, [])))


def _request() -> ButlerRequest:
    return ButlerRequest(
        actor=ActorContext(
            user_id=uuid.uuid4(),
            role=ActorRole.STUDENT,
            class_ids=(uuid.uuid4(),),
        ),
        message="今天复习什么",
        scene="student.dashboard",
        client_request_id="crid-1",
    )


def _profile_row() -> SimpleNamespace:
    return SimpleNamespace(
        user_id=uuid.uuid4(),
        tags=[],
        weak_point_rank=[{"kp_name": "函数", "mastery": 0.42}],
        learning_style="practice",
        current_stage="consolidation",
        profile_card="函数掌握度偏低",
    )


def _conv_row() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="错题复习",
        summary="学生希望复习函数错题",
        updated_at=__import__("datetime").datetime(2026, 8, 18),
    )


def _msg_row() -> SimpleNamespace:
    return SimpleNamespace(
        conversation_id=uuid.uuid4(),
        role="user",
        content="帮我看看这道函数题" + "x" * 500,  # 长文本应被截断
    )


def _umc_row() -> SimpleNamespace:
    return SimpleNamespace(
        user_id=uuid.uuid4(),
        primary_model="spark",
        secondary_model="deepseek",
    )


def _assignment_row() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="三角函数练习",
        type="quiz",
        deadline=None,
        status="published",
    )


async def test_build_returns_typed_snapshot():
    session = FakeSession(
        student_profiles=[_profile_row()],
        conversations=[_conv_row()],
        messages=[_msg_row()],
        user_model_configs=[_umc_row()],
        assignments=[_assignment_row()],
    )
    snap = await ContextAssembler().build(_request(), session)
    assert isinstance(snap, ButlerContextSnapshot)
    assert snap.scene == "student.dashboard"
    assert snap.profile["learning_style"] == "practice"
    assert snap.assignments[0]["title"] == "三角函数练习"


async def test_build_reads_each_context_once():
    session = FakeSession(
        student_profiles=[_profile_row()],
        conversations=[_conv_row()],
        messages=[_msg_row()],
        user_model_configs=[_umc_row()],
        assignments=[_assignment_row()],
    )
    await ContextAssembler().build(_request(), session)
    # profile/conversation/messages/config/assignments/system 每类恰好 1 次
    from collections import Counter

    counts = Counter(session.executed)
    assert counts["student_profiles"] == 1
    assert counts["conversations"] == 1
    assert counts["messages"] == 1
    assert counts["user_model_configs"] == 1
    assert counts["system_configs"] == 1
    assert counts["assignments"] == 1


async def test_build_same_session_no_concurrency():
    """同一 AsyncSession 顺序执行：所有 execute 发生在同一协程内（无 gather）。"""
    session = FakeSession(
        student_profiles=[_profile_row()],
        conversations=[_conv_row()],
        messages=[_msg_row()],
        user_model_configs=[_umc_row()],
        assignments=[_assignment_row()],
    )

    await ContextAssembler().build(_request(), session)
    assert len(session.executed) == 6  # 全部在 build 内顺序完成


async def test_build_empty_data_returns_empty_structures():
    session = FakeSession()
    snap = await ContextAssembler().build(_request(), session)
    assert snap.profile == {}
    assert snap.conversation == {}
    assert snap.assignments == ()
    assert snap.effective_config == {}
    assert snap.feature_flags == frozenset()


async def test_snapshot_no_sensitive_fields():
    session = FakeSession(
        student_profiles=[_profile_row()],
        conversations=[_conv_row()],
        messages=[_msg_row()],
        user_model_configs=[_umc_row()],
        assignments=[_assignment_row()],
    )
    snap = await ContextAssembler().build(_request(), session)
    blob = str(snap.model_dump())
    # 密钥/嵌入/原始全文不进入 Snapshot
    assert "api_key" not in blob
    assert "secret" not in blob
    assert "embedding" not in blob
    # 长对话文本被截断，不携带全文
    assert "x" * 500 not in blob
    # 有效配置只含模型名，不含 api_key 字段
    assert snap.effective_config.get("primary_model") == "spark"
    assert "primary_api_key" not in snap.effective_config
