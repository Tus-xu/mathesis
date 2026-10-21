import { describe, expect, it } from 'vitest'
import { practiceGenerationMeta } from '@/utils/dualClassroom'

describe('practiceGenerationMeta 练习题生成状态', () => {
  it('仅在明确 pending 时显示生成中', () => {
    expect(practiceGenerationMeta({ practice_generation: { status: 'pending' } })).toEqual(
      expect.objectContaining({ state: 'pending' }),
    )
  })

  it('AI 已失败时结束转圈并给出可恢复状态', () => {
    expect(practiceGenerationMeta({ practice_generation: { status: 'failed', error: 'provider timeout' } })).toEqual(
      expect.objectContaining({ state: 'failed', error: 'provider timeout' }),
    )
  })

  it('旧会话缺少状态时不无限显示加载', () => {
    expect(practiceGenerationMeta({})).toEqual(expect.objectContaining({ state: 'unknown' }))
  })
})
