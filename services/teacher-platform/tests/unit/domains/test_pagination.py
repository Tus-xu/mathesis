# coding: utf-8
"""分页引擎 golden 向量（≥10 组，含超长解答必须加页；B5 拿同一向量复核）。"""
from __future__ import annotations

import pytest

from app.pagination.engine import (
    CANVAS_H,
    MARGIN_BOTTOM,
    MARGIN_TOP,
    ContentBlock,
    _effective_width,
    _usable_height,
    block_height,
    paginate_blocks,
)

# ---- 手算基准（standard=24pt，版心宽 1136，版心高 720-56-56=608）----
USABLE_W = _effective_width(None)
USABLE_H = _usable_height(None)
assert USABLE_W == 1136 and USABLE_H == 608


def g(**kw) -> ContentBlock:
    return ContentBlock(**kw)


def test_g01_empty_input():
    r = paginate_blocks([])
    assert len(r.pages) == 0 and r.anchor_bars == ()


def test_g02_single_short_block_one_page():
    r = paginate_blocks([g(type='stem', text='已知椭圆 x²/a²+y²/b²=1 过点 (2,1)，求方程。')])
    assert len(r.pages) == 1
    assert r.pages[0].index == 0
    assert r.pages[0].anchor_bar == ''
    assert 0 < r.pages[0].fill_rate < 0.5


def test_g03_all_fit_one_page():
    blocks = [g(type='stem', text='第 1 题题干。'), g(type='sub', text='(1) 第一小问。'), g(type='solution-step', text='第一步：联立方程。')]
    r = paginate_blocks(blocks)
    assert len(r.pages) == 1
    assert [p.block for p in r.pages[0].blocks] == blocks


def test_g04_long_solution_must_add_pages():
    """超长解答必须加页（golden：16 步 × 每步 2 行 = 82pt/块 → ≥3 页）。"""
    blocks = [g(type='stem', text='题干。')]
    blocks += [g(type='solution-step', text='步' * 48) for _ in range(16)]
    r = paginate_blocks(blocks)
    assert len(r.pages) >= 3
    # 宁加页不缩字：所有块原样保留、字号不降
    total = sum(len(p.blocks) for p in r.pages)
    assert total == len(blocks)
    assert all(p.font_size == 24 for p in r.pages[0].blocks)


def test_g05_formula_never_split():
    """公式块放不下→整块移下页，绝不跨页。"""
    filler = [g(type='solution-step', text='步骤' + '长' * 45) for _ in range(12)]  # 12×46=552
    formula = g(type='sub', latex='\\frac{x^{2}}{a^{2}}+\\frac{y^{2}}{b^{2}}=1\\;(a>b>0)')  # 106
    r = paginate_blocks(filler + [formula])  # 552+106=658 > 608 → 公式独占下页
    assert len(r.pages) >= 2
    last_page_blocks = r.pages[-1].blocks
    assert len(last_page_blocks) == 1
    assert last_page_blocks[0].block.latex.startswith('\\frac')


def test_g06_title_never_orphaned():
    """stem 落页底且其后有页 → 移到下页首（标题不孤行）。"""
    body = [g(type='solution-step', text='解：' + '步' * 44) for _ in range(12)]  # 12×46=552
    stem = g(type='stem', text='第 2 题（被挤到第一页页底的题干）')                  # 46 → 598 ≤ 608
    tail = [g(type='solution-step', text='后续步骤，保证第一页之后还有内容触发孤行判定。') for _ in range(3)]
    r = paginate_blocks(body + [stem] + tail)
    assert len(r.pages) >= 2
    # stem 不允许停留在任何页的末位（其后同页无块），必须被移到下页首
    positions = [(i, j) for i, p in enumerate(r.pages) for j, b in enumerate(p.blocks) if b.block.type == 'stem']
    assert len(positions) == 1
    i, j = positions[0]
    assert j == 0  # 移页后必在页首


def test_g07_anchor_bar_on_continuation():
    blocks = [g(type='solution-step', text=f'第 {i} 步：继续推导直到完成本问的全部计算。') for i in range(1, 15)]
    r = paginate_blocks(blocks)
    assert len(r.pages) >= 2
    assert r.pages[0].anchor_bar == ''
    assert all(p.anchor_bar.startswith('接上页') for p in r.pages[1:])
    assert r.anchor_bars == tuple(p.anchor_bar for p in r.pages[1:])


def test_g08_invalid_font_tier_rejected():
    with pytest.raises(ValueError, match='font_tier'):
        paginate_blocks([g(type='stem', text='x')], font_tier='huge')


def test_g09_oversized_text_split_without_loss():
    """单块超整页空高 → 按行拆段，内容零丢失（唯一允许的拆分）。"""
    huge = g(type='solution-step', text='证' * 2000)
    r = paginate_blocks([huge])
    assert len(r.pages) >= 2
    joined = ''.join(p.block.text for p in (b for page in r.pages for b in page.blocks))
    assert joined == huge.text


def test_g10_deterministic_and_floor_20pt():
    blocks = [g(type='solution-step', text=f'第 {i} 步：推导与计算，保证内容超出一页触发分页行为。') for i in range(1, 13)]
    r1, r2 = paginate_blocks(blocks), paginate_blocks(blocks)
    assert r1 == r2
    compact = paginate_blocks(blocks, font_tier='compact')
    assert compact.font_size == 20  # 20pt 下限（R4）
    assert all(p.fill_rate <= 1.0 for p in compact.pages)


def test_g11_figure_block_intact():
    """图形块固定占位高、整块不拆：放不下即整块下页。"""
    filler = [g(type='solution-step', text='计算步骤，占位若干高度以触发翻页行为。') for _ in range(7)]
    fig = g(type='figure', text='（图：椭圆与焦点弦）')
    r = paginate_blocks(filler + [fig])
    fig_pages = [p for p in r.pages if any(b.block.type == 'figure' for b in p.blocks)]
    assert len(fig_pages) == 1
    assert len(fig_pages[0].blocks) == 1  # 图形块独占其所在页尾块（不与文本混切）


def test_g12_height_estimator_golden():
    """手算样例：45 汉字 @24pt 版心宽 1136 → 每行 47 字 → 1 行 = 24*1.5+10=46。"""
    b = g(type='solution-step', text='字' * 45)
    assert block_height(b, 24, USABLE_W) == pytest.approx(24 * 1.5 + 10)
    b2 = g(type='solution-step', text='字' * 94)   # 恰 2 行（47×2）
    assert block_height(b2, 24, USABLE_W) == pytest.approx(2 * 24 * 1.5 + 10)
    b3 = g(type='solution-step', text='字' * 96)   # 96 > 94 → 3 行
    assert block_height(b3, 24, USABLE_W) == pytest.approx(3 * 24 * 1.5 + 10)


def test_g13_canvas_override():
    """A4 纵向画布等自定义 canvas 生效（分页可服务非 16:9 场景）。"""
    r = paginate_blocks(
        [g(type='solution-step', text=f'第 {i} 段：自定义画布下依然正确分页。') for i in range(20)],
        canvas=(600, 800),
    )
    assert len(r.pages) >= 2
    assert _usable_height((600, 800)) == 800 - MARGIN_TOP - MARGIN_BOTTOM
