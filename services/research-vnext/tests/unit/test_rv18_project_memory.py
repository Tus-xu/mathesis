"""RV18 · 项目记忆摘要（宪法 §8）的**纯函数**单元测试（不联网、不连库）。

覆盖本轮改动里最容易回归的红线：
- §85：中文摘要里不得出现小写英文关键词碎片（`finite`/`element`/`error`）；
- §70：规则版摘要必须**数据驱动**，不同数据必须产出不同文本；
- §37/§90：摘要只能来自真实素材，素材为空时不得编出"文献"；
- 缓存：数据指纹变了必须失效。
"""

from __future__ import annotations

import re

from research_vnext.core.research_workspace import memory as m

LOWER_LATIN = re.compile(r"(?<![A-Za-z])[a-z]{4,}(?![A-Za-z])")


def _grounding(**over) -> dict:
    base = {
        "name": "有限元误差分析",
        "description": "有限元误差估计与椭圆型 PDE 数值解",
        "papers": [
            {
                "title": "Finite Element Methods for Interface Problems: Robust and Local Optimal A Priori Error Estimates",
                "year": 2015,
                "authors": "Zhiqiang Cai、Shun Zhang",
            },
            {
                "title": "Residual-Based a posteriori error estimation for hp-adaptive finite element methods for the Stokes equations",
                "year": 2018,
                "authors": "A. Ghesmati 等",
            },
            {"title": "Reliable a-posteriori error estimators for hp-adaptive finite element methods", "year": 2011, "authors": ""},
        ],
        "sections": ["1 Introduction", "2 Sobolev Space and Preliminaries", "3.1 mixed finite element method"],
        "notes": ["误差估计的常数 C 与网格尺寸无关"],
        "files": ["main.tex", "refs.bib"],
    }
    base.update(over)
    return base


_STATS = {"papers": 3, "drafts": 12, "notes": 5, "data": 2}


# --------------------------------------------------------------------------
# §85 · 主题词不能是小写英文碎片
# --------------------------------------------------------------------------


def test_theme_terms_prefers_cjk() -> None:
    terms = m.theme_terms(_grounding())
    assert terms, "有真实标题时必须能提出主题词"
    assert all(not LOWER_LATIN.fullmatch(t) for t in terms), terms


def test_theme_terms_never_returns_lowercase_latin_fragments() -> None:
    """只有英文标题、且都是小写常用词时，宁可返回空，也不吐 `finite`/`element`。"""
    g = _grounding(
        papers=[{"title": "finite element error estimates for problems", "year": 2020, "authors": ""}],
        sections=[],
        name="",
        description="",
    )
    terms = m.theme_terms(g)
    for t in terms:
        assert not LOWER_LATIN.fullmatch(t), f"泄漏小写英文碎片: {t!r}"


def test_theme_terms_keeps_proper_nouns() -> None:
    """专有名词（首字母大写）允许保留，否则纯英文项目会一个主题词都提不出来。"""
    g = _grounding(
        papers=[{"title": "Galerkin Methods for the Stokes Equations", "year": 2020, "authors": ""}],
        sections=[],
        name="",
        description="",
    )
    terms = m.theme_terms(g)
    assert "Galerkin" in terms or "Stokes" in terms, terms


# --------------------------------------------------------------------------
# §70 · 规则版摘要必须数据驱动
# --------------------------------------------------------------------------


def test_rule_summary_has_no_lowercase_latin_fragments() -> None:
    text = m.rule_summary(_grounding(), _STATS)
    # 允许出现专有名词/标题，但不允许小写英文关键词碎片
    leaked = [w for w in LOWER_LATIN.findall(text) if w not in {"tex", "bib"}]
    assert not leaked, f"§85 泄漏: {leaked} in {text!r}"


def test_rule_summary_is_data_driven_not_fixed_string() -> None:
    """§70：换一份真实数据必须换一段文本，不能是固定串。"""
    a = m.rule_summary(_grounding(), _STATS)
    b = m.rule_summary(
        _grounding(
            name="另一个项目",
            papers=[{"title": "Stokes 方程的混合有限元", "year": 2021, "authors": "张三"}],
        ),
        {"papers": 1, "drafts": 0, "notes": 0, "data": 0},
    )
    assert a != b
    assert "另一个项目" in b and "另一个项目" not in a
    assert "Stokes" in b


def test_rule_summary_without_papers_does_not_invent_literature() -> None:
    """§37/§90：没有文献就不能写出"收录了 N 篇文献"这种暗示。"""
    g = _grounding(papers=[], sections=[], notes=[], files=["main.tex"])
    text = m.rule_summary(g, {"papers": 0, "drafts": 1, "notes": 0, "data": 0})
    assert "还没有收录文献" in text
    assert "篇文献，" not in text


def test_rule_summary_mentions_real_first_paper_metadata() -> None:
    text = m.rule_summary(_grounding(), _STATS)
    assert "Cai" in text and "2015" in text, text


# --------------------------------------------------------------------------
# 缓存 · 指纹
# --------------------------------------------------------------------------


def test_fingerprint_changes_with_data() -> None:
    g1 = _grounding()
    g2 = _grounding(papers=_grounding()["papers"][:1])
    assert m.fingerprint(g1, _STATS) != m.fingerprint(g2, _STATS)
    assert m.fingerprint(g1, _STATS) == m.fingerprint(_grounding(), _STATS)
    # 统计数变了也要失效（加了一篇文献但标题列表被截断时仍有兜底）
    assert m.fingerprint(g1, _STATS) != m.fingerprint(g1, {**_STATS, "papers": 4})


def test_invalidate_clears_entry() -> None:
    import uuid

    t, p = uuid.uuid4(), uuid.uuid4()
    m._CACHE[(str(t), str(p))] = {
        "fingerprint": "x",
        "summary": "y",
        "source": "llm:project-memory",
        "expires_at": 0,
    }
    m.invalidate(tenant_id=t, project_id=p)
    assert (str(t), str(p)) not in m._CACHE


# --------------------------------------------------------------------------
# 超时上界
# --------------------------------------------------------------------------


def test_llm_timeout_is_bounded_and_short() -> None:
    """概览是"打开项目就要看"的接口，模型调用必须有**短**上界。"""
    assert 0 < m.LLM_TIMEOUT_S <= 20
    assert m.CACHE_TTL_S > m.FALLBACK_TTL_S > 0
