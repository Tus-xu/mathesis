import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'

const root = process.cwd()
const deckEditor = fs.readFileSync(
  path.join(root, 'src/features/teacher-prep-center/stores/deckEditor.ts'),
  'utf-8',
)
const props = fs.readFileSync(
  path.join(root, 'src/features/teacher-prep-center/ppt/PropertiesPanel.vue'),
  'utf-8',
)
const regen = fs.readFileSync(
  path.join(root, '..', 'math-arena/services/teacher-platform/app/ppt_v2/service/regen.py'),
  'utf-8',
)

describe('PPT AI 快捷操作 · 名称与真实能力一致', () => {
  it('推导动画：模式 derivation + 主题护栏，不再硬编码椭圆', () => {
    const start = deckEditor.indexOf('async aiDeriveAnimation')
    expect(start).toBeGreaterThan(-1)
    const block = deckEditor.slice(start, deckEditor.indexOf('async aiPracticeForPage', start))
    expect(block).not.toContain('椭圆')
    expect(block).toContain("mode: 'derivation'")
    expect(block).toContain('previewDemo = true')
    expect(block).toContain('previewOpen = true')
  })

  it('属性面板提供真实“生成推导动画”，但只对推导/例题页开放', () => {
    expect(props).toContain("title: '生成推导动画'")
    expect(props).toContain("['derivation', 'worked-example']")
    expect(props).not.toContain('生成动态演示')
  })

  it('后端 derivation 提示要求编号步骤，并用主题护栏拒绝串题', () => {
    expect(regen).toContain('_SYSTEM_PROMPT_DERIVATION')
    expect(regen).toContain('按 1. 2. 3. 编号')
    expect(regen).toContain('drift = generated_topics - deck_topics')
  })
})
