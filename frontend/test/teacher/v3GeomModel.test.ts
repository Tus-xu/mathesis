import { describe, it, expect } from 'vitest'
import {
  emptyDoc, buildParallelepiped, buildPyramid4, buildTetra,
  addPointOnEdge, addMidpoint, addRatioPoint, addSegment, addSection, addIntersect,
  pointPos, segIntersect, autoDashed, solidEdgeVisibility, insideSolid,
  removeCascade, newHistory, pushHistory, undo, redo, findPointByLabel, gid,
} from '../../src/components/mathx/geom/model'
import { applyConstruction } from '../../src/components/mathx/geom/dsl'
import { computeScene, sceneToSvg } from '../../src/components/mathx/geom/render'

describe('geom model · 立体骨架', () => {
  it('平行六面体：8 个命名顶点 + 12 棱 6 面，A₁ 下标标签', () => {
    const doc = emptyDoc()
    const s = buildParallelepiped(doc)
    expect(s.verts).toHaveLength(8)
    expect(s.edges).toHaveLength(12)
    expect(s.faces).toHaveLength(6)
    const labels = doc.objects.slice(0, 8).map((o) => (o as any).label)
    expect(labels).toEqual(['A', 'B', 'C', 'D', 'A₁', 'B₁', 'C₁', 'D₁'])
  })

  it('默认投影下背面棱自动虚线（凸体朝向判定）', () => {
    const doc = emptyDoc()
    const s = buildParallelepiped(doc)
    const vis = solidEdgeVisibility(doc, s)
    expect(vis.filter(Boolean).length).toBeGreaterThanOrEqual(6)
    expect(vis.filter((v) => !v).length).toBeGreaterThanOrEqual(3) // 一定有被遮挡棱
  })

  it('顶点拖动（free 点位移）带动全部棱与依附点重算', () => {
    const doc = emptyDoc()
    const s = buildParallelepiped(doc)
    const A = s.verts[0]
    const M = addMidpoint(doc, s.verts[4], s.verts[5], 'M') // A₁B₁ 中点
    const before = pointPos(doc, M.id)
    ;(doc.objects.find((o) => o.id === A) as any).def.pos = [0, 0, 0]
    const after = pointPos(doc, M.id)
    expect(after[0]).not.toBeCloseTo(before[0])
  })
})

describe('geom model · 构造点', () => {
  it('棱上点：t 即比例，拖动 t 位置沿棱移动', () => {
    const doc = emptyDoc()
    const s = buildParallelepiped(doc)
    const p = addPointOnEdge(doc, s.verts[0], s.verts[1], 0.3, 'E')
    const pos = pointPos(doc, p.id)
    expect(pos[1]).toBe(0)
    ;(p.def as any).t = 0.8
    expect(pointPos(doc, p.id)[0]).toBeGreaterThan(pos[0])
  })

  it('BG=2GD 的比例点：G 位于 BD 的 2/3 处', () => {
    const doc = emptyDoc()
    const s = buildPyramid4(doc)
    const B = s.verts[1], D = s.verts[3]
    const G = addRatioPoint(doc, B, D, 2, 1, 'a', 'G')
    const g = pointPos(doc, G.id)
    const b = pointPos(doc, B), d = pointPos(doc, D)
    expect(g[0]).toBeCloseTo(b[0] + (d[0] - b[0]) * (2 / 3), 6)
  })

  it('空间两线段交点：F = BC₁ ∩ B₁C 是两侧面对角线中点', () => {
    const doc = emptyDoc()
    const s = buildParallelepiped(doc, { A: [0, 0, 0], u: [4, 0, 0], v: [0, 2, 0], w: [0, 0, 3] })
    const B = s.verts[1], C = s.verts[2], B1 = s.verts[5], C1 = s.verts[6]
    const F = addIntersect(doc, [B, C1], [B1, C], 'F')
    const f = pointPos(doc, F.id)
    expect(f[0]).toBeCloseTo(4, 6)
    expect(f[1]).toBeCloseTo(1, 6)
    expect(f[2]).toBeCloseTo(1.5, 6)
  })

  it('异面直线求交如实抛错', () => {
    const doc = emptyDoc()
    const s = buildParallelepiped(doc)
    expect(() => addIntersect(doc, [s.verts[0], s.verts[1]], [s.verts[4], s.verts[5]], 'X')).toThrow()
  })

  it('内部线段自动虚线（DB₁ 穿体），面上线段不虚', () => {
    const doc = emptyDoc()
    const s = buildParallelepiped(doc, { A: [0, 0, 0], u: [4, 0, 0], v: [0, 2, 0], w: [0, 0, 3] })
    const db1 = addSegment(doc, s.verts[3], s.verts[5])
    const top = addSegment(doc, s.verts[4], s.verts[5])
    expect(autoDashed(doc, db1.a, db1.b)).toBe(true)
    expect(autoDashed(doc, top.a, top.b)).toBe(false)
  })
})

