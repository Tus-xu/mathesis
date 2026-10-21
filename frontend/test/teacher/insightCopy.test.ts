import { describe, expect, it } from 'vitest'
import { evidenceText, insightTitle, isKnownInsightKind } from '@/utils/insightCopy'

describe('insightCopy 洞察文案模板引擎（TC-L5-F03 / 红线 R2）', () => {
  it('evidenceText 保留人话证据，过滤 key=value 诊断字段', () => {
    expect(evidenceText('本班有 5 份低置信度作答需要确认。')).toBe('本班有 5 份低置信度作答需要确认。')
    expect(evidenceText('pending_count=5; low_confidence=2')).toBe('证据格式待更新，暂不展示内部诊断字段。')
    expect(evidenceText('type=rule')).toBe('证据格式待更新，暂不展示内部诊断字段。')
    // 数学赋值（a=0 边界）不误伤——数据世界核心话术
    expect(evidenceText('17/46 人在最近两次作业的 a=0 边界题失分。')).toContain('a=0 边界题失分')
    expect(evidenceText('')).toBe('暂无更多证据')
    expect(evidenceText(null)).toBe('暂无更多证据')
  })

  it('insightTitle 优先后端 summary 直传', () => {
    expect(insightTitle({ kind: 'error_cluster', summary: 'a=0 边界连续两次失分集中' })).toBe('a=0 边界连续两次失分集中')
  })

  it('summary 缺失时按 kind 模板生成，数字仅来自字段（无 count 不编造）', () => {
    const noCount = insightTitle({ kind: 'review_backlog' })
    expect(noCount).toBe('有待教师确认的作答')
    expect(noCount).not.toMatch(/\d/)
    expect(insightTitle({ kind: 'low_mastery', count: 12 })).toBe('知识点掌握偏弱：12 份')
    expect(insightTitle({ kind: 'error_cluster', count: 17, total: 46 })).toBe('跨作业错题集中出现：17 份（17/46）')
  })

  it('未知 kind 落通用句式，且 isKnownInsightKind 只认权威四枚举', () => {
    expect(insightTitle({ kind: 'mystery_kind' })).toBe('班级学情提示')
    expect(isKnownInsightKind('error_cluster')).toBe(true)
    expect(isKnownInsightKind('mastery_drop')).toBe(false)
  })
})
