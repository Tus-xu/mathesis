/**
 * R4a 资料页渲染管线（sliceMarkdown）单测：G22 反泄漏 + 结构化渲染 + chunk 作用域。
 *
 * 口径与验收探针一致：可见文本不得命中 `/!\[\]\(|\\-|\$\$|\\frac/`；
 * 插图必须解析为真实图片 URL（失败降级由 SliceFigure 负责，不在本层回退源码）。
 */
import { describe, expect, it } from 'vitest'

import {
  cleanInlineSlice,
  parseSliceBlocks,
  refSectionId,
  scopeBlocksToSection,
  sectionNoOf,
  sectionRef,
  sliceImageUrl,
  slicePreviewText,
  sliceTitleText,
} from '../../src/features/teacher-prep-center/math/sliceMarkdown'

/** G22 可见文本探针（与浏览器 `document.body.innerText` 判定同口径）。 */
const LEAK_RE = /!\[\]\(|\\-|\$\$|\\frac/

const REAL_CHUNK = [
  '## 3.1.1 椭圆的标准方程',
  '',
  '![](images/d13fb6b8f48bef164c6913c08474596da5fefd3f0521387ee265854ebdea0189.jpg) 图3-1-2',
  '',
  '设椭圆 C 的两个焦点分别为 $F_1$, $F_2$，\\- 焦距为 $2c$。',
  '',
  '$$',
  '',
  '\\frac {y - 3}{x - (- 1)} = - 2,',
  '',
  '$$',
  '',
  '定义：平面内与两个定点 $F_1,F_2$ 的距离之和等于常数的点的轨迹叫做椭圆。',
].join('\n')

/** 块 → 可读文本（figure 取图题、formula 取 tex）。 */
function textOf(b: { kind: string; [k: string]: unknown }): string {
  if (typeof b.text === 'string') return b.text
  if (typeof b.tex === 'string') return b.tex
  return typeof b.caption === 'string' ? b.caption : ''
}

describe('sliceMarkdown · 渲染管线', () => {
  it('插图行解析为 figure 块（源码不上屏）', () => {
    const blocks = parseSliceBlocks(REAL_CHUNK)
    const fig = blocks.find((b) => b.kind === 'figure')
    expect(fig).toBeTruthy()
    if (fig?.kind !== 'figure') throw new Error('unreachable')
    expect(fig.src).toBe('images/d13fb6b8f48bef164c6913c08474596da5fefd3f0521387ee265854ebdea0189.jpg')
    expect(fig.caption).toContain('图3-1-2')
  })

  it('公式块解析为 formula 块（tex 完整，$$ 不外泄）', () => {
    const blocks = parseSliceBlocks(REAL_CHUNK)
    const f = blocks.find((b) => b.kind === 'formula')
    expect(f).toBeTruthy()
    if (f?.kind !== 'formula') throw new Error('unreachable')
    expect(f.tex).toContain('\\frac')
    expect(f.tag).toBeNull()
  })

  it('定义行解析为 definition callout', () => {
    const blocks = parseSliceBlocks(REAL_CHUNK)
    const d = blocks.find((b) => b.kind === 'definition')
    expect(d).toBeTruthy()
    if (d?.kind !== 'definition') throw new Error('unreachable')
    expect(d.text).toContain('椭圆')
    expect(d.text.startsWith('定义')).toBe(false)
  })

  it('节号徽章标题：no 与标题分离', () => {
    const blocks = parseSliceBlocks(REAL_CHUNK)
    const h = blocks.find((b) => b.kind === 'heading')
    expect(h).toBeTruthy()
    if (h?.kind !== 'heading') throw new Error('unreachable')
    expect(h.no).toBe('3.1.1')
    expect(h.level).toBe(1)
    expect(h.text).toBe('椭圆的标准方程')
  })

  it('公式块编号（\\tag{2.3-1} / 末行 (2.3-1)）提取为 tag', () => {
    const a = parseSliceBlocks('$$\na+b=c\n\\tag{2.3-1}\n$$')
    expect(a[0]).toMatchObject({ kind: 'formula', tag: '2.3-1' })
    const b = parseSliceBlocks('$$\na+b=c\n(2.3-1)\n$$')
    expect(b[0]).toMatchObject({ kind: 'formula', tag: '2.3-1' })
    if (b[0]?.kind !== 'formula') throw new Error('unreachable')
    expect(b[0].tex).not.toContain('2.3-1')
  })
})

describe('sliceMarkdown · G22 反泄漏', () => {
  it('可见文本（块文本 + 图题）零源码探针', () => {
    const blocks = parseSliceBlocks(REAL_CHUNK)
    const visible = blocks
      .map((b) => (b.kind === 'figure' ? b.caption : b.kind === 'formula' ? '' : b.text))
      .join('\n')
    expect(visible.replace(/\s+/g, ' ')).not.toMatch(LEAK_RE)
  })

  it('cleanInlineSlice：`\\-` 转义清洗、数学保留、裸 \\frac 剥除', () => {
    expect(cleanInlineSlice('a \\- b')).toBe('a - b')
    expect(cleanInlineSlice('斜率 $\\frac{y}{x}$ 恒等')).toContain('$\\frac{y}{x}$')
    expect(cleanInlineSlice('孤立 \\frac 不上屏')).not.toMatch(/\\frac/)
    expect(cleanInlineSlice('图片 ![](images/x.jpg) 后文')).not.toContain('![](')
  })

  it('slicePreviewText：折叠预览零源码探针（含数学整段丢弃）', () => {
    const raw =
      '## 1.2.3 直线的一般式方程\n\n$$\nA x + B y + C = 0 (A, B \\text{} {不全为} 0)\\tag{*}\n$$\n\\- 已经学习过直线方程的两种形式'
    const preview = slicePreviewText(raw)
    expect(preview).not.toMatch(LEAK_RE)
    expect(preview).toContain('直线的一般式方程')
  })

  it('未闭合数学定界符不裸泄 LaTeX', () => {
    expect(cleanInlineSlice('未闭合 $\\frac{a}{b} 之后的文本')).not.toMatch(/\\frac|\\text/)
    expect(slicePreviewText('未闭合 $$\\frac{a}{b}')).not.toMatch(LEAK_RE)
  })
})

describe('sliceMarkdown · chunk 作用域', () => {
  const mixed = parseSliceBlocks(
    ['## 1.1 直线的斜率', '斜率公式 $k=\\frac{y_2-y_1}{x_2-x_1}$。', '', '## 1.2.1 直线的点斜式方程', '点斜式正文。', '', '## 圆的方程', '圆的正文。'].join('\n'),
  )

  it('只保留本会话章节（含子节），混入章节整段剔除', () => {
    const scoped = scopeBlocksToSection(mixed, '1.2.1', { chunkScoped: true })
    const texts = scoped.map(textOf)
    expect(texts.join('|')).toContain('点斜式正文')
    expect(texts.join('|')).not.toContain('斜率公式')
    expect(texts.join('|')).not.toContain('圆的正文')
  })

  it('全部为外节编号标题的 chunk 整块剔除', () => {
    const scoped = scopeBlocksToSection(mixed, '3.1.1', { chunkScoped: true })
    expect(scoped).toHaveLength(0)
  })

  it('无编号标题续段（思考/探究）：仅 chunkScoped 时保留', () => {
    const blocks = parseSliceBlocks('## 思考\n观察图 3-1-3, 能找出线段吗？')
    expect(scopeBlocksToSection(blocks, '1.2.1', { chunkScoped: true }).length).toBe(2)
    expect(scopeBlocksToSection(blocks, '1.2.1', { chunkScoped: false }).length).toBe(0)
  })

  it('无编号主题型小节（圆的方程）= 章节边界，混入内容剔除', () => {
    const blocks = parseSliceBlocks('## 1.2.1 直线的点斜式方程\n点斜式正文。\n## 圆的方程\n圆的正文。')
    const scoped = scopeBlocksToSection(blocks, '1.2.1', { chunkScoped: true, targetTitle: '1.2.1 直线的点斜式方程' })
    const texts = scoped.map(textOf).join('|')
    expect(texts).toContain('点斜式正文')
    expect(texts).not.toContain('圆的正文')
  })

  it('主题型标题 chunk（圆的方程）不冒充本节续段', () => {
    const blocks = parseSliceBlocks('## 圆的方程\n圆的正文。')
    expect(scopeBlocksToSection(blocks, '1.2.1', { chunkScoped: true, targetTitle: '1.2.1 直线的点斜式方程' })).toHaveLength(0)
  })

  it('父级标题（3.1）保留在 3.1.1 作用域内，兄弟节（3.1.2）剔除', () => {
    const blocks = parseSliceBlocks('## 3.1 椭圆\n父级引入。\n## 3.1.1 椭圆的标准方程\n正文。\n## 3.1.2 椭圆的几何性质\n兄弟节。')
    const scoped = scopeBlocksToSection(blocks, '3.1.1', { chunkScoped: true })
    const texts = scoped.map(textOf)
    expect(texts.join('|')).toContain('父级引入')
    expect(texts.join('|')).toContain('正文')
    expect(texts.join('|')).not.toContain('兄弟节')
  })
})

describe('sliceMarkdown · 定位与图源', () => {
  it('ref / 构造器与节号解析', () => {
    expect(sectionNoOf('1.2.1 直线的点斜式方程')).toBe('1.2.1')
    expect(sectionNoOf('没有编号')).toBeNull()
    const ref = 'textbook_v2/locator/v1/source/f9bf86f3-87cc-4067-9dd9-e8598b7c21c3/section/daa0baca-dca5-47a4-b64d-6c6c4bd668bf'
    expect(refSectionId(ref)).toBe('daa0baca-dca5-47a4-b64d-6c6c4bd668bf')
    expect(sectionRef('f9bf86f3-87cc-4067-9dd9-e8598b7c21c3', 'daa0baca-dca5-47a4-b64d-6c6c4bd668bf')).toBe(ref)
    expect(sectionRef('bad', 'daa0baca-dca5-47a4-b64d-6c6c4bd668bf')).toBeNull()
  })

  it('切片图路径 → 教材图资源 URL；非法路径 → null（降级占位，不回退源码）', () => {
    expect(sliceImageUrl('images/d13fb6b8f48bef164c6913c08474596da5fefd3f0521387ee265854ebdea0189.jpg')).toBe(
      '/api/teacher-v3/textbook-v2/slice-images/d13fb6b8f48bef164c6913c08474596da5fefd3f0521387ee265854ebdea0189.jpg',
    )
    expect(sliceImageUrl('../../etc/passwd')).toBeNull()
    expect(sliceImageUrl('')).toBeNull()
  })
})

describe('sliceMarkdown · sliceTitleText（R4afix S7 标题数学保真）', () => {
  it('标题保留 $…$ 行内数学（修复「如图, 设, 是…」符号剥失），上屏走 KaTeX', () => {
    const t = sliceTitleText('如图, 设 $P(x_{1}, y_{1})$ , $Q(x_{2}, y_{2})$ 是直线 $l$ 上不同的两点')
    expect(t).toContain('$P(x_{1}, y_{1})$')
    expect(t).toContain('$Q(x_{2}, y_{2})$')
    expect(t).toContain('是直线')
  })

  it('数学感知截断：不切进数学段、不产生未闭合定界符', () => {
    const t = sliceTitleText('一般地，设直线 $l$ 过点 $P_{1}(x_{1}, y_{1})$，斜率为 $k$，那么如何求直线的方程呢？', 8)
    expect(t.endsWith('…')).toBe(true)
    expect((t.match(/\$/g) || []).length % 2).toBe(0)
    expect(t).not.toMatch(LEAK_RE)
  })

  it('清洗产物零源码探针（G22：![]( / \\- / $$ / \\frac 不上屏）', () => {
    expect(sliceTitleText('\\- 列表项 ![](images/x.jpg) 带 $$裸公式$$ 与 \\frac 转义')).not.toMatch(LEAK_RE)
  })
})