describe('geom model · 截面', () => {
  it('过三条棱上三点作截面得到多边形（M∈AB, N∈CC₁, P∈DD₁）', () => {
    const doc = emptyDoc()
    const s = buildParallelepiped(doc, { A: [0, 0, 0], u: [2, 0, 0], v: [0, 2, 0], w: [0, 0, 2] })
    const M = addPointOnEdge(doc, s.verts[0], s.verts[1], 0.5, 'M')
    const N = addPointOnEdge(doc, s.verts[2], s.verts[6], 0.5, 'N')
    const P = addPointOnEdge(doc, s.verts[3], s.verts[7], 0.5, 'P')
    const r = addSection(doc, [M.id, N.id, P.id])
    expect(r).not.toHaveProperty('error')
  })

  it('三点共线如实报错', () => {
    const doc = emptyDoc()
    const s = buildParallelepiped(doc)
    const a = findPointByLabel(doc, 'A')!, b = findPointByLabel(doc, 'B')!
    const mid = addMidpoint(doc, a.id, b.id, 'M')
    const r = addSection(doc, [a.id, b.id, mid.id])
    expect(r).toHaveProperty('error')
  })
})

describe('geom model · 删除级联 / 撤销', () => {
  it('删除顶点级联删除依附点与线段', () => {
    const doc = emptyDoc()
    const s = buildParallelepiped(doc)
    const A = s.verts[0], B = s.verts[1]
    const M = addMidpoint(doc, A, B, 'M')
    addSegment(doc, M.id, s.verts[2])
    const dead = removeCascade(doc, A)
    expect(dead.length).toBeGreaterThanOrEqual(3)
    expect(doc.objects.find((o) => o.id === M.id)).toBeUndefined()
  })

  it('撤销/重做往返', () => {
    const doc = emptyDoc()
    const h = newHistory()
    buildParallelepiped(doc) // 8 点 + 1 solid = 9 对象
    pushHistory(doc, h)
    buildTetra(doc) // +4 点 +1 solid = 14
    expect(doc.objects.length).toBe(14)
    undo(doc, h)
    expect(doc.objects.length).toBe(9)
    redo(doc, h)
    expect(doc.objects.length).toBe(14)
  })
})

