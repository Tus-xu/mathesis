/**
 * B5 · 课堂状态机 / 分支建议 / 确定性加入序列（纯函数单测）
 */
import { describe, it, expect } from 'vitest'
import {
  canTransition, joinSequence, suggestBranch, BRANCH_CARDS,
  buildSummary, fmtClock, QUESTION_TIME_MAX, QUESTION_TIME_DEFAULT,
} from '@/pages/teacher-v3/classroomLogic'

describe('课堂状态机', () => {
  it('合法迁移全通，非法迁移全拒', () => {
    expect(canTransition('idle', 'waiting')).toBe(true)
    expect(canTransition('waiting', 'collecting')).toBe(true)
    expect(canTransition('collecting', 'stopped')).toBe(true)
    expect(canTransition('stopped', 'revealed')).toBe(true)
    expect(canTransition('stopped', 'collecting')).toBe(true) // 重发题
    expect(canTransition('revealed', 'collecting')).toBe(true) // 讲评后下一题
    expect(canTransition('revealed', 'ended')).toBe(true)
    // 非法
    expect(canTransition('idle', 'collecting')).toBe(false)
    expect(canTransition('waiting', 'revealed')).toBe(false)
    expect(canTransition('collecting', 'waiting')).toBe(false)
    expect(canTransition('ended', 'collecting')).toBe(false)
  })
})

describe('确定性加入序列', () => {
  it('人数=班级人数、顺序固定、间隔递增', () => {
    const roster = ['王雨桐', '陈子豪']
    const seq = joinSequence(5, roster)
    expect(seq.length).toBe(5)
    expect(seq[0].name).toBe('王雨桐')
    expect(seq[1].name).toBe('陈子豪')
    expect(seq[2].name).toContain('王雨桐')
    expect(seq.every((x, i) => i === 0 || x.atMs > seq[i - 1].atMs)).toBe(true)
  })
})

describe('分支建议（只排序，不自动执行）', () => {
  it('按正确率阈值排序，三张卡齐全', () => {
    expect(suggestBranch(80)[0].kind).toBe('boost')
    expect(suggestBranch(60)[0].kind).toBe('partial')
    expect(suggestBranch(30)[0].kind).toBe('reteach')
    expect(suggestBranch(80).length).toBe(BRANCH_CARDS.length)
    // 每张卡必须可解释：触发条件 + 耗时 + 动作
    for (const c of suggestBranch(60)) {
      expect(c.trigger).toBeTruthy()
      expect(c.minutes).toBeGreaterThan(0)
      expect(c.action).toBeTruthy()
    }
  })
})

describe('课堂小结与时钟', () => {
  it('汇总来自已发生数据，无数据不出 NaN', () => {
    const s = buildSummary({ askedCount: 3, rates: [80, 60, 40], wrongTags: ['概念混淆', '计算错误'], branchesUsed: ['partial'], elapsedMs: 9 * 60000 })
    expect(s).toEqual({ questions: 3, avgCorrectRate: 60, topWrong: '概念混淆', branchesUsed: ['部分卡住 · 对比例题'], durationMin: 9 })
    const empty = buildSummary({ askedCount: 0, rates: [], wrongTags: [], branchesUsed: [], elapsedMs: 0 })
    expect(empty.avgCorrectRate).toBe(0)
    expect(empty.topWrong).toBe('—')
    expect(Number.isFinite(empty.durationMin)).toBe(true)
  })
  it('时钟格式与上限', () => {
    expect(fmtClock(125)).toBe('2:05')
    expect(fmtClock(-1)).toBe('0:00')
    expect(QUESTION_TIME_MAX).toBeGreaterThanOrEqual(QUESTION_TIME_DEFAULT)
  })
})
