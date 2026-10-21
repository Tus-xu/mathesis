"""smart_quiz KP_MAP 动态加载单测（PLAN §7.4.5「KP 编码词汇断层」修法，B3）。

覆盖：静态码本兜底（fail-open）、结构节点合并过滤（册级 + ^\\d+\\. 小节、排除栏目噪声）、
_parse_request 命中真实 MATH-PEP-* code。零真实 LLM/外网。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.skills.smart_quiz.main import KP_MAP, SmartQuizExecutor, load_kp_map
from app.skills.base import SkillContext


def _ctx(db=None, llm=None) -> SkillContext:
    return SkillContext(
        user_id="u1",
        user_role="student",
        conversation_id="c1",
        request_id="r1",
        db=db,
        llm=llm,
        rag=None,
    )


def _db_with_rows(rows: list[tuple]):
    """模拟 AsyncSession.execute(...).all() → rows（(code, name, parent_id)）。"""
    return SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: rows)))


class _NullLLM:
    intended_provider = "mock"


def test_static_map_unchanged_and_exported():
    """静态粗粒度码本保持原样（question_bank.kp_codes 词本），KP_MAP 兼容别名仍在。"""
    assert KP_MAP["function"] == "函数"
    assert KP_MAP["set_logic"] == "集合逻辑"
    assert len(KP_MAP) == 11


async def test_load_kp_map_fail_open_on_bad_db():
    """DB 不可用（MagicMock execute 不可 await）→ fail-open 回退静态码本。"""
    from unittest.mock import MagicMock

    ctx = _ctx(db=MagicMock())
    merged = await load_kp_map(ctx)
    assert merged == KP_MAP


async def test_load_kp_map_merges_structural_nodes_only():
    """册级根 + ^\\d+\\. 小节并入；「探究/思考」等栏目噪声排除；静态码不被覆盖。"""
    book_root = ("MATH-PEP-BIXI1", "数学必修第一册（人教A版2019）", None)
    section = ("MATH-PEP-BIXI1-ABCDEF1234", "1.1 集合的概念", "uuid-parent")
    noise = ("MATH-PEP-BIXI1-NOISE00001", "探究", "uuid-parent")
    noise2 = ("MATH-PEP-BIXI1-NOISE00002", "思考与讨论", "uuid-parent")
    ctx = _ctx(db=_db_with_rows([book_root, section, noise, noise2]))
    merged = await load_kp_map(ctx)
    assert merged["MATH-PEP-BIXI1"] == "数学必修第一册（人教A版2019）"
    assert merged["MATH-PEP-BIXI1-ABCDEF1234"] == "1.1 集合的概念"
    assert "MATH-PEP-BIXI1-NOISE00001" not in merged
    assert "MATH-PEP-BIXI1-NOISE00002" not in merged
    assert merged["function"] == "函数", "静态码本必须保留（题库兜底词表）"


async def test_load_kp_map_static_not_shadowed():
    """动态结构节点与静态词重名时保留静态值（setdefault）。"""
    rows = [("function", "1.1 函数的概念", "uuid-parent")]
    merged = await load_kp_map(_ctx(db=_db_with_rows(rows)))
    assert merged["function"] == "函数"


async def test_parse_request_hits_structural_node():
    """消息引用真实小节名 → 返回 MATH-PEP-* 真实 code（词汇断层闭合）。"""
    code = "MATH-PEP-BIXI1-ABCDEF1234"
    name = "1.1 集合的概念"
    ex = SmartQuizExecutor()
    ctx = _ctx(db=_db_with_rows([(code, name, "p")]), llm=_NullLLM())
    got_code, got_name, diff, q_type, _theme = await ex._parse_request(
        f"帮我出几道{name}的题", ctx
    )
    assert (got_code, got_name) == (code, name)
    assert diff == "medium" and q_type == "choice"


async def test_parse_request_noise_message_does_not_hit_column_nodes():
    """含「思考/探究」的消息不得命中被排除的栏目节点（回归 R4 §5 噪声口径）。"""
    code = "MATH-PEP-BIXI1-NOISE00001"
    ex = SmartQuizExecutor()
    ctx = _ctx(db=_db_with_rows([(code, "探究", "p")]), llm=_NullLLM())
    got_code, _name, _d, _q, _t = await ex._parse_request("我想探究一下这个思考题", ctx)
    assert got_code != code
