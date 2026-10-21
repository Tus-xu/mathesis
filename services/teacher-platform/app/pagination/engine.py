# coding: utf-8
"""分页引擎 paginate_blocks —— 纯函数（B4 所有，golden 向量 B5 复核）。

规则（B4 规范 §3.7 / R4 宁加页不缩字 / R9）：
  1. 标题不孤行：题干/标题块永不落在页底（其后无正文时移到下页首）。
  2. 公式块不可跨页：isolated 公式整块同页；放不下→新页。
  3. 列表/步骤不腰斩：solution-step 块优先整块保留；单块超过整页空高才允许按行拆分。
  4. 字号下限 20pt：font_tier 映射的字号永不低于 20。
  5. 宁加页不缩字：任何情况下不压缩字号/挤压内容，放不下→新增页。
  6. 续页锚条 anchor_bar：非首页生成「接上页 · <上一页末块摘要>」。

契约参照：前端 V3RecognizeBlock（types/teacherV3.ts）与 mock paginate 事件载荷
（teacherV3Server.ts:586-590：pages/anchor_bars/font_tier）。纯函数、确定性：
同输入同输出，无时钟/随机/IO。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Sequence

# 字号档位（下限 20pt 纪律；compact 也不得低于 20）
FONT_TIER_SIZE: dict[str, int] = {'compact': 20, 'standard': 24, 'large': 28}
MIN_FONT_SIZE = 20

# 1280×720 逻辑画布（PPTist 式），版心边距
CANVAS_W, CANVAS_H = 1280, 720
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 72, 56, 56

LINE_HEIGHT_FACTOR = 1.5      # 行高 = font_size × 1.5
BLOCK_PADDING = 10            # 块间垂直间距
FORMULA_HEIGHT_FACTOR = 2.0   # isolated/display 公式块高度（占位行高因子）
FIGURE_BLOCK_HEIGHT = 300     # 图形块固定占位高
CHAR_WIDTH_FACTOR = 1.0       # CJK 字宽 ≈ font_size；ASCII ≈ 0.55


@dataclass(frozen=True)
class ContentBlock:
    """与前端 V3RecognizeBlock 对齐的内容块（引擎输入）。"""

    type: Literal['stem', 'sub', 'figure', 'solution-step']
    text: str = ''
    latex: str = ''
    confidence: float = 1.0
    editable: bool = True

    @property
    def is_formula(self) -> bool:
        return bool(self.latex) and not self.text


@dataclass(frozen=True)
class PagedBlock:
    block: ContentBlock
    font_size: int


@dataclass(frozen=True)
class Page:
    index: int
    blocks: tuple[PagedBlock, ...]
    fill_rate: float
    anchor_bar: str = ''


@dataclass(frozen=True)
class PagedResult:
    pages: tuple[Page, ...]
    font_tier: str
    font_size: int
    anchor_bars: tuple[str, ...] = field(default_factory=tuple)


def _effective_width(canvas: tuple[int, int] | None) -> int:
    return (canvas[0] if canvas else CANVAS_W) - 2 * MARGIN_X


def _usable_height(canvas: tuple[int, int] | None) -> int:
    return (canvas[1] if canvas else CANVAS_H) - MARGIN_TOP - MARGIN_BOTTOM


def _visual_len(s: str) -> float:
    """CJK 记 1，ASCII 记 0.55 的显示宽度。"""
    return sum(1.0 if ord(ch) > 0x2E7F else 0.55 for ch in s)


def block_height(block: ContentBlock, font_size: int, usable_width: int) -> float:
    """估算块渲染高度（确定性；公式/图形整块估高，不参与跨页拆分）。"""
    if block.type == 'figure':
        return FIGURE_BLOCK_HEIGHT
    if block.is_formula:
        # display 公式：按命令数量估 1-2 行，整块不跨页
        lines = max(1, 1 + (1 if block.latex.count('\\') > 6 else 0))
        return lines * FORMULA_HEIGHT_FACTOR * font_size + BLOCK_PADDING
    body = block.text or ''
    if not body:
        return font_size * LINE_HEIGHT_FACTOR
    chars_per_line = max(1, int(usable_width / (font_size * CHAR_WIDTH_FACTOR)))
    lines = max(1, math.ceil(_visual_len(body) / chars_per_line))
    return lines * font_size * LINE_HEIGHT_FACTOR + BLOCK_PADDING


def split_text_block(block: ContentBlock, usable_width: int, font_size: int) -> list[ContentBlock]:
    """仅当单块超过整页空高时，按行边界拆成段（列表不腰斩：行是最小单元）。"""
    chars_per_line = max(1, int(usable_width / (font_size * CHAR_WIDTH_FACTOR)))
    parts: list[str] = []
    body = block.text
    while body:
        parts.append(body[:chars_per_line])
        body = body[chars_per_line:]
    if len(parts) <= 1:
        return [block]
    return [
        ContentBlock(type=block.type, text=p, latex='', confidence=block.confidence, editable=block.editable)
        for p in parts
    ]


def paginate_blocks(
    blocks: Sequence[ContentBlock],
    *,
    font_tier: str = 'standard',
    canvas: tuple[int, int] | None = None,
) -> PagedResult:
    """块序列 → 分页结果（纯函数，同输入同输出）。"""
    if font_tier not in FONT_TIER_SIZE:
        raise ValueError(f'font_tier 必须是 {sorted(FONT_TIER_SIZE)}，收到 {font_tier!r}')
    font_size = max(FONT_TIER_SIZE[font_tier], MIN_FONT_SIZE)
    usable_width = _effective_width(canvas)
    page_height = _usable_height(canvas)

    # 预展开：超整页的纯文本块按行拆段（宁加页不缩字下唯一允许的拆分）
    expanded: list[ContentBlock] = []
    for b in blocks:
        if b.type != 'figure' and not b.is_formula and b.text \
                and block_height(b, font_size, usable_width) > page_height:
            expanded.extend(split_text_block(b, usable_width, font_size))
        else:
            expanded.append(b)

    pages_blocks: list[list[PagedBlock]] = []
    current: list[PagedBlock] = []
    used = 0.0
    for b in expanded:
        h = block_height(b, font_size, usable_width)
        if current and used + h > page_height:
            pages_blocks.append(current)
            current = []
            used = 0.0
        current.append(PagedBlock(block=b, font_size=font_size))
        used += h
    if current:
        pages_blocks.append(current)

    # 标题不孤行：stem 落在页底且其后还有页 → 移到下一页首
    for i, pb in enumerate(pages_blocks):
        if len(pb) >= 2 and pb[-1].block.type == 'stem' and i < len(pages_blocks) - 1:
            pages_blocks[i] = pb[:-1]
            pages_blocks[i + 1] = [pb[-1]] + pages_blocks[i + 1]
    pages_blocks = [pb for pb in pages_blocks if pb]

    pages: list[Page] = []
    anchor_bars: list[str] = []
    for idx, pb in enumerate(pages_blocks):
        heights = [block_height(p.block, p.font_size, usable_width) for p in pb]
        fill = min(1.0, round(sum(heights) / page_height, 4))
        anchor = ''
        if idx > 0:
            last = pages_blocks[idx - 1][-1].block
            summary = _shorten((last.latex.replace('\\', '') if last.is_formula else last.text).strip(), 18)
            anchor = f'接上页 · {summary}' if summary else '接上页'
            anchor_bars.append(anchor)
        pages.append(Page(index=idx, blocks=tuple(pb), fill_rate=fill, anchor_bar=anchor))

    return PagedResult(
        pages=tuple(pages),
        font_tier=font_tier,
        font_size=font_size,
        anchor_bars=tuple(anchor_bars),
    )


def _shorten(s: str, n: int) -> str:
    s = s.strip()
    return s if len(s) <= n else s[: n - 1] + '…'
