/**
 * B2 · 旧课二次备课 diff 测试（回滚重建版：原文件随旧编辑器清理被删，按 prepDiff.ts 确定性规则重建等效用例）
 * generateDiff / applyDiff / diffStats 全部纯函数，行为以同输入同输出可复现为验收。
 */
import { describe, it, expect } from 'vitest'
import { generateDiff, applyDiff, diffStats } from '@/pages/teacher-v3/prepDiff'
import { V3_LESSON_PLANS } from '@/mock/teacherV3Data'
import type { V3LessonPlan } from '@/types/teacherV3'

const oldPlan = (): V3LessonPlan => JSON.parse(JSON.stringify(V3_LESSON_PLANS[0]))

describe('prepDiff · generateDiff（确定性差异建议）', () => {
  it('同一份旧教案 + 同一上下文 → 同一组建议（可复现）', () => {
    const ctx = { className: '高二(5)班', weakKp: '椭圆离心率与几何性质', duration: 45 }
    const a = generateDiff(oldPlan(), ctx)
    const b = generateDiff(oldPlan(), ctx)
    expect(a.map((i) => i.id)).toEqual(b.map((i) => i.id))
    expect(a.map((i) => i.newValue ?? '')).toEqual(b.map((i) => i.newValue ?? ''))
  })

  it('依据只允许四类：class_evidence / teacher_directive / duration / ai_suggest；AI 项显式标注', () => {
    const items = generateDiff(oldPlan(), { className: '高二(5)班', weakKp: '判别', duration: 45 })
    expect(items.every((i) => ['class_evidence', 'teacher_directive', 'duration', 'ai_suggest'].includes(i.basis))).toBe(true)
    expect(items.some((i) => i.basis === 'ai_suggest')).toBe(true)
    expect(items.some((i) => i.basis === 'duration')).toBe(true)
  })

  it('每条建议都有标题与理由；保留项 kind=keep', () => {
    const items = generateDiff(oldPlan(), { className: '高二(5)班', weakKp: '判别', duration: 45 })
    expect(items.every((i) => i.title && i.reason)).toBe(true)
    expect(items.find((i) => i.kind === 'keep')).toBeTruthy()
  })
})

describe('prepDiff · applyDiff（应用已接受的建议）', () => {
  it('仅应用 accepted 项；未接受的内容按原教案保留', () => {
    const plan = oldPlan()
    const items = generateDiff(plan, { className: '高二(5)班', weakKp: '判别', duration: 45 })
    items.forEach((i) => { if (i.kind !== 'keep') i.status = i.id === 'd-intro' ? 'accepted' : 'rejected' })
    const next = applyDiff(plan, items)
    const introId = items.find((i) => i.id === 'd-intro')!.target.sectionId!
    const introNew = next.sections.find((s) => s.id === introId)!.teacher_activity
    const introOld = plan.sections.find((s) => s.id === introId)!.teacher_activity
    expect(introNew).not.toBe(introOld)
    expect(introNew).toContain('判别')
    // 其他环节未被改动
    const exploreOld = plan.sections.find((s) => s.id === 'bd-explore')!.teacher_activity
    const exploreNew = next.sections.find((s) => s.id === 'bd-explore')!.teacher_activity
    expect(exploreNew).toBe(exploreOld)
  })

  it('applyDiff 深拷贝：不修改旧 plan', () => {
    const plan = oldPlan()
    const snapshot = JSON.stringify(plan)
    const items = generateDiff(plan, { className: '高二(5)班', weakKp: '判别', duration: 45 })
    items.forEach((i) => { if (i.kind !== 'keep') i.status = 'accepted' })
    applyDiff(plan, items)
    expect(JSON.stringify(plan)).toBe(snapshot)
  })

  it('例题替换：替换类建议可换入新例题（带标签与来源）', () => {
    const plan = oldPlan()
    const items = generateDiff(plan, { className: '高二(5)班', weakKp: '判别', duration: 45 })
    const ex = items.find((i) => i.id === 'd-example')!
    ex.status = 'accepted'
    const next = applyDiff(plan, items)
    const secId = ex.target.sectionId!
    const examples = next.sections.find((s) => s.id === secId)!.examples || []
    expect(examples.length).toBe(1)
    expect(examples[0].label).toContain('二备')
  })
})

describe('prepDiff · diffStats', () => {
  it('按状态汇总 accepted/pending/rejected', () => {
    const items = generateDiff(oldPlan(), { className: '高二(5)班', weakKp: '判别', duration: 45 })
    items.forEach((i, idx) => { i.status = idx % 3 === 0 ? 'accepted' : idx % 3 === 1 ? 'rejected' : 'pending' })
    const st = diffStats(items)
    expect(st.accepted).toBe(items.filter((i) => i.status === 'accepted').length)
    expect(st.pending).toBe(items.filter((i) => i.status === 'pending').length)
    expect(st.rejected).toBe(items.filter((i) => i.status === 'rejected').length)
  })
})