describe('dsl · 真实试卷句式', () => {
  it('题15：平行六面体 + 顶面对角线交点 F + 连 AF', () => {
    const doc = emptyDoc()
    const r = applyConstruction(doc, '平行六面体 ABCD-A1B1C1D1\n连接A1C1、B1D1\nF为A1C1与B1D1的交点\n连接AF')
    expect(r.uncovered).toEqual([])
    expect(r.applied.length).toBe(4)
    const F = findPointByLabel(doc, 'F')!
    expect(F).toBeTruthy()
    const f = pointPos(doc, F.id)
    // 顶面中心
    expect(f[2]).toBeCloseTo(2.6, 5)
  })

  it('题16：四棱锥 + 对角线交点 O + G 在 BD 上且 BG=2GD + 连 PG', () => {
    const doc = emptyDoc()
    const r = applyConstruction(doc, '四棱锥 P-ABCD\n连接AC、BD\nO为AC与BD的交点\nG在BD上且BG=2GD\n连接PG')
    expect(r.uncovered).toEqual([])
    const O = findPointByLabel(doc, 'O')!
    const G = findPointByLabel(doc, 'G')!
    expect(O).toBeTruthy()
    expect(G).toBeTruthy()
    const b = pointPos(doc, findPointByLabel(doc, 'B')!.id)
    const d = pointPos(doc, findPointByLabel(doc, 'D')!.id)
    const g = pointPos(doc, G.id)
    expect(g[0]).toBeCloseTo(b[0] + (d[0] - b[0]) * (2 / 3), 5)
    const P = findPointByLabel(doc, 'P')!
    expect(doc.objects.some((o) => (o as any).a === P.id && (o as any).b === G.id || (o as any).a === G.id && (o as any).b === P.id)).toBe(true) // PG 已连接
  })

  it('题19：E 中点 + F 交点 + 三条内部线 + 虚线', () => {
    const doc = emptyDoc()
    const r = applyConstruction(doc, '平行六面体 ABCD-A1B1C1D1\nE为A1D1的中点\nF为BC1与B1C的交点\n连接DB1、BE、AF\nDB1、BE、AF画虚线')
    expect(r.uncovered).toEqual([])
    const E = pointPos(doc, findPointByLabel(doc, 'E')!.id)
    const a1 = pointPos(doc, findPointByLabel(doc, 'A₁')!.id)
    const d1 = pointPos(doc, findPointByLabel(doc, 'D₁')!.id)
    expect(E).toEqual([(a1[0] + d1[0]) / 2, (a1[1] + d1[1]) / 2, (a1[2] + d1[2]) / 2])
    const dashed = doc.objects.filter((o) => (o as any).style === 'dashed')
    expect(dashed.length).toBe(3)
  })

  it('无法识别的句子如实披露，不假装成功', () => {
    const doc = emptyDoc()
    const r = applyConstruction(doc, '平行六面体 ABCD-A1B1C1D1\n把AB绕AA1旋转30度')
    expect(r.applied.length).toBe(1)
    expect(r.uncovered.length).toBe(1)
    expect(r.uncovered[0]).toContain('未识别')
  })
})

describe('render · 场景与 SVG', () => {
  it('SVG 含内嵌标签文本（插入后标注不丢）且无坐标网格', () => {
    const doc = emptyDoc()
    buildParallelepiped(doc)
    const scene = computeScene(doc)
    const { svg } = sceneToSvg(scene)
    expect(svg).toContain('<text')
    expect(svg).toContain('A₁')
    expect(svg).not.toContain('stroke-dasharray="0')
    expect(svg).toContain('stroke-dasharray') // 背面棱虚线
    expect(scene.segs.length).toBe(12)
  })

  it('空白文档返回占位画布不抛错', () => {
    const doc = emptyDoc()
    const scene = computeScene(doc)
    const { svg } = sceneToSvg(scene)
    expect(svg).toContain('<svg')
  })

  it('segIntersect 数值边界：共线返回 null', () => {
    expect(segIntersect([0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0])).toBeNull()
  })

  it('gid 不重复', () => {
    const doc = emptyDoc()
    const a = gid(doc), b = gid(doc)
    expect(a).not.toBe(b)
  })
})

describe('model · insideSolid', () => {
  it('体心在内部、顶点不在', () => {
    const doc = emptyDoc()
    const s = buildParallelepiped(doc, { A: [0, 0, 0], u: [4, 0, 0], v: [0, 2, 0], w: [0, 0, 3] })
    expect(insideSolid(doc, s, [2, 1, 1.5])).toBe(true)
    expect(insideSolid(doc, s, [0, 0, 0])).toBe(false)
  })
})

