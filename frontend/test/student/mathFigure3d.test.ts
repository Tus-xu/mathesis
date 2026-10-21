/**
 * MathFigure3D 相关单测：
 * 1) 场景归一化（纯函数）——坏数据剔除、越界收敛、不执行表达式；
 * 2) 组件挂载兜底——jsdom 无 WebGL，应展示降级提示而非抛错。
 */
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { normalizeScene, toPt } from '@/utils/mathFigure3d'
import MathFigure3D from '@/components/chat/MathFigure3D.vue'

describe('toPt', () => {
  it('收敛合法坐标并四舍五入 4 位', () => {
    expect(toPt([1.123456, -2, 3])).toEqual([1.1235, -2, 3])
    expect(toPt([0, 0, 0])).toEqual([0, 0, 0])
  })
  it('拒绝越界 / 非数字 / 长度不足', () => {
    expect(toPt([999, 0, 0])).toBeNull()
    expect(toPt([1, 'x', 3])).toBeNull()
    expect(toPt([1, 2])).toBeNull()
    expect(toPt(null)).toBeNull()
  })
})

describe('normalizeScene', () => {
  it('空输入返回 null', () => {
    expect(normalizeScene(null)).toBeNull()
    expect(normalizeScene('junk')).toBeNull()
    expect(normalizeScene({})).toBeNull()
    expect(normalizeScene([1, 2])).toBeNull()
  })

  it('保留合法多面体场景并裁剪 caption', () => {
    const scene = normalizeScene({
      caption: '  四棱柱 ABCD-EFGH  ',
      solids: [
        {
          kind: 'polyhedron',
          vertices: [
            { name: 'A', pos: [0, 0, 0] },
            { name: 'B', pos: [1, 0, 0] },
            { name: 'C', pos: [1, 1, 0] },
          ],
          edges: [['A', 'B'], ['B', 'C'], ['B', 'X', 'Y']],
          opacity: 0.5,
        },
      ],
      segments: [{ a: [0, 0, 0], b: [1, 1, 0], dashed: true, label: ' AC ' }],
    })
    expect(scene).not.toBeNull()
    const s = scene!
    expect(s.caption).toBe('四棱柱 ABCD-EFGH')
    // 顶点统一重命名为 v0..vN（原 name 存为 labels），edges 引用下标名；非法边被剔除
    expect(s.solids[0].edges).toEqual([['v0', 'v1'], ['v1', 'v2']])
    expect(s.segments[0].dashed).toBe(true)
    expect(s.segments[0].label).toBe('AC')
  })

  it('越界中心点被剔除，坏实体不影响其余实体', () => {
    const scene = normalizeScene({
      solids: [
        { kind: 'box', center: [9999, 0, 0], size: [1, 1, 1] },
        { kind: 'sphere', center: [0, 0, 0], radius: 2 },
        { kind: 'warp', size: [1, 1, 1] },
      ],
    })
    expect(scene).not.toBeNull()
    const s = scene!
    expect(s.solids).toHaveLength(1)
    expect(s.solids[0].kind).toBe('sphere')
  })

  it('前端不执行表达式：仅含 expr 的曲线被剔除，显式点集直接透传', () => {
    const scene = normalizeScene({
      curves: [
        { kind: 'parametric', expr: ['cos(t)', 'sin(t)', '0'] },
        { kind: 'polyline', points: [[0, 0, 0], [1, 1, 0]] },
      ],
    })
    expect(scene).not.toBeNull()
    const s = scene!
    expect(s.curves).toHaveLength(1)
    expect(s.curves[0].points).toEqual([[0, 0, 0], [1, 1, 0]])
  })

  it('grid / axes 默认开启，可显式关闭', () => {
    const a = normalizeScene({ grid: false, curves: [{ points: [[0, 0, 0], [1, 0, 0]] }] })
    const b = normalizeScene({ axes: false, curves: [{ points: [[0, 0, 0], [1, 0, 0]] }] })
    expect(a?.grid).toBe(false)
    expect(b?.axes).toBe(false)
  })
})

describe('MathFigure3D 组件', () => {
  it('jsdom 无 WebGL：挂载展示降级提示且不抛错', () => {
    const w = mount(MathFigure3D, {
      props: {
        figure: {
          solids: [{ kind: 'box', center: [0, 0, 0], size: [2, 2, 2] }],
        },
        caption: '正方体',
      },
    })
    expect(w.exists()).toBe(true)
    // WebGL 创建失败 → err 展示；Canvas 上下文缺失时也不应抛异常
    expect(w.text()).toContain('交互图形')
  })
})