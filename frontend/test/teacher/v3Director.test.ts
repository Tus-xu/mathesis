/**
 * B7 · 构图导演任务路由与步骤生成（纯函数单测）
 */
import { describe, it, expect } from 'vitest'
import { DIRECTOR_TASKS, parseDirectorInput, taskDefaults, taskPreset, buildTaskSteps } from '@/components/mathx/director/tasks'

describe('构图导演 · 任务路由', () => {
  it('六类任务全部注册，词表命中路由正确', () => {
    // 六类首批任务：立方体/棱锥截面同属“多面体截面”类，模板数可 > 类别数
    expect(new Set(DIRECTOR_TASKS.map((t) => t.category)).size).toBe(6)
    expect(parseDirectorInput('正方体过三条棱的中点作截面').task?.id).toBe('cube-section')
    expect(parseDirectorInput('球被平面截出的截面圆半径').task?.id).toBe('sphere-section')
    expect(parseDirectorInput('直线和平面的位置关系有哪些').task?.id).toBe('line-plane')
    expect(parseDirectorInput('椭圆的切线与焦点弦').task?.id).toBe('ellipse-tangent')
    expect(parseDirectorInput('k变化时两个函数的交点怎么动').task?.id).toBe('function-intersection')
    expect(parseDirectorInput('正方体的展开图怎么折叠').task?.id).toBe('cube-net')
  })

  it('未命中如实 fallback（不假装支持）', () => {
    const r = parseDirectorInput('画一个正弦定理推导的流程图')
    expect(r.fallback).toBe(true)
    expect(r.task).toBeNull()
  })

  it('每个任务：预设存在、参数有默认值、步骤生成含依赖且确定性', () => {
    for (const t of DIRECTOR_TASKS) {
      const def = taskPreset(t)
      expect(def.id).toBe(t.presetId)
      const p = taskDefaults(t)
      const steps = buildTaskSteps(t, p)
      expect(steps.length).toBeGreaterThanOrEqual(3)
      expect(steps.every((s) => s.text && s.object)).toBe(true)
      // 确定性：同参数同步骤文本
      const again = buildTaskSteps(t, p)
      expect(steps.map((x) => x.text)).toEqual(again.map((x) => x.text))
    }
  })

  it('立方体截面步骤引用参数值（比例联动）', () => {
    const t = DIRECTOR_TASKS.find((x) => x.id === 'cube-section')!
    const p = { ...taskDefaults(t), t1: 0.3 }
    const steps = buildTaskSteps(t, p)
    expect(steps[0].text).toContain('30%')
  })
})