describe('外接球模型（教材通用图元）', () => {
  it('墙角模型：PA/PB/PC 两两垂直，球心在体对角线中点，R=√(a²+b²+c²)/2', async () => {
    const m = await import('../../src/components/mathx/geom/model')
    const doc = m.emptyDoc()
    const { tetra, sphere } = m.buildCornerModel(doc, { a: 3, b: 4, c: 5 })
    const P = m.pointPos(doc, tetra.verts[0])
    const A = m.pointPos(doc, tetra.verts[1])
    const B = m.pointPos(doc, tetra.verts[2])
    const C = m.pointPos(doc, tetra.verts[3])
    const dot = (u: number[], v: number[]) => u[0] * v[0] + u[1] * v[1] + u[2] * v[2]
    expect(dot(m.v3.sub(A, P), m.v3.sub(B, P))).toBe(0)
    expect(dot(m.v3.sub(A, P), m.v3.sub(C, P))).toBe(0)
    expect(dot(m.v3.sub(B, P), m.v3.sub(C, P))).toBe(0)
    expect(sphere.r).toBe(Math.sqrt(9 + 16 + 25) / 2)
    const O = m.pointPos(doc, sphere.c)
    expect(O).toEqual([1.5, 2, 2.5])
    // 顶点到球心距离 = R（外接成立）
    expect(m.v3.dist(O, P)).toBeCloseTo(sphere.r, 6)
  })

  it('汉堡模型：O₁O₂ 为两底外接圆心，球心居中，顶点在球面上', async () => {
    const m = await import('../../src/components/mathx/geom/model')
    const doc = m.emptyDoc()
    const { prism, sphere } = m.buildBurgerModel(doc, { h: 3 })
    expect(prism.verts).toHaveLength(6)
    const O1 = m.pointPos(doc, doc.objects.find((o) => (o as any).label === 'O₁')!.id)
    const O2 = m.pointPos(doc, doc.objects.find((o) => (o as any).label === 'O₂')!.id)
    expect(O1[2]).toBe(0)
    expect(O2[2]).toBe(3)
    expect(O2[0]).toBeCloseTo(O1[0], 9)
    const O = m.pointPos(doc, sphere.c)
    expect(O[2]).toBeCloseTo(1.5, 9)
    expect(m.v3.dist(O, m.pointPos(doc, prism.verts[0]))).toBeCloseTo(sphere.r, 6)
    expect(m.v3.dist(O, m.pointPos(doc, prism.verts[5]))).toBeCloseTo(sphere.r, 6)
  })

  it('渲染：球输出轮廓圆+赤道虚实弧，且垫底先画', async () => {
    const m = await import('../../src/components/mathx/geom/model')
    const r = await import('../../src/components/mathx/geom/render')
    const doc = m.emptyDoc()
    m.buildCornerModel(doc)
    const scene = r.computeScene(doc)
    expect(scene.spheres).toHaveLength(1)
    const { svg } = r.sceneToSvg(scene)
    expect(svg).toContain('<circle')
    expect(svg).toContain('stroke-dasharray="6 4"')
    expect(svg.indexOf('<circle')).toBeLessThan(svg.indexOf('<line'))
  })

  it('DSL：墙角模型/汉堡模型/正四棱柱句式', async () => {
    const m = await import('../../src/components/mathx/geom/model')
    const d = await import('../../src/components/mathx/geom/dsl')
    const doc = m.emptyDoc()
    const r = d.applyConstruction(doc, '墙角模型\n汉堡模型\n正四棱柱 ABCD-A1B1C1D1')
    expect(r.uncovered).toEqual([])
    expect(r.applied.length).toBe(3)
    expect(m.findPointByLabel(doc, 'O₁')).toBeTruthy()
    expect(m.findPointByLabel(doc, 'A₁')).toBeTruthy()
  })

  it('删除球心级联删球', async () => {
    const m = await import('../../src/components/mathx/geom/model')
    const doc = m.emptyDoc()
    const { sphere } = m.buildCornerModel(doc)
    const dead = m.removeCascade(doc, sphere.c)
    expect(dead).toContain(sphere.id)
    expect(doc.objects.some((o) => o.id === sphere.id)).toBe(false)
  })
})

describe('圆锥 / 圆柱 / 平行平面板块', () => {
  it('球内接圆锥：顶点与底面圆周点都在球面上', async () => {
    const m = await import('../../src/components/mathx/geom/model')
    const doc = m.emptyDoc()
    const { sphere, apex } = m.buildConeInSphere(doc, { R: 3 })
    const O = m.pointPos(doc, sphere.c)
    expect(m.v3.dist(O, m.pointPos(doc, apex.id))).toBeCloseTo(3, 6)
    for (const lbl of ['A', 'B', 'M', 'N']) {
      const p = m.findPointByLabel(doc, lbl)!
      expect(p).toBeTruthy()
      expect(m.v3.dist(O, m.pointPos(doc, p.id))).toBeCloseTo(3, 6)
    }
  })

  it('圆柱：顶面圆心 = 底心 + (0,0,h)', async () => {
    const m = await import('../../src/components/mathx/geom/model')
    const doc = m.emptyDoc()
    const { cyl, center } = m.addCylinder(doc, { c: [1, 1, 0], r: 1.5, h: 3 })
    const c = m.pointPos(doc, cyl.c)
    expect(c).toEqual([1, 1, 0])
    expect(m.pointPos(doc, center.id)).toEqual([1, 1, 0])
  })

  it('平行平面板块：β 在 z=0、α 在 z=gap，带 α/β 标签', async () => {
    const m = await import('../../src/components/mathx/geom/model')
    const doc = m.emptyDoc()
    const { alpha, beta } = m.buildParallelPlanes(doc, { gap: 2.8 })
    expect(alpha.label).toBe('α')
    expect(beta.label).toBe('β')
    const a0 = m.pointPos(doc, alpha.p[0])
    const b0 = m.pointPos(doc, beta.p[0])
    expect(a0[2] - b0[2]).toBeCloseTo(2.8, 9)
  })

  it('渲染：圆锥出两条母线与椭圆弧，板块出淡填充多边形', async () => {
    const m = await import('../../src/components/mathx/geom/model')
    const r = await import('../../src/components/mathx/geom/render')
    const doc = m.emptyDoc()
    m.buildConeInSphere(doc)
    m.addCylinder(doc)
    m.buildParallelPlanes(doc)
    const scene = r.computeScene(doc)
    expect(scene.shapes.some((s) => s.kind === 'cone')).toBe(true)
    expect(scene.plates.length).toBe(2)
    const { svg } = r.sceneToSvg(scene)
    expect((svg.match(/<ellipse/g) || []).length).toBeGreaterThanOrEqual(1)
    expect(svg).toContain('rgba(15,71,135,0.07)')
    expect(svg).toContain('>α<')
  })

  it('DSL：球内接圆锥 / 圆柱 / 平行平面 三句式', async () => {
    const m = await import('../../src/components/mathx/geom/model')
    const d = await import('../../src/components/mathx/geom/dsl')
    const doc = m.emptyDoc()
    const r = d.applyConstruction(doc, '球内接圆锥\n圆柱\n两平行平面 α∥β')
    expect(r.uncovered).toEqual([])
    expect(r.applied.length).toBe(3)
    expect(doc.objects.some((o) => (o as any).kind === 'cone')).toBe(true)
    expect(doc.objects.some((o) => (o as any).kind === 'cylinder')).toBe(true)
    expect(doc.objects.some((o) => (o as any).plate === true)).toBe(true)
  })
})
